"""WebSocket endpoint for real-time avatar viseme and expression streaming.

Clients connect to ``/ws/avatar?room=<room>`` and receive JSON frames:

  Server → Client:
    SKIN          — skin SVG URL for the current/default speaker
    EXPRESSION    — facial expression change
    VISEME        — single lip-sync frame during TTS playback
    SPEAKING_START — Atlas begins speaking (includes skin_id)
    SPEAKING_END  — Atlas finished speaking
    LISTENING     — microphone state change
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)

# Connected avatar display clients, keyed by room name.
# Each room can have multiple display clients.
_clients: dict[str, list[WebSocket]] = {}
_clients_lock = asyncio.Lock()


async def avatar_ws_handler(ws: WebSocket) -> None:
    """Handle an avatar display WebSocket connection."""
    await ws.accept()
    room = ws.query_params.get("room", "default")
    logger.info("avatar WS connect: room=%s", room)

    async with _clients_lock:
        _clients.setdefault(room, []).append(ws)

    try:
        # Send initial skin on connect
        skin = _resolve_skin_for_room(room)
        await ws.send_json({
            "type": "SKIN",
            "skin_id": skin["id"],
            "skin_url": f"/avatar/skin/{skin['id']}.svg",
            "skin_name": skin["name"],
        })
        await ws.send_json({"type": "EXPRESSION", "expression": "neutral", "intensity": 1.0})

        # Keep connection alive — client doesn't send data, just receives.
        while True:
            # Await pings/pongs or client close
            data = await ws.receive_text()
            # Client can send {"type": "PING"} for keepalive
            try:
                msg = json.loads(data)
                if msg.get("type") == "PING":
                    await ws.send_json({"type": "PONG", "ts": time.time()})
            except (json.JSONDecodeError, TypeError):
                pass

    except WebSocketDisconnect:
        logger.info("avatar WS disconnect: room=%s", room)
    except Exception:
        logger.exception("avatar WS error: room=%s", room)
    finally:
        async with _clients_lock:
            if room in _clients:
                _clients[room] = [c for c in _clients[room] if c is not ws]
                if not _clients[room]:
                    del _clients[room]


def _resolve_skin_for_room(room: str, user_id: str | None = None) -> dict[str, Any]:
    """Resolve the avatar skin for a room/user.

    Priority: user-specific assignment → default skin.
    """
    try:
        init_db()
        conn = get_db()
        if user_id:
            row = conn.execute(
                "SELECT s.id, s.name, s.path FROM avatar_assignments a "
                "JOIN avatar_skins s ON a.skin_id = s.id "
                "WHERE a.user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                return {"id": row[0], "name": row[1], "path": row[2]}
        # Fall back to default skin
        row = conn.execute(
            "SELECT id, name, path FROM avatar_skins WHERE is_default = TRUE"
        ).fetchone()
        if row:
            return {"id": row[0], "name": row[1], "path": row[2]}
    except Exception:
        logger.exception("Failed to resolve avatar skin")
    return {"id": "default", "name": "Atlas Default", "path": "cortex/avatar/skins/default.svg"}


async def broadcast_to_room(room: str, message: dict[str, Any]) -> None:
    """Send a JSON message to all avatar display clients in a room."""
    async with _clients_lock:
        clients = list(_clients.get(room, []))
    dead: list[WebSocket] = []
    for client in clients:
        try:
            await client.send_json(message)
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
    skin = _resolve_skin_for_room(room, user_id)
    await broadcast_to_room(room, {
        "type": "SPEAKING_START",
        "skin_id": skin["id"],
        "skin_url": f"/avatar/skin/{skin['id']}.svg",
    })


async def broadcast_speaking_end(room: str) -> None:
    """Notify avatar displays that Atlas has stopped speaking."""
    await broadcast_to_room(room, {"type": "SPEAKING_END"})


async def broadcast_listening(room: str, active: bool) -> None:
    """Notify avatar displays of mic/listening state change."""
    await broadcast_to_room(room, {"type": "LISTENING", "active": active})


async def broadcast_viseme_sequence(room: str, frames: list[dict[str, Any]]) -> None:
    """Broadcast a pre-computed viseme sequence, respecting timing.

    Each frame dict has: viseme, start_ms, duration_ms, intensity.
    Frames are sent at approximately the right time relative to the first.
    """
    if not frames:
        return
    base_time = time.monotonic()
    for frame in frames:
        target = base_time + (frame["start_ms"] / 1000.0)
        now = time.monotonic()
        if target > now:
            await asyncio.sleep(target - now)
        await broadcast_viseme(room, frame["viseme"], frame["duration_ms"], frame["intensity"])


def get_connected_rooms() -> list[str]:
    """Return a list of rooms with active avatar display connections."""
    return list(_clients.keys())
