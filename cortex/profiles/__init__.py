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


def set_user_age(
    conn: Any,
    user_id: str,
    *,
    birth_year: int,
    birth_month: int = 1,
) -> dict[str, Any]:
    """Set a user's age from their birth year and month (admin / self-report).

    Computes current age, maps to age group, and stores with high confidence.
    This is the **primary** age source — always preferred over voice estimation.
    """
    now = datetime.now(tz=timezone.utc)
    age = now.year - birth_year
    if now.month < birth_month:
        age -= 1  # birthday hasn't happened yet this year
    age = max(0, age)
    group = get_age_group(age)

    update_user_profile(
        conn,
        user_id,
        age=age,
        age_group=group,
        age_confidence=0.95,
    )
    return {"user_id": user_id, "age": age, "age_group": group, "age_confidence": 0.95}


def resolve_age_group(
    profile: dict[str, Any],
    voice_estimate: tuple[str, float] | None = None,
) -> tuple[str, float]:
    """Hybrid age resolution — admin-set age wins, voice is safety fallback.

    Priority:
      1. Admin/self-reported age (confidence 0.95) — stored in profile
      2. Voice-based estimate (confidence ~0.3) — only for unknown speakers
      3. Default "unknown" → safety guardrails treat as child-safe

    The voice estimator uses a threshold of ~25 years so that the ±8 year
    error band keeps real children safely in the child bucket while allowing
    older teens (15-16) who sound adult to pass.

    Returns ``(age_group, confidence)``.
    """
    # 1. If admin set the age, always use it
    stored_confidence = profile.get("age_confidence", 0.0) or 0.0
    stored_group = profile.get("age_group", "unknown")
    if stored_confidence >= 0.5 and stored_group != "unknown":
        return (stored_group, stored_confidence)

    # 2. Voice-based estimate as safety fallback
    if voice_estimate is not None:
        voice_group, voice_conf = voice_estimate
        if voice_group != "unknown" and voice_conf > 0.0:
            return (voice_group, voice_conf)

    # 3. Unknown — safety guardrails will default to child-safe
    return ("unknown", 0.0)


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


# ──────────────────────────────────────────────────────────────────
# Parental controls
# ──────────────────────────────────────────────────────────────────

def get_parental_controls(conn: Any, child_user_id: str) -> dict | None:
    """Get parental control settings for a child user.

    Returns dict with content_filter_level, allowed_hours, restricted_topics,
    or None if no controls set.
    """
    row = conn.execute(
        "SELECT * FROM parental_controls WHERE child_user_id = ?",
        (child_user_id,),
    ).fetchone()
    if not row:
        return None

    result = dict(row)

    # Fetch restricted actions as topics
    actions = conn.execute(
        "SELECT action FROM parental_restricted_actions WHERE child_user_id = ?",
        (child_user_id,),
    ).fetchall()
    result["restricted_topics"] = [r["action"] for r in actions]

    return result


def check_parental_allowed(conn: Any, user_id: str, hour: int | None = None) -> dict:
    """Check if current interaction is allowed by parental controls.

    Returns {allowed: bool, reason: str, filter_level: str}.
    """
    controls = get_parental_controls(conn, user_id)
    if controls is None:
        return {"allowed": True, "reason": "no parental controls", "filter_level": "none"}

    filter_level = controls.get("content_filter_level", "strict")

    if hour is not None:
        start_str = controls.get("allowed_hours_start", "07:00")
        end_str = controls.get("allowed_hours_end", "21:00")
        start_h = int(start_str.split(":")[0])
        end_h = int(end_str.split(":")[0])
        if start_h <= end_h:
            if hour < start_h or hour >= end_h:
                return {
                    "allowed": False,
                    "reason": f"outside allowed hours ({start_str}-{end_str})",
                    "filter_level": filter_level,
                }
        else:
            # Wraps midnight (e.g. 22:00-06:00)
            if end_h <= hour < start_h:
                return {
                    "allowed": False,
                    "reason": f"outside allowed hours ({start_str}-{end_str})",
                    "filter_level": filter_level,
                }

    return {"allowed": True, "reason": "allowed", "filter_level": filter_level}


