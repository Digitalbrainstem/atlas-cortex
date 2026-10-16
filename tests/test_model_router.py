"""Tests for cortex.pipeline.model_router — three-tier LLM routing."""

from __future__ import annotations

import os

import pytest

from cortex.pipeline.model_router import ModelChoice, ModelRouter, get_router, reset_router


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _router(**kw) -> ModelRouter:
    """Build a router with predictable model names for assertions."""
    defaults = dict(
        model_fast="fast-4b",
        model_expert="expert-9b",
        model_reasoning="reasoning-27b",
        url_fast="http://fast:11434",
        url_expert="http://expert:11434",
        url_reasoning="http://reasoning:11434",
    )
    defaults.update(kw)
    return ModelRouter(**defaults)


# ──────────────────────────────────────────────────────────────────
# Complexity scoring
# ──────────────────────────────────────────────────────────────────

class TestComplexityScoring:
    def test_empty_query_scores_zero(self):
        r = _router()
        assert r.score_complexity("") == 0

    def test_simple_greeting_scores_low(self):
        r = _router()
        assert r.score_complexity("hello") <= 10

    def test_simple_command_scores_low(self):
        r = _router()
        assert r.score_complexity("turn on the lights") <= 10

    def test_time_query_scores_low(self):
        r = _router()
        assert r.score_complexity("what time is it?") <= 15

    def test_reasoning_keywords_boost_score(self):
        r = _router()
        simple = r.score_complexity("tell me about dogs")
        complex_ = r.score_complexity("explain how neural networks learn")
        assert complex_ > simple
        assert complex_ >= 20  # at least one reasoning keyword hit

    def test_multiple_reasoning_keywords_stack(self):
        r = _router()
        one = r.score_complexity("explain recursion")
        two = r.score_complexity("explain and compare recursion and iteration step by step")
        assert two > one

    def test_code_keywords_boost_score(self):
        r = _router()
        score = r.score_complexity("write code for a binary search function")
        assert score >= 15  # at least code keyword hit

    def test_code_fence_detection(self):
        r = _router()
        score = r.score_complexity("fix this:\n```python\ndef broken():\n  pass\n```")
        assert score >= 15

    def test_technical_terms_boost_score(self):
        r = _router()
        plain = r.score_complexity("tell me about cooking")
        tech = r.score_complexity("explain the TCP handshake algorithm complexity O(n)")
        assert tech > plain

    def test_long_query_adds_length_bonus(self):
        r = _router()
        short = r.score_complexity("hi")
        medium = r.score_complexity("a" * 160)
        long = r.score_complexity("b" * 320)
        very_long = r.score_complexity("c" * 600)
        assert medium > short
        assert long > medium
        assert very_long >= long

    def test_multi_clause_detection(self):
        r = _router()
        simple = r.score_complexity("what is a dog")
        multi = r.score_complexity(
            "what is a dog, and how does it differ from a wolf, but also why are they related?"
        )
        assert multi > simple

    def test_multiple_question_marks_boost(self):
        r = _router()
        single_q = r.score_complexity("what is Python?")
        multi_q = r.score_complexity("what is Python? How does it compare to Java? Which should I learn?")
        assert multi_q > single_q

    def test_conversation_depth_boosts_score(self):
        r = _router()
        shallow = r.score_complexity("tell me more", {})
        deep = r.score_complexity("tell me more", {"conversation_length": 20})
        assert deep > shallow

    def test_score_clamped_to_0_100(self):
        r = _router()
        # Very simple should not go below 0
        assert r.score_complexity("hi") >= 0
        # Extremely complex should not exceed 100
        monster = (
            "explain and compare and analyze step by step, "
            "and also debug this algorithm with O(n) complexity, "
            "write code for a recursive binary tree function class, "
            "also refactor, and prove the theorem? Why? How does it work? "
            + "x" * 600
        )
        assert r.score_complexity(monster) <= 100

    def test_version_number_detected(self):
        r = _router()
        score = r.score_complexity("what changed in version 3.12.1?")
        assert score >= 10  # technical pattern match

    def test_constant_name_detected(self):
        r = _router()
        score = r.score_complexity("what does MAX_RETRY_COUNT mean in the config?")
        assert score >= 10


