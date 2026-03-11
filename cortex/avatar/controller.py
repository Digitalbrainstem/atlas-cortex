"""Avatar controller — single entry point for avatar state changes.

OWNERSHIP: This module is the ONLY interface the pipeline/orchestrator
should use to control the avatar. All expression, viseme, speaking state,
and TTS commands flow through here.

FORBIDDEN: Pipeline code must NOT import from broadcast.py, expressions.py,
or visemes.py directly — always go through the controller.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def set_expression(
    room: str | None,
    sentiment: str,
    confidence: float = 1.0,
    text: str = "",
) -> str | None:
    """Resolve and broadcast an avatar expression.

    Tries content-based detection first, then falls back to sentiment mapping.
    Returns the expression name that was set, or None if no room.
    """
    if not room:
        return None
    try:
        from cortex.avatar.expressions import resolve_from_content, resolve_from_sentiment
        from cortex.avatar.broadcast import broadcast_expression, get_connected_rooms

        expr = resolve_from_content(text) if text else None
        if not expr:
            expr = resolve_from_sentiment(sentiment, confidence)

        rooms = get_connected_rooms()
        logger.info("avatar: expression %s → room=%s (connected: %s)", expr.name, room, rooms)
        await broadcast_expression(room, expr.name, confidence)
        return expr.name
    except Exception:
        logger.exception("avatar: set_expression failed")
        return None


def send_visemes(room: str | None, text: str) -> None:
    """Generate and broadcast viseme sequence for text (non-blocking).

    Schedules the viseme broadcast as a fire-and-forget task.
    """
    if not room:
        return
    try:
        from cortex.avatar.visemes import text_to_visemes
        from cortex.avatar.broadcast import broadcast_viseme_sequence, get_connected_rooms

        frames = text_to_visemes(text)
        frame_dicts: list[dict[str, Any]] = [
            {
                "viseme": f.viseme,
                "start_ms": f.start_ms,
                "duration_ms": f.duration_ms,
                "intensity": f.intensity,
            }
            for f in frames
        ]
        rooms = get_connected_rooms()
        logger.info("avatar: %d visemes → room=%s (connected: %s)", len(frame_dicts), room, rooms)
        asyncio.ensure_future(broadcast_viseme_sequence(room, frame_dicts))
    except Exception:
        logger.exception("avatar: send_visemes failed")


async def speaking_start(room: str | None, user_id: str | None = None) -> None:
    """Notify avatar displays that Atlas is about to speak."""
    if not room:
        return
    try:
        from cortex.avatar.broadcast import broadcast_speaking_start, get_connected_rooms
        logger.info("avatar: speaking_start → room=%s (connected: %s)", room, get_connected_rooms())
        await broadcast_speaking_start(room, user_id)
    except Exception:
        logger.exception("avatar: speaking_start failed")


async def speaking_end(room: str | None) -> None:
    """Notify avatar displays that Atlas has stopped speaking."""
    if not room:
        return
    try:
        from cortex.avatar.broadcast import broadcast_speaking_end, get_connected_rooms
        logger.info("avatar: speaking_end → room=%s (connected: %s)", room, get_connected_rooms())
        await broadcast_speaking_end(room)
    except Exception:
        logger.exception("avatar: speaking_end failed")


async def stream_tts(room: str | None, text: str, expression: str | None = None) -> None:
    """Stream TTS audio to avatar displays (fire-and-forget background task).

    Only streams if the room has connected clients and audio routing allows it.
    """
    if not room or not text.strip():
        return
    try:
        from cortex.avatar.broadcast import stream_tts_to_avatar, get_connected_rooms
        rooms = get_connected_rooms()
        if room in rooms:
            logger.info("avatar: TTS %d chars → room=%s", len(text), room)
            asyncio.create_task(stream_tts_to_avatar(room, text, expression=expression))
    except Exception:
        logger.exception("avatar: stream_tts failed")


async def set_listening(room: str | None, active: bool) -> None:
    """Notify avatar displays of mic/listening state change."""
    if not room:
        return
    try:
        from cortex.avatar.broadcast import broadcast_listening
        await broadcast_listening(room, active)
    except Exception:
        logger.exception("avatar: set_listening failed")
