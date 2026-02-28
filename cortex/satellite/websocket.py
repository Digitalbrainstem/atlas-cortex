"""WebSocket endpoint for satellite audio streaming and control.

Handles the real-time communication channel between Atlas server and
satellite devices:

  Satellite → Server:
    ANNOUNCE, WAKE, AUDIO_START, AUDIO_CHUNK, AUDIO_END, STATUS, HEARTBEAT

  Server → Satellite:
    ACCEPTED, TTS_START, TTS_CHUNK, TTS_END, PLAY_FILLER,
    COMMAND, CONFIG, SYNC_FILLERS
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)


# ── Connection registry ───────────────────────────────────────────

_connected_satellites: dict[str, SatelliteConnection] = {}


class SatelliteConnection:
    """Tracks a connected satellite's WebSocket and metadata."""

    def __init__(self, websocket: WebSocket, satellite_id: str) -> None:
        self.websocket = websocket
        self.satellite_id = satellite_id
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.session_id: str | None = None

    async def send(self, message: dict) -> None:
        """Send a JSON message to the satellite."""
        await self.websocket.send_json(message)

    async def send_command(self, action: str, params: dict | None = None) -> None:
        """Send a COMMAND message."""
        await self.send({
            "type": "COMMAND",
            "action": action,
            "params": params or {},
        })


def get_connected_satellites() -> dict[str, SatelliteConnection]:
    """Return all currently connected satellite connections."""
    return _connected_satellites.copy()


def get_connection(satellite_id: str) -> SatelliteConnection | None:
    """Get a specific satellite's connection."""
    return _connected_satellites.get(satellite_id)


# ── WebSocket handler ─────────────────────────────────────────────


async def satellite_ws_handler(websocket: WebSocket) -> None:
    """Handle a satellite WebSocket connection.

    This is the main entry point — mount it in FastAPI via:
        app.add_api_websocket_route("/ws/satellite", satellite_ws_handler)
    """
    await websocket.accept()
    satellite_id: str | None = None
    conn = SatelliteConnection(websocket, "")

    try:
        # First message must be ANNOUNCE
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        if raw.get("type") != "ANNOUNCE":
            await websocket.send_json({"type": "ERROR", "detail": "Expected ANNOUNCE"})
            await websocket.close()
            return

        satellite_id = raw.get("satellite_id", "")
        if not satellite_id:
            await websocket.send_json({"type": "ERROR", "detail": "Missing satellite_id"})
            await websocket.close()
            return

        conn.satellite_id = satellite_id
        session_id = f"sess-{uuid.uuid4().hex[:8]}"
        conn.session_id = session_id

        # Register connection
        _connected_satellites[satellite_id] = conn

        # Update DB status
        _update_satellite_status(satellite_id, "online")

        # Send ACCEPTED
        await conn.send({
            "type": "ACCEPTED",
            "satellite_id": satellite_id,
            "session_id": session_id,
        })

        logger.info("Satellite connected: %s (session %s)", satellite_id, session_id)

        # Message loop
        async for raw_msg in websocket.iter_json():
            msg_type = raw_msg.get("type", "")

            if msg_type == "HEARTBEAT":
                await _handle_heartbeat(conn, raw_msg)

            elif msg_type == "WAKE":
                await _handle_wake(conn, raw_msg)

            elif msg_type == "AUDIO_START":
                await _handle_audio_start(conn, raw_msg)

            elif msg_type == "AUDIO_CHUNK":
                await _handle_audio_chunk(conn, raw_msg)

            elif msg_type == "AUDIO_END":
                await _handle_audio_end(conn, raw_msg)

            elif msg_type == "STATUS":
                await _handle_status(conn, raw_msg)

            else:
                logger.warning(
                    "Unknown message type from %s: %s", satellite_id, msg_type
                )

    except WebSocketDisconnect:
        logger.info("Satellite disconnected: %s", satellite_id)
    except asyncio.TimeoutError:
        logger.warning("Satellite connection timed out (no ANNOUNCE)")
    except Exception:
        logger.exception("Error in satellite WebSocket for %s", satellite_id)
    finally:
        if satellite_id:
            _connected_satellites.pop(satellite_id, None)
            _update_satellite_status(satellite_id, "offline")


