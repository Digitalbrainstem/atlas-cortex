"""Atlas Cortex pipeline orchestration.

Exports :func:`run_pipeline` (backward-compatible, yields text strings)
and :func:`run_pipeline_events` (yields typed :class:`PipelineEvent` objects).
"""

# Module ownership: Pure NLU pipeline: yields typed events

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncGenerator

from cortex.providers.base import LLMProvider

from .events import (
    PipelineEvent,
    TextToken,
    FillerToken,
    MicroAckToken,
    ExpressionEvent,
    SpeakingEvent,
    VisemeEvent,
    TTSEvent,
    LayerResult,
    TurnEvent,
)
from .layer0_context import assemble_context
from .layer1_instant import try_instant_answer
from .layer2_plugins import dispatch_plugins
from .layer3_llm import stream_llm_response
from .turns import DispatchResult, PreparedTurn, RouteStatus, TurnIdentity, sanitize_metadata

logger = logging.getLogger(__name__)

def _pipeline_safety(db_conn: Any = None) -> tuple[Any, Any]:
    from cortex.pipeline.turns import get_conversation_coordinator
    from cortex.safety.middleware import PipelineSafetyMiddleware
    coordinator = get_conversation_coordinator()
    if coordinator is not None:
        return coordinator.safety, coordinator.store.conn
    return PipelineSafetyMiddleware(db_conn), db_conn


def _validate_output(text: str, prepared: PreparedTurn, safety: Any, prompt: str = "") -> str:
    from cortex.safety import Severity
    checked = safety.check_output(text, prepared.identity.user_id, prepared.message,
                                  prompt, prepared.context.get("metadata", {}))
    if checked.severity >= Severity.SOFT_BLOCK:
        return checked.suggested_response or "I'm not able to provide that response."
    return text


async def run_pipeline(
    message: str,
    provider: LLMProvider,
    user_id: str = "default",
    speaker_id: str | None = None,
    satellite_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    model_fast: str = os.environ.get("MODEL_FAST", "qwen2.5:14b"),
    model_thinking: str = os.environ.get("MODEL_THINKING", "qwen3:30b-a3b"),
    system_prompt: str = "",
    memory_context: str = "",
    db_conn: Any = None,
) -> AsyncGenerator[str, None]:
    """Run the pipeline and yield text tokens only (backward compatible).

    This is a convenience wrapper around :func:`run_pipeline_events` that
    filters for :class:`TextToken` events and yields their text content.
    Callers that need full event control should use ``run_pipeline_events``.
    """
    async for event in run_pipeline_events(
        message=message,
        provider=provider,
        user_id=user_id,
        speaker_id=speaker_id,
        satellite_id=satellite_id,
        conversation_history=conversation_history,
        metadata=metadata,
        model_fast=model_fast,
        model_thinking=model_thinking,
        system_prompt=system_prompt,
        memory_context=memory_context,
        db_conn=db_conn,
    ):
        if isinstance(event, TextToken):
            yield event.text


