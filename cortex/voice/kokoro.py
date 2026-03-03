"""Kokoro TTS client — OpenAI-compatible API via Kokoro-FastAPI.

Kokoro-82M is a non-autoregressive TTS model that generates speech in
sub-second to low-second times on CPU, far faster than autoregressive
models like Orpheus for real-time voice assistant use.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class KokoroClient:
    """HTTP client for Kokoro-FastAPI server."""

    def __init__(self, host: str = "localhost", port: int = 8880, timeout: float = 30.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def synthesize(
        self,
        text: str,
        voice: str = "af_bella",
        response_format: str = "wav",
    ) -> tuple[bytes, dict]:
        """Synthesize text to audio via Kokoro-FastAPI.

        Returns (audio_bytes, info_dict) where info_dict contains
        sample rate and format metadata.
        """
        payload = {
            "model": "kokoro",
            "voice": voice,
            "input": text,
            "response_format": response_format,
        }

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise KokoroError(f"Kokoro API error {resp.status}: {body[:200]}")

                audio_data = await resp.read()

                # Kokoro outputs 24kHz audio by default
                info = {
                    "rate": 24000,
                    "format": response_format,
                    "channels": 1,
                    "sample_width": 2,
                }

                return audio_data, info

    async def list_voices(self) -> list[str]:
        """Get available voice names."""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(f"{self.base_url}/v1/audio/voices") as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("voices", [])

    async def health(self) -> bool:
        """Check if Kokoro server is responding."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{self.base_url}/v1/audio/voices") as resp:
                    return resp.status == 200
        except Exception:
            return False


class KokoroError(Exception):
    """Kokoro TTS error."""
