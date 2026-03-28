"""Multi-model router for the Atlas CLI.

Picks the *fast* or *thinking* model based on a simple heuristic
analysis of the user message and session state.

Module ownership: CLI model selection logic
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.cli.config import AtlasConfig

# Patterns that suggest a complex / reasoning-heavy request
_THINKING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(explain|analyze|compare|evaluate|design|architect)\b", re.I),
    re.compile(r"\b(step[- ]by[- ]step|think carefully|reason through)\b", re.I),
    re.compile(r"\b(proof|theorem|derive|prove)\b", re.I),
    re.compile(r"\b(refactor|rewrite|implement|build)\b", re.I),
    re.compile(r"\b(plan|strategy|trade[- ]?off)\b", re.I),
    re.compile(r"\b(debug|diagnose|root[- ]?cause)\b", re.I),
    re.compile(r"```", re.I),  # code fences often imply code generation
]

# Patterns that suggest a quick / factual request
_FAST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(what is|what's|who is|define|how old|when did)\b", re.I),
    re.compile(r"\b(convert|translate|summarize|tldr)\b", re.I),
    re.compile(r"\b(yes|no|true|false)\??\s*$", re.I),
    re.compile(r"^\S+\s*\?$"),  # very short questions
]

_LONG_MESSAGE_THRESHOLD = 500  # chars — longer messages lean toward thinking model


class ModelRouter:
    """Route queries to fast or thinking model based on complexity."""

    def __init__(self, config: AtlasConfig) -> None:
        self._fast = config.model.fast
        self._thinking = config.model.thinking

    @property
    def fast_model(self) -> str:
        return self._fast

    @property
    def thinking_model(self) -> str:
        return self._thinking

    def select_model(
        self,
        message: str,
        *,
        force: str | None = None,
        message_count: int = 0,
    ) -> str:
        """Return the model name to use for *message*.

        Parameters
        ----------
        message:
            The user's latest message.
        force:
            If ``"fast"`` or ``"think"``, bypass heuristics.
        message_count:
            Number of messages in the session so far (longer sessions
            may benefit from the thinking model for continuity).
        """
        if force == "fast":
            return self._fast
        if force in ("think", "thinking"):
            return self._thinking

        score = 0  # positive → thinking, negative → fast

        for pat in _THINKING_PATTERNS:
            if pat.search(message):
                score += 1

        for pat in _FAST_PATTERNS:
            if pat.search(message):
                score -= 1

        if len(message) > _LONG_MESSAGE_THRESHOLD:
            score += 1

        if message_count > 20:
            score += 1

        return self._thinking if score > 0 else self._fast
