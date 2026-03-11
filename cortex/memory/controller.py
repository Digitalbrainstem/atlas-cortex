"""Memory controller — unified HOT/COLD interface.

Single entry point for the rest of the system:
  - recall(query, user_id)  → HOT path retrieval
  - remember(text, user_id) → COLD path async write
"""
from __future__ import annotations

import logging
from typing import Any

from cortex.memory.types import MemoryHit
from cortex.memory.vector import VectorStore, get_embedding, _HAS_CHROMADB
from cortex.memory.hot import hot_query, format_memory_context
from cortex.memory.cold import MemoryWriter

logger = logging.getLogger(__name__)


class MemorySystem:
    """Unified interface for HOT (recall) and COLD (remember) paths."""

    def __init__(
        self,
        conn: Any,
        data_dir: str = "./data",
        provider: Any = None,
    ) -> None:
        self._conn = conn
        self._provider = provider

        self._vector_store: VectorStore | None = None
        if _HAS_CHROMADB:
            try:
                self._vector_store = VectorStore(data_dir)
            except Exception as exc:
                logger.warning("ChromaDB init failed, falling back to FTS5-only: %s", exc)

        self._writer = MemoryWriter(
            conn,
            vector_store=self._vector_store,
            provider=provider,
        )

    async def recall(
        self,
        query: str,
        user_id: str,
        top_k: int = 8,
    ) -> list[MemoryHit]:
        """Retrieve memories (HOT path).  Uses RRF fusion when vectors available."""
        embedding = await get_embedding(query, self._provider)
        return hot_query(
            query,
            user_id,
            self._conn,
            top_k=top_k,
            embedding=embedding,
            vector_store=self._vector_store,
        )

    async def remember(
        self,
        text: str,
        user_id: str,
        tags: list[str] | None = None,
    ) -> None:
        """Store a memory (COLD path, non-blocking)."""
        await self._writer.enqueue(text, user_id, tags)

    async def start(self) -> None:
        self._writer.start()

    async def stop(self) -> None:
        await self._writer.stop()
