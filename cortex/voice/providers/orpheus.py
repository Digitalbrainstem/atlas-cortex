"""Orpheus TTS provider via Ollama or Orpheus-FastAPI (C11.2)."""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import aiohttp

from cortex.voice.base import TTSProvider

logger = logging.getLogger(__name__)

# Default Orpheus built-in voices (from voice-engine.md)
_ORPHEUS_VOICES = [
    {"id": "orpheus_tara",  "provider": "orpheus", "name": "Tara",  "gender": "female", "style": "warm"},
    {"id": "orpheus_leah",  "provider": "orpheus", "name": "Leah",  "gender": "female", "style": "energetic"},
    {"id": "orpheus_jess",  "provider": "orpheus", "name": "Jess",  "gender": "female", "style": "casual"},
    {"id": "orpheus_leo",   "provider": "orpheus", "name": "Leo",   "gender": "male",   "style": "professional"},
    {"id": "orpheus_dan",   "provider": "orpheus", "name": "Dan",   "gender": "male",   "style": "casual"},
    {"id": "orpheus_mia",   "provider": "orpheus", "name": "Mia",   "gender": "female", "style": "gentle"},
    {"id": "orpheus_zac",   "provider": "orpheus", "name": "Zac",   "gender": "male",   "style": "energetic"},
    {"id": "orpheus_zoe",   "provider": "orpheus", "name": "Zoe",   "gender": "female", "style": "professional"},
]


class OrpheusTTSProvider(TTSProvider):
    """Orpheus TTS via Ollama + SNAC decoding, or Orpheus-FastAPI.

    Configuration keys (from cortex.env / environment):
      ORPHEUS_URL          — Ollama base URL for voice GPU (default: http://localhost:11435)
      ORPHEUS_MODEL        — Ollama model tag  (default: legraphista/Orpheus:3b-ft-q8)
      ORPHEUS_FASTAPI_URL  — Orpheus-FastAPI URL (takes priority when set)
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.ollama_url = cfg.get("ORPHEUS_URL", "http://localhost:11435").rstrip("/")
        self.model = cfg.get("ORPHEUS_MODEL", "legraphista/Orpheus:3b-ft-q8")
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
        if self.fastapi_url:
            prompt = self._format_prompt(text, voice, emotion)
            async for chunk in self._synthesize_fastapi(prompt, voice, stream):
                yield chunk
        else:
            async for chunk in self._synthesize_ollama(text, voice, emotion):
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
            bare = voice.replace("orpheus_", "")
            parts.append(bare)
        if emotion:
            parts.append(emotion)
        prefix = ", ".join(parts)
        return f"{prefix}: {text}" if prefix else text

    # ------------------------------------------------------------------
    # Backend: Ollama + SNAC decoding
    # ------------------------------------------------------------------

    async def _synthesize_ollama(
        self, text: str, voice: str | None, emotion: str | None
    ) -> AsyncGenerator[bytes, None]:
        """Generate audio via Ollama's native generate API + local SNAC decoding.

        1. Format prompt with Orpheus special tokens (<|audio|>...<|eot_id|>)
        2. Call Ollama /api/generate with raw=true
        3. Collect <custom_token_*> from streamed response
        4. Decode SNAC tokens → 24kHz PCM audio
        """
        from cortex.voice.snac_decoder import extract_token_ids, decode_tokens, SAMPLE_RATE

        bare_voice = (voice or "tara").replace("orpheus_", "")
        inner_text = self._format_prompt(text, voice, emotion)
        # Orpheus requires special tokens to enter audio generation mode
        prompt = f"<|audio|>{inner_text}<|eot_id|>"

        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "raw": True,
            "stream": True,
            "options": {
                "temperature": 0.6,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "num_predict": 8192,
            },
        }

        full_response = ""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error("Ollama Orpheus request failed (%d): %s", resp.status, error_text[:200])
                        return

                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            token_text = obj.get("response", "")
                            if token_text:
                                full_response += token_text
                            if obj.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            logger.error("Ollama Orpheus connection failed: %s", e)
            return

        if not full_response:
            logger.warning("Ollama Orpheus returned empty response for: %s", text[:100])
            return

        # Extract and decode SNAC tokens
        token_ids = extract_token_ids(full_response)
        if not token_ids:
            logger.warning("No SNAC tokens found in Orpheus response (%d chars)", len(full_response))
            return

        logger.info("Orpheus generated %d SNAC tokens, decoding...", len(token_ids))
        audio_bytes = decode_tokens(token_ids)

        if audio_bytes:
            logger.info("Orpheus SNAC decoded: %d bytes of 24kHz PCM audio", len(audio_bytes))
            yield audio_bytes
        else:
            logger.warning("SNAC decoding produced no audio")

    # ------------------------------------------------------------------
    # Backend: Orpheus-FastAPI
    # ------------------------------------------------------------------

    async def _synthesize_fastapi(
        self, prompt: str, voice: str | None, stream: bool
    ) -> AsyncGenerator[bytes, None]:
        """Generate audio via Orpheus-FastAPI server.

        The server exposes POST /v1/audio/speech (OpenAI-compatible).
        """
        url = f"{self.fastapi_url}/v1/audio/speech"
        bare_voice = (voice or "tara").replace("orpheus_", "")
        payload = {
            "input": prompt,
            "model": "orpheus",
            "voice": bare_voice,
            "response_format": "wav",
            "stream": stream,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        yield chunk
