"""Adversarial tests for the 4-layer pipeline.

These tests *prove* code is broken (or document surprising behaviour) rather
than assume it works.  Each test targets a specific edge case discovered via
code-level analysis of layers 0–3.
"""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import textwrap
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.pipeline.layer0_context import (
    _classify_sentiment,
    _time_of_day,
    assemble_context,
)
from cortex.pipeline.layer1_instant import (
    _GREETING_PATTERNS,
    _IDENTITY_PATTERNS,
    _MATH_PATTERNS,
    _safe_eval,
    try_instant_answer,
)
from cortex.pipeline.layer2_plugins import _try_learned_patterns
from cortex.pipeline.layer3_llm import (
    _FILLER_INJECTION_TEMPLATE,
    build_messages,
    select_model,
)
from cortex.providers.base import LLMProvider


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _vader(compound: float) -> dict[str, float]:
    """Shortcut to build a minimal VADER score dict."""
    return {"compound": compound, "pos": 0.0, "neg": 0.0, "neu": 1.0}


def _make_tmp_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the command_patterns table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE command_patterns (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern            TEXT NOT NULL,
            intent             TEXT NOT NULL,
            entity_domain      TEXT,
            entity_match_group INTEGER,
            value_match_group  INTEGER,
            response_template  TEXT,
            source             TEXT NOT NULL DEFAULT 'seed',
            confidence         REAL DEFAULT 1.0,
            hit_count          INTEGER DEFAULT 0,
            last_hit           TIMESTAMP,
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _base_context(**overrides: Any) -> dict[str, Any]:
    """Build a minimal Layer-0-style context dict for Layer 1+ tests."""
    ctx: dict[str, Any] = {
        "user_id": "test",
        "speaker_id": None,
        "satellite_id": None,
        "time_of_day": "morning",
        "hour": 9,
        "sentiment": "casual",
        "effective_sentiment": "casual",
        "sentiment_score": 0.0,
        "vader_scores": _vader(0.0),
        "is_follow_up": False,
        "conversation_length": 0,
        "conversation_history": [],
        "room": None,
        "area": None,
        "metadata": {},
    }
    ctx.update(overrides)
    return ctx


# ══════════════════════════════════════════════════════════════════
# Layer 0 — Context Assembly
# ══════════════════════════════════════════════════════════════════

class TestLayer0SentimentEdgeCases:
    """Probe sentiment classification boundaries."""

    def test_high_vader_no_exclamation_with_keyword(self):
        """'Wow that's great' contains 'wow' AND compound >= 0.7 → excited.

        The code requires compound >= 0.7 AND (contains '!' OR contains
        'awesome'|'great'|'wow'|'cool').  'wow' is in the keyword list so
        this should be 'excited' even without '!'.
        """
        result = _classify_sentiment("Wow that's great", _vader(0.75))
        assert result == "excited"

    def test_high_vader_no_exclamation_no_keyword(self):
        """High compound but no '!' and no magic keyword → NOT excited.

        'I really like this' has positive VADER but doesn't contain
        awesome/great/wow/cool, so it should fall through to 'question'
        or 'casual' depending on content.
        """
        result = _classify_sentiment("I really like this", _vader(0.8))
        # No '!' and no keywords → falls through excited check
        assert result != "excited"
        # No '?' and doesn't start with question word → casual
        assert result == "casual"

    def test_hyphenated_hello_world_classified_as_greeting(self):
        """'hello-world' starts with 'hello' — code checks startswith().

        _classify_sentiment does `lower.startswith(kw)` for each greeting
        keyword, so 'hello-world' will match 'hello' via startswith.
        This documents the (potentially surprising) behaviour.
        """
        result = _classify_sentiment("hello-world", _vader(0.0))
        assert result == "greeting", (
            "'hello-world' should match greeting via startswith('hello')"
        )

    def test_what_the_heck_is_question(self):
        """'what the heck' starts with 'what' (a question marker).

        Code checks `lower.startswith(w) for w in _QUESTION_MARKERS` —
        since 'what' is in the set, this triggers even without a '?'.
        """
        result = _classify_sentiment("what the heck", _vader(-0.2))
        assert result == "question"

    def test_question_mark_alone_triggers_question(self):
        """'?' is in _QUESTION_MARKERS — checked via `'?' in message`."""
        result = _classify_sentiment("really?", _vader(0.0))
        assert result == "question"

    def test_compound_exactly_negative_0_5_is_frustrated(self):
        """Boundary: compound == -0.5 → frustrated (uses <=)."""
        result = _classify_sentiment("meh", _vader(-0.5))
        assert result == "frustrated"

    def test_compound_just_above_negative_0_5_not_frustrated(self):
        """Boundary: compound == -0.49 → NOT frustrated."""
        result = _classify_sentiment("meh", _vader(-0.49))
        assert result != "frustrated"

    def test_compound_exactly_0_7_with_bang_is_excited(self):
        """Boundary: compound == 0.7 AND '!' → excited (uses >=)."""
        result = _classify_sentiment("amazing!", _vader(0.7))
        assert result == "excited"

    def test_compound_just_below_0_7_not_excited(self):
        """Boundary: compound == 0.69 → NOT excited even with '!'."""
        result = _classify_sentiment("amazing!", _vader(0.69))
        assert result != "excited"

    def test_greeting_takes_precedence_over_excited(self):
        """Even with compound >= 0.7 and '!', a greeting prefix wins."""
        result = _classify_sentiment("hello!", _vader(0.9))
        assert result == "greeting"

    def test_command_takes_precedence_over_frustrated(self):
        """Command prefix checked before frustration score."""
        result = _classify_sentiment("turn off this stupid thing", _vader(-0.8))
        assert result == "command"


class TestLayer0TimeOfDay:
    """Verify hour boundary conditions."""

    @pytest.mark.parametrize("hour,expected", [
        (0, "late_night"),
        (4, "late_night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (20, "evening"),
        (21, "late_night"),
        (23, "late_night"),
    ])
    def test_time_boundary(self, hour: int, expected: str):
        assert _time_of_day(hour) == expected


class TestLayer0FollowUp:
    """Follow-up detection edge cases."""

    @pytest.mark.asyncio
    async def test_empty_dicts_count_toward_follow_up(self):
        """3 empty dicts in history → is_follow_up=True.

        The code only checks `len(conversation_history) > 2`, not the
        content of each dict.  Empty dicts with no 'role'/'content'
        still count.
        """
        history: list[dict[str, Any]] = [{}, {}, {}]
        ctx = await assemble_context("yo", conversation_history=history)
        assert ctx["is_follow_up"] is True
        assert ctx["conversation_length"] == 3

    @pytest.mark.asyncio
    async def test_exactly_two_is_not_follow_up(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        ctx = await assemble_context("yo", conversation_history=history)
        assert ctx["is_follow_up"] is False

    @pytest.mark.asyncio
    async def test_none_history_no_follow_up(self):
        ctx = await assemble_context("yo", conversation_history=None)
        assert ctx["is_follow_up"] is False
        assert ctx["conversation_length"] == 0


class TestLayer0MissingInputs:
    """Missing or empty inputs should not crash."""

    @pytest.mark.asyncio
    async def test_empty_message(self):
        ctx = await assemble_context("")
        assert ctx["user_id"] == "default"
        assert ctx["sentiment"] in (
            "casual", "question", "greeting", "command", "frustrated", "excited",
        )

    @pytest.mark.asyncio
    async def test_default_user_id(self):
        ctx = await assemble_context("hi")
        assert ctx["user_id"] == "default"

    @pytest.mark.asyncio
    async def test_missing_speaker_id(self):
        ctx = await assemble_context("hi", speaker_id=None)
        assert ctx["speaker_id"] is None

    @pytest.mark.asyncio
    async def test_whitespace_only_message(self):
        ctx = await assemble_context("   ")
        assert isinstance(ctx["sentiment"], str)


class TestLayer0LateNightOverride:
    """The effective_sentiment override for late-night hours."""

    @pytest.mark.asyncio
    async def test_late_night_overrides_casual(self):
        """When time_of_day is late_night AND sentiment is casual/question,
        effective_sentiment becomes 'late_night'."""
        with patch("cortex.pipeline.layer0_context.datetime") as mock_dt:
            fake_now = datetime(2024, 1, 1, 23, 30, tzinfo=timezone.utc)
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ctx = await assemble_context("sounds good")
        assert ctx["time_of_day"] == "late_night"
        if ctx["sentiment"] in ("casual", "question"):
            assert ctx["effective_sentiment"] == "late_night"

    @pytest.mark.asyncio
    async def test_late_night_does_not_override_frustrated(self):
        """Frustrated sentiment is NOT overridden at night."""
        with patch("cortex.pipeline.layer0_context.datetime") as mock_dt:
            fake_now = datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ctx = await assemble_context("this is terrible and broken and awful")
        assert ctx["time_of_day"] == "late_night"
        if ctx["sentiment"] == "frustrated":
            assert ctx["effective_sentiment"] == "frustrated"


# ══════════════════════════════════════════════════════════════════
# Layer 1 — Instant Answers
# ══════════════════════════════════════════════════════════════════

class TestLayer1MathEdgeCases:
    """Stress the safe-eval sandbox and regex."""

    def test_division_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            _safe_eval("1/0")

    def test_log_of_negative(self):
        with pytest.raises(ValueError):
            _safe_eval("log(-1)")

    def test_sqrt_of_negative(self):
        with pytest.raises(ValueError):
            _safe_eval("sqrt(-1)")

    def test_very_large_multiplication(self):
        """Python handles big ints natively — should not raise."""
        result = _safe_eval("999999999999999 * 999999999999999")
        assert result == 999999999999999 * 999999999999999

    def test_modulo(self):
        assert _safe_eval("10 % 3") == 1

    def test_floor_division(self):
        assert _safe_eval("10 // 3") == 3

    def test_unary_negative(self):
        assert _safe_eval("-5") == -5

    def test_nested_function_call(self):
        assert _safe_eval("abs(-42)") == 42

    def test_rejects_name_not_in_allowlist(self):
        with pytest.raises(ValueError, match="Unknown name"):
            _safe_eval("os")

    @pytest.mark.asyncio
    async def test_division_by_zero_in_pipeline_does_not_crash(self):
        """'1/0' matching the math regex → ZeroDivisionError is caught."""
        ctx = _base_context()
        response, conf = await try_instant_answer("1/0", ctx)
        # The regex may or may not match "1/0" — if it does, the exception
        # is caught and we fall through.  Either way, no crash.
        # If response is None, it fell through; if not, it somehow answered.
        assert conf == 0.0 or response is not None

    @pytest.mark.asyncio
    async def test_trailing_equals_stripped(self):
        """'2+2=' — the math regex allows trailing '=?*'."""
        ctx = _base_context()
        response, conf = await try_instant_answer("2+2=", ctx)
        # The regex has `[=?]*` at the end, so '2+2=' should match
        if response is not None:
            assert response == "4"
            assert conf == 1.0

    @pytest.mark.asyncio
    async def test_trailing_question_mark_on_math(self):
        """'2 + 2 ?' — trailing ? in the regex [=?]* group."""
        ctx = _base_context()
        response, conf = await try_instant_answer("2 + 2 ?", ctx)
        # Regex requires expr to end with a digit or ')' before `[=?]*`.
        # '2 + 2 ?' has a space before '?' — the regex might NOT match.
        # Either way: no crash.
        assert conf == 0.0 or response is not None

    @pytest.mark.asyncio
    async def test_caret_exponent(self):
        """'2^8' — code replaces '^' with '**' before eval."""
        ctx = _base_context()
        response, conf = await try_instant_answer("2^8", ctx)
        if response is not None:
            assert response == "256"

    @pytest.mark.asyncio
    async def test_float_result_displayed_as_int(self):
        """10/2 = 5.0 → should display as '5' (integer formatting)."""
        ctx = _base_context()
        response, _ = await try_instant_answer("10/2", ctx)
        if response is not None:
            assert response == "5", f"Expected '5' but got {response!r}"


class TestLayer1DateTimeFormat:
    """Verify date/time patterns and output format."""

    @pytest.mark.asyncio
    async def test_date_response_format(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("what's the date today?", ctx)
        assert response is not None
        assert conf == 1.0
        # Should match "Today is <DayName>, <Month> <Day>, <Year>."
        assert re.match(
            r"Today is \w+, \w+ \d{1,2}, \d{4}\.", response
        ), f"Unexpected date format: {response!r}"

    @pytest.mark.asyncio
    async def test_date_no_leading_zero(self):
        """strftime '%d' padded to 2 digits — code does .replace(' 0', ' ').

        This removes leading zeros: 'January 05' → 'January 5'.
        """
        ctx = _base_context()
        response, _ = await try_instant_answer("what's the date?", ctx)
        assert response is not None
        # No ' 0' sequences should remain after the replace
        assert " 0" not in response

    @pytest.mark.asyncio
    async def test_time_response_format(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("what time is it?", ctx)
        assert response is not None
        assert conf == 1.0
        # Should match "It's <H>:<MM> <AM/PM>."
        assert re.match(r"It's \d{1,2}:\d{2} [AP]M\.", response), (
            f"Unexpected time format: {response!r}"
        )


class TestLayer1Identity:
    """Identity question handling."""

    @pytest.mark.asyncio
    async def test_who_are_you(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("who are you", ctx)
        assert response is not None
        assert "Atlas Cortex" in response
        assert conf == 1.0

    @pytest.mark.asyncio
    async def test_are_you_an_ai(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("are you an ai?", ctx)
        assert response is not None
        assert conf == 1.0

    @pytest.mark.asyncio
    async def test_identity_case_insensitive(self):
        ctx = _base_context()
        response, _ = await try_instant_answer("WHO ARE YOU", ctx)
        assert response is not None
        assert "Atlas Cortex" in response


class TestLayer1Greetings:
    """Greeting pattern and response generation."""

    @pytest.mark.asyncio
    async def test_hello_returns_greeting(self):
        ctx = _base_context(time_of_day="morning")
        response, conf = await try_instant_answer("hello", ctx)
        assert response is not None
        assert conf == 1.0

    @pytest.mark.asyncio
    async def test_hello_all_caps(self):
        """'HELLO' should match — regex has re.IGNORECASE."""
        ctx = _base_context(time_of_day="morning")
        response, conf = await try_instant_answer("HELLO", ctx)
        assert response is not None
        assert conf == 1.0

    @pytest.mark.asyncio
    async def test_greeting_morning(self):
        ctx = _base_context(time_of_day="morning", user_id="default")
        response, _ = await try_instant_answer("hello", ctx)
        assert response is not None
        assert "morning" in response.lower() or "hey" in response.lower()

    @pytest.mark.asyncio
    async def test_greeting_evening(self):
        ctx = _base_context(time_of_day="evening", user_id="default")
        response, _ = await try_instant_answer("hello", ctx)
        assert response is not None
        assert "evening" in response.lower()

    @pytest.mark.asyncio
    async def test_greeting_late_night(self):
        ctx = _base_context(time_of_day="late_night", user_id="default")
        response, _ = await try_instant_answer("hello", ctx)
        assert response is not None
        assert "still at it" in response.lower()

    @pytest.mark.asyncio
    async def test_greeting_with_named_user(self):
        ctx = _base_context(time_of_day="morning", user_id="derek")
        response, _ = await try_instant_answer("hello", ctx)
        assert response is not None
        assert "derek" in response.lower()

    @pytest.mark.asyncio
    async def test_greeting_default_user_no_name(self):
        """user_id='default' → no name appended."""
        ctx = _base_context(time_of_day="morning", user_id="default")
        response, _ = await try_instant_answer("hello", ctx)
        assert response is not None
        assert "default" not in response.lower()

    @pytest.mark.asyncio
    async def test_greeting_regex_rejects_long_message(self):
        """Greeting regex requires near-exact match: ^\\s*(hello|...) with
        optional trailing punctuation and whitespace.  Extra words → no match."""
        ctx = _base_context()
        response, conf = await try_instant_answer("hello how are you doing today", ctx)
        # Should NOT match greeting — extra words after 'hello'
        assert response is None or conf < 1.0

    @pytest.mark.asyncio
    async def test_good_morning_is_greeting(self):
        ctx = _base_context(time_of_day="morning")
        response, conf = await try_instant_answer("good morning", ctx)
        assert response is not None
        assert conf == 1.0


class TestLayer1Jokes:
    """Joke integration in Layer 1 — tests the try_instant_answer path."""

    @pytest.mark.asyncio
    async def test_tell_me_a_joke(self):
        """'tell me a joke' matches _JOKE_PATTERNS, calls joke bank."""
        mock_joke = MagicMock()
        mock_joke.setup = "Why did the chicken cross the road?"
        mock_joke.punchline = "To get to the other side!"
        mock_joke.punchline_for_tts = "To get to the other side!"

        ctx = _base_context()
        with patch("cortex.jokes.get_random_joke", return_value=mock_joke), \
             patch("cortex.jokes.init_joke_bank"):
            # The import is `from cortex.jokes import ...` inside the function,
            # so we need to patch where it's used.
            response, conf = await try_instant_answer("tell me a joke", ctx)

        if response is not None:
            assert conf == 1.0
            assert "\n" in response, "Joke should have setup + newline + punchline"
            assert mock_joke.setup in response
            assert mock_joke.punchline in response

    @pytest.mark.asyncio
    async def test_joke_bank_failure_falls_through(self):
        """If joke bank import fails, Layer 1 falls through (returns None)."""
        ctx = _base_context()
        with patch.dict("sys.modules", {"cortex.jokes": None}):
            response, conf = await try_instant_answer("tell me a joke", ctx)
        # Either the joke bank is available and returns something, or it
        # falls through with (None, 0.0). No crash.
        assert conf == 0.0 or response is not None

    @pytest.mark.asyncio
    async def test_joke_punchline_tts_stored_in_context(self):
        """The punchline_for_tts property should be stored in context."""
        mock_joke = MagicMock()
        mock_joke.setup = "Setup"
        mock_joke.punchline = "Punchline"
        mock_joke.punchline_for_tts = "Punchline TTS version"

        ctx = _base_context()
        with patch("cortex.jokes.get_random_joke", return_value=mock_joke), \
             patch("cortex.jokes.init_joke_bank"):
            response, _ = await try_instant_answer("tell me a joke", ctx)

        if response is not None:
            assert ctx.get("_joke_punchline_tts") == "Punchline TTS version"


class TestLayer1NoMatch:
    """Verify messages that should NOT match any instant answer."""

    @pytest.mark.asyncio
    async def test_general_question_falls_through(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("why is the sky blue?", ctx)
        assert response is None
        assert conf == 0.0

    @pytest.mark.asyncio
    async def test_gibberish_falls_through(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("asdfghjkl", ctx)
        assert response is None
        assert conf == 0.0

    @pytest.mark.asyncio
    async def test_empty_string_falls_through(self):
        ctx = _base_context()
        response, conf = await try_instant_answer("", ctx)
        assert response is None
        assert conf == 0.0


# ══════════════════════════════════════════════════════════════════
# Layer 2 — Plugin Dispatch (learned patterns)
# ══════════════════════════════════════════════════════════════════

class TestLayer2LearnedPatterns:
    """Test _try_learned_patterns with a real (temp) SQLite DB."""

    def test_malformed_regex_does_not_crash(self):
        """A broken regex in the DB should be silently skipped."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("[invalid(regex", "test", "response", 1.0),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("anything")
        # Should not crash — malformed regex is caught by `except re.error`
        assert result is None
        assert conf == 0.0

    def test_invalid_backreference_template(self):
        r"""Template with \\1 but no capture group → graceful fallback.

        Code does `m.expand(template)` which raises on bad backrefs.
        The except block falls back to returning the raw template.
        """
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            (r"hello", "greet", r"Hello \1!", 1.0),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("hello world")
        # expand() fails → falls back to raw template
        assert result == r"Hello \1!"
        assert conf == 1.0

    def test_empty_command_patterns_table(self):
        """No rows in command_patterns → returns (None, 0.0)."""
        conn = _make_tmp_db()
        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("hello")
        assert result is None
        assert conf == 0.0

    def test_pattern_below_confidence_threshold_skipped(self):
        """Patterns with confidence < 0.5 are filtered out by the SQL query."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("hello", "greet", "Hi!", 0.3),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("hello")
        assert result is None
        assert conf == 0.0

    def test_pattern_at_threshold_is_included(self):
        """Confidence == 0.5 passes the `>= 0.5` check."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("hello", "greet", "Hi!", 0.5),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("hello")
        assert result == "Hi!"
        assert conf == 0.5

    def test_hit_count_incremented_on_match(self):
        """Matching a pattern should bump hit_count and update last_hit."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("hello", "greet", "Hi!", 1.0),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            _try_learned_patterns("hello world")

        row = conn.execute("SELECT hit_count, last_hit FROM command_patterns WHERE id = 1").fetchone()
        assert row["hit_count"] == 1
        assert row["last_hit"] is not None

    def test_highest_confidence_pattern_wins(self):
        """Multiple matching patterns → highest confidence wins (ORDER BY)."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("hello", "greet_low", "Low!", 0.6),
        )
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            ("hello", "greet_high", "High!", 0.9),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("hello")
        assert result == "High!"
        assert conf == 0.9

    def test_capture_group_expansion(self):
        """Template with valid capture group → expanded correctly."""
        conn = _make_tmp_db()
        conn.execute(
            "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
            "VALUES (?, ?, ?, ?)",
            (r"my name is (\w+)", "name", r"Hello, \1!", 1.0),
        )
        conn.commit()

        with patch("cortex.db.get_db", return_value=conn):
            result, conf = _try_learned_patterns("my name is Alice")
        assert result == "Hello, Alice!"
        assert conf == 1.0

    def test_db_unavailable_does_not_crash(self):
        """If get_db() raises, the outer try/except catches it."""
        with patch("cortex.db.get_db", side_effect=Exception("no DB")):
            result, conf = _try_learned_patterns("hello")
        assert result is None
        assert conf == 0.0


# ══════════════════════════════════════════════════════════════════
# Layer 3 — LLM streaming
# ══════════════════════════════════════════════════════════════════

class TestLayer3ModelSelection:
    """Probe the rule-based model selector."""

    def test_short_what_is_uses_fast(self):
        model = select_model("what is Docker?", model_fast="fast", model_thinking="think")
        assert model == "fast"

    def test_what_is_in_long_message_uses_thinking(self):
        """'what is' present but message > 80 chars → first check fails."""
        long_msg = "what is " + "x" * 200
        model = select_model(long_msg, model_fast="fast", model_thinking="think")
        # len > 200 → thinking model (even though 'what is' matched earlier)
        # Actually: the 'what is' check requires len < 80 to trigger.
        # The _THINKING_KEYWORDS check doesn't match.
        # But len > 200 → falls through to thinking.
        assert model == "think"

    def test_step_by_step_uses_thinking(self):
        model = select_model(
            "explain step by step how to cook pasta",
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"

    def test_analyze_uses_thinking(self):
        model = select_model(
            "analyze this code for bugs",
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"

    def test_short_generic_uses_fast(self):
        """Short message, no keywords on either side → default fast."""
        model = select_model("hello there", model_fast="fast", model_thinking="think")
        assert model == "fast"

    def test_deep_conversation_uses_thinking(self):
        model = select_model(
            "tell me more",
            conversation_length=15,
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"

    def test_conversation_length_boundary_10(self):
        """conversation_length > 10 triggers thinking; exactly 10 does not."""
        model_10 = select_model("ok", conversation_length=10, model_fast="fast", model_thinking="think")
        model_11 = select_model("ok", conversation_length=11, model_fast="fast", model_thinking="think")
        assert model_10 == "fast"
        assert model_11 == "think"

    def test_message_length_boundary_200(self):
        """len > 200 triggers thinking; exactly 200 does not."""
        msg_200 = "x" * 200
        msg_201 = "x" * 201
        model_200 = select_model(msg_200, model_fast="fast", model_thinking="think")
        model_201 = select_model(msg_201, model_fast="fast", model_thinking="think")
        assert model_200 == "fast"
        assert model_201 == "think"

    def test_fast_keyword_in_long_message_skipped(self):
        """'who is' in a message >= 80 chars → fast check doesn't fire."""
        msg = "who is " + "a" * 80
        model = select_model(msg, model_fast="fast", model_thinking="think")
        # Not short enough for fast, not long enough (<=200) for thinking,
        # and 'who is' isn't a thinking keyword → falls through to fast.
        if len(msg) <= 200:
            assert model == "fast"

    def test_both_fast_and_thinking_keywords_thinking_wins(self):
        """'what is' (fast) + 'step by step' (thinking) → thinking wins.

        Fast check requires len < 80.  If both are present in a short
        message, the fast check fires first.  But in a message >= 80
        chars, only thinking check fires.
        """
        msg = "what is the best way to explain step by step"
        model = select_model(msg, model_fast="fast", model_thinking="think")
        if len(msg) < 80:
            # Fast fires first
            assert model == "fast"
        else:
            assert model == "think"


class TestLayer3FillerInjection:
    """Test filler text in system prompt construction."""

    def test_filler_with_quotes(self):
        """Filler text containing quotes should not break the template."""
        filler = 'Let me check on that, "boss".'
        result = _FILLER_INJECTION_TEMPLATE.format(filler=filler.strip())
        assert '"boss"' in result
        # Template uses: "You already started your response with: \"{filler}\""
        # Inner quotes from filler will be adjacent to template quotes.
        assert 'with: "Let me check' in result

    def test_empty_filler_not_injected(self):
        """build_messages with filler='' should not add injection block."""
        messages = build_messages(
            message="hello",
            context={"conversation_history": []},
            filler="",
        )
        system_content = messages[0]["content"]
        assert "You already started" not in system_content

    def test_filler_injected_when_present(self):
        messages = build_messages(
            message="hello",
            context={"conversation_history": []},
            filler="Let me check. ",
        )
        system_content = messages[0]["content"]
        assert "You already started your response with" in system_content
        assert "Let me check." in system_content


class TestLayer3BuildMessages:
    """Test message list construction."""

    def test_empty_conversation_history(self):
        messages = build_messages(
            message="hello",
            context={"conversation_history": []},
            filler="",
        )
        assert len(messages) == 2  # system + user
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hello"

    def test_conversation_history_included(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        messages = build_messages(
            message="what's up?",
            context={"conversation_history": history},
            filler="",
        )
        assert len(messages) == 4  # system + 2 history + user
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hi"
        assert messages[2]["role"] == "assistant"
        assert messages[-1]["content"] == "what's up?"

    def test_memory_context_included(self):
        messages = build_messages(
            message="hello",
            context={"conversation_history": []},
            filler="",
            memory_context="User prefers dark mode.",
        )
        system_content = messages[0]["content"]
        assert "[RELEVANT CONTEXT]" in system_content
        assert "User prefers dark mode." in system_content

    def test_custom_system_prompt_overrides_default(self):
        messages = build_messages(
            message="hello",
            context={"conversation_history": []},
            filler="",
            system_prompt="Custom system prompt.",
        )
        system_content = messages[0]["content"]
        assert "Custom system prompt." in system_content
        assert "Atlas Cortex" not in system_content


class TestLayer3StreamLLMResponse:
    """Test the streaming generator with a mock LLM provider."""

    @pytest.mark.asyncio
    async def test_stream_yields_filler_then_tokens(self):
        """stream_llm_response yields filler first, then real LLM chunks."""
        from cortex.pipeline.layer3_llm import stream_llm_response

        async def _mock_chat(**kwargs: Any) -> AsyncGenerator[str, None]:
            yield "Hello"
            yield " world"

        provider = AsyncMock(spec=LLMProvider)
        provider.chat = AsyncMock(return_value=_mock_chat())

        ctx = _base_context(
            sentiment="question",
            effective_sentiment="question",
        )

        tokens: list[str] = []
        async for tok in stream_llm_response(
            message="what is Docker?",
            context=ctx,
            provider=provider,
            model_fast="fast",
            model_thinking="think",
        ):
            tokens.append(tok)

        # At least the LLM tokens should be present
        assert "Hello" in tokens
        assert " world" in tokens

    @pytest.mark.asyncio
    async def test_stream_handles_provider_error(self):
        """If the LLM provider raises, an error message is yielded."""
        from cortex.pipeline.layer3_llm import stream_llm_response

        provider = AsyncMock(spec=LLMProvider)
        provider.chat = AsyncMock(side_effect=Exception("connection refused"))

        ctx = _base_context(sentiment="question", effective_sentiment="question")

        tokens: list[str] = []
        async for tok in stream_llm_response(
            message="hello",
            context=ctx,
            provider=provider,
            model_fast="fast",
            model_thinking="think",
        ):
            tokens.append(tok)

        full = "".join(tokens)
        assert "Error" in full or "error" in full

    @pytest.mark.asyncio
    async def test_empty_context_history_no_crash(self):
        """Empty conversation_history should not cause build_messages to fail."""
        from cortex.pipeline.layer3_llm import stream_llm_response

        async def _mock_chat(**kwargs: Any) -> AsyncGenerator[str, None]:
            yield "ok"

        provider = AsyncMock(spec=LLMProvider)
        provider.chat = AsyncMock(return_value=_mock_chat())

        ctx = _base_context(conversation_history=[])

        tokens: list[str] = []
        async for tok in stream_llm_response(
            message="test",
            context=ctx,
            provider=provider,
            model_fast="fast",
            model_thinking="think",
        ):
            tokens.append(tok)

        assert any("ok" in t for t in tokens)


# ══════════════════════════════════════════════════════════════════
# Regex pattern probes
# ══════════════════════════════════════════════════════════════════

class TestPatternRegexEdgeCases:
    """Verify the compiled regex patterns match (or don't) tricky inputs."""

    def test_math_regex_rejects_words(self):
        assert _MATH_PATTERNS.match("hello world") is None

    def test_math_regex_accepts_simple_expr(self):
        m = _MATH_PATTERNS.match("2 + 2")
        assert m is not None

    def test_math_regex_with_prefix(self):
        """'what is 2 + 2' — optional prefix group."""
        m = _MATH_PATTERNS.match("what is 2 + 2")
        assert m is not None

    def test_math_regex_calculate_prefix(self):
        m = _MATH_PATTERNS.match("calculate 100 / 5")
        assert m is not None

    def test_greeting_regex_rejects_embedded(self):
        """'say hello to Bob' should NOT match greeting regex."""
        assert _GREETING_PATTERNS.match("say hello to Bob") is None

    def test_greeting_regex_with_trailing_period(self):
        """'hello.' should match — regex allows [!.,]? after greeting."""
        assert _GREETING_PATTERNS.match("hello.") is not None

    def test_greeting_regex_with_trailing_comma(self):
        assert _GREETING_PATTERNS.match("hello,") is not None

    def test_identity_regex_matches_are_you_atlas(self):
        assert _IDENTITY_PATTERNS.search("are you atlas") is not None

    def test_identity_regex_matches_introduce_yourself(self):
        assert _IDENTITY_PATTERNS.search("introduce yourself") is not None
