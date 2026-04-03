"""Tests for the adaptive micro-acknowledgment engine."""

from __future__ import annotations

import pytest
from cortex.filler.micro_ack import (
    MicroAckEngine,
    POOL_CASUAL,
    POOL_REASSURING,
    POOL_COMPLEX,
    get_micro_ack_engine,
    _SETTINGS_KEY_EMA,
)


class TestEMAUpdates:
    """Exponential moving average tracks latency correctly."""

    def test_ema_increases_with_high_latency(self):
        engine = MicroAckEngine(ema_latency=2.0, alpha=0.3)
        engine.update_latency(10.0)
        # EMA = 0.3 * 10 + 0.7 * 2 = 4.4
        assert engine.ema_latency == pytest.approx(4.4)

    def test_ema_decreases_with_low_latency(self):
        engine = MicroAckEngine(ema_latency=5.0, alpha=0.3)
        engine.update_latency(1.0)
        # EMA = 0.3 * 1 + 0.7 * 5 = 3.8
        assert engine.ema_latency == pytest.approx(3.8)

    def test_ema_converges_over_many_updates(self):
        engine = MicroAckEngine(ema_latency=2.0, alpha=0.3)
        for _ in range(50):
            engine.update_latency(8.0)
        assert engine.ema_latency == pytest.approx(8.0, abs=0.01)

    def test_zero_latency_ignored(self):
        engine = MicroAckEngine(ema_latency=3.0)
        engine.update_latency(0)
        assert engine.ema_latency == 3.0

    def test_negative_latency_ignored(self):
        engine = MicroAckEngine(ema_latency=3.0)
        engine.update_latency(-1.0)
        assert engine.ema_latency == 3.0


class TestThresholdAdaptation:
    """Threshold adapts with latency and includes jitter."""

    def test_threshold_roughly_sixty_percent_of_ema(self):
        engine = MicroAckEngine(ema_latency=10.0)
        thresholds = [engine.get_threshold() for _ in range(100)]
        # base = 10 * 0.6 = 6.0, jitter ∈ [0, 2.0]
        assert all(6.0 <= t <= 8.0 for t in thresholds)

    def test_jitter_produces_variation(self):
        engine = MicroAckEngine(ema_latency=5.0)
        thresholds = {round(engine.get_threshold(), 4) for _ in range(50)}
        # Jitter should produce at least a few distinct values
        assert len(thresholds) >= 3

    def test_fast_system_has_lower_threshold(self):
        fast = MicroAckEngine(ema_latency=1.0)
        slow = MicroAckEngine(ema_latency=10.0)
        fast_avg = sum(fast.get_threshold() for _ in range(100)) / 100
        slow_avg = sum(slow.get_threshold() for _ in range(100)) / 100
        assert fast_avg < slow_avg


class TestShouldAck:
    """Decision logic respects filler, max-acks, and timing."""

    def test_suppressed_when_filler_played_first_ack(self):
        engine = MicroAckEngine(ema_latency=2.0)
        engine.reset()
        # Filler covers the first pause
        assert engine.should_ack(elapsed=5.0, filler_played=True) is False

    def test_second_ack_allowed_after_filler(self):
        engine = MicroAckEngine(ema_latency=2.0)
        engine.reset()
        # Simulate first ack already happened (e.g. manual bump)
        engine.ack_count = 1
        # Now elapsed is long enough and filler was played
        assert engine.should_ack(elapsed=5.0, filler_played=True) is True

    def test_fires_without_filler(self):
        engine = MicroAckEngine(ema_latency=2.0)
        engine.reset()
        # No filler → first ack should fire if elapsed is enough
        assert engine.should_ack(elapsed=5.0, filler_played=False) is True

    def test_max_two_acks_enforced(self):
        engine = MicroAckEngine(ema_latency=2.0)
        engine.reset()
        engine.ack_count = 2
        assert engine.should_ack(elapsed=100.0, filler_played=False) is False

    def test_not_fired_when_elapsed_too_short(self):
        engine = MicroAckEngine(ema_latency=10.0)
        engine.reset()
        # threshold ≈ 6-8s, elapsed=1s → no ack
        assert engine.should_ack(elapsed=1.0, filler_played=False) is False


