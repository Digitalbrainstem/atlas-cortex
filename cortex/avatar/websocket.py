"""WebSocket endpoint for real-time avatar viseme and expression streaming.

Clients connect to ``/ws/avatar?room=<room>`` and receive JSON frames.
This module handles connection lifecycle only — all broadcasting logic
lives in cortex.avatar.broadcast.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.avatar.broadcast import (
    register_client,
    unregister_client,
    broadcast_expression,
    broadcast_speaking_start,
    broadcast_speaking_end,
    stream_tts_to_avatar,
    get_connected_rooms,
)
from cortex.avatar.skins import resolve_skin

# Re-export for backward compatibility (other modules import from here)
from cortex.avatar.broadcast import (  # noqa: F811
    broadcast_to_room,
    broadcast_viseme,
    broadcast_viseme_sequence,
    broadcast_listening,
    broadcast_tts_start,
    broadcast_tts_chunk,
    broadcast_tts_end,
    set_audio_route,
    get_audio_route,
    should_play_on_avatar,
    should_play_on_satellite,
    get_connected_rooms,
)

logger = logging.getLogger(__name__)


async def avatar_ws_handler(ws: WebSocket) -> None:
    """Handle an avatar display WebSocket connection."""
    await ws.accept()
    room = ws.query_params.get("room", "default")
    logger.info("avatar WS connect: room=%s", room)

    await register_client(room, ws)

    try:
        # Send initial skin on connect
        skin = resolve_skin(room)
        await ws.send_json({
            "type": "SKIN",
            "skin_id": skin["id"],
            "skin_url": f"/avatar/skin/{skin['id']}.svg",
            "skin_name": skin["name"],
        })
        await ws.send_json({"type": "EXPRESSION", "expression": "neutral", "intensity": 1.0})

        # Greet on connect (deferred so skin loads first)
        asyncio.ensure_future(_handle_connect_greeting(room))

        # Keep connection alive — client can send PING or TELL_JOKE.
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "PING":
                    await ws.send_json({"type": "PONG", "ts": time.time()})
                elif msg.get("type") == "TELL_JOKE":
                    asyncio.ensure_future(_handle_tell_joke(room))
            except (json.JSONDecodeError, TypeError):
                pass

    except WebSocketDisconnect:
        logger.info("avatar WS disconnect: room=%s", room)
    except Exception:
        logger.exception("avatar WS error: room=%s", room)
    finally:
        await unregister_client(room, ws)


# Backward compatibility alias (tests import the old name)
_resolve_skin_for_room = resolve_skin


async def _handle_tell_joke(room: str) -> None:
    """Handle a TELL_JOKE request from the avatar display."""
    try:
        from cortex.jokes import (
            get_random_joke,
            init_joke_bank,
            stream_cached_joke_to_avatar,
        )

        init_joke_bank()
        joke = get_random_joke(room=room)
        if not joke:
            logger.warning("No jokes available for room=%s", room)
            return

        logger.info("Telling joke #%d to room=%s: %s", joke.id, room, joke.setup[:40])

        await broadcast_expression(room, "neutral", 1.0)
        await broadcast_speaking_start(room)

        ok = await stream_cached_joke_to_avatar(room, joke)
        if not ok:
            logger.warning("Failed to stream joke TTS for room=%s", room)

        # Post-punchline expression
        await broadcast_expression(room, "silly", 1.0)
        await broadcast_speaking_end(room)

    except Exception:
        logger.exception("TELL_JOKE handler failed for room=%s", room)


# Track which rooms have been greeted (reset on server restart)
_greeted_rooms: set[str] = set()


async def _handle_connect_greeting(room: str) -> None:
    """Play a short audio greeting when the avatar display connects.

    Skipped when a web satellite is connected — the satellite handles
    its own audio and the Kokoro greeting would interfere with timing.
    """
    import random
    from datetime import datetime

    # Small delay so the skin and audio context have time to initialize
    await asyncio.sleep(1.5)

    # Skip greeting if a web satellite is active — it handles its own audio
    # and the Kokoro greeting would play through the avatar WS, confusing timing.
    try:
        from cortex.satellite.websocket import get_connected_satellites
        sats = get_connected_satellites()
        if any(sid.startswith("web-satellite-") for sid in sats):
            logger.info("Connect greeting skipped for room=%s (web satellite active)", room)
            return
    except Exception:
        pass

    try:
        first_visit = room not in _greeted_rooms
        _greeted_rooms.add(room)

        hour = datetime.now().hour
        if hour < 12:
            tod = "morning"
        elif hour < 17:
            tod = "afternoon"
        elif hour < 21:
            tod = "evening"
        else:
            tod = "night"

        if first_visit:
            greeting = f"Hi, I'm Atlas! Good {tod}!"
            expression = "excited"
        else:
            _return_greetings = {
                "morning": ["Good morning!", "Morning!", "Hey, good morning!"],
                "afternoon": ["Hey there!", "Good afternoon!", "Hey!"],
                "evening": ["Good evening!", "Hey, welcome back!", "Evening!"],
                "night": ["Hey, still up?", "Welcome back!", "Hey there!"],
            }
            greeting = random.choice(_return_greetings.get(tod, ["Hey!"]))
            expression = "happy"

        logger.info("Connect greeting for room=%s: %r (first=%s)", room, greeting, first_visit)

        await broadcast_expression(room, expression, 1.0)
        await broadcast_speaking_start(room)
        await stream_tts_to_avatar(room, greeting, expression=expression)
        await broadcast_speaking_end(room)

    except Exception:
        logger.exception("Connect greeting failed for room=%s", room)
