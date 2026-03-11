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
import hashlib
import json
import logging
import os
import random
from collections import deque
from dataclasses import dataclass
from pathlib import Path

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

# Fingerprint of all phrases — changes when phrases are added/removed/edited
_PHRASES_HASH = hashlib.md5(
    json.dumps(CACHEABLE_FILLERS, sort_keys=True).encode()
).hexdigest()[:12]


def _cache_dir() -> Path:
    """Return the persistent filler cache directory."""
    data_dir = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
    d = data_dir / "tts_cache" / "fillers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(voice: str) -> str:
    """Cache key combining voice + phrase fingerprint."""
    return f"{voice}_{_PHRASES_HASH}"


@dataclass
class CachedFiller:
    """A pre-generated filler with PCM audio bytes."""

    phrase: str
    audio: bytes  # raw PCM
    sample_rate: int
    duration_ms: float  # audio length in milliseconds


class FillerCache:
    """In-memory cache of pre-generated filler audio.

    Persists to disk so fillers only need to be synthesized once per voice.
    On startup, loads from disk if the cache exists and the voice + phrases
    haven't changed. Only regenerates when the voice changes or phrases
    are edited.
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
        """Load filler cache from disk, or generate and persist if missing.

        Args:
            voice: TTS voice ID to use. Falls back to system default.
            force: If True, re-generate even if disk cache exists.
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

        from cortex.speech.voices import resolve_voice
        voice = voice or resolve_voice()
        key = _cache_key(voice)
        cache_file = _cache_dir() / f"{key}.json"

        # Try loading from disk first
        if not force and cache_file.exists():
            loaded = self._load_from_disk(cache_file)
            if loaded > 0:
                self._initialized = True
                self._initializing = False
                total_bytes = sum(
                    len(f.audio) for fillers in self._cache.values() for f in fillers
                )
                logger.info(
                    "Filler cache loaded from disk: %d phrases (%.1f MB) [voice=%s]",
                    loaded, total_bytes / 1024 / 1024, voice,
                )
                return

        # Generate all phrases and save to disk
        total = sum(len(v) for v in CACHEABLE_FILLERS.values())
        logger.info("Filler cache: generating %d phrases for voice=%s...", total, voice)

        generated = 0
        for sentiment, phrases in CACHEABLE_FILLERS.items():
            self._cache[sentiment] = []
            for phrase in phrases:
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
                except Exception as e:
                    logger.warning("Failed to cache filler %r: %s", phrase[:40], e)

        self._initialized = True
        self._initializing = False

        # Persist to disk
        self._save_to_disk(cache_file)

        # Clean up old cache files for different voices/phrases
        self._cleanup_old_caches(cache_file)

        total_bytes = sum(
            len(f.audio) for fillers in self._cache.values() for f in fillers
        )
        logger.info(
            "Filler cache ready: %d phrases cached (%.1f MB), saved to disk",
            generated, total_bytes / 1024 / 1024,
        )

    def _save_to_disk(self, path: Path) -> None:
        """Persist the cache to a JSON file with base64-encoded audio."""
        import base64
        data: dict[str, list[dict]] = {}
        for sentiment, fillers in self._cache.items():
            data[sentiment] = [
                {
                    "phrase": f.phrase,
                    "audio": base64.b64encode(f.audio).decode("ascii"),
                    "sample_rate": f.sample_rate,
                    "duration_ms": f.duration_ms,
                }
                for f in fillers
            ]
        try:
            path.write_text(json.dumps(data))
            logger.debug("Filler cache saved to %s", path)
        except Exception as e:
            logger.warning("Failed to save filler cache: %s", e)

    def _load_from_disk(self, path: Path) -> int:
        """Load cache from a JSON file. Returns number of phrases loaded."""
        import base64
        try:
            data = json.loads(path.read_text())
            loaded = 0
            for sentiment, entries in data.items():
                self._cache[sentiment] = []
                for entry in entries:
                    self._cache[sentiment].append(CachedFiller(
                        phrase=entry["phrase"],
                        audio=base64.b64decode(entry["audio"]),
                        sample_rate=entry["sample_rate"],
                        duration_ms=entry["duration_ms"],
                    ))
                    loaded += 1
            return loaded
        except Exception as e:
            logger.warning("Failed to load filler cache from disk: %s", e)
            return 0

    def _cleanup_old_caches(self, current: Path) -> None:
        """Remove cache files that don't match the current voice/phrases."""
        try:
            for f in current.parent.glob("*.json"):
                if f != current:
                    f.unlink()
                    logger.debug("Removed old filler cache: %s", f.name)
        except Exception:
            pass

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

    Delegates to the central speech service (Orpheus→Kokoro→Piper).
    Returns (pcm_bytes, sample_rate, provider_name).
    """
    from cortex.speech import synthesize_speech
    return await synthesize_speech(text, voice)
