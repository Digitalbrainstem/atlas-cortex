"""Pre-generated filler audio cache.

At server startup, all filler phrases are synthesized via TTS and the
resulting PCM audio is stored in memory.  During pipeline execution a
cached filler is selected and streamed to the satellite **instantly**
(zero TTS latency) instead of synthesizing on the fly.

Calibration (run at first install or when hardware changes):
  1.  Measure end-to-end pipeline latency: send 3-5 test queries and
      record the time from STT-complete to response-audio-ready.
  2.  Compute *average dead space* = avg total latency.
  3.  Target filler duration = dead_space × 0.50  (fills ~50 % of the
      wait, leaving natural breathing room before the real answer).
  4.  Generate filler phrases whose TTS audio length falls within
      [target × 0.70, target × 1.0] seconds.
  5.  Store calibration results in ``system_settings`` table:
      ``filler_target_seconds``, ``pipeline_avg_latency_ms``.

The nightly evolution job (``cortex.integrations.learning``) can
regenerate / expand the cache when new voices are added or latency
characteristics change.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Default phrases ──────────────────────────────────────────────
# Each is designed to take ~4-6 seconds when spoken naturally.
# Grouped by sentiment so the right tone is selected at runtime.

CACHEABLE_FILLERS: dict[str, list[str]] = {
    "question": [
        "Good question — let me think about that for just a moment.",
        "Hmm, let me look into that for you.",
        "Alright, let me pull that together for you real quick.",
        "Sure thing, let me see what I can find on that.",
        "That's a great question, give me just a second.",
        "Let me dig into that and see what comes up.",
        "Okay, one moment while I check on that for you.",
        "Hang on, let me find that out for you.",
        "Let me think on that — I want to give you a good answer.",
        "Bear with me for just a moment while I look that up.",
    ],
    "greeting": [
        "Hey there! Give me just a second.",
        "Hey! Let me see what I can help you with.",
    ],
    "frustrated": [
        "I hear you — let me look into that right away.",
        "Okay, let me see what I can do about that for you.",
    ],
    "excited": [
        "Oh, nice! Let me check on that for you.",
        "That sounds great — let me pull that up.",
    ],
    "late_night": [
        "Alright, let me take a look at that for you.",
        "Sure thing, one moment while I check.",
    ],
}


@dataclass
class CachedFiller:
    """A pre-generated filler with PCM audio bytes."""

    phrase: str
    audio: bytes  # raw PCM
    sample_rate: int
    duration_ms: float  # audio length in milliseconds


class FillerCache:
    """In-memory cache of pre-generated filler audio.

    Call ``await initialize()`` at startup to synthesize all phrases.
    Then ``get()`` returns a random cached filler instantly.
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[CachedFiller]] = {}
        self._recent: dict[str, deque[str]] = {}
        self._initialized = False
        self._initializing = False

    @property
    def ready(self) -> bool:
        return self._initialized

    async def initialize(self, voice: str | None = None, force: bool = False) -> None:
        """Pre-generate TTS audio for a subset of filler phrases.

        Only generates one phrase per sentiment category at startup to
        minimize GPU contention with user requests. Remaining phrases
        are generated lazily as needed.

        Args:
            voice: TTS voice ID to use. Falls back to system default.
            force: If True, re-generate even if already initialized.
        """
        if self._initializing:
            return
        if self._initialized and not force:
            return
        self._initializing = True
        if force:
            self._cache.clear()
            self._recent.clear()
            self._initialized = False

        # Only pre-generate ONE phrase per category to keep startup fast
        startup_phrases: list[tuple[str, str]] = []
        for sentiment, phrases in CACHEABLE_FILLERS.items():
            if phrases:
                startup_phrases.append((sentiment, phrases[0]))

        logger.info("Filler cache: pre-generating %d phrases (1 per category)...",
                     len(startup_phrases))

        from cortex.voice import stream_speech, resolve_default_voice

        tts_provider = os.environ.get("TTS_PROVIDER", "kokoro")
        voice = voice or resolve_default_voice()
        generated = 0

        for sentiment, phrase in startup_phrases:
            if sentiment not in self._cache:
                self._cache[sentiment] = []
            try:
                audio_bytes, sample_rate, _ = await _synthesize_for_cache(
                    phrase, voice)
                if audio_bytes:
                    duration_ms = len(audio_bytes) / (sample_rate * 2) * 1000
                    self._cache[sentiment].append(CachedFiller(
                        phrase=phrase,
                        audio=audio_bytes,
                        sample_rate=sample_rate,
                        duration_ms=duration_ms,
                    ))
                    generated += 1
                    logger.debug("Cached filler [%s] %.1fs: %r",
                                 sentiment, duration_ms / 1000, phrase[:50])
                # Small delay between generations to avoid hogging the GPU
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.warning("Failed to cache filler %r: %s", phrase[:40], e)

        self._initialized = True
        self._initializing = False
        total_bytes = sum(
            f.audio.__len__() for fillers in self._cache.values() for f in fillers
        )
        logger.info(
            "Filler cache ready: %d phrases cached (%.1f MB)",
            generated, total_bytes / 1024 / 1024,
        )

    def get(self, sentiment: str) -> CachedFiller | None:
        """Get a random cached filler for the given sentiment.

        Falls back to 'question' if sentiment has no cached entries.
        Avoids repeating the last 3 fillers per sentiment.
        """
        if not self._initialized:
            return None

        pool = self._cache.get(sentiment) or self._cache.get("question", [])
        if not pool:
            return None

        recent = self._recent.get(sentiment, deque(maxlen=3))
        candidates = [f for f in pool if f.phrase not in recent]
        if not candidates:
            candidates = pool

        choice = random.choice(candidates)
        if sentiment not in self._recent:
            self._recent[sentiment] = deque(maxlen=3)
        self._recent[sentiment].append(choice.phrase)
        return choice

    def reset(self) -> None:
        """Clear all cached fillers so ``initialize(force=True)`` can rebuild."""
        self._cache.clear()
        self._recent.clear()
        self._initialized = False
        self._initializing = False
        logger.info("Filler cache reset — ready for re-initialization")


