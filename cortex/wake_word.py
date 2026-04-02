"""Server-side wake word detection on STT transcripts.

This module provides transcript-level wake word filtering for the main
server.  It checks whether a transcribed utterance contains a configured
wake word (e.g. "atlas", "hey atlas") and strips it from the text so the
pipeline receives a clean command.

Two modes:
  * **required** (default) — input is dropped when no wake word is found.
  * **optional** — wake word is stripped if present but input is passed
    through either way.

Environment variables:
  WAKE_WORD_ENABLED   "1" (default) to require wake word, "0" to disable.
  WAKE_WORD_KEYWORDS  Comma-separated keywords (default "atlas,atmos,alice").
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────

_WAKE_WORD_ENABLED: bool = os.environ.get("WAKE_WORD_ENABLED", "1") == "1"

_DEFAULT_KEYWORDS = "atlas,atmos,alice"
_WAKE_KEYWORDS: set[str] = {
    kw.strip().lower()
    for kw in os.environ.get("WAKE_WORD_KEYWORDS", _DEFAULT_KEYWORDS).split(",")
    if kw.strip()
}

_CLEAN_PREFIXES = {"hey", "ok", "okay", "hi", "yo", ""}


def is_enabled() -> bool:
    """Return True when server-side wake word filtering is active."""
    return _WAKE_WORD_ENABLED


def get_keywords() -> set[str]:
    """Return the current set of wake word keywords."""
    return set(_WAKE_KEYWORDS)


def set_enabled(enabled: bool) -> None:
    """Toggle wake word filtering at runtime (e.g. from admin API)."""
    global _WAKE_WORD_ENABLED
    _WAKE_WORD_ENABLED = enabled


def set_keywords(keywords: set[str]) -> None:
    """Replace the wake word keyword set at runtime."""
    global _WAKE_KEYWORDS
    _WAKE_KEYWORDS = {kw.lower().strip() for kw in keywords if kw.strip()}


# ── Core logic ────────────────────────────────────────────────────

def check_wake_word(transcript: str) -> tuple[bool, str]:
    """Check a transcript for wake word presence and strip it.

    Returns
    -------
    (found, cleaned_transcript)
        *found* is True when a wake keyword was detected.
        *cleaned_transcript* has the wake word (and common filler prefix
        like "hey") removed.  If no keyword was found the original
        transcript is returned unchanged.
    """
    if not transcript:
        return False, ""

    transcript_lower = transcript.lower()
    found = any(kw in transcript_lower for kw in _WAKE_KEYWORDS)

    if not found:
        return False, transcript

    cleaned = transcript
    for kw in sorted(_WAKE_KEYWORDS, key=len, reverse=True):
        idx = transcript_lower.find(kw)
        if idx != -1:
            prefix = transcript[:idx].strip().lower()
            if prefix in _CLEAN_PREFIXES:
                cleaned = transcript[idx + len(kw):].strip()
            else:
                cleaned = (transcript[:idx] + transcript[idx + len(kw):]).strip()
            break

    return True, cleaned


def filter_transcript(transcript: str) -> str | None:
    """High-level filter: returns cleaned transcript or None to drop.

    When wake word is **disabled** the transcript passes through unchanged.
    When **enabled**, returns None if no wake word is found (caller should
    drop the input), or the cleaned transcript with wake word stripped.
    """
    if not _WAKE_WORD_ENABLED:
        return transcript

    found, cleaned = check_wake_word(transcript)
    if not found:
        logger.info("Wake word filter: no keyword in %r — dropping", transcript[:80])
        return None

    if not cleaned:
        logger.info("Wake word filter: empty after stripping keyword")
        return None

    logger.info("Wake word filter: %r → %r", transcript[:80], cleaned[:80])
    return cleaned
