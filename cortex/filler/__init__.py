"""Filler streaming engine for Atlas Cortex.

Fillers are short phrases streamed immediately to the user (0 ms perceived
latency) while the LLM generates the real response in the background.

Selection rules:
  1. Sentiment filler chosen first (never repeat last 2 used).
  2. Confidence filler appended when confidence < 0.8.
  3. No filler for 'command' or 'casual' sentiments, or follow-up turns.

Database-backed filler pools (per user, grown by nightly evolution job).
Falls back to built-in default pools when no DB rows are present.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

# ──────────────────────────────────────────────────────────────
# Default filler pools (used when DB has no entries for a user)
# ──────────────────────────────────────────────────────────────

DEFAULT_FILLERS: dict[str, list[str]] = {
    "greeting":   ["Hey! ", "Morning! ", "What's up? ", "Yo! ", "Hey there. "],
    "question":   ["Hmm — ", "Let me think... ", "So — ", "Alright — ", "Okay, "],
    "frustrated": ["I hear you. ", "Yeah, that's annoying. ", "Ugh, let me look at this. "],
    "excited":    ["Nice! ", "Oh cool — ", "Hell yeah! "],
    "late_night": ["Still at it? ", "Alright, ", "Late one, huh? "],
    "follow_up":  ["So — ", "Right, ", "Okay — "],
    # command / casual → no filler
    "command":    [],
    "casual":     [],
}

CONFIDENCE_FILLERS: dict[str, list[str]] = {
    "medium": [
        "I think — ",
        "If I remember right — ",
        "Pretty sure — ",
        "Let me make sure I've got this right... ",
    ],
    "low": [
        "I'm not 100% on that — checking now... ",
        "Let me verify... ",
        "Good question — I don't want to guess on this. Checking now... ",
        "I want to get this right — one moment... ",
    ],
    "none": [
        "I genuinely don't know this one. ",
        "That's outside what I can answer confidently. ",
        "I'd rather not guess — ",
    ],
}

# Sentiments where no filler is ever appropriate
_NO_FILLER_SENTIMENTS = {"command", "casual"}


def select_filler(
    sentiment: str,
    confidence: float,
    user_id: str = "default",
    conn: "sqlite3.Connection | None" = None,
    is_follow_up: bool = False,
) -> str:
    """Return a filler phrase (possibly empty) based on sentiment and confidence.

    Args:
        sentiment:   Detected sentiment category (e.g. 'question', 'excited').
        confidence:  Response confidence score (0.0–1.0).
        user_id:     User identifier for personalised filler pools.
        conn:        Optional DB connection for personalised/DB-backed fillers.
        is_follow_up: True when we're mid-conversation (often no filler needed).

    Returns:
        A filler string ready to stream, or ``""`` if no filler is appropriate.
    """
    if sentiment in _NO_FILLER_SENTIMENTS:
        return ""

    # Follow-up turns often need no filler
    if is_follow_up and random.random() < 0.6:
        return ""

    # Choose sentiment filler
    phrase = _pick_sentiment_filler(sentiment, user_id, conn)

    # Append confidence framing when uncertain
    if confidence < 0.8 and phrase:
        phrase += _pick_confidence_filler(confidence)
    elif confidence < 0.8 and not phrase:
        phrase = _pick_confidence_filler(confidence)

    return phrase


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _pick_sentiment_filler(
    sentiment: str,
    user_id: str,
    conn: "sqlite3.Connection | None",
) -> str:
    """Select a sentiment filler, preferring DB-backed personalised phrases."""
    # Try DB first
    if conn is not None:
        try:
            rows = conn.execute(
                """
                SELECT id, phrase FROM filler_phrases
                WHERE user_id = ? AND sentiment = ?
                AND id NOT IN (
                    SELECT id FROM filler_phrases
                    WHERE user_id = ?
                    ORDER BY last_used DESC LIMIT 2
                )
                ORDER BY RANDOM() LIMIT 1
                """,
                (user_id, sentiment, user_id),
            ).fetchone()
            if rows:
                # Update last_used
                conn.execute(
                    "UPDATE filler_phrases SET use_count = use_count + 1, last_used = ? WHERE id = ?",
                    (datetime.now(tz=timezone.utc).isoformat(), rows["id"]),
                )
                conn.commit()
                return rows["phrase"]
        except Exception:
            pass

    # Fall back to built-in defaults
    pool = DEFAULT_FILLERS.get(sentiment, [])
    if not pool:
        return ""
    return random.choice(pool)


def _pick_confidence_filler(confidence: float) -> str:
    """Return a confidence-level framing phrase."""
    if confidence >= 0.5:
        key = "medium"
    elif confidence >= 0.2:
        key = "low"
    else:
        key = "none"
    pool = CONFIDENCE_FILLERS[key]
    return random.choice(pool) if pool else ""


def seed_default_fillers(conn: "sqlite3.Connection", user_id: str = "default") -> None:
    """Populate the DB with built-in filler phrases for *user_id*.

    Safe to call multiple times — skips phrases that already exist.
    """
    for sentiment, phrases in DEFAULT_FILLERS.items():
        for phrase in phrases:
            conn.execute(
                """
                INSERT OR IGNORE INTO filler_phrases (user_id, sentiment, phrase, source)
                VALUES (?, ?, ?, 'default')
                """,
                (user_id, sentiment, phrase),
            )
    conn.commit()
