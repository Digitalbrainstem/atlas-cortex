"""Proactive knowledge loader — loads relevant branches before the LLM call.

The ``ProactiveLoader`` sits between Layer 0 (context assembly) and Layer 3
(LLM streaming).  Before every LLM call it:

1. Extracts keywords from the user message.
2. Resolves them to tree nodes via the in-memory :class:`MemoryIndex`.
3. Checks which branches are already loaded in the context window.
4. Loads any missing branches from the :class:`KnowledgeTree`.
5. Bundles everything into a :class:`ContextBundle` the pipeline can inject.

Branch prediction uses a simple co-occurrence heuristic: if branch A was
loaded alongside branch B in the past, loading A proactively pre-fetches B.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from cortex.memory.knowledge_tree import KnowledgeTree
from cortex.memory.memory_index import MemoryIndex

log = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class ContextBundle:
    """Everything the pipeline needs for a single turn."""

    system_prompt: str = ""
    knowledge_context: str = ""
    conversation_summary: str = ""
    branches_loaded: list[str] = field(default_factory=list)
    load_time_ms: float = 0.0
    from_cache: bool = False


# ── Main class ──────────────────────────────────────────────────────────


class ProactiveLoader:
    """Automatically loads relevant knowledge before the LLM sees the message."""

    def __init__(
        self,
        tree: KnowledgeTree,
        index: MemoryIndex | None = None,
    ) -> None:
        self._tree = tree
        self._index = index or tree.index
        self._loaded_branches: dict[str, str] = {}  # node_id → content
        self._load_history: list[str] = []           # ordered list of loaded branch IDs
        self._cooccurrence: Counter[tuple[str, str]] = Counter()

    # ── Public API ──────────────────────────────────────────────────────

    async def prepare_context(
        self,
        message: str,
        current_context: str = "",
    ) -> ContextBundle:
        """Main entry point — called by Layer 0 before every LLM call."""
        t0 = time.monotonic()

        # 1. Extract keywords
        keywords = self._index.extract_keywords(message)

        # 2. Identify relevant branches
        branch_ids = self._tree.get_relevant_branches(message, max_branches=5)

        # 3. Determine which are already loaded (cache hits)
        to_load = [bid for bid in branch_ids if bid not in self._loaded_branches]
        cached = [bid for bid in branch_ids if bid in self._loaded_branches]

        # 4. Load missing branches
        for bid in to_load:
            content = self._tree.get_branch_content(bid, max_depth=2)
            if content:
                self._loaded_branches[bid] = content
                self._load_history.append(bid)

        # 5. Assemble context
        all_loaded = cached + to_load
        knowledge_parts: list[str] = []
        for bid in all_loaded:
            part = self._loaded_branches.get(bid, "")
            if part:
                knowledge_parts.append(part)

        # Update co-occurrence for prediction
        self._update_cooccurrence(all_loaded)

        elapsed = (time.monotonic() - t0) * 1000
        from_cache = len(to_load) == 0 and len(cached) > 0

        bundle = ContextBundle(
            system_prompt=self._tree.get_all_roots_summary(),
            knowledge_context="\n\n---\n\n".join(knowledge_parts),
            conversation_summary=current_context,
            branches_loaded=all_loaded,
            load_time_ms=round(elapsed, 2),
            from_cache=from_cache,
        )

        log.debug(
            "Prepared context: %d branches (%d cached, %d loaded) in %.1fms",
            len(all_loaded),
            len(cached),
            len(to_load),
            elapsed,
        )
        return bundle

    async def predict_next(self, message: str) -> list[str]:
        """Predict which branches the user will need next.

        Uses co-occurrence: if branch A was loaded alongside branch B in the
        past, and A is currently loaded, pre-load B.
        """
        candidates: Counter[str] = Counter()
        current = set(self._loaded_branches.keys())

        for (a, b), count in self._cooccurrence.items():
            if a in current and b not in current:
                candidates[b] += count
            elif b in current and a not in current:
                candidates[a] += count

        # Also consider keyword-based prediction from message
        branch_ids = self._tree.get_relevant_branches(message, max_branches=3)
        for bid in branch_ids:
            if bid not in current:
                candidates[bid] += 1

        return [bid for bid, _ in candidates.most_common(3)]

    def get_loaded_branches(self) -> list[str]:
        return list(self._loaded_branches.keys())

    def unload_branch(self, node_id: str) -> None:
        self._loaded_branches.pop(node_id, None)

    def unload_all(self) -> None:
        self._loaded_branches.clear()

    def get_context_token_count(self) -> int:
        """Estimate total tokens across all loaded branches."""
        total = sum(len(c) for c in self._loaded_branches.values())
        return total // 4  # chars-per-token heuristic

    # ── Internals ───────────────────────────────────────────────────────

    def _update_cooccurrence(self, branch_ids: list[str]) -> None:
        """Track which branches are loaded together for prediction."""
        for i, a in enumerate(branch_ids):
            for b in branch_ids[i + 1:]:
                pair = (min(a, b), max(a, b))
                self._cooccurrence[pair] += 1
