"""Context window management for Atlas CLI.

Tracks what's in the LLM's context window and enforces a token budget.
Items can be pinned to survive automatic compaction.
"""

# Module ownership: CLI context window budget management
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_COMPACT_TARGET = 0.75  # compact down to 75 % of max_tokens


@dataclass
class ContextItem:
    """An item pinned in the context window."""

    source: str  # file path, "conversation", "memory", etc.
    content: str
    token_estimate: int  # rough estimate: len(content) // 4
    pinned: bool = False  # pinned items survive compaction
    added_at: float = field(default_factory=time.time)


class ContextManager:
    """Manages the LLM context window budget.

    Token estimation uses the simple ``len(content) // 4`` heuristic —
    good enough for deciding when to shed context.
    """

    def __init__(self, max_tokens: int = 8192) -> None:
        self.max_tokens = max_tokens
        self._items: list[ContextItem] = []

    # ── mutators ────────────────────────────────────────────────────

    def add_file(self, path: str, content: str, pinned: bool = False) -> None:
        """Add a file to context, replacing any previous version."""
        self.remove(path)
        tokens = len(content) // 4
        self._items.append(
            ContextItem(
                source=path,
                content=content,
                token_estimate=tokens,
                pinned=pinned,
            )
        )
        log.debug("context +file %s (%d tok)", path, tokens)

    def add_memory(self, query: str, results: str) -> None:
        """Add memory search results to context."""
        source = f"memory:{query}"
        self.remove(source)
        tokens = len(results) // 4
        self._items.append(
            ContextItem(source=source, content=results, token_estimate=tokens)
        )
        log.debug("context +memory '%s' (%d tok)", query, tokens)

    def remove(self, source: str) -> bool:
        """Remove an item by *source* identifier. Returns True if found."""
        before = len(self._items)
        self._items = [it for it in self._items if it.source != source]
        removed = len(self._items) < before
        if removed:
            log.debug("context -item %s", source)
        return removed

    def pin(self, source: str) -> bool:
        """Pin an item so it survives compaction."""
        for it in self._items:
            if it.source == source:
                it.pinned = True
                return True
        return False

    def unpin(self, source: str) -> bool:
        """Unpin an item."""
        for it in self._items:
            if it.source == source:
                it.pinned = False
                return True
        return False

    def clear(self) -> None:
        """Clear all non-pinned items."""
        self._items = [it for it in self._items if it.pinned]

    # ── queries ─────────────────────────────────────────────────────

    @property
    def used_tokens(self) -> int:
        """Estimated tokens currently in context."""
        return sum(it.token_estimate for it in self._items)

    @property
    def available_tokens(self) -> int:
        """Tokens remaining before *max_tokens*."""
        return max(0, self.max_tokens - self.used_tokens)

    def list_items(self) -> list[dict]:
        """List all context items with metadata."""
        return [
            {
                "source": it.source,
                "tokens": it.token_estimate,
                "pinned": it.pinned,
                "added_at": it.added_at,
            }
            for it in self._items
        ]

    def get_context_string(self) -> str:
        """Build the full context string for the LLM."""
        if not self._items:
            return ""
        parts: list[str] = []
        for it in self._items:
            header = f"[{it.source}]"
            parts.append(f"{header}\n{it.content}")
        return "\n\n".join(parts)

    # ── compaction ──────────────────────────────────────────────────

    def compact(self) -> int:
        """Remove oldest non-pinned items until under 75 % capacity.

        Returns the number of items removed.
        """
        target = int(self.max_tokens * _COMPACT_TARGET)
        if self.used_tokens <= target:
            return 0

        # Sort unpinned items oldest-first for eviction
        unpinned = sorted(
            (it for it in self._items if not it.pinned),
            key=lambda it: it.added_at,
        )
        removed = 0
        for item in unpinned:
            if self.used_tokens <= target:
                break
            self._items.remove(item)
            removed += 1
            log.debug("compact: evicted %s (%d tok)", item.source, item.token_estimate)

        return removed
