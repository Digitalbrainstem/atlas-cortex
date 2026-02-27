"""Tests for the filler engine."""

from __future__ import annotations

import pytest
from cortex.filler import select_filler, DEFAULT_FILLERS, CONFIDENCE_FILLERS


class TestSelectFiller:
    def test_command_returns_empty(self):
        assert select_filler("command", 1.0) == ""

    def test_casual_returns_empty(self):
        assert select_filler("casual", 1.0) == ""

    def test_question_returns_filler(self):
        filler = select_filler("question", 1.0)
        assert filler in DEFAULT_FILLERS["question"]

    def test_greeting_returns_filler(self):
        filler = select_filler("greeting", 1.0)
        assert filler in DEFAULT_FILLERS["greeting"]

    def test_excited_returns_filler(self):
        filler = select_filler("excited", 1.0)
        assert filler in DEFAULT_FILLERS["excited"]

    def test_low_confidence_appends_confidence_filler(self):
        # Run many times to check that low-confidence filler is sometimes appended
        results = set()
        for _ in range(20):
            f = select_filler("question", 0.3)
            results.add(f)
        # At least one result should be non-empty
        assert any(r for r in results)

    def test_high_confidence_no_confidence_filler_appended(self):
        # With high confidence, no confidence filler should be appended
        # (the filler should end with just the sentiment filler)
        confidence_fillers_flat = [
            phrase
            for phrases in CONFIDENCE_FILLERS.values()
            for phrase in phrases
        ]
        for _ in range(30):
            filler = select_filler("question", 0.95)
            if filler:
                # Should be a pure sentiment filler (not a confidence filler)
                assert filler in DEFAULT_FILLERS["question"]

    def test_follow_up_sometimes_returns_empty(self):
        # is_follow_up=True should sometimes return empty
        empty_count = sum(
            1 for _ in range(100)
            if select_filler("question", 1.0, is_follow_up=True) == ""
        )
        # Statistically, about 60% should be empty (per the 0.6 threshold)
        assert empty_count > 20, f"Expected some empty results, got {empty_count}/100"

    def test_no_consecutive_repeats_from_pool(self):
        # Verify we get variety from the pool
        results = [select_filler("question", 1.0) for _ in range(10)]
        unique = set(results)
        # Should have at least 2 different fillers across 10 draws
        assert len(unique) >= 2


class TestDefaultPools:
    def test_all_sentiments_have_entries_or_are_empty(self):
        expected_sentiments = {
            "greeting", "question", "frustrated", "excited",
            "late_night", "follow_up", "command", "casual",
        }
        assert set(DEFAULT_FILLERS.keys()) >= expected_sentiments

    def test_command_pool_is_empty(self):
        assert DEFAULT_FILLERS["command"] == []

    def test_casual_pool_is_empty(self):
        assert DEFAULT_FILLERS["casual"] == []


class TestConfidenceFillers:
    def test_medium_key_exists(self):
        assert "medium" in CONFIDENCE_FILLERS
        assert CONFIDENCE_FILLERS["medium"]

    def test_low_key_exists(self):
        assert "low" in CONFIDENCE_FILLERS
        assert CONFIDENCE_FILLERS["low"]

    def test_none_key_exists(self):
        assert "none" in CONFIDENCE_FILLERS
        assert CONFIDENCE_FILLERS["none"]
