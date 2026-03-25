"""Qwen3-TTS provider — neosun/qwen3-tts:2.0.0 Docker server (port 8766).

Qwen3-TTS is a high-quality multilingual TTS engine with 9 built-in speakers,
voice design from text descriptions, and voice cloning from reference audio.
Output is WAV (or streaming PCM via /stream endpoints).

Three synthesis modes:
  - Custom Voice: 9 preset speakers with instruction control
  - Voice Design: create new voices from text descriptions
  - Voice Clone: clone a voice from 3-second reference audio

Config env vars:
    QWEN_TTS_HOST  — server hostname (default: localhost)
    QWEN_TTS_PORT  — server port (default: 8766)
"""

from __future__ import annotations

import io
import logging
import os
import wave
from typing import AsyncGenerator

import aiohttp

from cortex.voice.base import TTSProvider

logger = logging.getLogger(__name__)

# Built-in speakers from the neosun/qwen3-tts server
_QWEN3_VOICES = [
    {"id": "qwen3_Ryan",      "provider": "qwen3_tts", "name": "Ryan",      "gender": "male",   "language": "en", "style": "dynamic"},
    {"id": "qwen3_Aiden",     "provider": "qwen3_tts", "name": "Aiden",     "gender": "male",   "language": "en", "style": "sunny"},
    {"id": "qwen3_Vivian",    "provider": "qwen3_tts", "name": "Vivian",    "gender": "female", "language": "zh", "style": "bright"},
    {"id": "qwen3_Serena",    "provider": "qwen3_tts", "name": "Serena",    "gender": "female", "language": "zh", "style": "warm"},
    {"id": "qwen3_Uncle_Fu",  "provider": "qwen3_tts", "name": "Uncle Fu",  "gender": "male",   "language": "zh", "style": "mellow"},
    {"id": "qwen3_Dylan",     "provider": "qwen3_tts", "name": "Dylan",     "gender": "male",   "language": "zh", "style": "natural"},
    {"id": "qwen3_Eric",      "provider": "qwen3_tts", "name": "Eric",      "gender": "male",   "language": "zh", "style": "lively"},
    {"id": "qwen3_Ono_Anna",  "provider": "qwen3_tts", "name": "Ono Anna",  "gender": "female", "language": "ja", "style": "playful"},
    {"id": "qwen3_Sohee",     "provider": "qwen3_tts", "name": "Sohee",     "gender": "female", "language": "ko", "style": "warm"},
]

# Map language codes to Qwen3-TTS language names
_LANG_MAP = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese",
    "es": "Spanish", "it": "Italian",
}


