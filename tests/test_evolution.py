"""Tests for the emotional evolution module (Phase C4)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.evolution import EmotionalEvolution, EmotionalState


@pytest.fixture
def db_conn():
    """In-memory SQLite DB with full schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


@pytest.fixture
def evo(db_conn):
    """EmotionalEvolution instance backed by the test DB."""
    return EmotionalEvolution(db_conn)


# ── C4.1  Emotional State ──────────────────────────────────────────


class TestGetEmotionalState:
    def test_new_user_gets_defaults(self, evo):
        state = evo.get_emotional_state("new_user")
        assert isinstance(state, EmotionalState)
        assert state.rapport == 0.5
        assert state.familiarity == 0
        assert state.dominant_sentiment == "neutral"
        assert state.mood_trend == "stable"
        assert state.personality_notes == []

    def test_existing_user_returns_stored_state(self, evo, db_conn):
        # Seed a profile manually
        db_conn.execute(
            "INSERT OR REPLACE INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, "
            "negative_count, preferred_tone, last_interaction, relationship_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice", 0.75, 42, 30, 5, "friendly",
             datetime.now(timezone.utc).isoformat(),
             json.dumps(["Loves Python", "Night owl"])),
        )
        db_conn.commit()

        state = evo.get_emotional_state("alice")
        assert state.rapport == 0.75
        assert state.familiarity == 42
        assert "Loves Python" in state.personality_notes
        assert "Night owl" in state.personality_notes

    def test_new_user_creates_profile_row(self, evo, db_conn):
        evo.get_emotional_state("brand_new")
        row = db_conn.execute(
            "SELECT * FROM emotional_profiles WHERE user_id = ?",
            ("brand_new",),
        ).fetchone()
        assert row is not None
        assert row["rapport_score"] == 0.5


# ── C4.1  Record Interaction ───────────────────────────────────────


