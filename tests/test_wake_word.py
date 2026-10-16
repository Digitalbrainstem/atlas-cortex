"""Tests for cortex.wake_word — server-side transcript wake word filtering."""

from __future__ import annotations

import pytest

from cortex import wake_word


# ── Helpers ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_wake_word():
    """Ensure default state before each test."""
    wake_word.set_enabled(True)
    wake_word.set_keywords({"atlas", "atmos", "alice"})
    yield
    wake_word.set_enabled(True)
    wake_word.set_keywords({"atlas", "atmos", "alice"})


# ── check_wake_word ──────────────────────────────────────────────

class TestCheckWakeWord:
    def test_simple_atlas(self):
        found, cleaned = wake_word.check_wake_word("Atlas what time is it")
        assert found is True
        assert cleaned == "what time is it"

    def test_hey_atlas(self):
        found, cleaned = wake_word.check_wake_word("Hey Atlas turn off the lights")
        assert found is True
        assert cleaned == "turn off the lights"

    def test_ok_atlas(self):
        found, cleaned = wake_word.check_wake_word("OK Atlas play some music")
        assert found is True
        assert cleaned == "play some music"

    def test_atmos_keyword(self):
        found, cleaned = wake_word.check_wake_word("atmos what is the weather")
        assert found is True
        assert cleaned == "what is the weather"

    def test_alice_keyword(self):
        found, cleaned = wake_word.check_wake_word("Alice set a timer")
        assert found is True
        assert cleaned == "set a timer"

    def test_no_wake_word(self):
        found, cleaned = wake_word.check_wake_word("what time is it")
        assert found is False
        assert cleaned == "what time is it"

    def test_embedded_wake_word(self):
        found, cleaned = wake_word.check_wake_word("tell atlas to stop")
        assert found is True
        assert "to stop" in cleaned

    def test_empty_input(self):
        found, cleaned = wake_word.check_wake_word("")
        assert found is False
        assert cleaned == ""

    def test_wake_word_only(self):
        found, cleaned = wake_word.check_wake_word("Atlas")
        assert found is True
        assert cleaned == ""

    def test_case_insensitive(self):
        found, cleaned = wake_word.check_wake_word("ATLAS what time is it")
        assert found is True
        assert cleaned.lower() == "what time is it"

    def test_hey_atlas_case(self):
        found, cleaned = wake_word.check_wake_word("HEY ATLAS lights on")
        assert found is True
        assert cleaned.lower() == "lights on"


# ── filter_transcript ────────────────────────────────────────────

class TestFilterTranscript:
    def test_enabled_with_wake_word(self):
        result = wake_word.filter_transcript("Atlas what time is it")
        assert result == "what time is it"

    def test_enabled_no_wake_word_drops(self):
        result = wake_word.filter_transcript("what time is it")
        assert result is None

    def test_enabled_wake_word_only_drops(self):
        result = wake_word.filter_transcript("Hey Atlas")
        assert result is None

    def test_disabled_passes_through(self):
        wake_word.set_enabled(False)
        result = wake_word.filter_transcript("what time is it")
        assert result == "what time is it"

    def test_disabled_still_passes_wake_word(self):
        wake_word.set_enabled(False)
        result = wake_word.filter_transcript("Atlas what time is it")
        assert result == "Atlas what time is it"


# ── Configuration ────────────────────────────────────────────────

class TestConfiguration:
    def test_is_enabled_default(self):
        assert wake_word.is_enabled() is True

    def test_set_enabled(self):
        wake_word.set_enabled(False)
        assert wake_word.is_enabled() is False
        wake_word.set_enabled(True)
        assert wake_word.is_enabled() is True

    def test_get_keywords(self):
        kw = wake_word.get_keywords()
        assert "atlas" in kw
        assert "atmos" in kw
        assert "alice" in kw

    def test_set_keywords(self):
        wake_word.set_keywords({"jarvis", "computer"})
        assert wake_word.get_keywords() == {"jarvis", "computer"}
        found, _ = wake_word.check_wake_word("jarvis lights on")
        assert found is True
        found, _ = wake_word.check_wake_word("atlas lights on")
        assert found is False

    def test_set_keywords_strips_whitespace(self):
        wake_word.set_keywords({"  jarvis  ", " computer "})
        assert wake_word.get_keywords() == {"jarvis", "computer"}

    def test_set_keywords_ignores_empty(self):
        wake_word.set_keywords({"jarvis", "", "  "})
        assert wake_word.get_keywords() == {"jarvis"}


# ── Satellite config default ─────────────────────────────────────

class TestSatelliteConfigDefault:
    def test_wake_word_enabled_by_default(self):
        from satellite.atlas_satellite.config import SatelliteConfig
        cfg = SatelliteConfig()
        assert cfg.wake_word_enabled is True