class Qwen3TTSProvider(TTSProvider):
    """Qwen3-TTS via neosun/qwen3-tts Docker server.

    Configuration keys (from environment):
      QWEN_TTS_HOST  — server hostname (default: localhost)
      QWEN_TTS_PORT  — server port (default: 8766)
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = config or {}
        self.host = cfg.get("QWEN_TTS_HOST", os.environ.get("QWEN_TTS_HOST", "localhost"))
        self.port = int(cfg.get("QWEN_TTS_PORT", os.environ.get("QWEN_TTS_PORT", "8766")))
        self.base_url = f"http://{self.host}:{self.port}"
        self.default_speaker = "Ryan"
        self.default_language = "English"

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
        """Generate speech via Qwen3-TTS custom-voice endpoint.

        Yields raw PCM bytes extracted from the returned WAV.
        """
        speaker = self._resolve_speaker(voice)
        language = kwargs.get("language") or self._detect_language(speaker)
        instruct = emotion or kwargs.get("instruct", "")

        if stream:
            async for chunk in self._synthesize_stream(text, speaker, language, instruct):
                yield chunk
        else:
            audio = await self._synthesize_wav(text, speaker, language, instruct)
            if audio:
                yield audio

    async def list_voices(self) -> list[dict]:
        """Return available voices — tries the server first, falls back to hardcoded."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self.base_url}/api/speakers") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        voices = []
                        for s in data if isinstance(data, list) else data.get("speakers", []):
                            name = s if isinstance(s, str) else s.get("name", "")
                            if name:
                                voices.append({
                                    "id": f"qwen3_{name}",
                                    "name": name,
                                    "provider": "qwen3_tts",
                                    "gender": self._guess_gender(name),
                                    "language": self._detect_language(name),
                                    "style": "natural",
                                })
                        if voices:
                            return voices
        except Exception as exc:
            logger.debug("Failed to query Qwen3-TTS speakers: %s", exc)

        return list(_QWEN3_VOICES)

    def supports_emotion(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return True

    def supports_phonemes(self) -> bool:
        return False

    def get_emotion_format(self) -> str | None:
        return "description"

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Check if Qwen3-TTS server is reachable."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self.base_url}/health") as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal: WAV synthesis (full audio in one shot)
    # ------------------------------------------------------------------

    async def _synthesize_wav(
        self, text: str, speaker: str, language: str, instruct: str,
    ) -> bytes:
        """Call /api/tts/custom-voice and return raw PCM bytes."""
        url = f"{self.base_url}/api/tts/custom-voice"
        payload = {
            "text": text,
            "language": language,
            "speaker": speaker,
        }
        if instruct:
            payload["instruct"] = instruct

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.warning(
                            "Qwen3-TTS synthesis failed (%d): %s",
                            resp.status, error[:200],
                        )
                        return b""
                    wav_data = await resp.read()
                    return self._wav_to_pcm(wav_data)
        except Exception as exc:
            logger.warning("Qwen3-TTS synthesis error: %s", exc)
            return b""

    # ------------------------------------------------------------------
    # Internal: PCM streaming
    # ------------------------------------------------------------------

    async def _synthesize_stream(
        self, text: str, speaker: str, language: str, instruct: str,
    ) -> AsyncGenerator[bytes, None]:
        """Call /api/tts/custom-voice/stream and yield PCM chunks."""
        url = f"{self.base_url}/api/tts/custom-voice/stream"
        payload = {
            "text": text,
            "language": language,
            "speaker": speaker,
        }
        if instruct:
            payload["instruct"] = instruct

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.warning(
                            "Qwen3-TTS stream failed (%d): %s",
                            resp.status, error[:200],
                        )
                        # Fall back to non-streaming
                        audio = await self._synthesize_wav(text, speaker, language, instruct)
                        if audio:
                            yield audio
                        return

                    async for chunk in resp.content.iter_chunked(4096):
                        if chunk:
                            yield chunk
        except Exception as exc:
            logger.warning("Qwen3-TTS streaming error: %s", exc)

    # ------------------------------------------------------------------
    # Voice Design synthesis (create a new voice from description)
    # ------------------------------------------------------------------

    async def synthesize_voice_design(
        self, text: str, language: str = "English", instruct: str = "",
    ) -> bytes:
        """Generate speech using voice design (no preset speaker).

        The *instruct* parameter describes the desired voice, e.g.
        ``"Young female voice, cheerful and bright"``.
        """
        url = f"{self.base_url}/api/tts/voice-design"
        payload = {"text": text, "language": language, "instruct": instruct}

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning("Qwen3-TTS voice-design failed (%d)", resp.status)
                        return b""
                    wav_data = await resp.read()
                    return self._wav_to_pcm(wav_data)
        except Exception as exc:
            logger.warning("Qwen3-TTS voice-design error: %s", exc)
            return b""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_speaker(self, voice: str | None) -> str:
        """Map a voice ID to a Qwen3-TTS speaker name."""
        if not voice:
            return self.default_speaker
        # Strip our prefix: "qwen3_Ryan" → "Ryan"
        bare = voice.replace("qwen3_", "")
        # Check against known speakers (case-insensitive)
        known = {v["name"].lower(): v["name"] for v in _QWEN3_VOICES}
        return known.get(bare.lower(), bare)

    @staticmethod
    def _detect_language(speaker: str) -> str:
        """Guess language from speaker name."""
        speaker_langs = {
            "ryan": "English", "aiden": "English",
            "vivian": "Chinese", "serena": "Chinese",
            "uncle_fu": "Chinese", "uncle fu": "Chinese",
            "dylan": "Chinese", "eric": "Chinese",
            "ono_anna": "Japanese", "ono anna": "Japanese",
            "sohee": "Korean",
        }
        return speaker_langs.get(speaker.lower(), "English")

    @staticmethod
    def _guess_gender(name: str) -> str:
        """Best-effort gender guess from speaker name."""
        female = {"vivian", "serena", "ono_anna", "ono anna", "sohee"}
        return "female" if name.lower() in female else "male"

    @staticmethod
    def _wav_to_pcm(wav_data: bytes) -> bytes:
        """Extract raw PCM frames from WAV data."""
        if not wav_data:
            return b""
        if wav_data[:4] == b"RIFF":
            try:
                with wave.open(io.BytesIO(wav_data), "rb") as wf:
                    return wf.readframes(wf.getnframes())
            except Exception:
                pass
        return wav_data
