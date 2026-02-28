"""Atlas Cortex Memory System — HOT/COLD architecture.

HOT path (read): synchronous, sub-50ms target
  - BM25 full-text search via SQLite FTS5
  - (Optional) ChromaDB vector search when available
  - RRF fusion of ranked results

COLD path (write): async, non-blocking (fire-and-forget via asyncio.Queue)
  - PII redaction
  - Memory decider (keep/drop/dedup heuristics)
  - Embedding + upsert to ChromaDB + FTS5 mirror

See docs/memory-system.md for full design.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Optional ChromaDB — graceful fallback to FTS5-only when not installed
try:
    import chromadb  # type: ignore[import-untyped]
    _HAS_CHROMADB = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    _HAS_CHROMADB = False

# ──────────────────────────────────────────────────────────────────
# PII redaction
# ──────────────────────────────────────────────────────────────────

_PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[CC]"),
    (re.compile(r"\b5[1-5]\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[CC]"),
]


def redact_pii(text: str) -> str:
    """Replace PII (email, phone, SSN, credit card) with placeholders."""
    for pattern, placeholder in _PII_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


# ──────────────────────────────────────────────────────────────────
# Memory entry types
# ──────────────────────────────────────────────────────────────────

_KEEP_TYPES = frozenset([
    "preference", "fact", "correction", "personal_detail",
    "relationship", "milestone", "instruction",
])

_DROP_TYPES = frozenset(["chit_chat", "greeting", "filler", "small_talk"])


def _classify_memory(text: str) -> str:
    """Heuristic: classify whether to keep or drop this memory."""
    lower = text.lower()
    # Very short = chit-chat
    if len(text.split()) < 4:
        return "chit_chat"
    # Preferences
    if any(kw in lower for kw in ("i like", "i love", "i hate", "i prefer", "i always", "my favorite")):
        return "preference"
    # Personal facts
    if any(kw in lower for kw in ("my name is", "i am", "i work", "i live", "i have", "my ")):
        return "fact"
    # Corrections
    if any(kw in lower for kw in ("that's wrong", "actually", "you were wrong", "correction")):
        return "correction"
    # Default: fact worth storing
    return "fact"


@dataclass
class MemoryEntry:
    doc_id: str
    user_id: str
    text: str
    memory_type: str = "fact"
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "interaction"


@dataclass
class MemoryHit:
    doc_id: str
    user_id: str
    text: str
    score: float
    source: str = "fts5"


# ──────────────────────────────────────────────────────────────────
# Vector store (optional ChromaDB wrapper)
# ──────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────
# Embedding helper
# ──────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────
# RRF fusion
# ──────────────────────────────────────────────────────────────────

def rrf_fuse(
    fts_hits: list[MemoryHit],
    vec_hits: list[MemoryHit],
    k: int = 60,
) -> list[MemoryHit]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank) across both lists."""
    scores: dict[str, float] = {}
    best_hit: dict[str, MemoryHit] = {}

    for rank, hit in enumerate(fts_hits, start=1):
        scores[hit.doc_id] = scores.get(hit.doc_id, 0.0) + 1.0 / (k + rank)
        best_hit[hit.doc_id] = hit

    for rank, hit in enumerate(vec_hits, start=1):
        scores[hit.doc_id] = scores.get(hit.doc_id, 0.0) + 1.0 / (k + rank)
        if hit.doc_id not in best_hit:
            best_hit[hit.doc_id] = hit

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results: list[MemoryHit] = []
    for doc_id, score in ranked:
        hit = best_hit[doc_id]
        results.append(MemoryHit(
            doc_id=hit.doc_id,
            user_id=hit.user_id,
            text=hit.text,
            score=score,
            source="rrf",
        ))
    return results


# ──────────────────────────────────────────────────────────────────
# HOT path — retrieval
# ──────────────────────────────────────────────────────────────────

