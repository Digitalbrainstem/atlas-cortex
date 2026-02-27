"""Tests for the processing pipeline layers."""

from __future__ import annotations

import asyncio
import pytest

from cortex.pipeline.layer0_context import assemble_context, _classify_sentiment, _time_of_day
from cortex.pipeline.layer1_instant import try_instant_answer, _safe_eval
from cortex.pipeline.layer3_llm import select_model


# ──────────────────────────────────────────────────────────────────
# Layer 0
# ──────────────────────────────────────────────────────────────────

class TestLayer0Context:
    @pytest.mark.asyncio
    async def test_basic_context(self):
        ctx = await assemble_context("Hello there!", user_id="alice")
        assert ctx["user_id"] == "alice"
        assert ctx["sentiment"] == "greeting"
        assert isinstance(ctx["sentiment_score"], float)
        assert ctx["is_follow_up"] is False

    @pytest.mark.asyncio
    async def test_follow_up_detection(self):
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hey!"},
            {"role": "user", "content": "What time is it?"},
        ]
        ctx = await assemble_context("tell me more", conversation_history=history)
        assert ctx["is_follow_up"] is True
        assert ctx["conversation_length"] == 3

    def test_classify_greeting(self):
        assert _classify_sentiment("hello", {"compound": 0.3}) == "greeting"
        assert _classify_sentiment("hey there", {"compound": 0.0}) == "greeting"

    def test_classify_command(self):
        assert _classify_sentiment("turn on the lights", {"compound": 0.0}) == "command"
        assert _classify_sentiment("turn off the fan", {"compound": 0.0}) == "command"

    def test_classify_frustrated(self):
        assert _classify_sentiment("this is terrible and broken", {"compound": -0.8}) == "frustrated"

    def test_classify_question(self):
        assert _classify_sentiment("what is Docker?", {"compound": 0.0}) == "question"
        assert _classify_sentiment("how does this work?", {"compound": 0.0}) == "question"

    def test_classify_casual(self):
        assert _classify_sentiment("sounds good to me", {"compound": 0.2}) == "casual"

    def test_time_of_day_morning(self):
        assert _time_of_day(8) == "morning"

    def test_time_of_day_afternoon(self):
        assert _time_of_day(14) == "afternoon"

    def test_time_of_day_evening(self):
        assert _time_of_day(19) == "evening"

    def test_time_of_day_late_night(self):
        assert _time_of_day(23) == "late_night"
        assert _time_of_day(2) == "late_night"


# ──────────────────────────────────────────────────────────────────
# Layer 1 — math eval
# ──────────────────────────────────────────────────────────────────

class TestSafeEval:
    def test_basic_addition(self):
        assert _safe_eval("2 + 2") == 4

    def test_multiplication(self):
        assert _safe_eval("3 * 4") == 12

    def test_division(self):
        assert _safe_eval("10 / 4") == 2.5

    def test_power(self):
        assert _safe_eval("2 ** 8") == 256

    def test_nested_parens(self):
        assert _safe_eval("(3 + 4) * 2") == 14

    def test_sqrt(self):
        import math
        assert abs(_safe_eval("sqrt(16)") - 4.0) < 1e-9

    def test_pi(self):
        import math
        assert abs(_safe_eval("pi") - math.pi) < 1e-9

    def test_rejects_strings(self):
        with pytest.raises((ValueError, TypeError)):
            _safe_eval('"hello"')

    def test_rejects_import(self):
        with pytest.raises(Exception):
            _safe_eval("__import__('os')")


# ──────────────────────────────────────────────────────────────────
# Layer 1 — instant answers
# ──────────────────────────────────────────────────────────────────

class TestLayer1Instant:
    @pytest.mark.asyncio
    async def test_date_query(self):
        ctx = {"time_of_day": "morning", "user_id": "test"}
        response, confidence = await try_instant_answer("what's the date today?", ctx)
        assert response is not None
        assert confidence == 1.0
        assert "Today is" in response

    @pytest.mark.asyncio
    async def test_time_query(self):
        ctx = {"time_of_day": "afternoon", "user_id": "test"}
        response, confidence = await try_instant_answer("what time is it?", ctx)
        assert response is not None
        assert confidence == 1.0
        assert "It's" in response

    @pytest.mark.asyncio
    async def test_math_query(self):
        ctx = {"time_of_day": "morning", "user_id": "test"}
        response, confidence = await try_instant_answer("2 + 2", ctx)
        assert response == "4"
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_math_with_decimals(self):
        ctx = {}
        response, confidence = await try_instant_answer("10 / 3", ctx)
        assert response is not None
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_identity_question(self):
        ctx = {"time_of_day": "morning"}
        response, confidence = await try_instant_answer("who are you?", ctx)
        assert response is not None
        assert "Atlas Cortex" in response
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_greeting(self):
        ctx = {"time_of_day": "morning", "user_id": "derek"}
        response, confidence = await try_instant_answer("hello", ctx)
        assert response is not None
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_no_instant_answer(self):
        ctx = {}
        response, confidence = await try_instant_answer("why is the sky blue?", ctx)
        assert response is None
        assert confidence == 0.0


# ──────────────────────────────────────────────────────────────────
# Layer 3 — model selection
# ──────────────────────────────────────────────────────────────────

class TestModelSelection:
    def test_short_factual_uses_fast(self):
        model = select_model("what is Docker?", model_fast="fast", model_thinking="think")
        assert model == "fast"

    def test_complex_uses_thinking(self):
        model = select_model(
            "explain in detail how TCP/IP works",
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"

    def test_long_message_uses_thinking(self):
        model = select_model(
            "x" * 300,
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"

    def test_deep_conversation_uses_thinking(self):
        model = select_model(
            "tell me more",
            conversation_length=15,
            model_fast="fast",
            model_thinking="think",
        )
        assert model == "think"
