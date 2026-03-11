"""Content modules — pre-generated media and entertainment.

Sub-modules:
  jokes — Kid-friendly joke bank with rotation + TTS caching
"""
from __future__ import annotations

from cortex.content.jokes import (
    Joke,
    init_joke_bank,
    get_random_joke,
    cache_tts,
    get_cached_audio,
    pre_generate_joke_audio,
    stream_cached_joke_to_avatar,
)

__all__ = [
    "Joke",
    "init_joke_bank",
    "get_random_joke",
    "cache_tts",
    "get_cached_audio",
    "pre_generate_joke_audio",
    "stream_cached_joke_to_avatar",
]
