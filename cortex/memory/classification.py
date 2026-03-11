"""Memory classification — decide what to keep vs drop.

Heuristic keyword-based classifier; no ML dependencies.
"""
from __future__ import annotations


KEEP_TYPES = frozenset([
    "preference", "fact", "correction", "personal_detail",
    "relationship", "milestone", "instruction",
])

DROP_TYPES = frozenset(["chit_chat", "greeting", "filler", "small_talk"])


def classify_memory(text: str) -> str:
    """Heuristic: classify whether to keep or drop this memory."""
    lower = text.lower()
    if len(text.split()) < 4:
        return "chit_chat"
    if any(kw in lower for kw in ("i like", "i love", "i hate", "i prefer", "i always", "my favorite")):
        return "preference"
    if any(kw in lower for kw in ("my name is", "i am", "i work", "i live", "i have", "my ")):
        return "fact"
    if any(kw in lower for kw in ("that's wrong", "actually", "you were wrong", "correction")):
        return "correction"
    return "fact"
