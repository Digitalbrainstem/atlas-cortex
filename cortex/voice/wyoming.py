"""Wyoming protocol client for STT / TTS services.

The Wyoming protocol uses line-delimited JSON over plain TCP.  Each event is
a JSON line optionally followed by ``data_length`` raw bytes.

See: https://github.com/rhasspy/wyoming
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class WyomingError(Exception):
    """Raised on protocol or connection errors."""


class WyomingClient:
    """Client for Wyoming-compatible STT/TTS services."""

    def __init__(self, host: str, port: int, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.read_timeout = max(timeout, 60.0)  # STT may need longer

    # ── STT ────────────────────────────────────────────────────────

    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Send audio to Wyoming STT and return transcription."""
        reader, writer = await self._connect()
        try:
            # audio-start
            await self._send_event(writer, "audio-start", {
                "rate": sample_rate, "width": 2, "channels": 1,
            })

            # Send audio in chunks with audio-chunk events
            chunk_size = 4096
            for offset in range(0, len(audio_data), chunk_size):
                chunk = audio_data[offset:offset + chunk_size]
                await self._send_event(writer, "audio-chunk", {
                    "rate": sample_rate, "width": 2, "channels": 1,
                }, payload=chunk)

            # audio-stop
            await self._send_event(writer, "audio-stop")

            # Wait for transcript
            evt_type, data, _ = await self._read_event(reader)
            if evt_type != "transcript":
                raise WyomingError(f"Expected transcript, got {evt_type}")
            return data.get("text", "")
        finally:
            writer.close()
            await writer.wait_closed()

    # ── TTS ────────────────────────────────────────────────────────

    async def synthesize(self, text: str, voice: str | None = None) -> tuple[bytes, dict]:
        """Send text to Wyoming TTS and return (pcm_audio, audio_info)."""
        reader, writer = await self._connect()
        try:
            data: dict[str, Any] = {"text": text}
            if voice:
                data["voice"] = {"name": voice}
            await self._send_event(writer, "synthesize", data)

            # Expect audio-start — format info in data dict
            evt_type, audio_info, _ = await self._read_event(reader)
            if evt_type != "audio-start":
                raise WyomingError(f"Expected audio-start, got {evt_type}")

            # Read audio-chunk events until audio-stop
            audio_chunks: list[bytes] = []
            while True:
                evt_type, _, payload = await self._read_event(reader)
                if evt_type == "audio-stop":
                    break
                if payload:
                    audio_chunks.append(payload)

            return b"".join(audio_chunks), audio_info
        finally:
            writer.close()
            await writer.wait_closed()

    async def synthesize_stream(self, text: str, voice: str | None = None) -> AsyncIterator[tuple[bytes, dict]]:
        """Stream TTS audio chunks as they arrive. Yields (chunk, audio_info)."""
        reader, writer = await self._connect()
        try:
            data: dict[str, Any] = {"text": text}
            if voice:
                data["voice"] = {"name": voice}
            await self._send_event(writer, "synthesize", data)

            evt_type, audio_info, _ = await self._read_event(reader)
            if evt_type != "audio-start":
                raise WyomingError(f"Expected audio-start, got {evt_type}")

            while True:
                evt_type, _, payload = await self._read_event(reader)
                if evt_type == "audio-stop":
                    break
                if evt_type == "audio-chunk" and payload:
                    yield payload, audio_info
        finally:
            writer.close()
            await writer.wait_closed()

    async def list_voices(self) -> list[dict]:
        """Query available TTS voices via describe event."""
        reader, writer = await self._connect()
        try:
            await self._send_event(writer, "describe")
            evt_type, data, payload = await self._read_event(reader)
            if evt_type == "info" and payload:
                info = json.loads(payload)
                return info.get("tts", [])
            return []
        finally:
            writer.close()
            await writer.wait_closed()

    async def health(self) -> bool:
        """Return True if the Wyoming service accepts connections."""
        try:
            reader, writer = await self._connect()
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    # ── Protocol helpers ───────────────────────────────────────────

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise WyomingError(f"Cannot connect to {self.host}:{self.port}: {exc}") from exc

    async def _send_event(
        self, writer: asyncio.StreamWriter,
        event_type: str, data: dict | None = None,
        payload: bytes | None = None,
    ) -> None:
        """Send a Wyoming event (JSON line + optional binary payload)."""
        msg: dict[str, Any] = {"type": event_type}
        data_bytes = b""
        if data:
            data_bytes = json.dumps(data).encode("utf-8")
            msg["data_length"] = len(data_bytes)
        if payload:
            msg["payload_length"] = len(payload)
        writer.write(json.dumps(msg).encode("utf-8") + b"\n")
        if data_bytes:
            writer.write(data_bytes)
        if payload:
            writer.write(payload)
        await writer.drain()

    async def _read_event(self, reader: asyncio.StreamReader) -> tuple[str, dict, bytes | None]:
        """Read a Wyoming event. Returns (type, data_dict, payload_bytes)."""
        line = await asyncio.wait_for(reader.readline(), timeout=self.read_timeout)
        if not line:
            raise WyomingError("Connection closed")
        header = json.loads(line.strip())
        evt_type = header.get("type", "")
        data = header.get("data", {})

        # Read data payload (JSON metadata like rate/width/channels)
        data_length = header.get("data_length", 0)
        if data_length > 0:
            meta_payload = await asyncio.wait_for(
                reader.readexactly(data_length), timeout=self.read_timeout
            )
            if not data:
                try:
                    data = json.loads(meta_payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

        # Read binary audio payload (separate from data_length)
        payload = None
        payload_length = header.get("payload_length", 0)
        if payload_length > 0:
            payload = await asyncio.wait_for(
                reader.readexactly(payload_length), timeout=self.read_timeout
            )

        return evt_type, data, payload
