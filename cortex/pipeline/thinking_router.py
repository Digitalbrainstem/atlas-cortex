"""Thinking Router — decides /think vs /no_think for Qwen3.5 models.

Independent of model selection: a 4B model can use thinking for hard
questions and a 9B can skip it for simple ones.  The decision is applied
by prepending the appropriate tag to the user message before it reaches
the LLM provider.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── env-driven defaults ──────────────────────────────────────────
THINKING_CONFIDENCE_THRESHOLD: float = float(
    os.environ.get("THINKING_CONFIDENCE_THRESHOLD", "0.5"),
)

# ── result dataclass ─────────────────────────────────────────────

@dataclass
class ThinkingDecision:
    """Routing result returned by :meth:`ThinkingRouter.route`."""

    use_thinking: bool
    reason: str = ""
    confidence_threshold: float = THINKING_CONFIDENCE_THRESHOLD


# ── keyword / pattern banks ──────────────────────────────────────

_NO_THINK_EXACT: frozenset[str] = frozenset([
    "yes", "no", "ok", "okay", "sure", "thanks", "thank you", "yep",
    "nope", "yeah", "nah", "bye", "hi", "hey", "hello", "yo",
    "good", "great", "fine", "cool", "nice", "right",
])

_NO_THINK_PATTERNS: list[re.Pattern[str]] = [
    # greetings & small talk
    re.compile(r"^(good\s+)?(morning|afternoon|evening|night)\b", re.I),
    re.compile(r"^how\s+are\s+you\b", re.I),
    re.compile(r"^what'?s\s+up\b", re.I),
    re.compile(r"^hey\s+there\b", re.I),
    # simple factual
    re.compile(r"^what\s+time\b", re.I),
    re.compile(r"^what'?s?\s+the\s+(time|date|weather|temperature)\b", re.I),
    # home commands
    re.compile(r"\b(turn\s+(on|off)|set\s+(a\s+)?timer|play\s+music|stop\s+music)\b", re.I),
    re.compile(r"\b(lock|unlock)\s+(the\s+)?(door|house)\b", re.I),
    re.compile(r"\b(dim|brighten)\s+(the\s+)?lights?\b", re.I),
    re.compile(r"\bset\s+(the\s+)?volume\b", re.I),
    # follow-up / confirmation
    re.compile(r"^(tell\s+me\s+more|go\s+on|continue|keep\s+going)\b", re.I),
    re.compile(r"^(that'?s?\s+(all|it|enough))\b", re.I),
]

_THINK_PATTERNS: list[re.Pattern[str]] = [
    # math / logic
    re.compile(r"\b(solve|calculate|compute|integral|derivative|equation)\b", re.I),
    re.compile(r"\b(proof|theorem|prove|disprove|contradict)\b", re.I),
    re.compile(r"\b(probability|statistics|standard\s+deviation)\b", re.I),
    # code
    re.compile(r"\b(write\s+code|write\s+a?\s*function|implement|refactor|debug)\b", re.I),
    re.compile(r"```", re.I),  # code fences in prompt
    re.compile(r"\b(python|javascript|typescript|rust|golang|java|c\+\+)\s+(code|script|function|class)\b", re.I),
    # explanation / reasoning
    re.compile(r"\b(explain|why\s+does|how\s+does|what\s+causes)\b", re.I),
    re.compile(r"\b(step[- ]by[- ]step|reason\s+through|think\s+carefully)\b", re.I),
    re.compile(r"\b(analyze|evaluate|assess|critique)\b", re.I),
    # comparison
    re.compile(r"\b(compare|contrast|difference\s+between|which\s+is\s+better)\b", re.I),
    re.compile(r"\bvs\.?\b", re.I),
    re.compile(r"\b(pros?\s+and\s+cons?|trade[- ]?offs?|advantages?)\b", re.I),
    # technical / medical
    re.compile(r"\b(diagnose|symptoms?|treatment|medication|dosage)\b", re.I),
    re.compile(r"\b(architecture|design\s+pattern|algorithm|complexity)\b", re.I),
    # creative writing
    re.compile(r"\b(write\s+(a|me|an?)\s+.{0,30}(story|poem|essay|article|letter|speech))\b", re.I),
    re.compile(r"\b(compose|draft|create\s+(a|an?))\b", re.I),
    # multi-step
    re.compile(r"\b(plan|strategy|outline|roadmap)\b", re.I),
    re.compile(r"\b(first|then|finally|steps?\s+to)\b", re.I),
]

# ── confidence heuristics ────────────────────────────────────────

_HEDGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bI\s+think\b", re.I),
    re.compile(r"\bI'?m\s+not\s+sure\b", re.I),
    re.compile(r"\bmaybe\b", re.I),
    re.compile(r"\bperhaps\b", re.I),
    re.compile(r"\bpossibly\b", re.I),
    re.compile(r"\bI\s+believe\b", re.I),
    re.compile(r"\bit\s+might\b", re.I),
    re.compile(r"\bnot\s+entirely\s+(sure|certain|clear)\b", re.I),
    re.compile(r"\bI\s+could\s+be\s+wrong\b", re.I),
    re.compile(r"\bdon'?t\s+quote\s+me\b", re.I),
]

_SHORT_MESSAGE_THRESHOLD = 20  # characters
_LONG_MESSAGE_THRESHOLD = 120  # characters


# ── main class ───────────────────────────────────────────────────

class ThinkingRouter:
    """Decide whether to enable extended thinking for a given query.

    Usage::

        router = ThinkingRouter()
        decision = router.route("explain how TCP works")
        prompt = router.apply(message, decision)
        # prompt starts with "/think " or "/no_think "
    """

    def __init__(
        self,
        confidence_threshold: float = THINKING_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.confidence_threshold = confidence_threshold

    # ── public API ───────────────────────────────────────────────

    def route(
        self,
        message: str,
        context: dict | None = None,
    ) -> ThinkingDecision:
        """Classify *message* and return a :class:`ThinkingDecision`."""
        stripped = message.strip()
        lower = stripped.lower()

        # very short / single-word → fast
        if not stripped or len(stripped) < _SHORT_MESSAGE_THRESHOLD:
            if lower in _NO_THINK_EXACT or len(stripped.split()) <= 2:
                return ThinkingDecision(
                    use_thinking=False,
                    reason="short_or_simple",
                    confidence_threshold=self.confidence_threshold,
                )

        # exact-match fast words (for slightly longer sentences starting with them)
        if lower in _NO_THINK_EXACT:
            return ThinkingDecision(
                use_thinking=False,
                reason="exact_fast_match",
                confidence_threshold=self.confidence_threshold,
            )

        # score-based routing (positive → think, negative → no_think)
        score = 0

        for pat in _NO_THINK_PATTERNS:
            if pat.search(stripped):
                score -= 1

        for pat in _THINK_PATTERNS:
            if pat.search(stripped):
                score += 1

        # length heuristic: long messages are usually complex
        if len(stripped) > _LONG_MESSAGE_THRESHOLD:
            score += 1

        # question marks with "why" / "how" boost thinking
        if "?" in stripped and re.search(r"\b(why|how)\b", lower):
            score += 1

        # context hints
        if context:
            conv_len = context.get("conversation_length", 0)
            if conv_len > 10:
                score += 1

        use_thinking = score > 0
        reason = f"score={score}"
        if score > 0:
            reason = f"think(score={score})"
        elif score < 0:
            reason = f"no_think(score={score})"
        else:
            reason = "default_no_think(score=0)"

        logger.debug("thinking_router: %r → %s", stripped[:80], reason)

        return ThinkingDecision(
            use_thinking=use_thinking,
            reason=reason,
            confidence_threshold=self.confidence_threshold,
        )

    @staticmethod
    def apply(message: str, decision: ThinkingDecision) -> str:
        """Prepend the Qwen3.5 thinking tag to *message*."""
        tag = "/think" if decision.use_thinking else "/no_think"
        return f"{tag} {message}"

    # ── confidence grounding ─────────────────────────────────────

    @staticmethod
    def check_confidence(response: str, query: str) -> float:
        """Return a 0.0–1.0 confidence score for a no_think *response*.

        Low confidence signals:
        - Very short response
        - Hedging language ("I think", "maybe", etc.)
        - Response largely repeats the query
        - Generic filler phrases
        """
        if not response or not response.strip():
            return 0.0

        resp = response.strip()
        score = 1.0

        # very short response for a non-trivial query
        if len(resp) < 30 and len(query.strip()) > 20:
            score -= 0.3

        # hedging language
        hedge_count = sum(1 for p in _HEDGE_PATTERNS if p.search(resp))
        score -= hedge_count * 0.15

        # response repeats the query almost verbatim
        query_lower = query.strip().lower()
        resp_lower = resp.lower()
        if query_lower and query_lower in resp_lower:
            score -= 0.3

        # single-sentence response for a complex-looking query
        sentences = [s.strip() for s in re.split(r"[.!?]+", resp) if s.strip()]
        if len(sentences) <= 1 and len(query.strip()) > 60:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def should_retry_with_thinking(
        self,
        response: str,
        query: str,
    ) -> bool:
        """Return *True* if the response looks weak and should be retried
        with thinking enabled."""
        confidence = self.check_confidence(response, query)
        should_retry = confidence < self.confidence_threshold
        if should_retry:
            logger.info(
                "thinking_router: low confidence %.2f < %.2f, recommending retry "
                "with thinking for: %r",
                confidence,
                self.confidence_threshold,
                query[:80],
            )
        return should_retry
