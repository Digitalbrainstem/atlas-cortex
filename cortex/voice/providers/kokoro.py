"""Kokoro TTS provider — wraps KokoroClient as a TTSProvider (C11).

Kokoro-82M is the primary TTS engine for Atlas Cortex. It runs on CPU
with sub-2s synthesis for typical sentences (200ms base + 180ms/word).
Output is 24kHz 16-bit mono PCM.

Config env vars:
    KOKORO_HOST  — server hostname (default: localhost)
    KOKORO_PORT  — server port (default: 8880)
    KOKORO_VOICE — default voice (default: af_bella)
"""

from __future__ import annotations

import os

from cortex.voice.base import TTSProvider
from cortex.voice.kokoro import KokoroClient


class KokoroTTSProvider(TTSProvider):
    """Kokoro TTS via Kokoro-FastAPI (OpenAI-compatible API)."""

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = config or {}
        self.host = cfg.get("KOKORO_HOST", os.environ.get("KOKORO_HOST", "localhost"))
        self.port = int(cfg.get("KOKORO_PORT", os.environ.get("KOKORO_PORT", "8880")))
        self.default_voice = cfg.get(
            "KOKORO_VOICE", os.environ.get("KOKORO_VOICE", "af_bella")
        )
        self._client = KokoroClient(self.host, self.port)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        emotion: str | None = None,
        speed: float = 1.0,
        stream: bool = True,
        **kwargs,
    ):
        """Generate speech via Kokoro.

        Yields raw PCM bytes (24kHz 16-bit mono) as a single chunk.
        Kokoro-FastAPI does not support chunked streaming, so the full
        audio is returned in one yield.
        """
        voice = voice or self.default_voice
        audio_bytes, info = await self._client.synthesize(
            text, voice=voice, response_format="pcm", speed=speed,
        )
        yield audio_bytes

    async def list_voices(self) -> list[dict]:
        """Return available Kokoro voices."""
        voice_ids = await self._client.list_voices()
        return [
            {
                "id": v,
                "name": v,
                "provider": "kokoro",
                "gender": "female" if v.startswith(("af_", "bf_", "ef_", "ff_", "gf_", "hf_", "if_", "jf_", "kf_")) else "male",
                "language": "en",
                "style": "natural",
            }
            for v in voice_ids
        ]

    def supports_emotion(self) -> bool:
        return False

    def supports_streaming(self) -> bool:
        return False

    def supports_phonemes(self) -> bool:
        return False

    def get_emotion_format(self) -> str | None:
        return None
