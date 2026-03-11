"""HOT path — synchronous memory retrieval (sub-50ms target).

BM25 via SQLite FTS5, optional ChromaDB vector search, RRF fusion.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from cortex.memory.types import MemoryHit
from cortex.memory.vector import VectorStore

logger = logging.getLogger(__name__)


def _fts_query(text: str) -> str:
    """Build a simple FTS5 MATCH expression from free text."""
    clean = re.sub(r'[^\w\s]', ' ', text)
    tokens = clean.split()[:10]
    return " ".join(tokens) if tokens else '""'


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
