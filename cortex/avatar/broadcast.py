"""Avatar WebSocket broadcast — send frames to connected displays.

OWNERSHIP: This module owns the client registry and all broadcast operations.
No other module should send directly to avatar WebSocket clients.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Connected avatar display clients, keyed by room name.
_clients: dict[str, list[WebSocket]] = {}
_clients_lock = asyncio.Lock()


async def register_client(room: str, ws: WebSocket) -> None:
    """Add a WebSocket client to a room's display list."""
    async with _clients_lock:
        _clients.setdefault(room, []).append(ws)


async def handle_client_hello(room: str, ws: WebSocket) -> None:
    """Handle a HELLO message from a client — respond with SKIN."""
    from cortex.avatar.skins import resolve_skin
    skin = resolve_skin(room)
    await ws.send_json({
        "type": "SKIN",
        "skin_id": skin["id"],
        "skin_url": f"/avatar/skin/{skin['id']}.svg",
        "skin_name": skin["name"],
    })


async def unregister_client(room: str, ws: WebSocket) -> None:
    """Remove a WebSocket client from a room's display list."""
    async with _clients_lock:
        if room in _clients:
            _clients[room] = [c for c in _clients[room] if c is not ws]
            if not _clients[room]:
                del _clients[room]


def get_connected_rooms() -> list[str]:
    """Return a list of rooms with active avatar display connections."""
    return list(_clients.keys())


def has_clients(room: str) -> bool:
    """Check if a room has any connected avatar display clients."""
    return bool(_clients.get(room))


# ── Core broadcast ───────────────────────────────────────────────

async def broadcast_to_avatars(message: dict[str, Any]) -> None:
    """Send a JSON message to ALL connected avatar display clients (every room)."""
    async with _clients_lock:
        rooms = list(_clients.keys())
    for room in rooms:
        await broadcast_to_room(room, message)


async def broadcast_to_room(room: str, message: dict[str, Any]) -> None:
    """Send a JSON message to all avatar display clients in a room."""
    msg_type = message.get("type", "?")
    async with _clients_lock:
        clients = list(_clients.get(room, []))
    if not clients:
        return
    dead: list[WebSocket] = []
    for client in clients:
        try:
            await client.send_json(message)
            logger.debug("broadcast: sent %s to room=%s", msg_type, room)
        except Exception:
            dead.append(client)
    if dead:
        async with _clients_lock:
            if room in _clients:
                _clients[room] = [c for c in _clients[room] if c not in dead]


async def broadcast_expression(room: str, expression: str, intensity: float = 1.0) -> None:
    """Broadcast an expression change to all avatar displays in a room."""
    await broadcast_to_room(room, {
        "type": "EXPRESSION",
        "expression": expression,
        "intensity": round(intensity, 2),
    })


async def broadcast_viseme(room: str, viseme: str, duration_ms: int, intensity: float) -> None:
    """Broadcast a single viseme frame to avatar displays in a room."""
    await broadcast_to_room(room, {
        "type": "VISEME",
        "viseme": viseme,
        "duration_ms": duration_ms,
        "intensity": round(intensity, 2),
    })


async def broadcast_speaking_start(room: str, user_id: str | None = None) -> None:
    """Notify avatar displays that Atlas is about to speak."""
    from cortex.avatar.skins import resolve_skin
    skin = resolve_skin(room, user_id)
    await broadcast_to_room(room, {
        "type": "SPEAKING_START",
        "skin_id": skin["id"],
        "skin_url": f"/avatar/skin/{skin['id']}.svg",
    })


async def broadcast_speaking_end(room: str) -> None:
    """Notify avatar displays that Atlas has stopped speaking."""
    await broadcast_to_room(room, {"type": "SPEAKING_END"})


async def broadcast_playback_stop(room: str) -> None:
    """Abort all audio playback and reset avatar to idle immediately."""
    await broadcast_to_room(room, {"type": "PLAYBACK_STOP"})


async def broadcast_listening(room: str, active: bool) -> None:
    """Notify avatar displays of mic/listening state change."""
    await broadcast_to_room(room, {"type": "LISTENING", "active": active})


