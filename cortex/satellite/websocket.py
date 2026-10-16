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
        self.room: str | None = None
        self.capabilities: list[str] = []
        self.capture_id: str | None = None
        self.capture_active = False
        self.activated = False
        self.voice_session = None
        self.delivery_metadata: dict[str, Any] = {}
        self.readiness: dict[str, Any] = {}

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
        previous = _connected_satellites.get(satellite_id)
        _connected_satellites[satellite_id] = conn
        if previous is not None and previous is not conn:
            if previous.voice_session is not None:
                await previous.voice_session.close()
            await previous.websocket.close(code=1000, reason="Replaced by a new device connection")
        if _connected_satellites.get(satellite_id) is not conn:
            return

        # Extract client IP and metadata from ANNOUNCE
        client_ip = websocket.client.host if websocket.client else None
        capabilities = raw.get("capabilities") or []
        conn.capabilities = capabilities
        saved_config = _saved_config(satellite_id)
        conn.room = saved_config.get("room", raw.get("room")) or None
        conn.has_wake_word = "wake_word" in capabilities
        _update_satellite_status(
            satellite_id, "online",
            ip_address=client_ip,
            hostname=raw.get("hostname"),
            room=conn.room,
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
        if saved_config:
            await send_config(satellite_id, saved_config)

        # Message loop
        async for raw_msg in websocket.iter_json():
            if _connected_satellites.get(satellite_id) is not conn:
                break
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
            elif msg_type == "PLAYBACK_ACK":
                if conn.voice_session is not None:
                    conn.voice_session.acknowledge(raw_msg)

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
        if conn.voice_session is not None:
            await conn.voice_session.close()
        if satellite_id and _connected_satellites.get(satellite_id) is conn:
            _connected_satellites.pop(satellite_id, None)
            _update_satellite_status(satellite_id, "offline")


# ── Message handlers ──────────────────────────────────────────────


async def _handle_heartbeat(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status from heartbeat."""
    conn.last_heartbeat = time.time()
    if isinstance(msg.get("readiness"), dict):
        conn.readiness = msg["readiness"]
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
    conn.activated = True
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
    conn.capture_id = msg.get("capture_id", uuid.uuid4().hex)
    if (not isinstance(conn.capture_id, str) or not 1 <= len(conn.capture_id) <= 128
            or conn.capture_id.strip() != conn.capture_id or not conn.capture_id.isprintable()):
        conn.capture_active = False
        await conn.send({"type": "PIPELINE_ERROR", "detail": "Invalid capture identity"})
        return
    expected = {"rate": 16000, "width": 2, "channels": 1}
    conn.capture_active = (
        isinstance(conn.audio_format, dict) and conn.audio_format == expected
        and all(type(value) is int for value in conn.audio_format.values())
    )
    if not conn.capture_active or msg.get("format", "pcm_16k_16bit_mono") != "pcm_16k_16bit_mono":
        conn.capture_active = False
        await conn.send({"type": "PIPELINE_ERROR", "detail": "Only mono PCM16 at 16000 Hz is supported"})
    logger.debug("Audio start from %s (format: %s)", conn.satellite_id, msg.get("format"))


async def _handle_audio_chunk(conn: SatelliteConnection, msg: dict) -> None:
    """Receive an audio chunk from the satellite and buffer it."""
    audio_b64 = msg.get("audio", "")
    if not conn.capture_active:
        return
    from cortex.orchestrator.voice_turns import MAX_CAPTURE_BYTES, MAX_CHUNK_BYTES
    try:
        if not isinstance(audio_b64, str) or len(audio_b64) > ((MAX_CHUNK_BYTES + 2) // 3) * 4:
            raise ValueError("Audio chunk exceeds the capture allocation")
        audio = base64.b64decode(audio_b64, validate=True)
        if len(audio) % 2 or len(conn.audio_buffer) + len(audio) > MAX_CAPTURE_BYTES:
            raise ValueError("Invalid PCM frame or recording limit exceeded; audio was not accepted")
        conn.audio_buffer.extend(audio)
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid capture from %s: %s", conn.satellite_id, exc)
        conn.capture_active = False
        conn.audio_buffer.clear()
        await conn.send({"type": "PIPELINE_ERROR", "detail": str(exc)})


async def _handle_audio_phrase_end(conn: SatelliteConnection, msg: dict) -> None:
    """A phrase boundary (short pause) — snapshot buffer and queue for processing.

    CE-2: Each phrase is queued independently so multiple questions
    spoken in sequence are processed one by one.
    """
    # A short acoustic pause is not a complete command. Retain the full
    # bounded capture until AUDIO_END; no tail is submitted twice.


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — queue final phrase for processing."""
    from cortex.orchestrator.voice_turns import Capture, get_voice_service
    if not conn.capture_active:
        return
    conn.capture_active = False
    audio = bytes(conn.audio_buffer)
    conn.audio_buffer.clear()
    reason = msg.get("reason", "vad_silence")
    if reason in ("auto_listen_timeout", "cancelled") or not audio:
        conn.activated = False
        return
    service = get_voice_service()
    if service is None:
        logger.error("Voice admission service is unavailable")
        await conn.send({"type": "PIPELINE_ERROR", "detail": "Voice admission service is unavailable"})
        return
    await service.session(conn).submit(Capture(
        audio, conn.capture_id, conn.activated, reason,
    ))
    conn.activated = False


async def _handle_barge_in(conn: SatelliteConnection, msg: dict) -> None:
    """User interrupted during TTS playback — save state and cancel pipeline."""
    logger.info("Barge-in from %s", conn.satellite_id)

    if conn.voice_session is not None:
        await conn.voice_session.pause(msg)

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


def _saved_config(satellite_id: str) -> dict:
    cursor = get_db().execute("SELECT * FROM satellites WHERE id=?", (satellite_id,))
    row = cursor.fetchone()
    if row is None:
        return {}
    stored = dict(zip((column[0] for column in cursor.description), row))
    fields = ("room", "wake_word", "volume", "mic_gain", "vad_sensitivity", "filler_enabled",
              "vad_enabled", "led_brightness", "button_mode", "audio_device_out")
    config = {key: stored[key] for key in fields if stored.get(key) is not None}
    if stored.get("features") is not None:
        config["features"] = json.loads(stored["features"])
    return config


async def send_config(satellite_id: str, config: dict) -> bool:
    """Push configuration to a connected satellite."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        payload = dict(config)
        if "vad_sensitivity" in payload:
            payload["vad_sensitivity"] = round(float(payload["vad_sensitivity"]) * 3)
        for key in ("filler_enabled", "vad_enabled"):
            if key in payload:
                payload[key] = bool(payload[key])
        if "room" in payload and (payload["room"] or None) != conn.room:
            playback = dict(conn.delivery_metadata)
            if conn.voice_session is not None:
                await conn.voice_session.close()
                conn.voice_session = None
            conn.capture_active = False
            conn.audio_buffer.clear()
            conn.activated = False
            await conn.send({"type": "COMMAND", "action": "stop", "params": playback})
            conn.room = payload["room"] or None
        await conn.send({"type": "CONFIG", **payload})
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
