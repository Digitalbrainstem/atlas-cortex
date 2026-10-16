"""Lightweight regex-based intent classification for Layer 2 plugins.

Classifies a user message into one of three intents:

* **inform** — Direct factual answer (default for action plugins).
* **learn** — Answer plus guided discovery (for knowledge/education plugins).
* **explore** — Fall through to LLM for rich, open-ended discussion.

Design goal: < 1 ms, zero external dependencies.
"""

from __future__ import annotations

import re
from typing import Any


def command_scope(context: dict[str, Any]) -> tuple[str, ...] | None:
    """Never infer a shared-device speaker from an anonymous user identifier."""
    identity = context.get("turn_identity")
    if identity is None:
        return None
    if (identity.device_id and not context.get("speaker_id")
            and identity.user_id in ("default", "unknown", "anonymous")):
        return None
    return (
        identity.user_id, identity.conversation_id, identity.source,
        identity.device_id or "", context.get("room") or "", context.get("speaker_id") or "",
    )


def correction_fragment(message: str) -> str | None:
    match = re.fullmatch(
        r"\s*(?:no|actually|sorry|i meant)[,\s]+(.+?)\s*[.!]?\s*",
        message, re.IGNORECASE,
    )
    if match is None:
        return None
    text = match.group(1)
    if re.match(
        r"^(?:why|what|how|when|where|who|explain|tell|describe|compare|"
        r"can|could|is|are|do|does)\b", text, re.IGNORECASE,
    ):
        return None
    return text

# ── Pattern banks ────────────────────────────────────────────────

_LEARN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\btell me about\b",
        r"\bexplain\b",
        r"\bhow does\b",
        r"\bhow do\b",
        r"\bwhy does\b",
        r"\bwhy do\b",
        r"\bwhy is\b",
        r"\bwhat causes\b",
        r"\bteach me\b",
        r"\bhelp me understand\b",
        r"\bwalk me through\b",
        r"\bbreak down\b",
        r"\bwhat.s the difference between\b",
    ]
]

_INFORM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^what is\b",
        r"^what are\b",
        r"^when is\b",
        r"^when did\b",
        r"^where is\b",
        r"^where are\b",
        r"^who is\b",
        r"^how much\b",
        r"^how many\b",
        r"^how long\b",
        r"^how far\b",
        r"^how old\b",
        r"^is it\b",
        r"^are there\b",
        r"^can you\b",
        r"^do you know\b",
    ]
]

_EXPLORE_INDICATORS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\band\b.*\bbut\b",
        r"\bwhat if\b",
        r"\bhow would\b",
        r"\bcould you\b.*\band\b",
        r"\bimagine\b",
        r"\bcompare\b",
        r"\bpros and cons\b",
        r"\bthink about\b",
        r"\bphilosophy\b",
        r"\bopinion\b",
    ]
]


def classify_intent(message: str, plugin_type: str = "action") -> str:
    """Classify query intent as ``'inform'``, ``'learn'``, or ``'explore'``.

    Args:
        message: The raw user message.
        plugin_type: The type of the matching plugin (``'action'``,
            ``'knowledge'``, ``'list_backend'``).  Knowledge plugins
            default to ``'learn'`` instead of ``'inform'``.

    Returns:
        One of ``'inform'``, ``'learn'``, or ``'explore'``.
    """
    text = message.strip()
    if not text:
        return "inform"

    # Explore takes priority — complex, multi-clause queries
    for pat in _EXPLORE_INDICATORS:
        if pat.search(text):
            return "explore"

    # Learn patterns — educational queries
    for pat in _LEARN_PATTERNS:
        if pat.search(text):
            return "learn"

    # Inform patterns — direct factual questions
    for pat in _INFORM_PATTERNS:
        if pat.search(text):
            return "inform"

    # Default: knowledge plugins lean toward learn, others toward inform
    return "learn" if plugin_type == "knowledge" else "inform"