# ──────────────────────────────────────────────────────────────────
# Routing decisions
# ──────────────────────────────────────────────────────────────────

class TestRouting:
    def test_simple_query_routes_to_fast(self):
        r = _router()
        choice = r.route("hello there")
        assert choice.tier == "fast"
        assert choice.model_name == "fast-4b"
        assert choice.api_url == "http://fast:11434"

    def test_timer_routes_to_fast(self):
        r = _router()
        choice = r.route("set timer for 5 minutes")
        assert choice.tier == "fast"

    def test_moderate_query_routes_to_expert(self):
        r = _router()
        choice = r.route("explain why photosynthesis matters and how does it work")
        assert choice.tier in ("expert", "reasoning")
        assert choice.model_name != "fast-4b"

    def test_complex_query_routes_to_reasoning(self):
        r = _router()
        choice = r.route(
            "compare and analyze the trade-offs between microservices and monolith "
            "architecture step by step, and explain why one might be better"
        )
        assert choice.tier == "reasoning"
        assert choice.model_name == "reasoning-27b"
        assert choice.api_url == "http://reasoning:11434"

    def test_code_query_routes_above_fast(self):
        r = _router()
        choice = r.route("write code for a function that implements binary search")
        assert choice.tier in ("expert", "reasoning")

    def test_fast_tier_has_lower_max_tokens(self):
        r = _router()
        fast = r.route("hi")
        reasoning = r.route(
            "explain and analyze the algorithm step by step, "
            "compare with alternatives, and debug the edge cases? Why?"
        )
        assert fast.max_tokens < reasoning.max_tokens

    def test_reasoning_tier_has_lower_temperature(self):
        r = _router()
        fast = r.route("hi")
        reasoning = r.route(
            "explain and analyze step by step, compare approaches, "
            "reason through the trade-offs? Why does it work?"
        )
        assert reasoning.temperature <= fast.temperature

    def test_choice_includes_reason(self):
        r = _router()
        choice = r.route("hello")
        assert "→" in choice.reason
        assert "fast" in choice.reason

    def test_choice_includes_complexity_score(self):
        r = _router()
        choice = r.route("explain quantum computing step by step")
        assert choice.complexity_score > 0

    def test_route_with_context(self):
        r = _router()
        choice = r.route("tell me more", {"conversation_length": 20})
        # Deep conversation should push toward expert or higher
        assert choice.complexity_score > r.route("tell me more").complexity_score


# ──────────────────────────────────────────────────────────────────
# Escalation
# ──────────────────────────────────────────────────────────────────

