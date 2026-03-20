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
    ExpressionEvent,
    SpeakingEvent,
    VisemeEvent,
    TTSEvent,
    LayerResult,
)
from .layer0_context import assemble_context
from .layer1_instant import try_instant_answer
from .layer2_plugins import try_plugin_dispatch
from .layer3_llm import stream_llm_response

logger = logging.getLogger(__name__)


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
    start_ms = int(time.monotonic() * 1000)

    # ── Layer 0: Context Assembly ─────────────────────────────
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
    logger.debug("Layer 0 (context): %.1fms", layer0_ms)

    room = context.get("room") or "default"
    sentiment = context.get("sentiment", "neutral")
    sentiment_score = context.get("sentiment_score", 0.5)

    # Initial expression from sentiment
    yield ExpressionEvent(
        expression="",  # resolved by consumer from sentiment
        intensity=sentiment_score,
        sentiment=sentiment,
    )

    # ── Layer 1: Instant Answers ─────────────────────────────
    t1 = time.monotonic()
    instant_response, instant_confidence = await try_instant_answer(message, context)
    layer1_ms = (time.monotonic() - t1) * 1000
    if instant_response is not None:
        total_ms = int(time.monotonic() * 1000) - start_ms
        logger.info("Layer 1 hit (%.1fms): %r [total %dms]", layer1_ms, instant_response[:80], total_ms)

        yield SpeakingEvent(speaking=True)
        yield VisemeEvent(text=instant_response)

        # Handle jokes: setup + punchline as separate TTS events
        _is_joke = "\n" in instant_response and any(
            w in message.lower() for w in ("joke", "funny", "laugh", "giggle")
        )
        if _is_joke:
            parts = instant_response.split("\n", 1)
            _joke_punchline_tts = context.get("_joke_punchline_tts")
            for i, part in enumerate(parts):
                part = part.strip()
                if part:
                    tts_text = _joke_punchline_tts if (i == 1 and _joke_punchline_tts) else part
                    yield TTSEvent(text=tts_text)
            yield ExpressionEvent(
                expression="laughing",
                intensity=0.8,
                sentiment=sentiment,
                text=f"{message} {instant_response}",
            )
        else:
            yield TTSEvent(text=instant_response)

        yield TextToken(text=instant_response)
        yield SpeakingEvent(speaking=False)
        yield LayerResult(
            layer="instant",
            confidence=instant_confidence,
            response_time_ms=total_ms,
            layer_times={"L0": layer0_ms, "L1": layer1_ms},
        )
        _log_interaction(
            db_conn, context, message, instant_response,
            matched_layer="instant",
            confidence=instant_confidence,
            response_time_ms=total_ms,
        )
        return
    logger.debug("Layer 1 (instant): %.1fms — no match", layer1_ms)

    # ── Layer 2: Plugin Dispatch ──────────────────────────────
    t2 = time.monotonic()
    plugin_response, plugin_confidence, entities = await try_plugin_dispatch(message, context)
    layer2_ms = (time.monotonic() - t2) * 1000
    if plugin_response is not None:
        total_ms = int(time.monotonic() * 1000) - start_ms
        logger.info("Layer 2 hit (%.1fms): %r [total %dms]", layer2_ms, plugin_response[:80], total_ms)

        yield SpeakingEvent(speaking=True)
        yield VisemeEvent(text=plugin_response)
        yield TTSEvent(text=plugin_response)
        yield TextToken(text=plugin_response)
        yield SpeakingEvent(speaking=False)
        yield LayerResult(
            layer="tool",
            confidence=plugin_confidence,
            response_time_ms=total_ms,
            layer_times={"L0": layer0_ms, "L1": layer1_ms, "L2": layer2_ms},
        )
        _log_interaction(
            db_conn, context, message, plugin_response,
            matched_layer="tool",
            confidence=plugin_confidence,
            entities_used=entities,
            response_time_ms=total_ms,
        )
        return
    logger.debug("Layer 2 (plugins): %.1fms — no match", layer2_ms)

    # ── Layer 3: Filler + LLM ────────────────────────────────
    t3 = time.monotonic()
    first_token_ms = 0.0
    full_response_parts: list[str] = []

    yield SpeakingEvent(speaking=True, user_id=user_id)

    _sentence_buf = ""
    async for chunk in stream_llm_response(
        message=message,
        context=context,
        provider=provider,
        model_fast=model_fast,
        model_thinking=model_thinking,
        memory_context=memory_context,
        system_prompt=system_prompt,
    ):
        if not first_token_ms:
            first_token_ms = (time.monotonic() - t3) * 1000
        full_response_parts.append(chunk)

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
    logger.info(
        "Layer 3 (LLM): %.0fms (TTFT %.0fms) [total %dms] L0=%.0f L1=%.0f L2=%.0f",
        layer3_ms, first_token_ms, total_ms,
        layer0_ms, layer1_ms, layer2_ms,
    )
    yield LayerResult(
        layer="llm",
        confidence=0.0,
        response_time_ms=total_ms,
        layer_times={"L0": layer0_ms, "L1": layer1_ms, "L2": layer2_ms, "L3": layer3_ms},
    )
    _log_interaction(
        db_conn, context, message, full_response,
        matched_layer="llm",
        confidence=0.0,
        response_time_ms=total_ms,
    )


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
