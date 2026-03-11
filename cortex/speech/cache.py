"""Audio cache manager.

Manages cached TTS audio on disk under data/tts_cache/.
Used by filler cache, joke pre-generation, and on-demand caching.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("CORTEX_DATA_DIR", "data")


def cache_dir() -> Path:
    """Root directory for all cached TTS audio."""
    p = Path(_DATA_DIR) / "tts_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_key(text: str, voice: str) -> str:
    """Generate a cache key from text + voice."""
    h = hashlib.md5(f"{voice}:{text}".encode()).hexdigest()[:16]
    return f"{voice}_{h}"
