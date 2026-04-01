"""Tests for cortex.evolution.self_improve — the self-improvement loop."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex.db import init_db, set_db_path
from cortex.evolution.self_improve import (
    ImprovementResult,
    ImprovementStats,
    SelfImprover,
    TrainingPair,
    WeaknessReport,
    WeaknessType,
    _looks_like_rephrase,
)


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Give every test an isolated DB with full schema + improvement tables."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    init_db()
    yield


@pytest.fixture()
def db():
    """Return a raw connection to the test DB (after schema init)."""
    from cortex.db import get_db

    return get_db()


@pytest.fixture()
def improver(db):
    """SelfImprover backed by the test DB."""
    return SelfImprover(conn=db)


# ── helpers ──────────────────────────────────────────────────────


def _count(db: sqlite3.Connection, table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    row = db.execute(f"SELECT COUNT(*) FROM {table}{clause}").fetchone()
    return row[0]


async def _seed_paired_rows(improver: SelfImprover, n: int = 60) -> None:
    """Insert *n* paired improvement_log rows ready for training."""
    for i in range(n):
        row_id = await improver.record_interaction(
            query=f"What is topic {i}?",
            response="I don't know.",
            feedback=f"The answer to topic {i} is X.",
            domain="general",
        )
        # Ensure the pair is explicitly generated & stored
        await improver.generate_training_pair(
            query=f"What is topic {i}?",
            weak_response="I don't know.",
            corrected_response=f"The answer to topic {i} is X.",
        )


# ── record_interaction ───────────────────────────────────────────


class TestRecordInteraction:
    async def test_basic_recording(self, improver, db):
        row_id = await improver.record_interaction("hello", "Hi there!")
        assert isinstance(row_id, str)
        assert len(row_id) == 16
        assert _count(db, "improvement_log") == 1

    async def test_records_query_and_response(self, improver, db):
        await improver.record_interaction("What is Python?", "A programming language.")
        row = db.execute(
            "SELECT query, response FROM improvement_log"
        ).fetchone()
        assert row["query"] == "What is Python?"
        assert row["response"] == "A programming language."

    async def test_good_response_status_recorded(self, improver, db):
        await improver.record_interaction("hello", "Hi there!")
        row = db.execute("SELECT status FROM improvement_log").fetchone()
        assert row["status"] == "recorded"

    async def test_weak_response_status(self, improver, db):
        await improver.record_interaction(
            "Explain quantum computing in detail",
            "I'm not sure.",
        )
        row = db.execute("SELECT status FROM improvement_log").fetchone()
        assert row["status"] == "weak"

    async def test_feedback_creates_training_pair(self, improver, db):
        await improver.record_interaction(
            "What is 2+2?",
            "I'm not sure.",
            feedback="It's 4.",
        )
        row = db.execute(
            "SELECT training_pair FROM improvement_log"
        ).fetchone()
        assert row["training_pair"] is not None
        tp = json.loads(row["training_pair"])
        assert tp["corrected_response"] == "It's 4."

    async def test_thinking_escalation_flagged(self, improver, db):
        await improver.record_interaction(
            "Explain TCP/IP in depth",
            "It's a network thing.",
            thinking_escalated=True,
        )
        row = db.execute(
            "SELECT weakness_type, status FROM improvement_log"
        ).fetchone()
        assert "thinking_escalation" in row["weakness_type"]
        assert row["status"] == "weak"

    async def test_domain_stored(self, improver, db):
        await improver.record_interaction(
            "What's the weather?",
            "Sunny.",
            domain="weather",
        )
        row = db.execute("SELECT domain FROM improvement_log").fetchone()
        assert row["domain"] == "weather"

    async def test_multiple_interactions(self, improver, db):
        for i in range(5):
            await improver.record_interaction(f"q{i}", f"a{i}")
        assert _count(db, "improvement_log") == 5


# ── detect_weak_response ─────────────────────────────────────────


class TestDetectWeakResponse:
    async def test_good_response_not_weak(self, improver):
        report = await improver.detect_weak_response(
            "What is Python?",
            "Python is a high-level, interpreted programming language "
            "known for its clear syntax and versatility.",
        )
        assert report.is_weak is False
        assert report.weakness_types == []
        assert report.confidence > 0.8

    async def test_short_response_detected(self, improver):
        report = await improver.detect_weak_response(
            "Explain the theory of relativity in detail",
            "E=mc²",
        )
        assert report.is_weak is True
        assert WeaknessType.SHORT in report.weakness_types

    async def test_generic_phrase_detected(self, improver):
        report = await improver.detect_weak_response(
            "How do I fix this error?",
            "I'm not sure how to help with that. As an AI, I have limitations.",
        )
        assert report.is_weak is True
        assert WeaknessType.GENERIC in report.weakness_types

    async def test_hedging_detected(self, improver):
        report = await improver.detect_weak_response(
            "Is Python faster than C?",
            "I think maybe Python is possibly slower, but I believe "
            "it might depend on the use case.",
        )
        assert report.is_weak is True
        assert WeaknessType.HEDGING in report.weakness_types

    async def test_thinking_escalation_signal(self, improver):
        report = await improver.detect_weak_response(
            "Explain TCP",
            "TCP is a protocol.",
            thinking_escalated=True,
        )
        assert report.is_weak is True
        assert WeaknessType.THINKING_ESCALATION in report.weakness_types
        assert report.confidence <= 0.4

    async def test_user_correction_signal(self, improver):
        report = await improver.detect_weak_response(
            "What is the capital of France?",
            "Berlin.",
            feedback="No, it's Paris.",
        )
        assert report.is_weak is True
        assert WeaknessType.USER_CORRECTION in report.weakness_types
        assert report.confidence <= 0.2

    async def test_rephrase_detected(self, improver):
        report = await improver.detect_weak_response(
            "What's a good recipe?",
            "Can you be more specific?",
            feedback="I meant a good pasta recipe.",
        )
        assert report.is_weak is True
        assert WeaknessType.USER_REPHRASE in report.weakness_types

    async def test_multiple_weakness_types(self, improver):
        report = await improver.detect_weak_response(
            "How do neural networks learn?",
            "I'm not sure.",
            thinking_escalated=True,
        )
        assert report.is_weak is True
        assert len(report.weakness_types) >= 2

    async def test_confidence_clamps_to_zero(self, improver):
        report = await improver.detect_weak_response(
            "Explain everything about quantum physics in great detail",
            "I don't know. I'm not sure. I can't help. Sorry, I don't have that info.",
            feedback="That's not what I asked.",
            thinking_escalated=True,
        )
        assert report.confidence >= 0.0

    async def test_empty_response_detected(self, improver):
        report = await improver.detect_weak_response(
            "Tell me a joke",
            "",
        )
        assert report.is_weak is True


# ── generate_training_pair ───────────────────────────────────────


class TestGenerateTrainingPair:
    async def test_creates_pair(self, improver):
        pair = await improver.generate_training_pair(
            "What is 2+2?",
            "I'm not sure.",
            "It's 4.",
        )
        assert isinstance(pair, TrainingPair)
        assert pair.query == "What is 2+2?"
        assert pair.corrected_response == "It's 4."
        assert pair.weak_response == "I'm not sure."
        assert len(pair.id) == 16
        assert pair.created_at != ""

    async def test_strips_whitespace(self, improver):
        pair = await improver.generate_training_pair(
            "  What is 2+2?  ",
            "  dunno  ",
            "  It's 4.  ",
        )
        assert pair.query == "What is 2+2?"
        assert pair.corrected_response == "It's 4."
        assert pair.weak_response == "dunno"

    async def test_includes_weakness_type(self, improver):
        pair = await improver.generate_training_pair(
            "Explain polymorphism in OOP",
            "I don't know.",
            "Polymorphism allows objects of different types to be "
            "treated through a uniform interface.",
        )
        assert pair.weakness_type != ""

    async def test_custom_domain(self, improver):
        pair = await improver.generate_training_pair(
            "How do I sort a list?",
            "Use sort.",
            "Use list.sort() for in-place or sorted() for a new list.",
            domain="coding",
        )
        assert pair.domain == "coding"

    async def test_persists_to_db(self, improver, db):
        # Record a weak interaction first
        await improver.record_interaction(
            "What is AI?",
            "I don't know.",
        )
        # Now generate the pair (updates the row)
        await improver.generate_training_pair(
            "What is AI?",
            "I don't know.",
            "AI is artificial intelligence.",
        )
        row = db.execute(
            "SELECT training_pair, status FROM improvement_log "
            "WHERE query = 'What is AI?'"
        ).fetchone()
        assert row is not None
        assert row["training_pair"] is not None
        assert row["status"] == "paired"


# ── get_training_candidates ──────────────────────────────────────


class TestGetTrainingCandidates:
    async def test_returns_empty_below_threshold(self, improver):
        # Only insert a few pairs
        for i in range(5):
            await improver.record_interaction(
                f"q{i}",
                "I don't know.",
                feedback=f"Answer is {i}.",
            )
            await improver.generate_training_pair(
                f"q{i}",
                "I don't know.",
                f"Answer is {i}.",
            )
        result = await improver.get_training_candidates(min_count=50)
        assert result == []

    async def test_returns_pairs_above_threshold(self, improver):
        await _seed_paired_rows(improver, n=60)
        result = await improver.get_training_candidates(min_count=50)
        assert len(result) >= 50
        assert all(isinstance(p, TrainingPair) for p in result)

    async def test_custom_threshold(self, improver):
        await _seed_paired_rows(improver, n=10)
        result = await improver.get_training_candidates(min_count=5)
        assert len(result) >= 5

    async def test_pairs_have_content(self, improver):
        await _seed_paired_rows(improver, n=10)
        pairs = await improver.get_training_candidates(min_count=5)
        for p in pairs:
            assert p.query != ""
            assert p.corrected_response != ""
            assert p.id != ""


# ── trigger_improvement ──────────────────────────────────────────


class TestTriggerImprovement:
    async def test_fails_without_enough_pairs(self, improver):
        result = await improver.trigger_improvement()
        assert isinstance(result, ImprovementResult)
        assert result.success is False
        assert "Not enough" in result.message

    async def test_succeeds_with_enough_pairs(self, improver, db):
        await _seed_paired_rows(improver, n=60)
        result = await improver.trigger_improvement()
        assert result.success is True
        assert result.run_id > 0
        assert result.pairs_used > 0
        assert "Improvement run" in result.message

    async def test_creates_evolution_run(self, improver, db):
        await _seed_paired_rows(improver, n=60)
        result = await improver.trigger_improvement()
        row = db.execute(
            "SELECT * FROM evolution_runs WHERE id = ?",
            (result.run_id,),
        ).fetchone()
        assert row is not None
        assert row["run_type"] == "self_improve"

    async def test_marks_pairs_consumed(self, improver, db):
        await _seed_paired_rows(improver, n=60)
        await improver.trigger_improvement()
        consumed = db.execute(
            "SELECT COUNT(*) FROM improvement_log WHERE status = 'consumed'"
        ).fetchone()[0]
        assert consumed >= 50

    async def test_consumed_pairs_not_reused(self, improver):
        await _seed_paired_rows(improver, n=60)
        await improver.trigger_improvement()
        # Second call should fail — all pairs consumed
        result = await improver.trigger_improvement()
        assert result.success is False


# ── get_improvement_stats ────────────────────────────────────────


class TestGetImprovementStats:
    def test_empty_stats(self, improver):
        stats = improver.get_improvement_stats()
        assert isinstance(stats, ImprovementStats)
        assert stats.total_interactions == 0
        assert stats.weak_responses == 0
        assert stats.corrections == 0
        assert stats.training_pairs_ready == 0

    async def test_counts_interactions(self, improver):
        await improver.record_interaction("q1", "a1")
        await improver.record_interaction("q2", "a2")
        stats = improver.get_improvement_stats()
        assert stats.total_interactions == 2

    async def test_counts_weak_responses(self, improver):
        await improver.record_interaction("What is AI?", "I don't know.")
        await improver.record_interaction("hello", "Hi there!")
        stats = improver.get_improvement_stats()
        assert stats.weak_responses >= 1

    async def test_counts_corrections(self, improver):
        await improver.record_interaction("q", "bad", feedback="good")
        stats = improver.get_improvement_stats()
        assert stats.corrections == 1

    async def test_counts_training_pairs(self, improver):
        await _seed_paired_rows(improver, n=10)
        stats = improver.get_improvement_stats()
        assert stats.training_pairs_ready >= 5

    async def test_counts_improvement_runs(self, improver):
        await _seed_paired_rows(improver, n=60)
        await improver.trigger_improvement()
        stats = improver.get_improvement_stats()
        assert stats.improvements_attempted >= 1


# ── _looks_like_rephrase helper ──────────────────────────────────


class TestLooksLikeRephrase:
    def test_rephrase_detected(self):
        assert _looks_like_rephrase("q", "I meant a pasta recipe") is True
        assert _looks_like_rephrase("q", "let me rephrase that") is True
        assert _looks_like_rephrase("q", "No, I said something else") is True
        assert _looks_like_rephrase("q", "can you try again please") is True
        assert _looks_like_rephrase("q", "that's not what I asked") is True

    def test_not_rephrase(self):
        assert _looks_like_rephrase("q", "thanks") is False
        assert _looks_like_rephrase("q", "great answer") is False
        assert _looks_like_rephrase("q", None) is False
        assert _looks_like_rephrase("q", "") is False


# ── WeaknessReport dataclass ─────────────────────────────────────


class TestWeaknessReportDataclass:
    def test_defaults(self):
        r = WeaknessReport(is_weak=False)
        assert r.weakness_types == []
        assert r.confidence == 1.0
        assert r.details == ""

    def test_with_types(self):
        r = WeaknessReport(
            is_weak=True,
            weakness_types=[WeaknessType.SHORT, WeaknessType.HEDGING],
            confidence=0.3,
            details="short; hedging",
        )
        assert len(r.weakness_types) == 2
        assert r.confidence == 0.3


# ── TrainingPair dataclass ───────────────────────────────────────


class TestTrainingPairDataclass:
    def test_defaults(self):
        p = TrainingPair()
        assert p.id == ""
        assert p.query == ""
        assert p.domain == "general"

    def test_with_values(self):
        p = TrainingPair(
            id="abc123",
            query="What is AI?",
            corrected_response="Artificial Intelligence.",
            domain="tech",
        )
        assert p.id == "abc123"
        assert p.domain == "tech"


# ── ImprovementStats dataclass ───────────────────────────────────


class TestImprovementStatsDataclass:
    def test_defaults(self):
        s = ImprovementStats()
        assert s.total_interactions == 0
        assert s.improvements_accepted == 0


# ── ImprovementResult dataclass ──────────────────────────────────


class TestImprovementResultDataclass:
    def test_defaults(self):
        r = ImprovementResult()
        assert r.success is False
        assert r.run_id == 0
        assert r.message == ""

    def test_success(self):
        r = ImprovementResult(success=True, run_id=42, message="ok")
        assert r.success is True
        assert r.run_id == 42


# ── edge cases ───────────────────────────────────────────────────


class TestEdgeCases:
    async def test_unicode_query(self, improver, db):
        row_id = await improver.record_interaction(
            "什么是人工智能？",
            "人工智能是计算机科学的一个分支。",
        )
        assert len(row_id) == 16
        assert _count(db, "improvement_log") == 1

    async def test_very_long_response(self, improver, db):
        long_resp = "A" * 10_000
        row_id = await improver.record_interaction("q", long_resp)
        row = db.execute(
            "SELECT response FROM improvement_log WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert len(row["response"]) == 10_000

    async def test_empty_query(self, improver):
        report = await improver.detect_weak_response("", "some response")
        assert isinstance(report, WeaknessReport)

    async def test_concurrent_recording(self, improver, db):
        """Multiple rapid recordings don't collide."""
        import asyncio

        tasks = [
            improver.record_interaction(f"q{i}", f"a{i}")
            for i in range(20)
        ]
        ids = await asyncio.gather(*tasks)
        assert len(set(ids)) == 20
        assert _count(db, "improvement_log") == 20
