"""Orpheus TTS provider via Ollama or Orpheus-FastAPI (C11.2)."""

from __future__ import annotations

import json
from typing import AsyncGenerator

import aiohttp

from cortex.voice.base import TTSProvider

# Default Orpheus built-in voices (from voice-engine.md)
_ORPHEUS_VOICES = [
    {"id": "orpheus_tara",  "provider": "orpheus", "name": "Tara",  "gender": "female", "style": "warm"},
    {"id": "orpheus_leah",  "provider": "orpheus", "name": "Leah",  "gender": "female", "style": "energetic"},
    {"id": "orpheus_jess",  "provider": "orpheus", "name": "Jess",  "gender": "female", "style": "casual"},
    {"id": "orpheus_leo",   "provider": "orpheus", "name": "Leo",   "gender": "male",   "style": "professional"},
    {"id": "orpheus_dan",   "provider": "orpheus", "name": "Dan",   "gender": "male",   "style": "casual"},
    {"id": "orpheus_mia",   "provider": "orpheus", "name": "Mia",   "gender": "female", "style": "gentle"},
    {"id": "orpheus_zac",   "provider": "orpheus", "name": "Zac",   "gender": "male",   "style": "energetic"},
    {"id": "orpheus_anna",  "provider": "orpheus", "name": "Anna",  "gender": "female", "style": "professional"},
]


class OrpheusTTSProvider(TTSProvider):
    """Orpheus TTS via Ollama or Orpheus-FastAPI.

    Configuration keys (from cortex.env / environment):
      ORPHEUS_URL          — Ollama base URL (default: http://localhost:11434)
      ORPHEUS_MODEL        — Ollama model tag  (default: legraphista/Orpheus:latest)
      ORPHEUS_FASTAPI_URL  — Orpheus-FastAPI URL (takes priority when set)
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.ollama_url = cfg.get("ORPHEUS_URL", "http://localhost:11434").rstrip("/")
        self.model = cfg.get("ORPHEUS_MODEL", "legraphista/Orpheus:latest")
        self.fastapi_url = (cfg.get("ORPHEUS_FASTAPI_URL") or "").rstrip("/") or None

    # ------------------------------------------------------------------
    # TTSProvider interface
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = "tara",
        emotion: str | None = None,
        speed: float = 1.0,
        stream: bool = True,
        **kwargs,
    ) -> AsyncGenerator[bytes, None]:
        """Generate speech, yielding raw audio bytes."""
        prompt = self._format_prompt(text, voice, emotion)
        if self.fastapi_url:
            async for chunk in self._synthesize_fastapi(prompt, stream):
                yield chunk
        else:
            async for chunk in self._synthesize_ollama(prompt, stream):
                yield chunk

    async def list_voices(self) -> list[dict]:
        return list(_ORPHEUS_VOICES)

    def supports_emotion(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True

    def get_emotion_format(self) -> str:
        return "tags"

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    def _format_prompt(self, text: str, voice: str | None, emotion: str | None) -> str:
        """Format text as 'voice, emotion: text' per Orpheus convention."""
        parts = []
        if voice:
            # Strip 'orpheus_' prefix if present (DB uses full ID, model uses bare name)
            bare = voice.replace("orpheus_", "")
            parts.append(bare)
        if emotion:
            parts.append(emotion)
        prefix = ", ".join(parts)
        return f"{prefix}: {text}" if prefix else text

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    async def _synthesize_ollama(self, prompt: str, stream: bool) -> AsyncGenerator[bytes, None]:
        """Generate audio via Ollama's generate API.

        Ollama returns base64-encoded audio chunks in the 'response' field when
        the Orpheus GGUF model is loaded.  Each streaming line is a JSON object.
        """
        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
        }
        import base64
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                async for raw_line in resp.content:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    audio_b64 = obj.get("response", "")
                    if audio_b64:
                        yield base64.b64decode(audio_b64)

    async def _synthesize_fastapi(self, prompt: str, stream: bool) -> AsyncGenerator[bytes, None]:
        """Generate audio via Orpheus-FastAPI server.

        The server exposes POST /v1/audio/speech (OpenAI-compatible).
        """
        url = f"{self.fastapi_url}/v1/audio/speech"
        payload = {
            "input": prompt,
            "model": "orpheus",
            "response_format": "wav",
            "stream": stream,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        yield chunk
