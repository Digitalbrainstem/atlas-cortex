"""Voice resolution and mapping.

Single source of truth for resolving which voice to use for TTS.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Valid Orpheus voice names
ORPHEUS_VOICES = {"tara", "leah", "jess", "leo", "dan", "mia", "zac", "nova"}

# Kokoro TTS defaults
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_bella")


def to_orpheus_voice(voice: str) -> str:
    """Map any voice name to a valid Orpheus voice.

    Kokoro voices (af_bella, af_heart, etc.) get mapped to the default.
    Orpheus-prefixed names get the prefix stripped.
    """
    bare = (voice or "tara").replace("orpheus_", "")
    return bare if bare in ORPHEUS_VOICES else "tara"


def resolve_voice(user_id: str = "", satellite_id: str = "") -> str:
    """Resolve the effective TTS voice: satellite → user pref → system default → env.

    Priority:
    1. Satellite-specific voice (if satellite_id provided)
    2. User preference (if user_id provided)
    3. System-wide default (admin setting)
    4. KOKORO_VOICE env var fallback
    """
    # 1. Satellite-specific voice
    if satellite_id:
        try:
            from cortex.db import get_db
            row = get_db().execute(
                "SELECT tts_voice FROM satellites WHERE id = ?", (satellite_id,)
            ).fetchone()
            if row and row["tts_voice"]:
                return row["tts_voice"]
        except Exception:
            pass

    # 2. Per-user preference
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

    # 3. System-wide default (admin setting)
    try:
        from cortex.db import get_db
        row = get_db().execute(
            "SELECT value FROM system_settings WHERE key = 'default_tts_voice'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]
    except Exception:
        pass

    # 4. Environment variable fallback
    return KOKORO_VOICE
