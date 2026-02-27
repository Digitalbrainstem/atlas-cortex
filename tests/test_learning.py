"""Tests for FallthroughAnalyzer, PatternLifecycle, NightlyEvolution."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.learning.analyzer import FallthroughAnalyzer
from cortex.integrations.learning.evolution import NightlyEvolution
from cortex.integrations.learning.lifecycle import PatternLifecycle


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


class TestFallthroughAnalyzer:
    def test_get_fallthroughs_empty(self, db_conn):
        analyzer = FallthroughAnalyzer(db_conn)
        results = analyzer.get_fallthroughs(since_hours=24)
        assert results == []

    def test_get_fallthroughs_finds_llm(self, db_conn):
        db_conn.execute(
            "INSERT INTO interactions (user_id, message, matched_layer, response)"
            " VALUES (?, ?, ?, ?)",
            ("user1", "turn on the bedroom lights", "llm", "Sure!"),
        )
        db_conn.commit()
        analyzer = FallthroughAnalyzer(db_conn)
        results = analyzer.get_fallthroughs(since_hours=24)
        assert len(results) >= 1
        assert any(r["message"] == "turn on the bedroom lights" for r in results)

    def test_get_fallthroughs_skips_instant(self, db_conn):
        db_conn.execute(
            "INSERT INTO interactions (user_id, message, matched_layer, response)"
            " VALUES (?, ?, ?, ?)",
            ("user1", "what time is it", "instant", "It's 3pm"),
        )
        db_conn.commit()
        analyzer = FallthroughAnalyzer(db_conn)
        results = analyzer.get_fallthroughs(since_hours=24)
        assert all(r["message"] != "what time is it" for r in results)

    def test_extract_candidate_patterns_ha_phrase(self, db_conn):
        analyzer = FallthroughAnalyzer(db_conn)
        interactions = [{"id": 1, "message": "turn on the bedroom lights", "response": ""}]
        candidates = analyzer.extract_candidate_patterns(interactions)
        assert len(candidates) >= 1
        assert any(c["intent"] == "toggle" for c in candidates)

    def test_extract_candidate_patterns_no_match(self, db_conn):
        analyzer = FallthroughAnalyzer(db_conn)
        interactions = [{"id": 1, "message": "the quick brown fox", "response": ""}]
        candidates = analyzer.extract_candidate_patterns(interactions)
        assert len(candidates) == 0

    def test_save_learned_patterns(self, db_conn):
        # Insert a real interaction row so FK is satisfied
        db_conn.execute(
            "INSERT INTO interactions (id, user_id, message, matched_layer) VALUES (1, 'u1', 'turn on bedroom lights', 'llm')",
        )
        db_conn.commit()
        analyzer = FallthroughAnalyzer(db_conn)
        candidates = [
            {
                "pattern": "(?i)turn on bedroom lights test",
                "intent": "toggle",
                "confidence": 0.8,
                "source_interaction_id": 1,
            }
        ]
        saved = analyzer.save_learned_patterns(candidates)
        assert saved == 1
        row = db_conn.execute(
            "SELECT source FROM command_patterns WHERE pattern = ?",
            ("(?i)turn on bedroom lights test",),
        ).fetchone()
        assert row is not None
        assert row["source"] == "learned"

    def test_run_full_pipeline(self, db_conn):
        for msg in ["turn on the bedroom lights", "switch off the fan"]:
            db_conn.execute(
                "INSERT INTO interactions (user_id, message, matched_layer) VALUES (?, ?, ?)",
                ("user1", msg, "llm"),
            )
        db_conn.commit()
        analyzer = FallthroughAnalyzer(db_conn)
        result = analyzer.run(since_hours=24)
        assert isinstance(result, dict)
        for key in ("fallthroughs_analyzed", "patterns_proposed", "patterns_saved"):
            assert key in result
        assert result["fallthroughs_analyzed"] >= 2


class TestPatternLifecycle:
    def test_get_stats_empty(self, db_conn):
        lifecycle = PatternLifecycle(db_conn)
        stats = lifecycle.get_stats()
        assert isinstance(stats, dict)

    def test_prune_zero_hit_patterns(self, db_conn):
        db_conn.execute(
            """INSERT INTO command_patterns
               (pattern, intent, source, confidence, hit_count, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now', '-31 days'))""",
            ("(?i)old learned pattern", "toggle", "learned", 0.7, 0),
        )
        db_conn.commit()
        lifecycle = PatternLifecycle(db_conn)
        deleted = lifecycle.prune_zero_hit_patterns(older_than_days=30)
        assert deleted == 1
        remaining = db_conn.execute(
            "SELECT count(*) FROM command_patterns WHERE pattern = ?",
            ("(?i)old learned pattern",),
        ).fetchone()[0]
        assert remaining == 0

    def test_prune_skips_seed_patterns(self, db_conn):
        db_conn.execute(
            """INSERT INTO command_patterns
               (pattern, intent, source, confidence, hit_count, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now', '-31 days'))""",
            ("(?i)old seed pattern", "toggle", "seed", 0.7, 0),
        )
        db_conn.commit()
        lifecycle = PatternLifecycle(db_conn)
        deleted = lifecycle.prune_zero_hit_patterns(older_than_days=30)
        assert deleted == 0

    def test_prune_skips_recent_patterns(self, db_conn):
        db_conn.execute(
            """INSERT INTO command_patterns
               (pattern, intent, source, confidence, hit_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("(?i)recent learned pattern", "toggle", "learned", 0.7, 0),
        )
        db_conn.commit()
        lifecycle = PatternLifecycle(db_conn)
        deleted = lifecycle.prune_zero_hit_patterns(older_than_days=30)
        assert deleted == 0

    def test_boost_frequent_patterns(self, db_conn):
        db_conn.execute(
            """INSERT INTO command_patterns
               (pattern, intent, source, confidence, hit_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("(?i)popular pattern boost", "toggle", "seed", 0.8, 15),
        )
        db_conn.commit()
        lifecycle = PatternLifecycle(db_conn)
        updated = lifecycle.boost_frequent_patterns(min_hits=10)
        assert updated >= 1
        row = db_conn.execute(
            "SELECT confidence FROM command_patterns WHERE pattern = ?",
            ("(?i)popular pattern boost",),
        ).fetchone()
        assert row["confidence"] > 0.8

    def test_weekly_report_structure(self, db_conn):
        lifecycle = PatternLifecycle(db_conn)
        report = lifecycle.weekly_report()
        for key in (
            "total_patterns",
            "seed_patterns",
            "learned_patterns",
            "discovered_patterns",
            "zero_hit_patterns",
            "pruned_eligible",
        ):
            assert key in report


class TestNightlyEvolution:
    async def test_run_returns_stats_dict(self, db_conn):
        evolution = NightlyEvolution(db_conn)
        stats = await evolution.run()
        assert isinstance(stats, dict)
        for key in (
            "devices_discovered",
            "devices_removed",
            "patterns_generated",
            "patterns_learned",
            "patterns_pruned",
        ):
            assert key in stats

    async def test_log_run_writes_to_db(self, db_conn):
        evolution = NightlyEvolution(db_conn)
        await evolution.run()
        row = db_conn.execute("SELECT * FROM evolution_log").fetchone()
        assert row is not None
