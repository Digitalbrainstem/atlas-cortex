"""WebSocket endpoint for satellite audio streaming and control.

Handles the real-time communication channel between Atlas server and
satellite devices:

  Satellite → Server:
    ANNOUNCE, WAKE, AUDIO_START, AUDIO_CHUNK, AUDIO_PHRASE_END,
    AUDIO_END, STATUS, HEARTBEAT, BARGE_IN

  Server → Satellite:
    ACCEPTED, TTS_START, TTS_CHUNK, TTS_END, PLAY_FILLER,
    COMMAND, CONFIG, SYNC_FILLERS
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)


# ── Event callbacks (registered by server.py to avoid import cycles) ──

_barge_in_callbacks: list = []


def on_barge_in(callback) -> None:
    """Register a callback for barge-in events: async fn(satellite_id, room)."""
    _barge_in_callbacks.append(callback)


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
        self.audio_buffer: bytearray = bytearray()
        self.audio_format: dict = {}
        self.has_wake_word: bool = False  # True if satellite has local wake word detection
        self.pipeline_task: asyncio.Task | None = None  # in-progress voice pipeline

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

        # Extract client IP and metadata from ANNOUNCE
        client_ip = websocket.client.host if websocket.client else None
        capabilities = raw.get("capabilities") or []
        conn.has_wake_word = "wake_word" in capabilities
        _update_satellite_status(
            satellite_id, "online",
            ip_address=client_ip,
            hostname=raw.get("hostname"),
            room=raw.get("room"),
            capabilities=capabilities,
            hardware_info=raw.get("hw_info"),
        )

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

            elif msg_type == "AUDIO_PHRASE_END":
                await _handle_audio_phrase_end(conn, raw_msg)

            elif msg_type == "AUDIO_END":
                await _handle_audio_end(conn, raw_msg)

            elif msg_type == "STATUS":
                await _handle_status(conn, raw_msg)

            elif msg_type == "BARGE_IN":
                await _handle_barge_in(conn, raw_msg)

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
    conn.audio_buffer = bytearray()
    conn.audio_format = msg.get("format_info", {"rate": 16000, "width": 2, "channels": 1})
    logger.debug("Audio start from %s (format: %s)", conn.satellite_id, msg.get("format"))


async def _handle_audio_chunk(conn: SatelliteConnection, msg: dict) -> None:
    """Receive an audio chunk from the satellite and buffer it."""
    audio_b64 = msg.get("audio", "")
    if audio_b64:
        conn.audio_buffer.extend(base64.b64decode(audio_b64))


async def _handle_audio_phrase_end(conn: SatelliteConnection, msg: dict) -> None:
    """A phrase boundary (short pause) — satellite is still listening.

    For now we just log it; the audio stays in the buffer and will be
    processed when AUDIO_END arrives.  The message type is defined so
    the protocol is ready for future streaming-STT support.
    """
    logger.debug(
        "Phrase boundary from %s (%d bytes buffered so far)",
        conn.satellite_id, len(conn.audio_buffer),
    )


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — run STT → Pipeline → TTS."""
    reason = msg.get("reason", "vad_silence")
    audio_data = bytes(conn.audio_buffer)
    conn.audio_buffer = bytearray()

    logger.info(
        "Audio end from %s (reason: %s, %d bytes)",
        conn.satellite_id, reason, len(audio_data),
    )

    # Auto-listen timeout means no one spoke — discard silently
    if reason == "auto_listen_timeout":
        logger.info("Auto-listen timeout from %s — no speech, discarding", conn.satellite_id)
        return

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

    if len(audio_data) < 1600:
        # Too short to be meaningful speech (~50ms)
        logger.debug("Audio too short (%d bytes), ignoring", len(audio_data))
        return

    # Cap audio at ~15 seconds (480000 bytes at 16kHz 16-bit mono) to prevent
    # VAD runaway and whisper hallucination on very long recordings.
    MAX_AUDIO_BYTES = 480000  # 15 seconds
    if len(audio_data) > MAX_AUDIO_BYTES:
        logger.warning(
            "Audio from %s too long (%d bytes / %.1fs), truncating to last %.0fs",
            conn.satellite_id, len(audio_data),
            len(audio_data) / 32000, MAX_AUDIO_BYTES / 32000,
        )
        audio_data = audio_data[-MAX_AUDIO_BYTES:]

    # Run the voice pipeline in a background task so websocket stays responsive
    from cortex.orchestrator.voice import process_voice_pipeline
    task = asyncio.create_task(process_voice_pipeline(conn, audio_data))
    conn.pipeline_task = task


async def _handle_barge_in(conn: SatelliteConnection, msg: dict) -> None:
    """User interrupted during TTS playback — cancel pipeline and notify avatars."""
    logger.info("Barge-in from %s", conn.satellite_id)

    # Cancel any in-progress pipeline task
    task = getattr(conn, "pipeline_task", None)
    if task and not task.done():
        task.cancel()
        logger.info("Cancelled in-progress pipeline for %s", conn.satellite_id)
    conn.pipeline_task = None

    # Clear any buffered audio from a previous turn
    conn.audio_buffer = bytearray()

    # Notify registered callbacks (avatar broadcast, etc.)
    try:
        db = get_db()
        row = db.execute(
            "SELECT room FROM satellites WHERE id = ?",
            (conn.satellite_id,),
        ).fetchone()
        room = row[0] if row else None
        for cb in _barge_in_callbacks:
            try:
                await cb(conn.satellite_id, room)
            except Exception:
                logger.debug("Barge-in callback failed", exc_info=True)
    except Exception:
        logger.debug("Could not process barge-in callbacks for %s", conn.satellite_id)


async def _handle_status(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status (idle, listening, speaking)."""
    status = msg.get("status", "idle")
    logger.debug("Satellite %s status: %s", conn.satellite_id, status)

# ── Helpers ───────────────────────────────────────────────────────


def _update_satellite_status(
    satellite_id: str,
    status: str,
    ip_address: str | None = None,
    hostname: str | None = None,
    room: str | None = None,
    capabilities: list | None = None,
    hardware_info: dict | None = None,
) -> None:
    """Update the satellite status in the database (upsert)."""
    try:
        import json as _json

        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        caps_json = _json.dumps(capabilities) if capabilities else None
        hw_json = _json.dumps(hardware_info) if hardware_info else None

        db.execute(
            """INSERT INTO satellites (id, display_name, status, last_seen,
                   ip_address, hostname, room, capabilities, hardware_info)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status = ?,
                   last_seen = ?,
                   ip_address = COALESCE(?, ip_address),
                   hostname = COALESCE(?, hostname),
                   room = COALESCE(?, room),
                   capabilities = COALESCE(?, capabilities),
                   hardware_info = COALESCE(?, hardware_info)""",
            (
                satellite_id, satellite_id, status, now,
                ip_address, hostname, room, caps_json, hw_json,
                status, now,
                ip_address, hostname, room, caps_json, hw_json,
            ),
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
