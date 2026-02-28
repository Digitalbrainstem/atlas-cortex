"""Piper TTS provider — fast CPU fallback for Layer 1/2 instant answers (C11.2)."""

from __future__ import annotations

from typing import AsyncGenerator

from cortex.voice.base import TTSProvider

_PIPER_VOICES = [
    {"id": "piper_amy",    "provider": "piper", "name": "Amy",    "gender": "female", "style": "neutral", "language": "en"},
    {"id": "piper_jenny",  "provider": "piper", "name": "Jenny",  "gender": "female", "style": "neutral", "language": "en"},
    {"id": "piper_ryan",   "provider": "piper", "name": "Ryan",   "gender": "male",   "style": "neutral", "language": "en"},
]


class PiperTTSProvider(TTSProvider):
    """Piper TTS — runs on CPU, <100ms latency, minimal emotion support.

    Configuration keys:
      PIPER_URL  — Wyoming/HTTP endpoint (default: http://localhost:10200)
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.piper_url = cfg.get("PIPER_URL", "http://localhost:10200").rstrip("/")

    # ------------------------------------------------------------------
    # TTSProvider interface
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        emotion: str | None = None,
        speed: float = 1.0,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[bytes, None]:
        """Generate speech via Piper HTTP API, yielding WAV audio bytes."""
        # Strip SSML wrappers if Piper doesn't handle them gracefully
        plain = _strip_ssml(text)
        async for chunk in self._call_piper(plain, voice, speed):
            yield chunk

    async def list_voices(self) -> list[dict]:
        return list(_PIPER_VOICES)

    def supports_emotion(self) -> bool:
        return False  # Piper supports basic SSML only; no inline emotion tags

    def supports_streaming(self) -> bool:
        return True

    def get_emotion_format(self) -> str:
        return "ssml"

    # ------------------------------------------------------------------
    # Backend
    # ------------------------------------------------------------------

    async def _call_piper(
        self, text: str, voice: str | None, speed: float
    ) -> AsyncGenerator[bytes, None]:
        """POST text to the Piper HTTP server, stream WAV chunks."""
        import aiohttp

        url = f"{self.piper_url}/api/tts"
        params: dict = {"text": text}
        if voice:
            bare = voice.replace("piper_", "")
            params["voice"] = bare
        if speed != 1.0:
            params["lengthScale"] = str(round(1.0 / speed, 3))  # Piper uses length scale

        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params) as resp:
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        yield chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_ssml(text: str) -> str:
    """Remove SSML tags, leaving only the inner text."""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()