async def broadcast_viseme_sequence(room: str, frames: list[dict[str, Any]]) -> None:
    """Broadcast a pre-computed viseme sequence, respecting timing."""
    if not frames:
        return
    base_time = time.monotonic()
    for frame in frames:
        target = base_time + (frame["start_ms"] / 1000.0)
        now = time.monotonic()
        if target > now:
            await asyncio.sleep(target - now)
        await broadcast_viseme(room, frame["viseme"], frame["duration_ms"], frame["intensity"])


# ── Audio routing ────────────────────────────────────────────────

_audio_routes: dict[str, str] = {}
_DEFAULT_AUDIO_ROUTE = "avatar"


def set_audio_route(room: str, route: str) -> None:
    """Configure audio routing for a room ('avatar', 'satellite', or 'both')."""
    if route not in ("avatar", "satellite", "both"):
        raise ValueError(f"Invalid audio route: {route!r}")
    _audio_routes[room] = route
    logger.info("audio route for room=%s set to %s", room, route)


def get_audio_route(room: str) -> str:
    """Return the audio routing mode for a room."""
    return _audio_routes.get(room, _DEFAULT_AUDIO_ROUTE)


def should_play_on_avatar(room: str) -> bool:
    """Check if audio should be played on the avatar display for this room."""
    return get_audio_route(room) in ("avatar", "both")


def should_play_on_satellite(room: str) -> bool:
    """Check if audio should be played on the satellite for this room."""
    return get_audio_route(room) in ("satellite", "both")


# ── TTS audio streaming ─────────────────────────────────────────

async def broadcast_tts_start(
    room: str, session_id: str, sample_rate: int = 24000, text: str = "",
) -> None:
    """Notify avatar displays that TTS audio streaming is starting."""
    if not should_play_on_avatar(room):
        return
    await broadcast_to_room(room, {
        "type": "TTS_START",
        "session_id": session_id,
        "format": f"pcm_{sample_rate // 1000}k_16bit_mono",
        "sample_rate": sample_rate,
        "text": text,
    })


async def broadcast_tts_chunk(room: str, session_id: str, audio_b64: str) -> None:
    """Send a base64-encoded PCM audio chunk to avatar displays."""
    if not should_play_on_avatar(room):
        return
    await broadcast_to_room(room, {
        "type": "TTS_CHUNK",
        "session_id": session_id,
        "audio": audio_b64,
    })


async def broadcast_tts_end(room: str, session_id: str) -> None:
    """Notify avatar displays that TTS audio streaming is complete."""
    if not should_play_on_avatar(room):
        return
    await broadcast_to_room(room, {
        "type": "TTS_END",
        "session_id": session_id,
    })


async def stream_tts_to_avatar(
    room: str, text: str, session_id: str | None = None, expression: str | None = None,
) -> None:
    """Synthesize TTS for text and stream audio chunks to avatar displays."""
    if not should_play_on_avatar(room):
        return
    if not has_clients(room):
        return

    sid = session_id or uuid.uuid4().hex[:12]

    _EXPR_SPEED = {
        "silly": 1.15, "laughing": 1.1, "excited": 1.15, "love": 1.05,
        "happy": 1.1, "proud": 1.05, "sleepy": 0.85, "scared": 1.2,
        "angry": 1.1, "crying": 0.9,
    }
    speed = _EXPR_SPEED.get(expression, 1.0)

    try:
        from cortex.speech import synthesize_speech
        pcm_bytes, sample_rate, _provider = await synthesize_speech(text, voice="")
        if not pcm_bytes:
            return

        await broadcast_tts_start(room, sid, sample_rate, text)

        chunk_size = 4096
        offset = 0
        while offset < len(pcm_bytes):
            chunk = pcm_bytes[offset:offset + chunk_size]
            offset += chunk_size
            await broadcast_tts_chunk(room, sid, base64.b64encode(chunk).decode("ascii"))

        await broadcast_tts_end(room, sid)
        logger.info("avatar TTS: streamed %r to room=%s (speed=%.2f)", text[:60], room, speed)

    except Exception:
        logger.exception("avatar TTS streaming failed for room=%s", room)
        try:
            await broadcast_tts_end(room, sid)
        except Exception:
            pass
