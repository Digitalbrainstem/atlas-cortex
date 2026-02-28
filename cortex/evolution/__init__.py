"""Emotional evolution — personality drift and relationship tracking.

Manages per-user emotional profiles that evolve over time based on
interaction patterns, sentiment history, and relationship depth.

Phase C4 implementation: Emotional Profile Engine (C4.1), Nightly
Personality Evolution (C4.2), Contextual Response Personalization (C4.3),
and Memory & Proactive Suggestions (C4.4).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class EmotionalState:
    """Snapshot of a user's emotional relationship with Atlas."""

    rapport: float  # 0.0–1.0 relationship warmth
    familiarity: int  # interaction count
    dominant_sentiment: str  # most common sentiment recently
    mood_trend: str  # "improving", "stable", "declining"
    last_interaction: str  # ISO timestamp
    personality_notes: list[str] = field(default_factory=list)


# ── Rapport adjustment constants (C4.1) ─────────────────────────────
_RAPPORT_POSITIVE = 0.01
_RAPPORT_NEGATIVE = -0.02
_RAPPORT_NEUTRAL = 0.0
_RAPPORT_DECAY_PER_DAY = 0.005
_RAPPORT_DECAY_TARGET = 0.5
_RAPPORT_MIN = 0.0
_RAPPORT_MAX = 1.0

_SENTIMENT_DELTAS: dict[str, float] = {
    "positive": _RAPPORT_POSITIVE,
    "negative": _RAPPORT_NEGATIVE,
    "frustrated": _RAPPORT_NEGATIVE,
    "neutral": _RAPPORT_NEUTRAL,
}


