"""Backward-compat shim — jokes moved to cortex.content.jokes.

Import from cortex.content.jokes (or cortex.content) instead.
"""
from cortex.content.jokes import *  # noqa: F401, F403
from cortex.content.jokes import (
    Joke,
    init_joke_bank,
    get_random_joke,
    cache_tts,
    get_cached_audio,
    pre_generate_joke_audio,
    stream_cached_joke_to_avatar,
    _migrate_flat_cache,
    _JOKES,
)