def set_parental_controls(
    conn: Any,
    child_user_id: str,
    parent_user_id: str,
    content_filter_level: str = "strict",
    allowed_hours: str | None = None,
    restricted_topics: str | None = None,
) -> None:
    """Set or update parental controls for a child."""
    start = "07:00"
    end = "21:00"
    if allowed_hours:
        parts = allowed_hours.split("-")
        if len(parts) == 2:
            start = parts[0].strip()
            end = parts[1].strip()

    conn.execute(
        """
        INSERT INTO parental_controls
            (child_user_id, parent_user_id, content_filter_level,
             allowed_hours_start, allowed_hours_end)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(child_user_id) DO UPDATE SET
            parent_user_id       = excluded.parent_user_id,
            content_filter_level = excluded.content_filter_level,
            allowed_hours_start  = excluded.allowed_hours_start,
            allowed_hours_end    = excluded.allowed_hours_end
        """,
        (child_user_id, parent_user_id, content_filter_level, start, end),
    )

    if restricted_topics:
        # Clear existing and insert new
        conn.execute(
            "DELETE FROM parental_restricted_actions WHERE child_user_id = ?",
            (child_user_id,),
        )
        for topic in restricted_topics.split(","):
            topic = topic.strip()
            if topic:
                conn.execute(
                    "INSERT INTO parental_restricted_actions (child_user_id, action) VALUES (?, ?)",
                    (child_user_id, topic),
                )

    conn.commit()


# ──────────────────────────────────────────────────────────────────
# Conversational onboarding
# ──────────────────────────────────────────────────────────────────

def get_onboarding_state(conn: Any, user_id: str) -> dict:
    """Get onboarding progress for a user.

    Returns {is_new: bool, name_known: bool, age_known: bool,
             preferences_collected: int, suggested_question: str | None}.
    """
    profile = get_or_create_user_profile(conn, user_id)

    name_known = bool(
        profile.get("display_name")
        and profile["display_name"] != user_id
    )
    age_known = profile.get("age") is not None
    onboarding_complete = bool(profile.get("onboarding_complete"))

    # Count collected preferences
    prefs = 0
    if name_known:
        prefs += 1
    if age_known:
        prefs += 1
    if profile.get("preferred_tone") and profile["preferred_tone"] != "neutral":
        prefs += 1
    if profile.get("communication_style") and profile["communication_style"] != "moderate":
        prefs += 1

    suggested = get_onboarding_prompt(user_id, profile)

    return {
        "is_new": not onboarding_complete and prefs == 0,
        "name_known": name_known,
        "age_known": age_known,
        "preferences_collected": prefs,
        "suggested_question": suggested,
    }


def get_onboarding_prompt(user_id: str, profile: dict) -> str | None:
    """Generate the next onboarding question based on what we don't know yet.

    Returns a natural question string, or None if onboarding is complete.
    Priority: name > age > preferences > communication style.
    Never interrogates — phrases as natural conversation.
    """
    if bool(profile.get("onboarding_complete")):
        return None

    name_known = bool(
        profile.get("display_name")
        and profile["display_name"] != user_id
    )
    if not name_known:
        return "By the way, what should I call you?"

    name = profile.get("display_name", "")
    if profile.get("age") is None:
        return f"Nice to meet you, {name}! How old are you, if you don't mind me asking?"

    if profile.get("preferred_tone", "neutral") == "neutral":
        return f"Hey {name}, do you prefer me to be more casual or more formal when we chat?"

    if profile.get("communication_style", "moderate") == "moderate":
        return f"{name}, would you like me to give you detailed explanations or keep things short and sweet?"

    return None
