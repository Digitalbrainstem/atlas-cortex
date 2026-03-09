"""Atlas Cortex pipeline orchestration.

Exports :func:`run_pipeline` which sequences Layers 0-3 and handles
interaction logging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncGenerator

from cortex.providers.base import LLMProvider

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
    """Run the full Atlas Cortex pipeline for a single message.

    Yields text tokens as soon as they are available.

    Layer 0 → context assembly
    Layer 1 → instant answers (date, math, greetings, identity)
    Layer 2 → plugin dispatch (smart home, lists, knowledge)
    Layer 3 → filler streaming + LLM background call
    """
    return _pipeline_generator(
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
    )


async def _pipeline_generator(
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
) -> AsyncGenerator[str, None]:
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

    # Broadcast avatar expression from sentiment (non-blocking)
    room = context.get("room") or "default"
    _fire_avatar_expression(room, context.get("sentiment", "neutral"), context.get("sentiment_score", 0.5), message)

    # ── Layer 1: Instant Answers ─────────────────────────────
    t1 = time.monotonic()
    instant_response, instant_confidence = await try_instant_answer(message, context)
    layer1_ms = (time.monotonic() - t1) * 1000
    if instant_response is not None:
        total_ms = int(time.monotonic() * 1000) - start_ms
        logger.info("Layer 1 hit (%.1fms): %r [total %dms]", layer1_ms, instant_response[:80], total_ms)
        _fire_avatar_speaking(room, True)
        _fire_avatar_visemes(room, instant_response)
        _fire_avatar_tts(room, instant_response)
        yield instant_response
        _fire_avatar_speaking(room, False)
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
        _fire_avatar_speaking(room, True)
        _fire_avatar_visemes(room, plugin_response)
        _fire_avatar_tts(room, plugin_response)
        yield plugin_response
        _fire_avatar_speaking(room, False)
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
    _fire_avatar_speaking(room, True, user_id)
    # Accumulate sentences for viseme streaming
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
        # Fire visemes per sentence for smoother lip-sync
        _sentence_buf += chunk
        if any(_sentence_buf.rstrip().endswith(p) for p in (".", "!", "?", "\n")):
            _fire_avatar_visemes(room, _sentence_buf)
            _fire_avatar_tts(room, _sentence_buf)
            _sentence_buf = ""
        yield chunk

    # Flush any remaining text
    if _sentence_buf.strip():
        _fire_avatar_visemes(room, _sentence_buf)
        _fire_avatar_tts(room, _sentence_buf)
    _fire_avatar_speaking(room, False)

    layer3_ms = (time.monotonic() - t3) * 1000
    total_ms = int(time.monotonic() * 1000) - start_ms
    full_response = "".join(full_response_parts)
    logger.info(
        "Layer 3 (LLM): %.0fms (TTFT %.0fms) [total %dms] L0=%.0f L1=%.0f L2=%.0f",
        layer3_ms, first_token_ms, total_ms,
        layer0_ms, layer1_ms, layer2_ms,
    )
    _log_interaction(
        db_conn, context, message, full_response,
        matched_layer="llm",
        confidence=0.0,  # Will be updated by grounding layer
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


# ── Avatar broadcasting (best-effort, never blocks pipeline) ─────

def _fire_avatar_expression(room: str | None, sentiment: str, confidence: float, text: str = "") -> None:
    """Schedule an avatar expression broadcast (non-blocking)."""
    if not room:
        return
    try:
        from cortex.avatar import AvatarState
        from cortex.avatar.websocket import broadcast_expression, get_connected_rooms
        state = AvatarState()
        # Content-aware expression takes priority over sentiment
        content_expr = state.expression_from_content(text) if text else None
        if content_expr:
            expr = content_expr
        else:
            expr = state.expression_from_sentiment(sentiment, confidence)
        rooms = get_connected_rooms()
        logger.info("avatar: fire expression %s to room=%s (connected: %s)", expr.name, room, rooms)
        asyncio.ensure_future(broadcast_expression(room, expr.name, confidence))
    except Exception:
        logger.exception("avatar: expression fire failed")


def _fire_avatar_visemes(room: str | None, text: str) -> None:
    """Schedule avatar viseme sequence for a text response (non-blocking)."""
    if not room:
        return
    try:
        from cortex.avatar import AvatarState
        from cortex.avatar.websocket import broadcast_viseme_sequence, get_connected_rooms
        state = AvatarState()
        frames = state.text_to_visemes(text)
        frame_dicts = [
            {"viseme": f.viseme, "start_ms": f.start_ms, "duration_ms": f.duration_ms, "intensity": f.intensity}
            for f in frames
        ]
        rooms = get_connected_rooms()
        logger.info("avatar: fire %d visemes to room=%s (connected: %s)", len(frame_dicts), room, rooms)
        asyncio.ensure_future(broadcast_viseme_sequence(room, frame_dicts))
    except Exception:
        logger.exception("avatar: viseme fire failed")


def _fire_avatar_speaking(room: str | None, start: bool, user_id: str | None = None) -> None:
    """Notify avatar displays of speaking state change (non-blocking)."""
    if not room:
        return
    try:
        if start:
            from cortex.avatar.websocket import broadcast_speaking_start, get_connected_rooms
            logger.info("avatar: fire speaking_start to room=%s (connected: %s)", room, get_connected_rooms())
            asyncio.ensure_future(broadcast_speaking_start(room, user_id))
        else:
            from cortex.avatar.websocket import broadcast_speaking_end, get_connected_rooms
            logger.info("avatar: fire speaking_end to room=%s (connected: %s)", room, get_connected_rooms())
            asyncio.ensure_future(broadcast_speaking_end(room))
    except Exception:
        logger.exception("avatar: speaking fire failed")


def _fire_avatar_tts(room: str | None, text: str) -> None:
    """Schedule TTS audio streaming to avatar displays (non-blocking)."""
    if not room or not text.strip():
        return
    try:
        from cortex.avatar.websocket import stream_tts_to_avatar, get_connected_rooms
        rooms = get_connected_rooms()
        if room in rooms:
            logger.info("avatar: fire TTS for %d chars to room=%s", len(text), room)
            asyncio.ensure_future(stream_tts_to_avatar(room, text))
    except Exception:
        logger.exception("avatar: TTS fire failed")