# ── Message handlers ──────────────────────────────────────────────


async def _handle_heartbeat(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status from heartbeat."""
    conn.last_heartbeat = time.time()
    try:
        db = get_db()
        db.execute(
            """UPDATE satellites
               SET last_seen = ?, uptime_seconds = ?, wifi_rssi = ?, cpu_temp = ?
               WHERE id = ?""",
            (
                datetime.now(timezone.utc).isoformat(),
                msg.get("uptime"),
                msg.get("wifi_rssi"),
                msg.get("cpu_temp"),
                conn.satellite_id,
            ),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to update heartbeat for %s", conn.satellite_id)


async def _handle_wake(conn: SatelliteConnection, msg: dict) -> None:
    """Handle wake word detection from satellite."""
    logger.info(
        "Wake word from %s (confidence: %.2f)",
        conn.satellite_id,
        msg.get("wake_word_confidence", 0),
    )
    # Create an audio session
    session_id = f"audio-{uuid.uuid4().hex[:8]}"
    conn.session_id = session_id
    try:
        db = get_db()
        db.execute(
            "INSERT INTO satellite_audio_sessions (id, satellite_id) VALUES (?, ?)",
            (session_id, conn.satellite_id),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to create audio session")


async def _handle_audio_start(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has started from the satellite."""
    logger.debug("Audio start from %s (format: %s)", conn.satellite_id, msg.get("format"))


async def _handle_audio_chunk(conn: SatelliteConnection, msg: dict) -> None:
    """Receive an audio chunk from the satellite.

    In a full implementation, this would buffer audio and feed it to the
    STT engine. For now we just acknowledge receipt.
    """
    # TODO: Buffer audio chunks and feed to STT pipeline
    pass


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — process the utterance.

    In a full implementation, this would:
    1. Run STT on the buffered audio
    2. Feed the transcript through the pipeline (Layer 1-3)
    3. Generate TTS response
    4. Stream TTS back via TTS_START/TTS_CHUNK/TTS_END
    5. If processing > filler_threshold, send PLAY_FILLER first
    """
    reason = msg.get("reason", "vad_silence")
    logger.info("Audio end from %s (reason: %s)", conn.satellite_id, reason)

    # Update session
    if conn.session_id:
        try:
            db = get_db()
            db.execute(
                "UPDATE satellite_audio_sessions SET ended_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), conn.session_id),
            )
            db.commit()
        except Exception:
            pass

    # TODO: Full STT → Pipeline → TTS implementation
    # For now, send a placeholder acknowledgment
    await conn.send({
        "type": "TTS_START",
        "session_id": conn.session_id,
        "format": "pcm_22k_16bit_mono",
    })
    await conn.send({
        "type": "TTS_END",
        "session_id": conn.session_id,
    })


async def _handle_status(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status (idle, listening, speaking)."""
    status = msg.get("status", "idle")
    logger.debug("Satellite %s status: %s", conn.satellite_id, status)


# ── Helpers ───────────────────────────────────────────────────────


def _update_satellite_status(satellite_id: str, status: str) -> None:
    """Update the satellite status in the database."""
    try:
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE satellites SET status = ?, last_seen = ? WHERE id = ?",
            (status, now, satellite_id),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to update satellite status: %s", satellite_id)


# ── Utility functions for sending to satellites ───────────────────


async def send_play_filler(satellite_id: str) -> bool:
    """Tell a satellite to play a cached filler phrase."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send({"type": "PLAY_FILLER", "session_id": conn.session_id})
        return True
    return False


async def send_command(satellite_id: str, action: str, params: dict | None = None) -> bool:
    """Send a command to a connected satellite."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send_command(action, params)
        return True
    return False


async def send_config(satellite_id: str, config: dict) -> bool:
    """Push configuration to a connected satellite."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send({"type": "CONFIG", **config})
        return True
    return False