async def run_pipeline_events(
    message: str,
    provider: LLMProvider,
    user_id: str = "default",
    speaker_id: str | None = None,
    satellite_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    model_fast: str = os.environ.get("MODEL_FAST", "qwen2.5:14b"),
    model_thinking: str = os.environ.get("MODEL_THINKING", "qwen3:30b-a3b"),
    system_prompt: str = "",
    memory_context: str = "",
    db_conn: Any = None,
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the full Atlas Cortex pipeline, yielding typed events.

    Event stream for each layer:
      Layer 1/2 (instant/plugin):
        ExpressionEvent → SpeakingEvent(True) → VisemeEvent → TTSEvent
        → TextToken → SpeakingEvent(False) → LayerResult

      Layer 3 (LLM):
        ExpressionEvent → SpeakingEvent(True) → FillerToken
        → [TextToken + VisemeEvent + TTSEvent per sentence]
        → ExpressionEvent (reaction) → SpeakingEvent(False) → LayerResult
    """
    async for event in _pipeline_event_generator(
        message=message,
        provider=provider,
        user_id=user_id,
        speaker_id=speaker_id,
        satellite_id=satellite_id,
        conversation_history=conversation_history,
        metadata=metadata,
        model_fast=model_fast,
        model_thinking=model_thinking,
        system_prompt=system_prompt,
        memory_context=memory_context,
        db_conn=db_conn,
    ):
        yield event


async def _pipeline_event_generator(
    message: str,
    provider: LLMProvider,
    user_id: str,
    speaker_id: str | None,
    satellite_id: str | None,
    conversation_history: list[dict[str, Any]] | None,
    metadata: dict[str, Any] | None,
    model_fast: str,
    model_thinking: str,
    system_prompt: str,
    memory_context: str,
    db_conn: Any,
) -> AsyncGenerator[PipelineEvent, None]:
    from cortex.pipeline.turns import GenerationOptions, get_conversation_coordinator
    metadata = sanitize_metadata(metadata)
    identity = TurnIdentity.from_metadata(user_id, metadata, satellite_id)
    metadata.update(turn_id=identity.turn_id, conversation_id=identity.conversation_id)

    async def prepare() -> PreparedTurn:
        return await prepare_turn(
            message, user_id=user_id, speaker_id=speaker_id, satellite_id=satellite_id,
            conversation_history=conversation_history, metadata=metadata, db_conn=db_conn,
        )

    coordinator = get_conversation_coordinator()
    if coordinator is None:
        prepared = await prepare()
    else:
        prepared = await coordinator.prepare(
            identity, message, {"metadata": metadata, "speaker_id": speaker_id,
                                "conversation_history": conversation_history or []},
            GenerationOptions(model_fast, model_thinking, system_prompt, memory_context), prepare,
        )
    async for event in run_prepared_pipeline_events(
        prepared, provider, model_fast=model_fast, model_thinking=model_thinking,
        system_prompt=system_prompt, memory_context=memory_context, db_conn=db_conn,
    ):
        yield event


async def prepare_turn(
    message: str,
    *,
    user_id: str = "default",
    speaker_id: str | None = None,
    satellite_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    db_conn: Any = None,
) -> PreparedTurn:
    """Assemble context and execute fast paths without acquiring an LLM slot."""
    from cortex.safety import Severity
    from cortex.profiles import check_parental_allowed, get_parental_controls
    from datetime import datetime
    metadata = sanitize_metadata(metadata)
    identity = TurnIdentity.from_metadata(user_id, metadata, satellite_id)
    start_ms = int(time.monotonic() * 1000)
    safety, policy_db = _pipeline_safety(db_conn)
    checked = safety.check_input(message, user_id, metadata)
    blocked = None
    if checked.severity >= Severity.SOFT_BLOCK:
        blocked = checked.suggested_response or "I'm not able to help with that request."
    if policy_db is not None:
        allowed = check_parental_allowed(policy_db, user_id, hour=datetime.now().hour)
        if not allowed["allowed"]:
            logger.info("Household interaction denied by current parental policy")
            blocked = "This interaction is not available under your current household settings."
    if blocked:
        return PreparedTurn(
            identity, message, {"user_id": user_id, "metadata": metadata, "turn_identity": identity},
            DispatchResult(RouteStatus.FAILED, blocked, layer="safety"),
            start_ms, {"L0": 0, "L1": 0, "L2": 0},
        )
    t0 = time.monotonic()
    context = await assemble_context(
        message=message,
        user_id=user_id,
        speaker_id=speaker_id,
        satellite_id=satellite_id,
        conversation_history=conversation_history,
        metadata=metadata,
    )
    layer0_ms = (time.monotonic() - t0) * 1000
    context["turn_identity"] = identity
    context["room"] = identity.room or context.get("room")
    if policy_db is not None:
        controls = get_parental_controls(policy_db, user_id)
        context["restricted_actions"] = controls["restricted_topics"] if controls else []
    times = {"L0": layer0_ms}

    t1 = time.monotonic()
    response, confidence = await try_instant_answer(message, context)
    times["L1"] = (time.monotonic() - t1) * 1000
    if response is not None:
        result = DispatchResult(RouteStatus.HANDLED, response, confidence, layer="instant")
    else:
        t2 = time.monotonic()
        result = await dispatch_plugins(message, context)
        times["L2"] = (time.monotonic() - t2) * 1000
    if result.status == RouteStatus.NOT_MATCHED:
        from cortex.memory.conversation import recall_context
        context["memory_context"] = await recall_context(message, user_id)
    return PreparedTurn(identity, message, context, result, start_ms, times)


async def run_prepared_pipeline_events(
    prepared: PreparedTurn,
    provider: LLMProvider,
    *,
    model_fast: str = os.environ.get("MODEL_FAST", "qwen2.5:14b"),
    model_thinking: str = os.environ.get("MODEL_THINKING", "qwen3:30b-a3b"),
    system_prompt: str = "",
    memory_context: str = "",
    db_conn: Any = None,
) -> AsyncGenerator[PipelineEvent, None]:
    """Consume fast-path results or generate only for a genuine fallthrough."""
    if prepared.consumed:
        logger.error("Prepared turn already consumed: %s", prepared.identity.turn_id)
        raise ValueError("Prepared turn has already been consumed")
    prepared.consumed = True
    message = prepared.message
    context = prepared.context
    identity = prepared.identity
    user_id = identity.user_id
    start_ms = prepared.start_ms
    sentiment = context.get("sentiment", "neutral")
    sentiment_score = context.get("sentiment_score", 0.5)
    layer0_ms = prepared.layer_times["L0"]
    layer1_ms = prepared.layer_times["L1"]
    layer2_ms = prepared.layer_times.get("L2", 0.0)

    yield TurnEvent(identity)

    # Initial expression from sentiment
    yield ExpressionEvent(
        expression="",  # resolved by consumer from sentiment
        intensity=sentiment_score,
        sentiment=sentiment,
    )

    result = prepared.dispatch
    memory_context = memory_context or context.get("memory_context", "")
    safety, _ = _pipeline_safety(db_conn)
    safety_prompt = safety.build_system_prompt(user_id, context.get("metadata", {}))
    if safety_prompt and safety_prompt not in system_prompt:
        system_prompt = f"{system_prompt}\n\n{safety_prompt}".strip()
    if result.status != RouteStatus.NOT_MATCHED:
        response = result.response
        if response is None:
            raise ValueError("Handled turn must contain a response")
        response = _validate_output(response, prepared, safety, system_prompt)
        total_ms = int(time.monotonic() * 1000) - start_ms
        logger.info("Fast path %s: %s [total %dms]", result.layer, result.status.value, total_ms)

        yield SpeakingEvent(speaking=True)
        yield VisemeEvent(text=response)

        # Handle jokes: setup + punchline as separate TTS events
        _is_joke = result.layer == "instant" and "\n" in response and any(
            w in message.lower() for w in ("joke", "funny", "laugh", "giggle")
        )
        if _is_joke:
            parts = response.split("\n", 1)
            _joke_punchline_tts = context.get("_joke_punchline_tts")
            for i, part in enumerate(parts):
                part = part.strip()
                if part:
                    tts_text = _validate_output(
                        _joke_punchline_tts if (i == 1 and _joke_punchline_tts) else part,
                        prepared, safety, system_prompt,
                    )
                    yield TTSEvent(text=tts_text)
            yield ExpressionEvent(
                expression="laughing",
                intensity=0.8,
                sentiment=sentiment,
                text=f"{message} {response}",
            )
        else:
            yield TTSEvent(text=response)

        yield TextToken(text=response)
        yield SpeakingEvent(speaking=False)
        yield LayerResult(
            layer=result.layer,
            confidence=result.confidence,
            response_time_ms=total_ms,
            layer_times=prepared.layer_times,
            status=result.status,
            action_outcome=result.action_outcome,
            turn_id=identity.turn_id,
            revision=identity.revision,
        )
        _log_interaction(
            db_conn, context, message, response,
            matched_layer=result.layer,
            confidence=result.confidence,
            entities_used=result.entities,
            response_time_ms=total_ms,
        )
        from cortex.pipeline.turns import get_conversation_coordinator
        if get_conversation_coordinator() is None and result.status == RouteStatus.HANDLED:
            from cortex.memory.conversation import remember_response
            await remember_response(message, response, user_id)
        return

    # ── Layer 3: Filler + LLM ────────────────────────────────
    t3 = time.monotonic()
    first_token_ms = 0.0
    full_response_parts: list[str] = []

    yield SpeakingEvent(speaking=True, user_id=user_id)

    # Micro-ack engine: adaptive "thinking" sounds during long pauses
    from cortex.filler.micro_ack import get_micro_ack_engine
    _micro_ack = get_micro_ack_engine()
    _is_complex_query = any(
        kw in message.lower()
        for kw in ("explain", "analyze", "compare", "step by step", "why does")
    )
    _micro_ack.reset(is_complex=_is_complex_query)
    _filler_played = False
    _micro_ack_clock = time.monotonic()

    _sentence_buf = ""
    from cortex.pipeline.turns import GenerationOptions, get_conversation_coordinator
    coordinator = get_conversation_coordinator()
    if coordinator is not None:
        chunks = coordinator.stream(
            prepared, GenerationOptions(model_fast, model_thinking, system_prompt, memory_context),
        )
    else:
        chunks = stream_llm_response(
            message=message, context=context, provider=provider,
            model_fast=model_fast, model_thinking=model_thinking,
            memory_context=memory_context, system_prompt=system_prompt,
        )
    from cortex.pipeline.turns import PipelineGenerationError, output_byte_limit
    maximum = coordinator.store.policy.max_output_bytes if coordinator else output_byte_limit()
    buffered: list[str] = []
    size = 0
    try:
        async for chunk in chunks:
            size += len(chunk.encode("utf-8"))
            if size > maximum:
                raise PipelineGenerationError("Generated response exceeded the output byte limit")
            buffered.append(chunk)
    finally:
        await chunks.aclose()
    validated = _validate_output("".join(buffered), prepared, safety, system_prompt)
    for chunk in (validated,):
        if not first_token_ms:
            first_token_ms = (time.monotonic() - t3) * 1000
            # The very first chunk from stream_llm_response is the filler
            if chunk.strip():
                _filler_played = True
        full_response_parts.append(chunk)

        # Check if a micro-ack should fire before yielding this token
        _elapsed = time.monotonic() - _micro_ack_clock
        if _micro_ack.should_ack(_elapsed, _filler_played):
            _phrase = _micro_ack.get_phrase()
            yield MicroAckToken(text=_phrase, level=_micro_ack.ack_count)
            _micro_ack_clock = time.monotonic()  # reset for next ack

        _sentence_buf += chunk
        if any(_sentence_buf.rstrip().endswith(p) for p in (".", "!", "?", "\n")):
            yield VisemeEvent(text=_sentence_buf)
            yield TTSEvent(text=_sentence_buf)
            _sentence_buf = ""
        yield TextToken(text=chunk)

    # Flush remaining text
    if _sentence_buf.strip():
        yield VisemeEvent(text=_sentence_buf)
        yield TTSEvent(text=_sentence_buf)

    # Post-response content-aware expression
    full_response = "".join(full_response_parts)
    combined_text = f"{message} {full_response}"
    yield ExpressionEvent(
        expression="",
        intensity=sentiment_score,
        sentiment=sentiment,
        text=combined_text,
    )

    yield SpeakingEvent(speaking=False)

    layer3_ms = (time.monotonic() - t3) * 1000
    total_ms = int(time.monotonic() * 1000) - start_ms

    # Update micro-ack EMA with observed latency and persist
    _micro_ack.update_latency(layer3_ms / 1000.0)
    _micro_ack.save_ema(db_conn)

    logger.info(
        "Layer 3 (LLM): %.0fms (TTFT %.0fms) [total %dms] L0=%.0f L1=%.0f L2=%.0f",
        layer3_ms, first_token_ms, total_ms,
        layer0_ms, layer1_ms, layer2_ms,
    )
    yield LayerResult(
        layer="llm",
        status=context.get("perspective_status", RouteStatus.HANDLED),
        confidence=0.0,
        response_time_ms=total_ms,
        layer_times={"L0": layer0_ms, "L1": layer1_ms, "L2": layer2_ms, "L3": layer3_ms},
        turn_id=identity.turn_id,
        revision=identity.revision,
    )
    _log_interaction(
        db_conn, context, message, full_response,
        matched_layer="llm",
        confidence=0.0,
        response_time_ms=total_ms,
    )
    if coordinator is None:
        from cortex.memory.conversation import remember_response
        await remember_response(message, full_response, user_id)


def _log_interaction(
    conn: Any,
    context: dict[str, Any],
    message: str,
    response: str,
    matched_layer: str,
    confidence: float,
    response_time_ms: int = 0,
    entities_used: list[str] | None = None,
) -> None:
    """Persist an interaction to SQLite (best-effort, never raises)."""
    if conn is None:
        return
    try:
        cur = conn.execute(
            """
            INSERT INTO interactions
              (user_id, speaker_id, message, matched_layer, sentiment,
               sentiment_score, response, response_time_ms, confidence_score,
               resolved_area)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context.get("user_id"),
                context.get("speaker_id"),
                message,
                matched_layer,
                context.get("sentiment"),
                context.get("sentiment_score"),
                response[:4000],  # cap to avoid huge blobs
                response_time_ms,
                confidence,
                context.get("room"),
            ),
        )
        interaction_id = cur.lastrowid
        # Log entity refs for Layer 2
        for entity_id in (entities_used or []):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO interaction_entities (interaction_id, entity_id) VALUES (?, ?)",
                    (interaction_id, entity_id),
                )
            except Exception:
                pass
        conn.commit()
    except Exception as exc:
        logger.debug("Interaction logging failed: %s", exc)