def _clamp(value: float, lo: float = _RAPPORT_MIN, hi: float = _RAPPORT_MAX) -> float:
    return max(lo, min(hi, value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmotionalEvolution:
    """Tracks and evolves emotional relationships per user."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── C4.1  Emotional Profile Engine ──────────────────────────────

    def get_emotional_state(self, user_id: str) -> EmotionalState:
        """Get current emotional state for *user_id*.

        Creates a default profile if the user has never been seen before.
        """
        row = self._conn.execute(
            "SELECT rapport_score, interaction_count, preferred_tone, "
            "relationship_notes, last_interaction, positive_count, "
            "negative_count FROM emotional_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            self._ensure_profile(user_id)
            return EmotionalState(
                rapport=0.5,
                familiarity=0,
                dominant_sentiment="neutral",
                mood_trend="stable",
                last_interaction=_now_iso(),
                personality_notes=[],
            )

        notes = _parse_notes(row["relationship_notes"])
        dominant = self._dominant_sentiment(user_id)
        trend = self._mood_trend(user_id)

        return EmotionalState(
            rapport=row["rapport_score"],
            familiarity=row["interaction_count"],
            dominant_sentiment=dominant,
            mood_trend=trend,
            last_interaction=row["last_interaction"] or _now_iso(),
            personality_notes=notes,
        )

    def record_interaction(
        self,
        user_id: str,
        sentiment: str,
        topics: list[str] | None = None,
    ) -> None:
        """Record a new interaction and update emotional state (C4.1).

        *sentiment* should be one of ``positive``, ``negative``,
        ``frustrated``, or ``neutral``.
        """
        self._ensure_profile(user_id)
        delta = _SENTIMENT_DELTAS.get(sentiment, _RAPPORT_NEUTRAL)

        now = _now_iso()
        hour = datetime.now(timezone.utc).hour

        # Update core counters & rapport
        self._conn.execute(
            "UPDATE emotional_profiles SET "
            "rapport_score = MIN(?, MAX(?, rapport_score + ?)), "
            "interaction_count = interaction_count + 1, "
            "positive_count = positive_count + ?, "
            "negative_count = negative_count + ?, "
            "last_interaction = ? "
            "WHERE user_id = ?",
            (
                _RAPPORT_MAX,
                _RAPPORT_MIN,
                delta,
                1 if sentiment == "positive" else 0,
                1 if sentiment in ("negative", "frustrated") else 0,
                now,
                user_id,
            ),
        )

        # Activity-hour tracking
        self._conn.execute(
            "INSERT INTO user_activity_hours (user_id, hour, interaction_count) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, hour) DO UPDATE SET interaction_count = interaction_count + 1",
            (user_id, hour),
        )

        # Topic tracking
        for topic in topics or []:
            self._conn.execute(
                "INSERT INTO user_topics (user_id, topic, mention_count, last_mentioned) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(user_id, topic) DO UPDATE SET "
                "mention_count = mention_count + 1, last_mentioned = ?",
                (user_id, topic, now, now),
            )

        self._conn.commit()

    # ── C4.3  Contextual Response Personalization ───────────────────

    def get_personality_modifiers(self, user_id: str) -> dict:
        """Return personality modifiers to inject into the system prompt.

        Keys: ``tone``, ``formality``, ``humor_level``, ``verbosity``,
        ``proactivity``, ``relationship_note``.

        Rapport thresholds (from docs/phases.md C4.1 & personality.md):

        * **Low (0.0–0.3):** formal, helpful, professional
        * **Medium (0.3–0.6):** friendly, occasional humor, remembers prefs
        * **High (0.6–0.8):** casual, humor, proactive suggestions
        * **Very high (0.8–1.0):** playful, inside jokes, pushback OK
        """
        state = self.get_emotional_state(user_id)
        r = state.rapport

        if r < 0.3:
            return {
                "tone": "professional",
                "formality": "formal",
                "humor_level": "none",
                "verbosity": "concise",
                "proactivity": "low",
                "relationship_note": "New user — be helpful and welcoming.",
            }
        if r < 0.6:
            return {
                "tone": "friendly",
                "formality": "moderate",
                "humor_level": "occasional",
                "verbosity": "moderate",
                "proactivity": "medium",
                "relationship_note": "Familiar user — remember their preferences.",
            }
        if r < 0.8:
            return {
                "tone": "casual",
                "formality": "relaxed",
                "humor_level": "regular",
                "verbosity": "natural",
                "proactivity": "high",
                "relationship_note": "Good rapport — proactive suggestions welcome.",
            }
        return {
            "tone": "playful",
            "formality": "informal",
            "humor_level": "frequent",
            "verbosity": "natural",
            "proactivity": "very_high",
            "relationship_note": "Close relationship — banter and pushback are OK.",
        }

    # ── C4.4  Proactive Suggestions ─────────────────────────────────

    def suggest_proactive(self, user_id: str) -> str | None:
        """Generate a proactive suggestion based on interaction patterns.

        Returns a human-readable suggestion or ``None``.
        """
        # Find the user's most-active hour
        row = self._conn.execute(
            "SELECT hour, interaction_count FROM user_activity_hours "
            "WHERE user_id = ? ORDER BY interaction_count DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if row is None:
            return None

        peak_hour = row["hour"]
        current_hour = datetime.now(timezone.utc).hour

        # Only suggest if we're within ±1h of their peak hour
        if abs(current_hour - peak_hour) > 1:
            return None

        # Find their most-discussed topic
        topic_row = self._conn.execute(
            "SELECT topic FROM user_topics "
            "WHERE user_id = ? ORDER BY mention_count DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if topic_row is None:
            return f"You're usually active around this time."

        return (
            f"You usually ask about {topic_row['topic']} around this time — "
            f"anything I can help with?"
        )

    # ── C4.2  Nightly Personality Evolution ─────────────────────────

    async def run_nightly_evolution(self) -> dict:
        """Nightly job: evolve all user emotional profiles.

        * Analyse sentiment trends over the last 24 h.
        * Decay rapport toward 0.5 for inactive users.
        * Update personality notes from interaction patterns.
        * Generate proactive suggestions for tomorrow.

        Returns a summary dict with ``profiles_evolved`` count.
        """
        profiles = self._conn.execute(
            "SELECT user_id, rapport_score, interaction_count, "
            "positive_count, negative_count, last_interaction "
            "FROM emotional_profiles"
        ).fetchall()

        evolved = 0

        for p in profiles:
            user_id = p["user_id"]
            rapport = p["rapport_score"]
            last = p["last_interaction"]

            # ── Rapport decay (C4.1) ────────────────────────────────
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    days_inactive = (
                        datetime.now(timezone.utc) - last_dt
                    ).total_seconds() / 86400
                except (ValueError, TypeError):
                    days_inactive = 0
            else:
                days_inactive = 0

            if days_inactive > 0:
                decay = _RAPPORT_DECAY_PER_DAY * days_inactive
                if rapport > _RAPPORT_DECAY_TARGET:
                    rapport = max(_RAPPORT_DECAY_TARGET, rapport - decay)
                elif rapport < _RAPPORT_DECAY_TARGET:
                    rapport = min(_RAPPORT_DECAY_TARGET, rapport + decay)

            # ── Update mood trend note ──────────────────────────────
            trend = self._mood_trend(user_id)
            notes = self._build_evolution_notes(user_id, trend)

            self._conn.execute(
                "UPDATE emotional_profiles SET "
                "rapport_score = ?, relationship_notes = ?, "
                "last_evolved_at = ? "
                "WHERE user_id = ?",
                (rapport, json.dumps(notes), _now_iso(), user_id),
            )
            evolved += 1

        self._conn.commit()
        return {"profiles_evolved": evolved}

    # ── internal helpers ────────────────────────────────────────────

    def _ensure_profile(self, user_id: str) -> None:
        """Insert a default emotional profile if one doesn't exist."""
        self._conn.execute(
            "INSERT OR IGNORE INTO emotional_profiles "
            "(user_id, rapport_score, preferred_tone, interaction_count, "
            "positive_count, negative_count, last_interaction) "
            "VALUES (?, 0.5, 'neutral', 0, 0, 0, ?)",
            (user_id, _now_iso()),
        )
        self._conn.commit()

    def _dominant_sentiment(self, user_id: str) -> str:
        """Return the dominant sentiment from the last 20 interactions."""
        rows = self._conn.execute(
            "SELECT sentiment, COUNT(*) AS cnt FROM interactions "
            "WHERE user_id = ? AND sentiment IS NOT NULL "
            "GROUP BY sentiment ORDER BY cnt DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        if rows is None:
            return "neutral"
        return rows["sentiment"]

    def _mood_trend(self, user_id: str) -> str:
        """Determine mood trend from recent interactions.

        Compares the positive/negative ratio of the last 10 interactions
        against the preceding 10.
        """
        rows = self._conn.execute(
            "SELECT sentiment FROM interactions "
            "WHERE user_id = ? AND sentiment IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 20",
            (user_id,),
        ).fetchall()

        if len(rows) < 2:
            return "stable"

        recent = rows[: len(rows) // 2]
        older = rows[len(rows) // 2 :]

        def _score(batch: list) -> float:
            total = len(batch) or 1
            pos = sum(1 for r in batch if r["sentiment"] == "positive")
            neg = sum(1 for r in batch if r["sentiment"] in ("negative", "frustrated"))
            return (pos - neg) / total

        diff = _score(recent) - _score(older)
        if diff > 0.1:
            return "improving"
        if diff < -0.1:
            return "declining"
        return "stable"

    def _build_evolution_notes(self, user_id: str, trend: str) -> list[str]:
        """Build updated personality notes for nightly evolution."""
        notes: list[str] = []

        # Top topics
        topics = self._conn.execute(
            "SELECT topic, mention_count FROM user_topics "
            "WHERE user_id = ? ORDER BY mention_count DESC LIMIT 3",
            (user_id,),
        ).fetchall()

        if topics:
            topic_list = ", ".join(t["topic"] for t in topics)
            notes.append(f"Frequently discusses: {topic_list}")

        # Peak hours
        hours = self._conn.execute(
            "SELECT hour FROM user_activity_hours "
            "WHERE user_id = ? ORDER BY interaction_count DESC LIMIT 2",
            (user_id,),
        ).fetchall()

        if hours:
            hour_list = ", ".join(f"{h['hour']}:00" for h in hours)
            notes.append(f"Most active around: {hour_list}")

        # Mood trend
        notes.append(f"Recent mood trend: {trend}")

        return notes


def _parse_notes(raw: str | None) -> list[str]:
    """Parse stored relationship_notes (JSON list or empty)."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [raw] if raw else []
