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
import base64
import json
import logging
import os
import struct
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)

# Wyoming service addresses (configurable via env)
_STT_HOST = os.environ.get("STT_HOST", "172.17.0.5")
_STT_PORT = int(os.environ.get("STT_PORT", "10300"))
_TTS_HOST = os.environ.get("TTS_HOST", "172.17.0.4")
_TTS_PORT = int(os.environ.get("TTS_PORT", "10200"))


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
        _update_satellite_status(
            satellite_id, "online",
            ip_address=client_ip,
            hostname=raw.get("hostname"),
            room=raw.get("room"),
            capabilities=raw.get("capabilities"),
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
    conn.audio_buffer = bytearray()
    conn.audio_format = msg.get("format_info", {"rate": 16000, "width": 2, "channels": 1})
    logger.debug("Audio start from %s (format: %s)", conn.satellite_id, msg.get("format"))


async def _handle_audio_chunk(conn: SatelliteConnection, msg: dict) -> None:
    """Receive an audio chunk from the satellite and buffer it."""
    audio_b64 = msg.get("audio", "")
    if audio_b64:
        conn.audio_buffer.extend(base64.b64decode(audio_b64))


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — run STT → Pipeline → TTS."""
    reason = msg.get("reason", "vad_silence")
    audio_data = bytes(conn.audio_buffer)
    conn.audio_buffer = bytearray()

    logger.info(
        "Audio end from %s (reason: %s, %d bytes)",
        conn.satellite_id, reason, len(audio_data),
    )

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

    # Run the voice pipeline in a background task so websocket stays responsive
    asyncio.create_task(_process_voice_pipeline(conn, audio_data))


async def _handle_status(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status (idle, listening, speaking)."""
    status = msg.get("status", "idle")
    logger.debug("Satellite %s status: %s", conn.satellite_id, status)


# ── Voice pipeline ────────────────────────────────────────────────


async def _process_voice_pipeline(conn: SatelliteConnection, audio_data: bytes) -> None:
    """Full STT → Pipeline → TTS → stream back to satellite."""
    from cortex.voice.wyoming import WyomingClient, WyomingError

    satellite_id = conn.satellite_id

    try:
        # ── Step 1: STT ──────────────────────────────────────────
        stt = WyomingClient(_STT_HOST, _STT_PORT, timeout=30.0)
        logger.info("Running STT on %d bytes from %s", len(audio_data), satellite_id)

        try:
            transcript = await stt.transcribe(audio_data, sample_rate=16000)
        except WyomingError as e:
            logger.error("STT failed for %s: %s", satellite_id, e)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": f"STT failed: {e}"})
            except Exception:
                pass
            return

        transcript = transcript.strip()
        if not transcript:
            logger.info("Empty transcript from %s, ignoring", satellite_id)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "Empty transcript"})
            except Exception:
                pass
            return

        logger.info("STT result from %s: %r", satellite_id, transcript)

        # ── Step 2: Pipeline ─────────────────────────────────────
        from cortex.providers import get_provider
        from cortex.pipeline import run_pipeline

        provider = get_provider()

        # Collect full response text from pipeline
        response_parts: list[str] = []
        pipeline_gen = await run_pipeline(
            message=transcript,
            provider=provider,
            satellite_id=satellite_id,
            model_fast="qwen2.5:7b",
        )
        async for token in pipeline_gen:
            response_parts.append(token)

        full_response = "".join(response_parts).strip()
        if not full_response:
            logger.warning("Empty pipeline response for %r", transcript)
            return

        logger.info("Pipeline response for %s: %r", satellite_id, full_response[:200])

        # ── Step 3: TTS ──────────────────────────────────────────
        tts = WyomingClient(_TTS_HOST, _TTS_PORT, timeout=30.0)

        try:
            tts_audio, audio_info = await tts.synthesize(full_response)
        except WyomingError as e:
            logger.error("TTS failed for %s: %s", satellite_id, e)
            return

        if not tts_audio:
            logger.warning("Empty TTS audio for %s", satellite_id)
            return

        tts_rate = audio_info.get("rate", 22050)
        tts_width = audio_info.get("width", 2)
        tts_channels = audio_info.get("channels", 1)

        logger.info(
            "TTS produced %d bytes (%dHz %dch) for %s",
            len(tts_audio), tts_rate, tts_channels, satellite_id,
        )

        # ── Step 4: Send at native rate (hardware handles conversion) ──
        playback_audio = tts_audio
        playback_rate = tts_rate

        # ── Step 5: Stream back to satellite ─────────────────────
        await conn.send({
            "type": "TTS_START",
            "session_id": conn.session_id,
            "format": f"pcm_{playback_rate // 1000}k_16bit_mono",
            "sample_rate": playback_rate,
            "text": full_response,
        })

        # Send in chunks (~4KB each)
        chunk_size = 4096
        for offset in range(0, len(playback_audio), chunk_size):
            chunk = playback_audio[offset:offset + chunk_size]
            await conn.send({
                "type": "TTS_CHUNK",
                "session_id": conn.session_id,
                "audio": base64.b64encode(chunk).decode("ascii"),
            })

        await conn.send({
            "type": "TTS_END",
            "session_id": conn.session_id,
        })

        logger.info("Streamed TTS to %s (%d bytes)", satellite_id, len(playback_audio))

    except WebSocketDisconnect:
        logger.warning("Satellite %s disconnected during pipeline", satellite_id)
    except Exception:
        logger.exception("Voice pipeline error for %s", satellite_id)
        # Notify satellite to return to IDLE on failure
        try:
            await conn.send({"type": "PIPELINE_ERROR", "detail": "Voice pipeline failed"})
        except Exception:
            pass


def _resample_pcm(data: bytes, src_rate: int, dst_rate: int, channels: int = 1) -> bytes:
    """Simple linear interpolation resampling for 16-bit PCM."""
    if src_rate == dst_rate:
        return data
    samples_per_frame = channels
    n_frames = len(data) // (2 * samples_per_frame)
    if n_frames == 0:
        return data

    # Decode to samples (mono for simplicity)
    if channels > 1:
        # Mix to mono first
        all_samples = struct.unpack(f"<{n_frames * channels}h", data[:n_frames * channels * 2])
        mono = []
        for i in range(0, len(all_samples), channels):
            mono.append(sum(all_samples[i:i+channels]) // channels)
    else:
        mono = list(struct.unpack(f"<{n_frames}h", data[:n_frames * 2]))

    # Resample via linear interpolation
    ratio = src_rate / dst_rate
    new_len = int(n_frames / ratio)
    resampled = []
    for i in range(new_len):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx
        if idx + 1 < len(mono):
            val = int(mono[idx] * (1 - frac) + mono[idx + 1] * frac)
        else:
            val = mono[min(idx, len(mono) - 1)]
        resampled.append(max(-32768, min(32767, val)))

    return struct.pack(f"<{len(resampled)}h", *resampled)


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
