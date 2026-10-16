"""Tests for the thinking router (``/think`` vs ``/no_think`` for Qwen3.5)."""

from __future__ import annotations

import pytest

from cortex.pipeline.thinking_router import (
    ThinkingDecision,
    ThinkingRouter,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def router() -> ThinkingRouter:
    return ThinkingRouter(confidence_threshold=0.5)


# ──────────────────────────────────────────────────────────────────
# Simple queries → no_think
# ──────────────────────────────────────────────────────────────────

class TestNoThinkRouting:
    """Queries that should be fast-pathed (no thinking)."""

    @pytest.mark.parametrize("msg", [
        "hello",
        "hi",
        "hey",
        "yo",
        "yes",
        "no",
        "ok",
        "thanks",
        "nope",
        "sure",
    ])
    def test_exact_fast_words(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False

    @pytest.mark.parametrize("msg", [
        "good morning",
        "good evening",
        "good night",
    ])
    def test_greetings(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False

    @pytest.mark.parametrize("msg", [
        "how are you",
        "how are you doing today?",
        "what's up",
    ])
    def test_small_talk(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False

    @pytest.mark.parametrize("msg", [
        "what time is it",
        "what's the weather",
        "what's the temperature",
        "what's the date",
    ])
    def test_simple_factual(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False

    @pytest.mark.parametrize("msg", [
        "turn on the lights",
        "turn off the fan",
        "set a timer for 5 minutes",
        "play music",
        "stop music",
        "lock the door",
        "dim the lights",
    ])
    def test_home_commands(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False

    @pytest.mark.parametrize("msg", [
        "tell me more",
        "go on",
        "continue",
        "that's all",
    ])
    def test_follow_ups(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is False


# ──────────────────────────────────────────────────────────────────
# Complex queries → think
# ──────────────────────────────────────────────────────────────────

class TestThinkRouting:
    """Queries that should activate extended thinking."""

    @pytest.mark.parametrize("msg", [
        "solve x^2 + 2x - 15 = 0",
        "calculate the integral of sin(x) from 0 to pi",
        "compute the standard deviation of [1, 2, 3, 4, 5]",
        "prove that the square root of 2 is irrational",
    ])
    def test_math_logic(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "write code to sort a linked list",
        "implement a binary search tree in Python",
        "debug this function that returns the wrong result",
        "refactor this class to use dependency injection",
    ])
    def test_code_tasks(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "explain how TCP three-way handshake works",
        "why does the sky look blue during the day?",
        "how does a transformer neural network work?",
        "what causes inflation in an economy?",
    ])
    def test_explanations(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "compare Python vs Rust for systems programming",
        "what's the difference between TCP and UDP?",
        "pros and cons of microservices vs monolith",
        "which is better, PostgreSQL or MySQL?",
    ])
    def test_comparisons(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "what are the symptoms of hypothyroidism?",
        "explain the treatment options for type 2 diabetes",
    ])
    def test_medical(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "write me a short story about a dragon",
        "compose a poem about the ocean",
        "write a speech for a graduation ceremony",
    ])
    def test_creative_writing(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True

    @pytest.mark.parametrize("msg", [
        "plan a 7-day trip to Japan",
        "outline the steps to deploy a Kubernetes cluster",
        "create a strategy for learning machine learning",
    ])
    def test_multi_step(self, router: ThinkingRouter, msg: str) -> None:
        decision = router.route(msg)
        assert decision.use_thinking is True


# ──────────────────────────────────────────────────────────────────
# Confidence scoring
# ──────────────────────────────────────────────────────────────────

class TestConfidenceScoring:
    """Tests for check_confidence() heuristic."""

    def test_empty_response_zero(self, router: ThinkingRouter) -> None:
        assert ThinkingRouter.check_confidence("", "what is DNS?") == 0.0

    def test_whitespace_response_zero(self, router: ThinkingRouter) -> None:
        assert ThinkingRouter.check_confidence("   ", "what is DNS?") == 0.0

    def test_solid_response_high(self, router: ThinkingRouter) -> None:
        response = (
            "DNS stands for Domain Name System. It translates human-readable "
            "domain names like example.com into IP addresses that computers "
            "use to identify each other on the network."
        )
        score = ThinkingRouter.check_confidence(response, "what is DNS?")
        assert score >= 0.8

    def test_hedging_lowers_score(self, router: ThinkingRouter) -> None:
        hedged = "I think maybe it's something to do with networks, I'm not sure."
        confident = "DNS is the Domain Name System, responsible for resolving names to IPs."
        assert ThinkingRouter.check_confidence(hedged, "what is DNS?") < \
               ThinkingRouter.check_confidence(confident, "what is DNS?")

    def test_short_response_for_complex_query_low(self, router: ThinkingRouter) -> None:
        query = "explain how the TCP three-way handshake works in detail"
        short = "It uses SYN packets."
        score = ThinkingRouter.check_confidence(short, query)
        assert score < 0.8

    def test_echo_response_penalized(self, router: ThinkingRouter) -> None:
        query = "what is machine learning"
        echo = "What is machine learning? It's a type of AI."
        non_echo = "Machine learning is a subset of AI that enables systems to learn from data."
        assert ThinkingRouter.check_confidence(echo, query) < \
               ThinkingRouter.check_confidence(non_echo, query)

    def test_score_clamped_to_range(self, router: ThinkingRouter) -> None:
        # Even with many penalties the score stays ≥ 0.0
        terrible = "I think maybe perhaps possibly I'm not sure I believe it might be something"
        score = ThinkingRouter.check_confidence(terrible, "explain quantum mechanics step by step")
        assert 0.0 <= score <= 1.0


# ──────────────────────────────────────────────────────────────────
# Retry logic
# ──────────────────────────────────────────────────────────────────

class TestRetryLogic:
    """Tests for should_retry_with_thinking()."""

    def test_weak_response_triggers_retry(self, router: ThinkingRouter) -> None:
        query = "explain how photosynthesis converts light energy into chemical energy"
        weak = "Maybe plants use light."
        assert router.should_retry_with_thinking(weak, query) is True

    def test_strong_response_no_retry(self, router: ThinkingRouter) -> None:
        query = "what is DNS?"
        strong = (
            "DNS stands for Domain Name System. It translates human-readable "
            "domain names into IP addresses using a hierarchical lookup process."
        )
        assert router.should_retry_with_thinking(strong, query) is False

    def test_empty_response_triggers_retry(self, router: ThinkingRouter) -> None:
        assert router.should_retry_with_thinking("", "tell me about relativity") is True

    def test_custom_threshold_respected(self) -> None:
        strict = ThinkingRouter(confidence_threshold=0.9)
        # A mediocre-but-not-terrible response
        response = "I think DNS is for translating domain names to IPs."
        assert strict.should_retry_with_thinking(response, "what is DNS?") is True

        lenient = ThinkingRouter(confidence_threshold=0.1)
        assert lenient.should_retry_with_thinking(response, "what is DNS?") is False


# ──────────────────────────────────────────────────────────────────
# apply() — tag prepending
# ──────────────────────────────────────────────────────────────────

class TestApply:
    """Tests for the apply() static helper."""

    def test_think_tag(self, router: ThinkingRouter) -> None:
        decision = ThinkingDecision(use_thinking=True, reason="test")
        assert router.apply("hello", decision) == "/think hello"

    def test_no_think_tag(self, router: ThinkingRouter) -> None:
        decision = ThinkingDecision(use_thinking=False, reason="test")
        assert router.apply("hello", decision) == "/no_think hello"

    def test_preserves_message(self, router: ThinkingRouter) -> None:
        msg = "explain how TCP works in detail"
        decision = ThinkingDecision(use_thinking=True)
        result = router.apply(msg, decision)
        assert result.endswith(msg)


# ──────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_empty_string(self, router: ThinkingRouter) -> None:
        decision = router.route("")
        assert decision.use_thinking is False

    def test_whitespace_only(self, router: ThinkingRouter) -> None:
        decision = router.route("   ")
        assert decision.use_thinking is False

    def test_single_character(self, router: ThinkingRouter) -> None:
        decision = router.route("?")
        assert decision.use_thinking is False

    def test_mixed_case(self, router: ThinkingRouter) -> None:
        decision = router.route("EXPLAIN how DNS works")
        assert decision.use_thinking is True

    def test_context_long_conversation_boosts_thinking(self, router: ThinkingRouter) -> None:
        # A borderline message that might not trigger on its own
        decision = router.route(
            "what about the other approach?",
            context={"conversation_length": 15},
        )
        assert decision.use_thinking is True

    def test_context_none_safe(self, router: ThinkingRouter) -> None:
        # Must not crash with context=None
        decision = router.route("hello", context=None)
        assert isinstance(decision, ThinkingDecision)

    def test_decision_dataclass_fields(self, router: ThinkingRouter) -> None:
        decision = router.route("solve 2+2")
        assert hasattr(decision, "use_thinking")
        assert hasattr(decision, "reason")
        assert hasattr(decision, "confidence_threshold")
        assert isinstance(decision.confidence_threshold, float)

    def test_very_long_message_favors_thinking(self, router: ThinkingRouter) -> None:
        long_msg = "I need help with " + "a really complex situation " * 10
        decision = router.route(long_msg)
        assert decision.use_thinking is True
