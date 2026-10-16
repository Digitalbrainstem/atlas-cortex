"""In-memory inverted index for O(1) keyword → knowledge node lookup.

The ``MemoryIndex`` holds a two-way mapping between normalised keywords and
knowledge-tree node IDs.  Lookups return results ranked by the number of
matching keywords (TF-based scoring) and run in microseconds since the
entire structure lives in a plain Python ``dict``.

The index is rebuilt at startup from the ``knowledge_nodes`` table and kept
in sync via ``add`` / ``remove`` as nodes are created or deleted.
"""

from __future__ import annotations

import json
import logging
import math
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ── Stop-words (common English words that add noise) ────────────────────
_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "as", "be", "was", "were",
    "been", "are", "am", "do", "did", "does", "has", "had", "have", "will",
    "would", "could", "should", "may", "might", "shall", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "we", "they", "me",
    "him", "her", "us", "them", "my", "your", "his", "its", "our", "their",
    "what", "which", "who", "whom", "when", "where", "why", "how", "not",
    "no", "so", "if", "then", "than", "too", "very", "just", "about",
    "up", "out", "all", "also", "into", "over", "some", "such", "only",
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


class MemoryIndex:
    """In-memory inverted index for O(1) keyword → knowledge lookup."""

    def __init__(self) -> None:
        self._index: dict[str, set[str]] = {}       # keyword → set of node_ids
        self._node_keywords: dict[str, set[str]] = {}  # node_id → keywords
        self._total_nodes: int = 0

    # ── Mutation ────────────────────────────────────────────────────────

    def add(self, node_id: str, keywords: list[str]) -> None:
        """Index *node_id* under every normalised keyword."""
        normalised = {self._normalise(k) for k in keywords} - {""}
        if not normalised:
            return
        self._node_keywords[node_id] = normalised
        for kw in normalised:
            self._index.setdefault(kw, set()).add(node_id)
        self._total_nodes = len(self._node_keywords)

    def remove(self, node_id: str) -> None:
        """Remove *node_id* from the index entirely."""
        keywords = self._node_keywords.pop(node_id, None)
        if not keywords:
            return
        for kw in keywords:
            bucket = self._index.get(kw)
            if bucket:
                bucket.discard(node_id)
                if not bucket:
                    del self._index[kw]
        self._total_nodes = len(self._node_keywords)

    # ── Query ───────────────────────────────────────────────────────────

    def search(
        self,
        keywords: list[str],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Return up to *top_k* ``(node_id, score)`` pairs ranked by match.

        Scoring uses TF–IDF-like weighting: keywords that appear in fewer
        nodes are worth more, and nodes that match more keywords rank higher.
        """
        normalised = [self._normalise(k) for k in keywords]
        normalised = [k for k in normalised if k and k in self._index]
        if not normalised:
            return []

        scores: Counter[str] = Counter()
        total = max(self._total_nodes, 1)
        for kw in normalised:
            hits = self._index.get(kw, set())
            idf = math.log(total / max(len(hits), 1)) + 1.0
            for nid in hits:
                scores[nid] += idf

        # Normalise to 0-1 range
        if not scores:
            return []
        max_score = max(scores.values())
        return [
            (nid, round(s / max_score, 4))
            for nid, s in scores.most_common(top_k)
        ]

    # ── Keyword Extraction ──────────────────────────────────────────────

    def extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from free text.

        Returns de-duplicated, stemmed, lowercased tokens with stop-words removed.
        """
        tokens = _WORD_RE.findall(text.lower())
        seen: set[str] = set()
        result: list[str] = []
        for tok in tokens:
            stemmed = _simple_stem(tok)
            if stemmed not in _STOP_WORDS and stemmed not in seen and len(stemmed) > 1:
                seen.add(stemmed)
                result.append(stemmed)
        return result

    # ── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "total_keywords": len(self._index),
            "total_nodes": self._total_nodes,
        }

    # ── Persistence ─────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Serialise the index to a JSON file."""
        data = {
            nid: sorted(kws)
            for nid, kws in self._node_keywords.items()
        }
        Path(path).write_text(json.dumps(data))

    def load(self, path: str) -> None:
        """Deserialise a previously saved index."""
        p = Path(path)
        if not p.exists():
            return
        data: dict[str, list[str]] = json.loads(p.read_text())
        self._index.clear()
        self._node_keywords.clear()
        for nid, kws in data.items():
            self.add(nid, kws)

    # ── Internal ────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(keyword: str) -> str:
        w = keyword.strip().lower().strip(string.punctuation)
        return _simple_stem(w)


def _simple_stem(word: str) -> str:
    """Minimal suffix stripping so 'refunds' matches 'refund' etc."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    if word.endswith("ness") and len(word) > 5:
        return word[:-4]
    if word.endswith("ed") and len(word) > 4:
        return word[:-2]
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word