class TestPhraseEscalation:
    """Phrase selection escalates from casual → reassuring."""

    def test_first_phrase_from_casual_pool(self):
        engine = MicroAckEngine()
        engine.reset()
        phrase = engine.get_phrase()
        assert phrase in POOL_CASUAL

    def test_second_phrase_from_reassuring_pool(self):
        engine = MicroAckEngine()
        engine.reset()
        engine.get_phrase()  # first → casual
        phrase = engine.get_phrase()  # second → reassuring
        assert phrase in POOL_REASSURING

    def test_complex_query_uses_complex_pool(self):
        engine = MicroAckEngine()
        engine.reset(is_complex=True)
        phrase = engine.get_phrase()
        assert phrase in POOL_COMPLEX

    def test_complex_second_ack_still_reassuring(self):
        engine = MicroAckEngine()
        engine.reset(is_complex=True)
        engine.get_phrase()  # first → complex
        phrase = engine.get_phrase()  # second → reassuring
        assert phrase in POOL_REASSURING

    def test_ack_count_increments(self):
        engine = MicroAckEngine()
        engine.reset()
        assert engine.ack_count == 0
        engine.get_phrase()
        assert engine.ack_count == 1
        engine.get_phrase()
        assert engine.ack_count == 2


class TestReset:
    """Reset clears state between generations."""

    def test_reset_clears_ack_count(self):
        engine = MicroAckEngine()
        engine.ack_count = 2
        engine.reset()
        assert engine.ack_count == 0

    def test_reset_clears_complex_flag(self):
        engine = MicroAckEngine()
        engine.reset(is_complex=True)
        assert engine.is_complex is True
        engine.reset()
        assert engine.is_complex is False

    def test_ema_preserved_across_reset(self):
        engine = MicroAckEngine(ema_latency=7.5)
        engine.reset()
        assert engine.ema_latency == 7.5


class TestLevel:
    """Escalation level property."""

    def test_level_starts_at_one(self):
        engine = MicroAckEngine()
        engine.reset()
        assert engine.level == 1

    def test_level_becomes_two_after_first_ack(self):
        engine = MicroAckEngine()
        engine.reset()
        engine.get_phrase()
        assert engine.level == 2

    def test_level_capped_at_two(self):
        engine = MicroAckEngine()
        engine.reset()
        engine.get_phrase()
        engine.get_phrase()
        assert engine.level == 2


class TestSingleton:
    """Module-level singleton works."""

    def test_singleton_returns_same_instance(self):
        a = get_micro_ack_engine()
        b = get_micro_ack_engine()
        assert a is b


class TestPersistence:
    """EMA save/load round-trips through a real SQLite DB."""

    def test_save_and_load_ema(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE system_settings "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP)"
        )
        engine = MicroAckEngine(ema_latency=4.2)
        engine.save_ema(conn)

        engine2 = MicroAckEngine(ema_latency=1.0)
        engine2.load_ema(conn)
        assert engine2.ema_latency == pytest.approx(4.2)

    def test_load_missing_key_keeps_default(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE system_settings "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP)"
        )
        engine = MicroAckEngine(ema_latency=9.9)
        engine.load_ema(conn)
        assert engine.ema_latency == 9.9

    def test_save_with_none_conn_is_noop(self):
        engine = MicroAckEngine(ema_latency=3.0)
        engine.save_ema(None)  # should not raise

    def test_load_with_none_conn_is_noop(self):
        engine = MicroAckEngine(ema_latency=3.0)
        engine.load_ema(None)
        assert engine.ema_latency == 3.0


class TestPhraseDedup:
    """Phrase selection avoids immediate repeats."""

    def test_variety_over_multiple_draws(self):
        engine = MicroAckEngine()
        phrases = set()
        for _ in range(10):
            engine.reset()
            phrases.add(engine.get_phrase())
        # Should get variety from the 5-phrase casual pool
        assert len(phrases) >= 2
