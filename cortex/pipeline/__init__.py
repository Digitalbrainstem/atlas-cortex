"""Atlas Cortex pipeline orchestration.

Exports :func:`run_pipeline` which sequences Layers 0-3 and handles
interaction logging.
"""

from __future__ import annotations

import logging
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
    model_fast: str = "qwen2.5:14b",
    model_thinking: str = "qwen3:30b-a3b",
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
    context = await assemble_context(
        message=message,
        user_id=user_id,
        speaker_id=speaker_id,
        satellite_id=satellite_id,
        conversation_history=conversation_history,
        metadata=metadata,
    )

    # ── Layer 1: Instant Answers ─────────────────────────────
    instant_response, instant_confidence = await try_instant_answer(message, context)
    if instant_response is not None:
        yield instant_response
        _log_interaction(
            db_conn, context, message, instant_response,
            matched_layer="instant",
            confidence=instant_confidence,
            response_time_ms=int(time.monotonic() * 1000) - start_ms,
        )
        return

    # ── Layer 2: Plugin Dispatch ──────────────────────────────
    plugin_response, plugin_confidence, entities = await try_plugin_dispatch(message, context)
    if plugin_response is not None:
        yield plugin_response
        _log_interaction(
            db_conn, context, message, plugin_response,
            matched_layer="tool",
            confidence=plugin_confidence,
            entities_used=entities,
            response_time_ms=int(time.monotonic() * 1000) - start_ms,
        )
        return

    # ── Layer 3: Filler + LLM ────────────────────────────────
    full_response_parts: list[str] = []
    async for chunk in stream_llm_response(
        message=message,
        context=context,
        provider=provider,
        model_fast=model_fast,
        model_thinking=model_thinking,
        memory_context=memory_context,
        system_prompt=system_prompt,
    ):
        full_response_parts.append(chunk)
        yield chunk

    full_response = "".join(full_response_parts)
    _log_interaction(
        db_conn, context, message, full_response,
        matched_layer="llm",
        confidence=0.0,  # Will be updated by grounding layer
        response_time_ms=int(time.monotonic() * 1000) - start_ms,
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
