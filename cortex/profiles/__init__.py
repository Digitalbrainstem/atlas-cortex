"""User profiles and emotional evolution for Atlas Cortex.

Manages:
  - User profiles (age-awareness, vocabulary level, communication style)
  - Emotional profiles (rapport, filler phrases, tone)
  - Conversational onboarding
  - Parental controls

See docs/user-profiles.md for full design.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# User profiles
# ──────────────────────────────────────────────────────────────────

def get_or_create_user_profile(conn: Any, user_id: str, display_name: str = "") -> dict[str, Any]:
    """Return the user profile, creating it if it doesn't exist."""
    row = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        return dict(row)
    # Create new profile
    name = display_name or user_id
    conn.execute(
        """
        INSERT INTO user_profiles (user_id, display_name)
        VALUES (?, ?)
        """,
        (user_id, name),
    )
    conn.commit()
    return {
        "user_id": user_id,
        "display_name": name,
        "age": None,
        "age_group": "unknown",
        "vocabulary_level": "moderate",
        "preferred_tone": "neutral",
        "communication_style": "moderate",
        "onboarding_complete": False,
    }


def update_user_profile(conn: Any, user_id: str, **fields: Any) -> None:
    """Update selected fields of a user profile."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [datetime.now(tz=timezone.utc).isoformat(), user_id]
    conn.execute(
        f"UPDATE user_profiles SET {set_clause}, updated_at = ? WHERE user_id = ?",
        values,
    )
    conn.commit()


def get_age_group(age: int | None) -> str:
    """Map numeric age to an age group string."""
    if age is None:
        return "unknown"
    if age < 5:
        return "toddler"
    if age < 13:
        return "child"
    if age < 18:
        return "teen"
    return "adult"


def age_appropriate_system_prompt(profile: dict[str, Any]) -> str:
    """Return a system-prompt modifier based on the user's age group."""
    age_group = profile.get("age_group", "unknown")
    modifiers = {
        "toddler": (
            "Respond in very simple language. Use short sentences. "
            "Be warm, gentle, and encouraging. Avoid any mature content."
        ),
        "child": (
            "Respond in clear, simple language suitable for children. "
            "Be friendly and patient. Avoid mature or scary content. "
            "Explain things in an easy-to-understand way."
        ),
        "teen": (
            "Respond in a casual, respectful tone. Be direct and honest. "
            "Avoid being condescending. Treat them as a capable young adult."
        ),
        "adult": "",  # no special modifier — base personality applies
        "unknown": "",
    }
    return modifiers.get(age_group, "")


# ──────────────────────────────────────────────────────────────────
# Emotional profiles
# ──────────────────────────────────────────────────────────────────

def get_or_create_emotional_profile(conn: Any, user_id: str, display_name: str = "") -> dict[str, Any]:
    """Return (or create) the emotional profile for *user_id*."""
    row = conn.execute(
        "SELECT * FROM emotional_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row:
        return dict(row)
    name = display_name or user_id
    conn.execute(
        "INSERT INTO emotional_profiles (user_id, display_name) VALUES (?, ?)",
        (user_id, name),
    )
    conn.commit()
    return {
        "user_id": user_id,
        "display_name": name,
        "rapport_score": 0.5,
        "preferred_tone": "neutral",
        "interaction_count": 0,
        "positive_count": 0,
        "negative_count": 0,
    }


def update_rapport(conn: Any, user_id: str, sentiment_score: float) -> None:
    """Adjust the rapport score based on interaction sentiment.

    Rules (from docs/phases.md C4.1):
      +0.01 per positive interaction
      -0.02 per frustrated interaction
      Clamped to [0.0, 1.0]
    """
    profile = get_or_create_emotional_profile(conn, user_id)
    current = float(profile.get("rapport_score", 0.5))

    if sentiment_score >= 0.05:
        delta = 0.01
        positive_inc = 1
        negative_inc = 0
    elif sentiment_score <= -0.5:
        delta = -0.02
        positive_inc = 0
        negative_inc = 1
    else:
        delta = 0.0
        positive_inc = 0
        negative_inc = 0

    new_score = max(0.0, min(1.0, current + delta))
    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE emotional_profiles
        SET rapport_score = ?,
            interaction_count = interaction_count + 1,
            positive_count    = positive_count + ?,
            negative_count    = negative_count + ?,
            last_interaction  = ?
        WHERE user_id = ?
        """,
        (new_score, positive_inc, negative_inc, now, user_id),
    )
    conn.commit()


def record_activity_hour(conn: Any, user_id: str, hour: int) -> None:
    """Increment the interaction count for this hour-of-day."""
    conn.execute(
        """
        INSERT INTO user_activity_hours (user_id, hour, interaction_count)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, hour) DO UPDATE SET
            interaction_count = interaction_count + 1
        """,
        (user_id, hour),
    )
    conn.commit()


def record_topic(conn: Any, user_id: str, topic: str) -> None:
    """Increment the mention count for a topic."""
    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO user_topics (user_id, topic, mention_count, last_mentioned)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(user_id, topic) DO UPDATE SET
            mention_count  = mention_count + 1,
            last_mentioned = ?
        """,
        (user_id, topic, now, now),
    )
    conn.commit()