# ── Module-level singleton ───────────────────────────────────────

_filler_cache = FillerCache()


def get_filler_cache() -> FillerCache:
    """Return the module-level filler cache singleton."""
    return _filler_cache


async def _synthesize_for_cache(
    text: str, voice: str,
) -> tuple[bytes, int, str]:
    """Synthesize text to PCM audio for caching.

    Uses Orpheus→Kokoro→Piper fallback chain.
    Returns (pcm_bytes, sample_rate, provider_name).
    """
    import os
    import struct
    import wave
    import io

    kokoro_host = os.environ.get("KOKORO_HOST", "localhost")
    kokoro_port = int(os.environ.get("KOKORO_PORT", "8880"))
    piper_host = os.environ.get("PIPER_HOST", "localhost")
    piper_port = int(os.environ.get("PIPER_PORT", "10200"))
    tts_provider = os.environ.get("TTS_PROVIDER", "orpheus")
    orpheus_url = os.environ.get("ORPHEUS_FASTAPI_URL", "http://localhost:5005")

    def _wav_to_pcm(raw: bytes, default_rate: int = 24000) -> tuple[bytes, int]:
        with io.BytesIO(raw) as buf:
            with wave.open(buf, "rb") as wf:
                rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
                if wf.getsampwidth() != 2:
                    pcm = b""
        return pcm, rate

    # --- Orpheus (GPU, primary) ---
    if tts_provider in ("orpheus", "auto"):
        try:
            import aiohttp
            bare_voice = (voice or "tara").replace("orpheus_", "")
            payload = {
                "input": text,
                "model": "orpheus",
                "voice": bare_voice,
                "response_format": "wav",
                "stream": False,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{orpheus_url}/v1/audio/speech",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if raw:
                            pcm, rate = _wav_to_pcm(raw, 24000)
                            return pcm, rate, "orpheus"
                    else:
                        error = await resp.text()
                        logger.warning("Orpheus TTS failed for cache (%d): %s", resp.status, error[:200])
        except Exception as e:
            logger.warning("Orpheus TTS failed for cache: %s", e)

    # --- Kokoro (CPU fallback) ---
    if tts_provider in ("kokoro", "auto"):
        try:
            from cortex.voice.kokoro import KokoroClient
            kokoro = KokoroClient(kokoro_host, kokoro_port, timeout=30.0)
            raw, info = await kokoro.synthesize(text, voice=voice, response_format="wav")
            if raw:
                pcm, rate = _wav_to_pcm(raw, info.get("rate", 24000))
                return pcm, rate, "kokoro"
        except Exception as e:
            logger.warning("Kokoro TTS failed for cache: %s", e)

    # --- Piper (last resort) ---
    try:
        from cortex.voice.wyoming import WyomingClient
        piper = WyomingClient(piper_host, piper_port, timeout=30.0)
        audio, info = await piper.synthesize(text)
        return audio, info.get("rate", 22050), "piper"
    except Exception as e:
        logger.warning("Piper TTS failed for cache: %s", e)

    return b"", 24000, "none"
