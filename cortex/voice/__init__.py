"""cortex.voice — Voice & Speech Engine (C11).

Public re-exports for convenient import.
"""

from __future__ import annotations

import logging
import os

from cortex.voice.base import TTSProvider
from cortex.voice.composer import EmotionComposer
from cortex.voice.identity import IdentifyResult, SpeakerIdentifier
from cortex.voice.providers import OrpheusTTSProvider, PiperTTSProvider, get_tts_provider
from cortex.voice.registry import (
    init_voice_registry,
    get_default_voice,
    list_voices,
)
from cortex.voice.streaming import extract_complete_sentence, stream_speech

_logger = logging.getLogger(__name__)


def resolve_default_voice(user_id: str = "") -> str:
    """Resolve the effective TTS voice: user pref → system default → env.

    Shared helper used by streaming, jokes, fillers, and avatar greeting.
    """
    # 1. Per-user preference
    if user_id:
        try:
            from cortex.db import get_db
            row = get_db().execute(
                "SELECT preferred_voice FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row and row["preferred_voice"]:
                return row["preferred_voice"]
        except Exception:
            pass

    # 2. System-wide default (admin setting)
    try:
        from cortex.db import get_db
        row = get_db().execute(
            "SELECT value FROM system_settings WHERE key = 'default_tts_voice'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]
    except Exception:
        pass

    # 3. Environment variable fallback
    return os.environ.get("KOKORO_VOICE", "af_bella")


__all__ = [
    "TTSProvider",
    "EmotionComposer",
    "IdentifyResult",
    "SpeakerIdentifier",
    "OrpheusTTSProvider",
    "PiperTTSProvider",
    "get_tts_provider",
    "init_voice_registry",
    "get_default_voice",
    "list_voices",
    "extract_complete_sentence",
    "stream_speech",
    "resolve_default_voice",
]
