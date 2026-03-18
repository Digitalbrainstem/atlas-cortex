"""WebSocket handler for ESP32 satellite devices.

ESP32 devices are simpler than Pi satellites:
- No on-device VAD (server handles silence detection)
- No phrase boundary detection (server handles it)
- No filler audio caching (server streams directly)
- Raw PCM audio in/out over base64-encoded JSON
- Lighter registration protocol

Protocol specification: satellite/esp32/PROTOCOL.md
"""

# Module ownership: ESP32 satellite WebSocket protocol

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from cortex.db import get_db

logger = logging.getLogger(__name__)


# ── LED color map ─────────────────────────────────────────────────

LED_COLORS: dict[str, str] = {
    "idle": "#0000ff",
    "listening": "#00ff00",
    "processing": "#ffff00",
    "speaking": "#00ffff",
    "error": "#ff0000",
}


class ESP32SatelliteHandler:
    """Handle WebSocket connection from an ESP32 satellite.

    The ESP32 protocol is simpler than the Pi protocol:
    - Registration uses ``register`` / ``registered`` (not ANNOUNCE / ACCEPTED)
    - Audio is ``audio_start`` / ``audio_data`` / ``audio_end``
    - TTS is ``speaking_start`` / ``audio_chunk`` / ``speaking_end``
    - LED state is pushed from the server (not controlled on-device)
    """

    def __init__(self, websocket: WebSocket, satellite_id: str) -> None:
        self.ws = websocket
        self.satellite_id = satellite_id
        self.device_type = "esp32"
        self.hardware = ""
        self.firmware_version = ""
        self._audio_buffer = bytearray()
        self._is_listening = False
        self._session_id: str | None = None
        self.last_heartbeat = time.time()

    async def handle_message(self, data: dict) -> None:
        """Route incoming messages from ESP32."""
        msg_type = data.get("type", "")

        if msg_type == "audio_start":
            await self._handle_audio_start()
        elif msg_type == "audio_data":
            await self._handle_audio_data(data)
        elif msg_type == "audio_end":
            await self._handle_audio_end()
        elif msg_type == "button":
            await self._handle_button(data)
        elif msg_type == "heartbeat":
            await self._handle_heartbeat(data)
        else:
            logger.warning(
                "Unknown message type from ESP32 %s: %s",
                self.satellite_id, msg_type,
            )

    # ── Registration ──────────────────────────────────────────────

    async def handle_register(self, data: dict) -> None:
        """Register ESP32 satellite — called once on connect."""
        self.hardware = data.get("hardware", "esp32")
        self.firmware_version = data.get("firmware_version", "")
        name = data.get("name", self.satellite_id)

        client_ip = self.ws.client.host if self.ws.client else None

        try:
            db = get_db()
            now = datetime.now(timezone.utc).isoformat()
            hw_json = json.dumps({
                "device_type": "esp32",
                "hardware": self.hardware,
                "firmware_version": self.firmware_version,
            })
            caps_json = json.dumps(["mic", "speaker"])

            db.execute(
                """INSERT INTO satellites
                       (id, display_name, status, last_seen, ip_address,
                        platform, hardware_info, capabilities)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = 'online',
                       last_seen = ?,
                       ip_address = COALESCE(?, ip_address),
                       platform = 'esp32',
                       hardware_info = ?,
                       capabilities = ?""",
                (
                    self.satellite_id, name, "online", now, client_ip,
                    "esp32", hw_json, caps_json,
                    now, client_ip, hw_json, caps_json,
                ),
            )
            db.commit()
        except Exception:
            logger.exception("Failed to register ESP32 satellite %s", self.satellite_id)

        await self.ws.send_json({
            "type": "registered",
            "satellite_id": self.satellite_id,
        })
        await self.send_led("idle")
        logger.info(
            "ESP32 satellite registered: %s (hw=%s)", self.satellite_id, self.hardware,
        )

    # ── Audio handling ────────────────────────────────────────────

    async def _handle_audio_start(self) -> None:
        """ESP32 started listening (wake word or button)."""
        self._audio_buffer = bytearray()
        self._is_listening = True

        self._session_id = f"audio-{uuid.uuid4().hex[:8]}"
        try:
            db = get_db()
            db.execute(
                "INSERT INTO satellite_audio_sessions (id, satellite_id) VALUES (?, ?)",
                (self._session_id, self.satellite_id),
            )
            db.commit()
        except Exception:
            logger.exception("Failed to create audio session for %s", self.satellite_id)

        await self.send_led("listening")

    async def _handle_audio_data(self, data: dict) -> None:
        """Receive raw PCM audio chunk from ESP32."""
        audio_b64 = data.get("data", "")
        if audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64)
                self._audio_buffer.extend(audio_bytes)
            except Exception:
                logger.warning("Invalid base64 audio from %s", self.satellite_id)

    async def _handle_audio_end(self) -> None:
        """ESP32 stopped sending audio — process the buffered audio."""
        self._is_listening = False
        await self.send_led("processing")

        audio_data = bytes(self._audio_buffer)
        self._audio_buffer = bytearray()

        if self._session_id:
            try:
                db = get_db()
                db.execute(
                    "UPDATE satellite_audio_sessions SET ended_at = ?, audio_length_ms = ? WHERE id = ?",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        len(audio_data) * 1000 // 32000,  # 16kHz 16-bit = 32000 bytes/sec
                        self._session_id,
                    ),
                )
                db.commit()
            except Exception:
                pass

        # Cap audio at ~15 seconds to prevent Whisper hallucination
        max_audio_bytes = 480000  # 15s at 16kHz 16-bit mono
        if len(audio_data) > max_audio_bytes:
            logger.warning(
                "Audio from ESP32 %s too long (%d bytes), truncating",
                self.satellite_id, len(audio_data),
            )
            audio_data = audio_data[-max_audio_bytes:]

        if len(audio_data) < 1600:
            logger.debug(
                "Audio from ESP32 %s too short (%d bytes), discarding",
                self.satellite_id, len(audio_data),
            )
            await self.send_led("idle")
            return

        await self._process_audio(audio_data)

    async def _process_audio(self, audio_data: bytes) -> None:
        """Run audio through STT → pipeline → TTS and stream back.

        TODO: Wire to actual STT/pipeline/TTS once the orchestrator
        supports ESP32 satellites. The flow is:
          1. Send audio_data to Whisper STT → get transcription
          2. Run transcription through run_pipeline() → get response tokens
          3. Collect response text, generate TTS audio
          4. Stream TTS chunks back via send_audio()

        For now this is a stub that logs the audio size and sends idle LED.
        """
        logger.info(
            "ESP32 %s: processing %d bytes of audio (%.1fs)",
            self.satellite_id,
            len(audio_data),
            len(audio_data) / 32000,
        )

        # TODO: Integrate with cortex.orchestrator.voice.process_voice_pipeline
        # or a simplified version for ESP32 (no phrase queue, no barge-in).
        #
        # Rough outline:
        #   from cortex.orchestrator.voice import process_voice_pipeline
        #   # Build a lightweight connection adapter that wraps this handler
        #   # to match the SatelliteConnection interface expected by the pipeline.
        #   await process_voice_pipeline(adapter, audio_data)

        await self.send_led("idle")

    # ── Button handling ───────────────────────────────────────────

    async def _handle_button(self, data: dict) -> None:
        """Handle physical button event from ESP32."""
        action = data.get("action", "press")
        logger.info("ESP32 %s button: %s", self.satellite_id, action)

        if action == "press":
            if self._is_listening:
                await self._handle_audio_end()
            else:
                await self._handle_audio_start()

    # ── Heartbeat ─────────────────────────────────────────────────

    async def _handle_heartbeat(self, data: dict) -> None:
        """Update satellite from heartbeat."""
        self.last_heartbeat = time.time()
        try:
            db = get_db()
            db.execute(
                """UPDATE satellites
                   SET last_seen = ?, uptime_seconds = ?, wifi_rssi = ?
                   WHERE id = ?""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    data.get("uptime"),
                    data.get("wifi_rssi"),
                    self.satellite_id,
                ),
            )
            db.commit()
        except Exception:
            logger.exception("Failed to update heartbeat for ESP32 %s", self.satellite_id)

    # ── Outbound messages ─────────────────────────────────────────

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send audio chunk to ESP32 for playback."""
        await self.ws.send_json({
            "type": "audio_chunk",
            "data": base64.b64encode(pcm_data).decode(),
        })

    async def send_speaking_start(self) -> None:
        """Signal that TTS audio is about to be streamed."""
        await self.send_led("speaking")
        await self.ws.send_json({"type": "speaking_start"})

    async def send_speaking_end(self) -> None:
        """Signal that TTS streaming is complete."""
        await self.ws.send_json({"type": "speaking_end"})
        await self.send_led("idle")

    async def send_led(self, pattern: str) -> None:
        """Send LED state change to ESP32."""
        await self.ws.send_json({
            "type": "led",
            "pattern": pattern,
            "color": LED_COLORS.get(pattern, "#ffffff"),
        })

    async def send_playback_stop(self) -> None:
        """Tell ESP32 to stop playing audio."""
        await self.ws.send_json({"type": "playback_stop"})
        await self.send_led("idle")

    # ── Cleanup ───────────────────────────────────────────────────

    async def on_disconnect(self) -> None:
        """Clean up when ESP32 disconnects."""
        try:
            db = get_db()
            db.execute(
                "UPDATE satellites SET status = 'offline' WHERE id = ?",
                (self.satellite_id,),
            )
            db.commit()
        except Exception:
            logger.exception("Failed to update disconnect for ESP32 %s", self.satellite_id)
        logger.info("ESP32 satellite disconnected: %s", self.satellite_id)
