"""Interrupt classification for conversational pause & pivot (CE-4).

After a barge-in, the user's new utterance is classified to decide
whether Atlas should stop, resume, or pivot to a new query.
All classification is regex-based for speed (< 1 ms).
"""
from __future__ import annotations

import re

# ── Stop: user wants Atlas to be quiet ──────────────────────────────
_STOP_PATTERNS = re.compile(
    r"^("
    r"stop|okay stop|ok stop|atlas stop|"
    r"never\s*mind|nevermind|"
    r"shut up|be quiet|quiet|hush|"
    r"that'?s enough|enough|"
    r"cancel|forget it|"
    r"no more|stop talking|please stop"
    r")\.?!?\s*$",
    re.IGNORECASE,
)

# ── Resume: user wants Atlas to continue where it left off ──────────
_RESUME_PATTERNS = re.compile(
    r"^("
    r"go on|continue|keep going|go ahead|"
    r"uh[ -]?huh|mm[ -]?hmm|"
    r"yeah|yes|yep|yup|"
    r"okay|ok|"
    r"what else|and then\??|"
    r"finish|carry on|keep talking|"
    r"you were saying|go on please|"
    r"what were you saying|please continue"
    r")\.?\s*$",
    re.IGNORECASE,
)


def classify_interrupt(text: str) -> str:
    """Classify what the user wants after interrupting Atlas.

    Returns one of:
      - ``'stop'``   — User wants Atlas to be quiet.
      - ``'resume'`` — User wants Atlas to continue the paused response.
      - ``'pivot'``  — User is asking something new (default).

    The classifier is pure regex — no LLM call — so it runs in < 1 ms.
    """
    cleaned = text.strip()
    if not cleaned:
        return "stop"

    if _STOP_PATTERNS.match(cleaned):
        return "stop"

    if _RESUME_PATTERNS.match(cleaned):
        return "resume"

    return "pivot"
