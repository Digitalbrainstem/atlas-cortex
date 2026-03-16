"""Tests for CE-4: Conversational Pause & Pivot.

Covers the interrupt classifier and the remaining-text extraction helper.
"""
from __future__ import annotations

import pytest

from cortex.orchestrator.interrupt import classify_interrupt
from cortex.orchestrator.voice import _get_remaining_text


# ── classify_interrupt ────────────────────────────────────────────


class TestClassifyInterruptStop:
    """Stop patterns — user wants Atlas to be quiet."""

    @pytest.mark.parametrize("text", [
        "stop",
        "Stop",
        "STOP",
        "okay stop",
        "ok stop",
        "atlas stop",
        "nevermind",
        "never mind",
        "shut up",
        "be quiet",
        "quiet",
        "hush",
        "that's enough",
        "thats enough",
        "enough",
        "cancel",
        "forget it",
        "no more",
        "stop talking",
        "please stop",
        "Stop.",
        "Enough!",
    ])
    def test_stop_patterns(self, text: str) -> None:
        assert classify_interrupt(text) == "stop"

    def test_empty_string_is_stop(self) -> None:
        assert classify_interrupt("") == "stop"

    def test_whitespace_only_is_stop(self) -> None:
        assert classify_interrupt("   ") == "stop"


class TestClassifyInterruptResume:
    """Resume patterns — user wants Atlas to continue."""

    @pytest.mark.parametrize("text", [
        "go on",
        "continue",
        "keep going",
        "go ahead",
        "uh huh",
        "uh-huh",
        "uhhuh",
        "mm hmm",
        "mm-hmm",
        "mmhmm",
        "yeah",
        "yes",
        "yep",
        "yup",
        "okay",
        "ok",
        "what else",
        "and then",
        "and then?",
        "finish",
        "carry on",
        "keep talking",
        "you were saying",
        "go on please",
        "what were you saying",
        "please continue",
        "Continue.",
        "Go on",
        "OK",
    ])
    def test_resume_patterns(self, text: str) -> None:
        assert classify_interrupt(text) == "resume"


class TestClassifyInterruptPivot:
    """Pivot — user is asking something new (default)."""

    @pytest.mark.parametrize("text", [
        "what time is it",
        "actually, what time is it?",
        "turn off the lights",
        "tell me a joke",
        "who is the president",
        "stop the music",  # "stop the music" has extra words → pivot, not stop
        "can you continue with something else",  # too long for resume
        "hey atlas what's the weather",
    ])
    def test_pivot_patterns(self, text: str) -> None:
        assert classify_interrupt(text) == "pivot"


# ── _get_remaining_text ──────────────────────────────────────────


class TestGetRemainingText:
    """Test sentence-based remaining text extraction."""

    def test_basic_remaining(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        # Position in the middle of the first sentence
        remaining = _get_remaining_text(text, 10)
        assert "Second sentence" in remaining
        assert "Third sentence" in remaining
        assert "First sentence" not in remaining

    def test_position_at_start(self) -> None:
        text = "Hello there. How are you. I am fine."
        remaining = _get_remaining_text(text, 0)
        assert "How are you" in remaining
        assert "I am fine" in remaining

    def test_position_past_end(self) -> None:
        text = "Only one sentence here."
        remaining = _get_remaining_text(text, 9999)
        assert remaining == ""

    def test_empty_text(self) -> None:
        remaining = _get_remaining_text("", 0)
        assert remaining == ""

    def test_single_sentence(self) -> None:
        text = "Just one sentence."
        remaining = _get_remaining_text(text, 0)
        # The first sentence is the one being spoken, so nothing remains
        assert remaining == ""

    def test_position_at_second_sentence(self) -> None:
        text = "First part. Second part. Third part."
        # Position well into the second sentence
        remaining = _get_remaining_text(text, 20)
        assert "Third part" in remaining
        assert "First part" not in remaining