class TestRecordInteraction:
    def test_positive_increases_rapport(self, evo, db_conn):
        evo.record_interaction("bob", "positive")
        row = db_conn.execute(
            "SELECT rapport_score, interaction_count, positive_count "
            "FROM emotional_profiles WHERE user_id = ?",
            ("bob",),
        ).fetchone()
        assert row["rapport_score"] == pytest.approx(0.51, abs=0.001)
        assert row["interaction_count"] == 1
        assert row["positive_count"] == 1

    def test_negative_decreases_rapport(self, evo, db_conn):
        evo.record_interaction("carol", "negative")
        row = db_conn.execute(
            "SELECT rapport_score, negative_count FROM emotional_profiles WHERE user_id = ?",
            ("carol",),
        ).fetchone()
        assert row["rapport_score"] == pytest.approx(0.48, abs=0.001)
        assert row["negative_count"] == 1

    def test_frustrated_decreases_rapport(self, evo, db_conn):
        evo.record_interaction("dave", "frustrated")
        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("dave",),
        ).fetchone()
        assert row["rapport_score"] == pytest.approx(0.48, abs=0.001)

    def test_neutral_no_rapport_change(self, evo, db_conn):
        evo.record_interaction("eve", "neutral")
        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("eve",),
        ).fetchone()
        assert row["rapport_score"] == pytest.approx(0.5, abs=0.001)

    def test_rapport_clamped_at_max(self, evo, db_conn):
        # Set rapport near max then push it over
        db_conn.execute(
            "INSERT INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, negative_count) "
            "VALUES (?, 0.999, 100, 90, 0)",
            ("max_user",),
        )
        db_conn.commit()
        evo.record_interaction("max_user", "positive")
        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("max_user",),
        ).fetchone()
        assert row["rapport_score"] <= 1.0

    def test_rapport_clamped_at_min(self, evo, db_conn):
        db_conn.execute(
            "INSERT INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, negative_count) "
            "VALUES (?, 0.005, 100, 0, 90)",
            ("min_user",),
        )
        db_conn.commit()
        evo.record_interaction("min_user", "negative")
        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("min_user",),
        ).fetchone()
        assert row["rapport_score"] >= 0.0

    def test_topics_tracked(self, evo, db_conn):
        evo.record_interaction("frank", "positive", topics=["docker", "python"])
        topics = db_conn.execute(
            "SELECT topic, mention_count FROM user_topics WHERE user_id = ? ORDER BY topic",
            ("frank",),
        ).fetchall()
        assert len(topics) == 2
        assert {t["topic"] for t in topics} == {"docker", "python"}

    def test_repeated_topic_increments_count(self, evo, db_conn):
        evo.record_interaction("grace", "neutral", topics=["weather"])
        evo.record_interaction("grace", "positive", topics=["weather"])
        row = db_conn.execute(
            "SELECT mention_count FROM user_topics WHERE user_id = ? AND topic = ?",
            ("grace", "weather"),
        ).fetchone()
        assert row["mention_count"] == 2

    def test_activity_hour_tracked(self, evo, db_conn):
        evo.record_interaction("hank", "neutral")
        rows = db_conn.execute(
            "SELECT hour, interaction_count FROM user_activity_hours WHERE user_id = ?",
            ("hank",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["interaction_count"] == 1


# ── C4.3  Personality Modifiers ────────────────────────────────────


class TestPersonalityModifiers:
    def _set_rapport(self, db_conn, user_id, rapport):
        db_conn.execute(
            "INSERT OR REPLACE INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, negative_count) "
            "VALUES (?, ?, 10, 5, 2)",
            (user_id, rapport),
        )
        db_conn.commit()

    def test_low_rapport_formal(self, evo, db_conn):
        self._set_rapport(db_conn, "low", 0.15)
        mods = evo.get_personality_modifiers("low")
        assert mods["tone"] == "professional"
        assert mods["formality"] == "formal"
        assert mods["humor_level"] == "none"

    def test_medium_rapport_friendly(self, evo, db_conn):
        self._set_rapport(db_conn, "mid", 0.45)
        mods = evo.get_personality_modifiers("mid")
        assert mods["tone"] == "friendly"
        assert mods["formality"] == "moderate"
        assert mods["humor_level"] == "occasional"

    def test_high_rapport_casual(self, evo, db_conn):
        self._set_rapport(db_conn, "high", 0.7)
        mods = evo.get_personality_modifiers("high")
        assert mods["tone"] == "casual"
        assert mods["formality"] == "relaxed"
        assert mods["humor_level"] == "regular"
        assert mods["proactivity"] == "high"

    def test_very_high_rapport_playful(self, evo, db_conn):
        self._set_rapport(db_conn, "vhigh", 0.9)
        mods = evo.get_personality_modifiers("vhigh")
        assert mods["tone"] == "playful"
        assert mods["formality"] == "informal"
        assert mods["humor_level"] == "frequent"
        assert mods["proactivity"] == "very_high"

    def test_all_keys_present(self, evo, db_conn):
        self._set_rapport(db_conn, "keys", 0.5)
        mods = evo.get_personality_modifiers("keys")
        expected_keys = {"tone", "formality", "humor_level", "verbosity",
                         "proactivity", "relationship_note"}
        assert set(mods.keys()) == expected_keys

    def test_boundary_0_3(self, evo, db_conn):
        """Rapport exactly at 0.3 should be medium tier."""
        self._set_rapport(db_conn, "edge1", 0.3)
        mods = evo.get_personality_modifiers("edge1")
        assert mods["tone"] == "friendly"

    def test_boundary_0_6(self, evo, db_conn):
        """Rapport exactly at 0.6 should be high tier."""
        self._set_rapport(db_conn, "edge2", 0.6)
        mods = evo.get_personality_modifiers("edge2")
        assert mods["tone"] == "casual"

    def test_boundary_0_8(self, evo, db_conn):
        """Rapport exactly at 0.8 should be very-high tier."""
        self._set_rapport(db_conn, "edge3", 0.8)
        mods = evo.get_personality_modifiers("edge3")
        assert mods["tone"] == "playful"


# ── C4.4  Proactive Suggestions ────────────────────────────────────


class TestProactiveSuggestions:
    def test_no_data_returns_none(self, evo):
        assert evo.suggest_proactive("unknown_user") is None

    def test_returns_suggestion_at_peak_hour(self, evo, db_conn):
        user = "peak_user"
        evo._ensure_profile(user)
        current_hour = datetime.now(timezone.utc).hour
        # Seed activity at current hour
        db_conn.execute(
            "INSERT INTO user_activity_hours (user_id, hour, interaction_count) "
            "VALUES (?, ?, 50)",
            (user, current_hour),
        )
        db_conn.execute(
            "INSERT INTO user_topics (user_id, topic, mention_count) VALUES (?, ?, ?)",
            (user, "weather", 10),
        )
        db_conn.commit()

        suggestion = evo.suggest_proactive(user)
        assert suggestion is not None
        assert "weather" in suggestion

    def test_no_suggestion_outside_peak_hour(self, evo, db_conn):
        user = "off_peak"
        evo._ensure_profile(user)
        current_hour = datetime.now(timezone.utc).hour
        far_hour = (current_hour + 12) % 24  # 12 hours away
        db_conn.execute(
            "INSERT INTO user_activity_hours (user_id, hour, interaction_count) "
            "VALUES (?, ?, 50)",
            (user, far_hour),
        )
        db_conn.commit()

        assert evo.suggest_proactive(user) is None


# ── C4.2  Nightly Evolution ────────────────────────────────────────


class TestNightlyEvolution:
    @pytest.mark.asyncio
    async def test_evolves_all_profiles(self, evo, db_conn):
        # Create two users
        evo.record_interaction("user_a", "positive", topics=["docker"])
        evo.record_interaction("user_b", "neutral", topics=["python"])

        result = await evo.run_nightly_evolution()
        assert result["profiles_evolved"] == 2

    @pytest.mark.asyncio
    async def test_rapport_decays_for_inactive_user(self, evo, db_conn):
        # Insert a user who was last active 10 days ago with high rapport
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db_conn.execute(
            "INSERT INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, "
            "negative_count, last_interaction) "
            "VALUES (?, 0.8, 50, 40, 5, ?)",
            ("inactive_user", ten_days_ago),
        )
        db_conn.commit()

        await evo.run_nightly_evolution()

        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("inactive_user",),
        ).fetchone()
        # Should have decayed: 0.8 - (0.005 * 10) = 0.75, but not below 0.5
        assert row["rapport_score"] < 0.8
        assert row["rapport_score"] >= 0.5

    @pytest.mark.asyncio
    async def test_rapport_does_not_decay_below_target(self, evo, db_conn):
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db_conn.execute(
            "INSERT INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, "
            "negative_count, last_interaction) "
            "VALUES (?, 0.55, 20, 10, 5, ?)",
            ("slight_above", thirty_days_ago),
        )
        db_conn.commit()

        await evo.run_nightly_evolution()

        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("slight_above",),
        ).fetchone()
        assert row["rapport_score"] == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_low_rapport_recovers_toward_target(self, evo, db_conn):
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db_conn.execute(
            "INSERT INTO emotional_profiles "
            "(user_id, rapport_score, interaction_count, positive_count, "
            "negative_count, last_interaction) "
            "VALUES (?, 0.2, 20, 5, 10, ?)",
            ("low_rapport", ten_days_ago),
        )
        db_conn.commit()

        await evo.run_nightly_evolution()

        row = db_conn.execute(
            "SELECT rapport_score FROM emotional_profiles WHERE user_id = ?",
            ("low_rapport",),
        ).fetchone()
        # Should recover toward 0.5: 0.2 + (0.005 * 10) = 0.25
        assert row["rapport_score"] > 0.2
        assert row["rapport_score"] <= 0.5

    @pytest.mark.asyncio
    async def test_evolution_updates_notes(self, evo, db_conn):
        evo.record_interaction("noted_user", "positive", topics=["docker"])

        await evo.run_nightly_evolution()

        row = db_conn.execute(
            "SELECT relationship_notes FROM emotional_profiles WHERE user_id = ?",
            ("noted_user",),
        ).fetchone()
        notes = json.loads(row["relationship_notes"])
        assert isinstance(notes, list)
        assert any("docker" in n for n in notes)

    @pytest.mark.asyncio
    async def test_evolution_sets_last_evolved_at(self, evo, db_conn):
        evo.record_interaction("evolved_user", "neutral")

        await evo.run_nightly_evolution()

        row = db_conn.execute(
            "SELECT last_evolved_at FROM emotional_profiles WHERE user_id = ?",
            ("evolved_user",),
        ).fetchone()
        assert row["last_evolved_at"] is not None
