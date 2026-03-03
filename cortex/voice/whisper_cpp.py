"""whisper.cpp HTTP client for speech-to-text.

Connects to whisper.cpp's built-in HTTP server at ``/inference``.
Sends raw PCM audio wrapped in a WAV container and returns the
transcribed text.
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class WhisperCppError(Exception):
    """Raised on connection or transcription errors."""


class WhisperCppClient:
    """Client for whisper.cpp HTTP server."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 10300,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def transcribe(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
        language: str = "en",
    ) -> str:
        """Transcribe raw 16-bit mono PCM audio and return text."""
        wav_bytes = _pcm_to_wav(audio_data, sample_rate=sample_rate)

        form = aiohttp.FormData()
        form.add_field(
            "file",
            wav_bytes,
            filename="audio.wav",
            content_type="audio/wav",
        )
        form.add_field("temperature", "0.0")
        form.add_field("temperature_inc", "0.2")
        form.add_field("response_format", "json")
        if language:
            form.add_field("language", language)

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/inference", data=form
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise WhisperCppError(
                            f"whisper.cpp returned {resp.status}: {body}"
                        )
                    result = await resp.json(content_type=None)
                    return result.get("text", "").strip()
        except aiohttp.ClientError as exc:
            raise WhisperCppError(f"Cannot reach whisper.cpp at {self.base_url}: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise WhisperCppError(f"Timeout connecting to whisper.cpp at {self.base_url}") from exc

    async def health(self) -> bool:
        """Return True if the whisper.cpp server is reachable."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(self.base_url) as resp:
                    return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False


def _pcm_to_wav(
    pcm: bytes,
    sample_rate: int = 16000,
    sample_width: int = 2,
    channels: int = 1,
) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