class TestEscalation:
    def test_weak_empty_response_triggers_escalation(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        escalated = r.should_escalate("", fast_choice)
        assert escalated is not None
        assert escalated.tier == "expert"
        assert escalated.model_name == "expert-9b"

    def test_weak_short_response_triggers_escalation(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        escalated = r.should_escalate("Hmm, ok.", fast_choice)
        assert escalated is not None
        assert escalated.tier == "expert"

    def test_hedging_triggers_escalation(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        escalated = r.should_escalate(
            "I'm not sure about that, but I think it might be related to something.",
            fast_choice,
        )
        assert escalated is not None

    def test_expert_escalates_to_reasoning(self):
        r = _router()
        expert_choice = ModelChoice(
            model_name="expert-9b", api_url="http://expert:11434", tier="expert",
        )
        escalated = r.should_escalate("I don't know the answer to that.", expert_choice)
        assert escalated is not None
        assert escalated.tier == "reasoning"
        assert escalated.model_name == "reasoning-27b"

    def test_reasoning_does_not_escalate(self):
        r = _router()
        reasoning_choice = ModelChoice(
            model_name="reasoning-27b", api_url="http://reasoning:11434",
            tier="reasoning",
        )
        assert r.should_escalate("I'm not sure.", reasoning_choice) is None

    def test_good_response_does_not_escalate(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        good_resp = (
            "Docker is a platform for building and running containers. "
            "It packages your application with all its dependencies into a "
            "standardized unit called a container."
        )
        assert r.should_escalate(good_resp, fast_choice) is None

    def test_escalation_preserves_complexity_score(self):
        r = _router()
        original = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434",
            tier="fast", complexity_score=25,
        )
        escalated = r.should_escalate("I can't help.", original)
        assert escalated is not None
        assert escalated.complexity_score == 25

    def test_as_an_ai_triggers_escalation(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        escalated = r.should_escalate(
            "As an AI language model, I don't have personal experience with that topic.",
            fast_choice,
        )
        assert escalated is not None

    def test_whitespace_only_triggers_escalation(self):
        r = _router()
        fast_choice = ModelChoice(
            model_name="fast-4b", api_url="http://fast:11434", tier="fast",
        )
        assert r.should_escalate("   \n\n  ", fast_choice) is not None


# ──────────────────────────────────────────────────────────────────
# Configurable thresholds
# ──────────────────────────────────────────────────────────────────

class TestConfigurableThresholds:
    def test_custom_thresholds_shift_routing(self):
        # Lower thresholds → easier to reach expert/reasoning
        lenient = _router(threshold_expert=10, threshold_reasoning=30)
        choice = lenient.route("explain recursion")
        assert choice.tier in ("expert", "reasoning")

    def test_high_thresholds_keep_fast(self):
        strict = _router(threshold_expert=90, threshold_reasoning=95)
        choice = strict.route("explain how recursion works in Python")
        assert choice.tier == "fast"

    def test_env_var_thresholds(self, monkeypatch):
        monkeypatch.setenv("MODEL_THRESHOLD_EXPERT", "15")
        monkeypatch.setenv("MODEL_THRESHOLD_REASONING", "40")
        r = ModelRouter(
            model_fast="f", model_expert="e", model_reasoning="r",
            url_fast="http://f", url_expert="http://e", url_reasoning="http://r",
        )
        assert r.threshold_expert == 15
        assert r.threshold_reasoning == 40

    def test_env_var_model_names(self, monkeypatch):
        monkeypatch.setenv("MODEL_FAST", "tiny-1b")
        monkeypatch.setenv("MODEL_EXPERT", "mid-7b")
        monkeypatch.setenv("MODEL_REASONING", "big-70b")
        monkeypatch.setenv("MODEL_FAST_URL", "http://tiny:8080")
        monkeypatch.setenv("MODEL_EXPERT_URL", "http://mid:8080")
        monkeypatch.setenv("MODEL_REASONING_URL", "http://big:8080")
        r = ModelRouter()
        assert r.model_fast == "tiny-1b"
        assert r.model_expert == "mid-7b"
        assert r.model_reasoning == "big-70b"
        assert r.url_fast == "http://tiny:8080"

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("MODEL_FAST", "env-model")
        r = ModelRouter(model_fast="explicit-model", model_expert="e", model_reasoning="r")
        assert r.model_fast == "explicit-model"


# ──────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_query(self):
        r = _router()
        choice = r.route("")
        assert choice.tier == "fast"
        assert choice.complexity_score == 0

    def test_very_long_query(self):
        r = _router()
        long_msg = "explain " * 200
        choice = r.route(long_msg)
        assert choice.complexity_score >= 20  # length + keyword bonus

    def test_unicode_query(self):
        r = _router()
        choice = r.route("¿Cómo funciona la fotosíntesis? Explícalo paso a paso.")
        assert isinstance(choice, ModelChoice)
        assert choice.tier in ("fast", "expert", "reasoning")

    def test_emoji_query(self):
        r = _router()
        choice = r.route("🤔 explain this 🧠")
        assert isinstance(choice, ModelChoice)

    def test_newlines_and_whitespace(self):
        r = _router()
        choice = r.route("  \n\n  hello  \n  ")
        assert isinstance(choice, ModelChoice)

    def test_mixed_case_keywords(self):
        r = _router()
        lower = r.score_complexity("explain how it works")
        upper = r.score_complexity("EXPLAIN HOW IT WORKS")
        assert lower == upper  # case-insensitive matching

    def test_only_punctuation(self):
        r = _router()
        choice = r.route("???")
        assert isinstance(choice, ModelChoice)


# ──────────────────────────────────────────────────────────────────
# Singleton helpers
# ──────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_router_returns_router(self):
        reset_router()
        r = get_router()
        assert isinstance(r, ModelRouter)

    def test_get_router_is_singleton(self):
        reset_router()
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_reset_clears_singleton(self):
        reset_router()
        r1 = get_router()
        reset_router()
        r2 = get_router()
        assert r1 is not r2
