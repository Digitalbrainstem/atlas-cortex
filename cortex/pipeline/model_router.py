"""Pipeline Model Router — auto-select LLM by query complexity.

Three-tier routing: fast (4B) → expert (9B) → reasoning (27B).
Scores each query 0-100 on complexity, then maps to the appropriate
model tier.  Also provides escalation logic when a model's response
looks weak.

Integration: called in layer3_llm.py before provider.chat().
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Configuration defaults (all overridable via env)
# ──────────────────────────────────────────────────────────────────

_DEFAULT_FAST = "qwen2.5:3b"
_DEFAULT_EXPERT = "qwen2.5:14b"
_DEFAULT_REASONING = "qwen3:30b-a3b"

_DEFAULT_FAST_URL = "http://localhost:11434"
_DEFAULT_EXPERT_URL = "http://localhost:11434"
_DEFAULT_REASONING_URL = "http://localhost:11434"

_THRESHOLD_EXPERT = 31
_THRESHOLD_REASONING = 71

# ──────────────────────────────────────────────────────────────────
# Keyword / pattern sets for scoring
# ──────────────────────────────────────────────────────────────────

_SIMPLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(what time|what'?s the time|current time)\b", re.I),
    re.compile(r"\b(turn (on|off)|set timer|set alarm|start timer)\b", re.I),
    re.compile(r"\b(hello|hey|hi|good morning|good night|goodbye|bye)\b", re.I),
    re.compile(r"\b(thank(s| you)|ok(ay)?|sure|yes|no)\b", re.I),
    re.compile(r"\b(play |pause |stop |next |previous )\b", re.I),
    re.compile(r"\b(what('?s| is) the (date|day|weather))\b", re.I),
]
_SIMPLE_SCORE = -30

_REASONING_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bexplain\b", re.I),
    re.compile(r"\bcompare\b", re.I),
    re.compile(r"\banalyze\b", re.I),
    re.compile(r"\bdebug\b", re.I),
    re.compile(r"\bwhy\b", re.I),
    re.compile(r"\bhow does\b", re.I),
    re.compile(r"\bstep[- ]by[- ]step\b", re.I),
    re.compile(r"\btrade[- ]?off\b", re.I),
    re.compile(r"\bprove\b", re.I),
    re.compile(r"\bderive\b", re.I),
    re.compile(r"\btheorem\b", re.I),
    re.compile(r"\broot[- ]?cause\b", re.I),
    re.compile(r"\bdiagnose\b", re.I),
    re.compile(r"\bin detail\b", re.I),
    re.compile(r"\breason(ing)?\b", re.I),
]
_REASONING_SCORE = 20

_CODE_KEYWORDS: list[re.Pattern[str]] = [
    re.compile(r"\bwrite code\b", re.I),
    re.compile(r"\bfunction\b", re.I),
    re.compile(r"\bclass\b", re.I),
    re.compile(r"\brefactor\b", re.I),
    re.compile(r"\bfix\b", re.I),
    re.compile(r"\bimplement\b", re.I),
    re.compile(r"\bbug\b", re.I),
    re.compile(r"```", re.I),
]
_CODE_SCORE = 15

_TECHNICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d+(\.\d+){1,3}\b"),               # version numbers / IPs
    re.compile(r"[=<>!]{1,2}\s*\d"),                    # comparisons with numbers
    re.compile(r"\b(algorithm|complexity|O\(n)\b", re.I),
    re.compile(r"\b(SQL|API|REST|GraphQL|HTTP|TCP|UDP)\b"),
    re.compile(r"\b(regex|recursion|hash|binary|tree)\b", re.I),
    re.compile(r"[A-Z]{2,}_[A-Z]{2,}"),                # CONSTANT_NAMES
]
_TECHNICAL_SCORE = 10

# Weak-response signals for escalation
_WEAK_PHRASES = [
    "i'm not sure",
    "i don't know",
    "i cannot",
    "i can't",
    "as an ai",
    "i'm unable",
    "i am not sure",
    "i do not know",
    "it depends",
]


# ──────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────

@dataclass
class ModelChoice:
    """Result of the routing decision."""
    model_name: str
    api_url: str
    max_tokens: int = 2048
    temperature: float = 0.7
    reason: str = ""
    tier: str = "fast"          # fast | expert | reasoning
    complexity_score: int = 0


# ──────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────

class ModelRouter:
    """Three-tier model router for the pipeline.

    Scores each query on a 0–100 complexity scale and returns the
    appropriate model tier:

    * **fast** (0–30): 4B general model for quick answers.
    * **expert** (31–70): 9B expert for moderately complex tasks.
    * **reasoning** (71–100): 27B for heavy reasoning / analysis.

    All thresholds and model names are configurable via environment
    variables.
    """

    def __init__(
        self,
        *,
        model_fast: str | None = None,
        model_expert: str | None = None,
        model_reasoning: str | None = None,
        url_fast: str | None = None,
        url_expert: str | None = None,
        url_reasoning: str | None = None,
        threshold_expert: int | None = None,
        threshold_reasoning: int | None = None,
    ) -> None:
        self.model_fast = model_fast or os.environ.get("MODEL_FAST", _DEFAULT_FAST)
        self.model_expert = model_expert or os.environ.get("MODEL_EXPERT", _DEFAULT_EXPERT)
        self.model_reasoning = model_reasoning or os.environ.get("MODEL_REASONING", _DEFAULT_REASONING)

        self.url_fast = url_fast or os.environ.get("MODEL_FAST_URL", _DEFAULT_FAST_URL)
        self.url_expert = url_expert or os.environ.get("MODEL_EXPERT_URL", _DEFAULT_EXPERT_URL)
        self.url_reasoning = url_reasoning or os.environ.get("MODEL_REASONING_URL", _DEFAULT_REASONING_URL)

        self.threshold_expert = (
            threshold_expert if threshold_expert is not None
            else int(os.environ.get("MODEL_THRESHOLD_EXPERT", str(_THRESHOLD_EXPERT)))
        )
        self.threshold_reasoning = (
            threshold_reasoning if threshold_reasoning is not None
            else int(os.environ.get("MODEL_THRESHOLD_REASONING", str(_THRESHOLD_REASONING)))
        )

    # ── public API ────────────────────────────────────────────────

    def score_complexity(self, message: str, context: dict[str, Any] | None = None) -> int:
        """Score *message* from 0 (trivial) to 100 (very complex).

        The score is built from additive/subtractive heuristics:
        * Length bonus
        * Keyword category matches
        * Multi-clause detection
        * Conversation depth
        """
        ctx = context or {}
        score = 0

        # --- length contribution (0-20) ---
        length = len(message)
        if length > 500:
            score += 20
        elif length > 300:
            score += 15
        elif length > 150:
            score += 10
        elif length > 80:
            score += 5

        lower = message.lower()

        # --- simple pattern penalty ---
        for pat in _SIMPLE_PATTERNS:
            if pat.search(message):
                score += _SIMPLE_SCORE
                break  # one match is enough

        # --- reasoning keywords ---
        reasoning_hits = sum(1 for pat in _REASONING_KEYWORDS if pat.search(message))
        score += min(reasoning_hits * _REASONING_SCORE, 60)  # cap at 60

        # --- code keywords ---
        code_hits = sum(1 for pat in _CODE_KEYWORDS if pat.search(message))
        score += min(code_hits * _CODE_SCORE, 30)  # cap at 30

        # --- technical terms ---
        tech_hits = sum(1 for pat in _TECHNICAL_PATTERNS if pat.search(message))
        score += min(tech_hits * _TECHNICAL_SCORE, 20)  # cap at 20

        # --- multi-clause detection ---
        clause_seps = len(re.findall(r"[;,]\s*(and|but|or|because|however|although|if|then)\b", lower))
        if clause_seps >= 2:
            score += 15
        elif clause_seps == 1:
            score += 8

        # --- question depth: multiple question marks ---
        qmarks = message.count("?")
        if qmarks >= 3:
            score += 12
        elif qmarks == 2:
            score += 6

        # --- conversation depth ---
        conv_len = ctx.get("conversation_length", 0)
        if conv_len > 15:
            score += 10
        elif conv_len > 8:
            score += 5

        return max(0, min(100, score))

    def route(self, message: str, context: dict[str, Any] | None = None) -> ModelChoice:
        """Select a model tier based on query complexity."""
        score = self.score_complexity(message, context)

        if score >= self.threshold_reasoning:
            choice = ModelChoice(
                model_name=self.model_reasoning,
                api_url=self.url_reasoning,
                max_tokens=4096,
                temperature=0.6,
                reason=f"complexity {score} ≥ {self.threshold_reasoning} → reasoning",
                tier="reasoning",
                complexity_score=score,
            )
        elif score >= self.threshold_expert:
            choice = ModelChoice(
                model_name=self.model_expert,
                api_url=self.url_expert,
                max_tokens=2048,
                temperature=0.7,
                reason=f"complexity {score} ≥ {self.threshold_expert} → expert",
                tier="expert",
                complexity_score=score,
            )
        else:
            choice = ModelChoice(
                model_name=self.model_fast,
                api_url=self.url_fast,
                max_tokens=1024,
                temperature=0.8,
                reason=f"complexity {score} < {self.threshold_expert} → fast",
                tier="fast",
                complexity_score=score,
            )

        logger.debug(
            "model_router: score=%d tier=%s model=%s (%s)",
            score, choice.tier, choice.model_name, choice.reason,
        )
        return choice

    def should_escalate(self, response: str, model_used: ModelChoice) -> ModelChoice | None:
        """Check if *response* is weak and return an escalated choice, or ``None``.

        Escalation path: fast → expert → reasoning.  If already at the
        reasoning tier there is nowhere to escalate.
        """
        if model_used.tier == "reasoning":
            return None

        if not self._is_weak_response(response):
            return None

        if model_used.tier == "fast":
            escalated = ModelChoice(
                model_name=self.model_expert,
                api_url=self.url_expert,
                max_tokens=2048,
                temperature=0.7,
                reason=f"escalated from fast (weak response)",
                tier="expert",
                complexity_score=model_used.complexity_score,
            )
        else:  # expert → reasoning
            escalated = ModelChoice(
                model_name=self.model_reasoning,
                api_url=self.url_reasoning,
                max_tokens=4096,
                temperature=0.6,
                reason=f"escalated from expert (weak response)",
                tier="reasoning",
                complexity_score=model_used.complexity_score,
            )

        logger.info(
            "model_router: escalating %s → %s (%s)",
            model_used.tier, escalated.tier, escalated.reason,
        )
        return escalated

    # ── internals ─────────────────────────────────────────────────

    @staticmethod
    def _is_weak_response(response: str) -> bool:
        """Return ``True`` if *response* looks like a weak / hedging answer."""
        if not response or not response.strip():
            return True

        stripped = response.strip()

        # Very short responses are suspect
        if len(stripped) < 40:
            return True

        lower = stripped.lower()
        for phrase in _WEAK_PHRASES:
            if phrase in lower:
                return True

        # Mostly whitespace / filler
        words = stripped.split()
        if len(words) < 8:
            return True

        return False


# ──────────────────────────────────────────────────────────────────
# Module-level convenience
# ──────────────────────────────────────────────────────────────────

_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    """Return (and lazily create) the singleton ``ModelRouter``."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def reset_router() -> None:
    """Reset the singleton — mainly for tests."""
    global _router
    _router = None