def hot_query(
    query: str,
    user_id: str,
    conn: Any,
    top_k: int = 8,
    embedding: list[float] | None = None,
    vector_store: VectorStore | None = None,
) -> list[MemoryHit]:
    """Retrieve relevant memories for *query* using BM25 (FTS5).

    When *embedding* and *vector_store* are provided, also performs vector
    search and fuses results via RRF.  Falls back to FTS5-only otherwise.
    """
    start = time.monotonic()
    fts_results: list[MemoryHit] = []
    try:
        rows = conn.execute(
            """
            SELECT doc_id, user_id, text,
                   bm25(memory_fts) AS score
            FROM memory_fts
            WHERE memory_fts MATCH ?
              AND user_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (_fts_query(query), user_id, top_k),
        ).fetchall()
        for row in rows:
            fts_results.append(MemoryHit(
                doc_id=row["doc_id"],
                user_id=row["user_id"],
                text=row["text"],
                score=abs(float(row["score"])),
                source="fts5",
            ))
    except Exception as exc:
        logger.debug("HOT query error: %s", exc)

    # Vector search + RRF fusion when available
    if embedding is not None and vector_store is not None:
        vec_results: list[MemoryHit] = []
        try:
            for doc_id, text, score in vector_store.query(embedding, user_id, top_k):
                vec_results.append(MemoryHit(
                    doc_id=doc_id,
                    user_id=user_id,
                    text=text,
                    score=score,
                    source="vector",
                ))
        except Exception as exc:
            logger.debug("Vector query error: %s", exc)
        if vec_results:
            results = rrf_fuse(fts_results, vec_results)[:top_k]
        else:
            results = fts_results
    else:
        results = fts_results

    elapsed = (time.monotonic() - start) * 1000
    logger.debug("HOT query returned %d hits in %.1f ms", len(results), elapsed)
    return results


def _fts_query(text: str) -> str:
    """Build a simple FTS5 MATCH expression from free text."""
    # Escape special FTS5 characters
    clean = re.sub(r'[^\w\s]', ' ', text)
    tokens = clean.split()[:10]  # limit token count
    return " ".join(tokens) if tokens else '""'


def format_memory_context(hits: list[MemoryHit], max_chars: int = 1000) -> str:
    """Format memory hits as a compact context string for the LLM prompt."""
    if not hits:
        return ""
    lines = []
    total = 0
    for hit in hits:
        line = f"- {hit.text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────
# COLD path — async write queue
# ──────────────────────────────────────────────────────────────────

class MemoryWriter:
    """Non-blocking memory writer.  Enqueue items; worker processes them."""

    def __init__(
        self,
        conn: Any,
        vector_store: VectorStore | None = None,
        provider: Any = None,
    ) -> None:
        self._conn = conn
        self._vector_store = vector_store
        self._provider = provider
        self._queue: asyncio.Queue[MemoryEntry] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.ensure_future(self._worker())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def enqueue(self, text: str, user_id: str, tags: list[str] | None = None) -> None:
        """Fire-and-forget: queue text for background memory processing."""
        clean = redact_pii(text)
        mem_type = _classify_memory(clean)
        if mem_type in _DROP_TYPES:
            return
        doc_id = hashlib.sha256(f"{user_id}:{clean}".encode()).hexdigest()[:24]
        entry = MemoryEntry(
            doc_id=doc_id,
            user_id=user_id,
            text=clean,
            memory_type=mem_type,
            tags=tags or [],
        )
        await self._queue.put(entry)

    async def _worker(self) -> None:
        while self._running:
            try:
                entry = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._write(entry)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Memory write error: %s", exc)

    async def _write(self, entry: MemoryEntry) -> None:
        """Persist entry to FTS5 (and optionally ChromaDB)."""
        try:
            self._conn.execute(
                "DELETE FROM memory_fts WHERE doc_id = ?", (entry.doc_id,)
            )
            self._conn.execute(
                "INSERT INTO memory_fts (doc_id, user_id, text, type, tags) VALUES (?, ?, ?, ?, ?)",
                (
                    entry.doc_id,
                    entry.user_id,
                    entry.text,
                    entry.memory_type,
                    " ".join(entry.tags),
                ),
            )
            self._conn.commit()
            logger.debug("Memory written: %s", entry.doc_id)
        except Exception as exc:
            logger.error("Memory FTS write failed: %s", exc)

        # Upsert to ChromaDB when available
        if self._vector_store is not None and self._provider is not None:
            try:
                emb = await get_embedding(entry.text, self._provider)
                if emb is not None:
                    self._vector_store.upsert(
                        doc_id=entry.doc_id,
                        text=entry.text,
                        embedding=emb,
                        metadata={
                            "user_id": entry.user_id,
                            "type": entry.memory_type,
                            "tags": " ".join(entry.tags),
                        },
                    )
                    logger.debug("Memory vector upserted: %s", entry.doc_id)
            except Exception as exc:
                logger.error("Memory vector write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────
# Convenience wrapper
# ──────────────────────────────────────────────────────────────────

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

        # Try to set up vector store; gracefully degrade if unavailable
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


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

__all__ = [
    "redact_pii",
    "MemoryEntry",
    "MemoryHit",
    "VectorStore",
    "get_embedding",
    "rrf_fuse",
    "hot_query",
    "format_memory_context",
    "MemoryWriter",
    "MemorySystem",
]
