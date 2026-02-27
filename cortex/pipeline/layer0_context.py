"""Layer 0: Context Assembly.

Runs for every message (~1 ms). Assembles the context dict used by all
subsequent layers:
  - user identification
  - sentiment analysis (VADER)
  - time-of-day category
  - basic conversation state
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore

logger = logging.getLogger(__name__)

_analyzer = SentimentIntensityAnalyzer()

# ──────────────────────────────────────────────────────────────────
# Sentiment mapping
# ──────────────────────────────────────────────────────────────────

_GREETING_KEYWORDS = frozenset([
    "hello", "hi", "hey", "good morning", "good afternoon",
    "good evening", "howdy", "what's up", "sup",
])

_QUESTION_MARKERS = frozenset(["what", "who", "where", "when", "why", "how", "?"])

_COMMAND_PREFIXES = frozenset([
    "turn on", "turn off", "set ", "switch ", "open ", "close ", "lock ",
    "unlock ", "dim ", "brighten ", "play ", "pause ", "stop ", "resume ",
])


def _classify_sentiment(message: str, vader_scores: dict[str, float]) -> str:
    """Map message + VADER scores to an Atlas sentiment category."""
    lower = message.lower().strip()

    # Greeting
    if any(lower.startswith(kw) or lower == kw for kw in _GREETING_KEYWORDS):
        return "greeting"

    # Command (checked before frustration)
    if any(lower.startswith(prefix) for prefix in _COMMAND_PREFIXES):
        return "command"

    compound = vader_scores["compound"]

    # Frustrated
    if compound <= -0.5:
        return "frustrated"

    # Excited
    if compound >= 0.7 and ("!" in message or any(w in lower for w in ("awesome", "great", "wow", "cool"))):
        return "excited"

    # Question
    if "?" in message or any(lower.startswith(w) for w in _QUESTION_MARKERS):
        return "question"

    # Casual / neutral
    return "casual"


def _time_of_day(hour: int) -> str:
    """Map wall-clock hour (0-23) to a category."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "late_night"


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

async def assemble_context(
    message: str,
    user_id: str = "default",
    speaker_id: str | None = None,
    satellite_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and return the context dict for a single request.

    Args:
        message:             The user's raw message text.
        user_id:             Open WebUI user ID (or ``"default"``).
        speaker_id:          Voice-identified speaker ID (nullable).
        satellite_id:        Wyoming satellite device ID (nullable).
        conversation_history: Prior turns in this session (list of {role, content}).
        metadata:            Extra metadata from the caller (voice pipeline, etc.).

    Returns a dict used by Layers 1-3.
    """
    now = datetime.now(tz=timezone.utc)
    vader_scores: dict[str, float] = _analyzer.polarity_scores(message)
    sentiment = _classify_sentiment(message, vader_scores)
    tod = _time_of_day(now.hour)

    # Effective sentiment for fillers: late_night overrides others at night
    if tod == "late_night" and sentiment in ("casual", "question"):
        effective_sentiment = "late_night"
    else:
        effective_sentiment = sentiment

    # Is this a follow-up in an active conversation?
    history_len = len(conversation_history) if conversation_history else 0
    is_follow_up = history_len > 2

    ctx: dict[str, Any] = {
        # Identity
        "user_id": user_id,
        "speaker_id": speaker_id,
        "satellite_id": satellite_id,
        # Temporal
        "timestamp": now.isoformat(),
        "time_of_day": tod,
        "hour": now.hour,
        # Sentiment
        "sentiment": sentiment,
        "effective_sentiment": effective_sentiment,
        "sentiment_score": vader_scores["compound"],
        "vader_scores": vader_scores,
        # Conversation state
        "is_follow_up": is_follow_up,
        "conversation_length": history_len,
        "conversation_history": conversation_history or [],
        # Spatial (populated later by voice pipeline / Part 2)
        "room": None,
        "area": None,
        # Extra
        "metadata": metadata or {},
    }
    return ctx
