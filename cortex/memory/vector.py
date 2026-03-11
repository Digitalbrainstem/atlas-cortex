"""Optional ChromaDB vector store + embedding helper.

Gracefully degrades to no-op when chromadb is not installed.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb  # type: ignore[import-untyped]
    _HAS_CHROMADB = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    _HAS_CHROMADB = False

from cortex.memory.types import MemoryHit


class VectorStore:
    """Thin wrapper around a persistent ChromaDB collection."""

    def __init__(self, data_dir: str) -> None:
        if not _HAS_CHROMADB:
            raise RuntimeError("chromadb is not installed")
        self._client = chromadb.PersistentClient(path=data_dir)
        self._collection = self._client.get_or_create_collection("atlas_memory")

    @property
    def is_available(self) -> bool:
        return self._collection is not None

    def upsert(
        self,
        doc_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def query(
        self,
        embedding: list[float],
        user_id: str,
        top_k: int = 8,
    ) -> list[tuple[str, str, float]]:
        """Return list of (doc_id, text, score) tuples."""
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"user_id": user_id},
        )
        out: list[tuple[str, str, float]] = []
        if results and results["ids"]:
            ids = results["ids"][0]
            docs = results["documents"][0] if results["documents"] else [""] * len(ids)
            dists = results["distances"][0] if results["distances"] else [0.0] * len(ids)
            for doc_id, text, dist in zip(ids, docs, dists):
                score = 1.0 / (1.0 + dist)
                out.append((doc_id, text, score))
        return out


async def get_embedding(text: str, provider: Any = None) -> list[float] | None:
    """Get an embedding vector from the LLM provider, or None if unavailable."""
    if provider is None:
        return None
    try:
        result = await provider.embed(text)
        return result
    except Exception as exc:
        logger.debug("Embedding failed: %s", exc)
        return None
