"""COLD path — async memory write queue.

Non-blocking: enqueue text for PII redaction → classification →
dedup → FTS5 upsert → optional ChromaDB vector upsert.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from cortex.memory.types import MemoryEntry
from cortex.memory.pii import redact_pii
from cortex.memory.classification import classify_memory, DROP_TYPES
from cortex.memory.vector import VectorStore, get_embedding

logger = logging.getLogger(__name__)


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
        mem_type = classify_memory(clean)
        if mem_type in DROP_TYPES:
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
