"""Wyoming protocol client for STT / TTS services (Phase I3.1).

The Wyoming protocol uses line-delimited JSON over plain TCP.  Audio data is
sent as raw bytes between ``audio-start`` and ``audio-stop`` JSON frames.

See: https://github.com/rhasspy/wyoming
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


class WyomingError(Exception):
    """Raised on protocol or connection errors."""


class WyomingClient:
    """Client for Wyoming-compatible STT/TTS services.

    Parameters
    ----------
    host:
        Hostname or IP of the Wyoming service.
    port:
        TCP port of the Wyoming service.
    timeout:
        Seconds to wait for responses (default 10).
    """

    def __init__(self, host: str, port: int, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Send audio to a Wyoming STT service and return the transcription.

        Parameters
        ----------
        audio_data:
            Raw PCM audio bytes (16-bit signed LE, mono).
        sample_rate:
            Sample rate in Hz (default 16 000).

        Returns
        -------
        str
            The transcribed text.
        """
        reader, writer = await self._connect()
        try:
            # audio-start
            await self._send_json(writer, {
                "type": "audio-start",
                "data": {"rate": sample_rate, "width": 2, "channels": 1},
            })

            # Send raw audio in chunks
            chunk_size = 4096
            for offset in range(0, len(audio_data), chunk_size):
                writer.write(audio_data[offset:offset + chunk_size])
                await writer.drain()

            # audio-stop
            await self._send_json(writer, {"type": "audio-stop"})

            # Wait for transcript
            response = await self._read_json(reader)
            if response.get("type") != "transcript":
                raise WyomingError(f"Expected transcript, got {response.get('type')}")

            return response.get("data", {}).get("text", "")
        finally:
            writer.close()
            await writer.wait_closed()

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Send text to a Wyoming TTS service and return audio bytes.

        Parameters
        ----------
        text:
            The text to synthesize.
        voice:
            Optional voice name.

        Returns
        -------
        bytes
            Raw PCM audio.
        """
        reader, writer = await self._connect()
        try:
            payload: dict[str, Any] = {"type": "synthesize", "data": {"text": text}}
            if voice:
                payload["data"]["voice"] = {"name": voice}
            await self._send_json(writer, payload)

            # Expect audio-start
            header = await self._read_json(reader)
            if header.get("type") != "audio-start":
                raise WyomingError(f"Expected audio-start, got {header.get('type')}")

            # Read until audio-stop
            audio_chunks: list[bytes] = []
            while True:
                line = await self._read_line(reader)
                if not line:
                    break
                line_stripped = line.strip()
                try:
                    msg = json.loads(line_stripped)
                    if msg.get("type") == "audio-stop":
                        break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Raw audio bytes
                    audio_chunks.append(line)

            return b"".join(audio_chunks)
        finally:
            writer.close()
            await writer.wait_closed()

    async def health(self) -> bool:
        """Return ``True`` if the Wyoming service accepts TCP connections."""
        try:
            reader, writer = await self._connect()
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a TCP connection with timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise WyomingError(f"Cannot connect to {self.host}:{self.port}: {exc}") from exc

    async def _send_json(self, writer: asyncio.StreamWriter, obj: dict) -> None:
        """Write a JSON line to the stream."""
        writer.write(json.dumps(obj).encode("utf-8") + b"\n")
        await writer.drain()

    async def _read_line(self, reader: asyncio.StreamReader) -> bytes:
        """Read a single line from the stream with timeout."""
        try:
            return await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise WyomingError("Timed out waiting for response") from exc

    async def _read_json(self, reader: asyncio.StreamReader) -> dict:
        """Read and parse the next JSON line from the stream."""
        line = await self._read_line(reader)
        if not line:
            raise WyomingError("Connection closed before response")
        try:
            return json.loads(line.strip())
        except json.JSONDecodeError as exc:
            raise WyomingError(f"Invalid JSON from Wyoming: {line!r}") from exc
