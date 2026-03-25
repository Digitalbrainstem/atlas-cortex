"""WebSocket endpoint for satellite audio streaming and control.

Handles the real-time communication channel between Atlas server and
satellite devices:

  Satellite → Server (Pi protocol):
    ANNOUNCE, WAKE, AUDIO_START, AUDIO_CHUNK, AUDIO_PHRASE_END,
    AUDIO_END, STATUS, HEARTBEAT, BARGE_IN

  Satellite → Server (ESP32 protocol):
    register, audio_start, audio_data, audio_end, button, heartbeat

  Server → Satellite:
    ACCEPTED, TTS_START, TTS_CHUNK, TTS_END, PLAY_FILLER,
    COMMAND, CONFIG, SYNC_FILLERS (Pi)
    registered, speaking_start, audio_chunk, speaking_end,
    led, playback_stop (ESP32)
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
from cortex.satellite.esp32_handler import ESP32SatelliteHandler

logger = logging.getLogger(__name__)


# ── Event callbacks (registered by server.py to avoid import cycles) ──

_barge_in_callbacks: list = []


def on_barge_in(callback) -> None:
    """Register a callback for barge-in events: async fn(satellite_id, room)."""
    _barge_in_callbacks.append(callback)


# ── Connection registry ───────────────────────────────────────────

_connected_satellites: dict[str, SatelliteConnection] = {}


_MAX_PHRASE_QUEUE_SIZE = 5  # prevent memory issues from run-away queuing


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
        # CE-2: per-connection phrase queue for multi-question support
        self.phrase_queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_PHRASE_QUEUE_SIZE)
        self._queue_worker_task: asyncio.Task | None = None

        # CE-4: Pause buffer for conversational pause & pivot
        self.paused_response: str | None = None  # Full text that was being spoken
        self.paused_position: int = 0            # Char position where interrupted
        self.paused_at: float = 0.0              # Timestamp of pause (for staleness)

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

    Detects device type from the first message:
      - ``{"type": "register", "device_type": "esp32", ...}`` → ESP32 handler
      - ``{"type": "register", "device_type": "tablet", ...}`` → Tablet (uses Pi protocol with display)
      - ``{"type": "ANNOUNCE", ...}`` → Pi handler (existing protocol)
    """
    await websocket.accept()
    satellite_id: str | None = None
    conn = SatelliteConnection(websocket, "")

    try:
        # First message determines device type
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        msg_type = raw.get("type", "")

        # ── ESP32 satellite (lightweight protocol) ────────────────
        if msg_type == "register" and raw.get("device_type") == "esp32":
            name = raw.get("name", "esp32-satellite")
            satellite_id = f"esp32-{name}"
            esp32 = ESP32SatelliteHandler(websocket, satellite_id)
            await esp32.handle_register(raw)
            logger.info("ESP32 satellite connected: %s", satellite_id)

            try:
                async for raw_msg in websocket.iter_json():
                    await esp32.handle_message(raw_msg)
            except WebSocketDisconnect:
                logger.info("ESP32 satellite disconnected: %s", satellite_id)
            finally:
                await esp32.on_disconnect()
            return

        # ── Tablet satellite (full protocol with display) ─────────
        # Tablets register like ESP32 but use the Pi protocol for
        # audio streaming.  Re-write the first message as ANNOUNCE so
        # the existing Pi handler picks it up seamlessly.
        if msg_type == "register" and raw.get("device_type") == "tablet":
            name = raw.get("name", "tablet-satellite")
            satellite_id = f"tablet-{name}"
            hardware = raw.get("hardware", "generic-x86")
            tablet_caps = raw.get("capabilities") or [
                "mic", "speaker", "display", "touch",
            ]
            if "camera" in raw:
                tablet_caps.append("camera")
            # Synthesise an ANNOUNCE message so the Pi handler runs
            raw = {
                "type": "ANNOUNCE",
                "satellite_id": satellite_id,
                "hostname": raw.get("hostname", name),
                "room": raw.get("room", ""),
                "capabilities": tablet_caps,
                "hw_info": {"device_type": "tablet", "hardware": hardware},
            }
            msg_type = "ANNOUNCE"
            logger.info("Tablet satellite connected: %s (%s)", satellite_id, hardware)

        # ── Pi satellite (full protocol) ──────────────────────────
        if msg_type != "ANNOUNCE":
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

            elif msg_type == "CMD_ACK":
                await _handle_cmd_ack(conn, raw_msg)

            elif msg_type == "LOG_UPLOAD":
                await _handle_log_upload(conn, raw_msg)

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
        # CE-2: Stop the queue worker on disconnect
        _stop_queue_worker(conn)
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
    """A phrase boundary (short pause) — snapshot buffer and queue for processing.

    CE-2: Each phrase is queued independently so multiple questions
    spoken in sequence are processed one by one.
    """
    audio_data = bytes(conn.audio_buffer)
    conn.audio_buffer = bytearray()

    if len(audio_data) < 1600:
        logger.debug("Phrase too short (%d bytes), discarding", len(audio_data))
        return

    logger.info(
        "Phrase boundary from %s — queuing %d bytes (%d already queued)",
        conn.satellite_id, len(audio_data), conn.phrase_queue.qsize(),
    )

    try:
        conn.phrase_queue.put_nowait(audio_data)
    except asyncio.QueueFull:
        logger.warning("Phrase queue full for %s, dropping oldest phrase", conn.satellite_id)
        try:
            conn.phrase_queue.get_nowait()
            conn.phrase_queue.task_done()
        except asyncio.QueueEmpty:
            pass
        conn.phrase_queue.put_nowait(audio_data)

    _ensure_queue_worker(conn)


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — queue final phrase for processing."""
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
        # If no phrases were queued either, nothing to do
        if conn.phrase_queue.empty():
            logger.debug("Audio too short (%d bytes) and queue empty, ignoring", len(audio_data))
            return
        # Phrases already queued — just ensure the worker is running
        _ensure_queue_worker(conn)
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

    # CE-2: Enqueue the final phrase for processing
    try:
        conn.phrase_queue.put_nowait(audio_data)
    except asyncio.QueueFull:
        logger.warning("Phrase queue full for %s on AUDIO_END, dropping oldest", conn.satellite_id)
        try:
            conn.phrase_queue.get_nowait()
            conn.phrase_queue.task_done()
        except asyncio.QueueEmpty:
            pass
        conn.phrase_queue.put_nowait(audio_data)

    _ensure_queue_worker(conn)


async def _handle_barge_in(conn: SatelliteConnection, msg: dict) -> None:
    """User interrupted during TTS playback — save state and cancel pipeline."""
    logger.info("Barge-in from %s", conn.satellite_id)

    # CE-4: Capture paused response before cancelling.  The response text and
    # approximate playback position are sent by the satellite in the BARGE_IN
    # message, or were stored on conn by the voice pipeline as it streamed.
    barge_text = msg.get("response_text") or getattr(conn, "_current_response", None)
    barge_pos = msg.get("char_position", 0) or getattr(conn, "_spoken_chars", 0)
    if barge_text:
        conn.paused_response = barge_text
        conn.paused_position = barge_pos
        conn.paused_at = time.monotonic()
        logger.info(
            "Saved paused response for %s (pos=%d, len=%d)",
            conn.satellite_id, barge_pos, len(barge_text),
        )

    # Cancel any in-progress pipeline task
    task = getattr(conn, "pipeline_task", None)
    if task and not task.done():
        task.cancel()
        logger.info("Cancelled in-progress pipeline for %s", conn.satellite_id)
    conn.pipeline_task = None

    # CE-2: Drain the phrase queue — discard pending phrases on barge-in
    _drain_phrase_queue(conn)

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


# ── CE-2: Phrase queue worker ─────────────────────────────────────


def _ensure_queue_worker(conn: SatelliteConnection) -> None:
    """Start the phrase queue worker if not already running."""
    task = conn._queue_worker_task
    if task is not None and not task.done():
        return
    conn._queue_worker_task = asyncio.create_task(
        _phrase_queue_worker(conn),
        name=f"phrase-worker-{conn.satellite_id}",
    )


def _stop_queue_worker(conn: SatelliteConnection) -> None:
    """Cancel the queue worker and drain remaining phrases."""
    task = conn._queue_worker_task
    if task and not task.done():
        task.cancel()
    conn._queue_worker_task = None
    _drain_phrase_queue(conn)


def _drain_phrase_queue(conn: SatelliteConnection) -> None:
    """Discard all pending phrases from the queue."""
    drained = 0
    while True:
        try:
            conn.phrase_queue.get_nowait()
            conn.phrase_queue.task_done()
            drained += 1
        except asyncio.QueueEmpty:
            break
    if drained:
        logger.info("Drained %d pending phrases from %s queue", drained, conn.satellite_id)


async def _phrase_queue_worker(conn: SatelliteConnection) -> None:
    """Process queued phrase audio one at a time: STT → Pipeline → TTS.

    Runs as a background task per connection.  Each phrase is handed off
    to ``process_voice_pipeline``.  The ``more_pending`` flag is set on
    the connection so the voice pipeline can include it in TTS_END messages.
    """
    from cortex.orchestrator.voice import process_voice_pipeline

    satellite_id = conn.satellite_id
    logger.info("Phrase queue worker started for %s", satellite_id)

    try:
        while True:
            phrase_audio = await conn.phrase_queue.get()
            try:
                # Signal whether more phrases follow this one
                conn._more_phrases_pending = not conn.phrase_queue.empty()
                task = asyncio.create_task(process_voice_pipeline(conn, phrase_audio))
                conn.pipeline_task = task
                await task
            except asyncio.CancelledError:
                logger.info("Phrase queue worker cancelled for %s", satellite_id)
                raise
            except Exception:
                logger.exception(
                    "Error processing queued phrase for %s — continuing to next",
                    satellite_id,
                )
            finally:
                conn._more_phrases_pending = False
                conn.pipeline_task = None
                conn.phrase_queue.task_done()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Phrase queue worker stopped for %s", satellite_id)


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


# ── Remote management commands ────────────────────────────────────

REMOTE_CMD_TYPES = {
    "CONFIG_UPDATE",
    "EXEC_SCRIPT",
    "RESTART_SERVICE",
    "UPDATE_AGENT",
    "KIOSK_URL",
    "REBOOT",
    "LOG_REQUEST",
}

_EXEC_SCRIPT_MAX_TIMEOUT = 300


async def send_remote_command(
    satellite_id: str,
    command_type: str,
    payload: dict | None = None,
) -> dict:
    """Send a remote management command to a satellite.

    Stores the command in the DB for audit trail, sends it over
    WebSocket, and returns the DB row as a dict.
    """
    if command_type not in REMOTE_CMD_TYPES:
        raise ValueError(f"Unknown command type: {command_type}")

    payload = payload or {}

    # Enforce timeout limits on EXEC_SCRIPT
    if command_type == "EXEC_SCRIPT":
        timeout = payload.get("timeout", 30)
        payload["timeout"] = max(1, min(int(timeout), _EXEC_SCRIPT_MAX_TIMEOUT))

    db = get_db()
    cur = db.execute(
        "INSERT INTO satellite_commands (satellite_id, command_type, payload, status) "
        "VALUES (?, ?, ?, 'pending')",
        (satellite_id, command_type, json.dumps(payload)),
    )
    cmd_id = cur.lastrowid
    db.commit()

    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send({
            "type": command_type,
            "cmd_id": cmd_id,
            "payload": payload,
        })
        db.execute(
            "UPDATE satellite_commands SET status = 'sent' WHERE id = ?",
            (cmd_id,),
        )
        db.commit()
        status = "sent"
    else:
        status = "pending"

    return {
        "id": cmd_id,
        "satellite_id": satellite_id,
        "command_type": command_type,
        "payload": payload,
        "status": status,
    }


async def _handle_cmd_ack(conn: SatelliteConnection, msg: dict) -> None:
    """Process a CMD_ACK from a satellite."""
    cmd_id = msg.get("cmd_id")
    result = msg.get("result", "")
    if cmd_id is None:
        return
    try:
        db = get_db()
        db.execute(
            "UPDATE satellite_commands SET status = 'ack', result = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(result) if not isinstance(result, str) else result, int(cmd_id)),
        )
        db.commit()
        logger.info("CMD_ACK for cmd %s from %s: %s", cmd_id, conn.satellite_id, result)
    except Exception:
        logger.exception("Failed to process CMD_ACK for cmd %s", cmd_id)


async def _handle_log_upload(conn: SatelliteConnection, msg: dict) -> None:
    """Process uploaded logs from a satellite LOG_REQUEST."""
    cmd_id = msg.get("cmd_id")
    logs = msg.get("logs", "")
    if cmd_id is None:
        return
    try:
        db = get_db()
        db.execute(
            "UPDATE satellite_commands SET status = 'ack', result = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (logs, int(cmd_id)),
        )
        db.commit()
        logger.info("Log upload for cmd %s from %s (%d chars)", cmd_id, conn.satellite_id, len(logs))
    except Exception:
        logger.exception("Failed to store log upload for cmd %s", cmd_id)


def get_command_history(
    satellite_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Retrieve command history for a satellite."""
    db = get_db()
    rows = db.execute(
        "SELECT id, satellite_id, command_type, payload, status, result, "
        "created_at, completed_at FROM satellite_commands "
        "WHERE satellite_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (satellite_id, limit, offset),
    ).fetchall()
    return [
        {
            "id": r[0],
            "satellite_id": r[1],
            "command_type": r[2],
            "payload": json.loads(r[3]) if r[3] else {},
            "status": r[4],
            "result": r[5],
            "created_at": r[6],
            "completed_at": r[7],
        }
        for r in rows
    ]
