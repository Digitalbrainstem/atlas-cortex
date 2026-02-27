"""Knowledge index — SQLite FTS5-backed document store with privacy enforcement (Phase I5.1)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from cortex.integrations.knowledge.privacy import AccessGate
from cortex.integrations.knowledge.processor import DocumentChunk
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

_TRIGGER_PHRASES = [
    "in my files",
    "in my documents",
    "do i have",
    "what does my",
    "from my email",
    "in my calendar",
]


def _fts_query(text: str) -> str:
    """Sanitize free text into a safe FTS5 MATCH expression (avoids syntax errors)."""
    clean = re.sub(r"[^\w\s]", " ", text)
    tokens = clean.split()[:10]
    return " ".join(tokens) if tokens else '""'


class KnowledgeIndex:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._gate = AccessGate(conn)

    def add_document(self, chunks: list[DocumentChunk], metadata: dict) -> int:
        content_hash = metadata.get("content_hash", "")
        if content_hash:
            existing = self._conn.execute(
                "SELECT doc_id FROM knowledge_docs WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if existing:
                logger.debug("Skipping already-indexed doc hash=%s", content_hash)
                return 0

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        for chunk in chunks:
            self._conn.execute(
                """INSERT OR REPLACE INTO knowledge_docs
                   (doc_id, owner_id, access_level, source, source_path,
                    content_type, title, chunk_index, total_chunks,
                    content_hash, created_at, modified_at, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{chunk.doc_id}_{chunk.chunk_index}",
                    metadata.get("owner_id", ""),
                    metadata.get("access_level", "private"),
                    metadata.get("source", ""),
                    metadata.get("source_path"),
                    metadata.get("content_type"),
                    chunk.title,
                    chunk.chunk_index,
                    chunk.total_chunks,
                    content_hash,
                    now,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """INSERT INTO knowledge_fts
                   (doc_id, owner_id, access_level, source, title, text, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{chunk.doc_id}_{chunk.chunk_index}",
                    metadata.get("owner_id", ""),
                    metadata.get("access_level", "private"),
                    metadata.get("source", ""),
                    chunk.title,
                    chunk.text,
                    " ".join(chunk.tags),
                ),
            )
            inserted += 1

        self._conn.commit()
        return inserted

    def search(
        self,
        query: str,
        user_id: str,
        identity_confidence: str = "high",
        top_k: int = 8,
    ) -> list[dict]:
        where, params = self._gate.filter_query(user_id, identity_confidence)
        sql = (
            f"SELECT doc_id, title, text, access_level, "
            f"bm25(knowledge_fts) AS score "
            f"FROM knowledge_fts "
            f"WHERE knowledge_fts MATCH ? AND {where} "
            f"ORDER BY score "
            f"LIMIT ?"
        )
        try:
            rows = self._conn.execute(sql, [_fts_query(query)] + params + [top_k]).fetchall()
        except Exception as exc:
            logger.warning("knowledge search error: %s", exc)
            return []
        return [
            {
                "doc_id": r["doc_id"],
                "title": r["title"],
                "text": r["text"],
                "access_level": r["access_level"],
                "score": abs(float(r["score"])),
            }
            for r in rows
        ]

    def remove_document(self, doc_id: str) -> None:
        self._conn.execute("DELETE FROM knowledge_docs WHERE doc_id = ?", (doc_id,))
        self._conn.execute("DELETE FROM knowledge_fts WHERE doc_id = ?", (doc_id,))
        self._conn.commit()

    def list_documents(self, owner_id: str | None = None) -> list[dict]:
        if owner_id:
            rows = self._conn.execute(
                "SELECT doc_id, owner_id, access_level, source, source_path, "
                "content_type, title, chunk_index, total_chunks, indexed_at "
                "FROM knowledge_docs WHERE owner_id = ? ORDER BY indexed_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT doc_id, owner_id, access_level, source, source_path, "
                "content_type, title, chunk_index, total_chunks, indexed_at "
                "FROM knowledge_docs ORDER BY indexed_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        total = self._conn.execute(
            "SELECT COUNT(*) FROM knowledge_docs"
        ).fetchone()[0]

        by_source = {}
        for row in self._conn.execute(
            "SELECT source, COUNT(*) AS n FROM knowledge_docs GROUP BY source"
        ).fetchall():
            by_source[row["source"]] = row["n"]

        by_access = {}
        for row in self._conn.execute(
            "SELECT access_level, COUNT(*) AS n FROM knowledge_docs GROUP BY access_level"
        ).fetchall():
            by_access[row["access_level"]] = row["n"]

        return {"total_docs": total, "by_source": by_source, "by_access_level": by_access}


class KnowledgePlugin(CortexPlugin):
    plugin_id = "knowledge"
    display_name = "Personal Knowledge"
    plugin_type = "knowledge"

    def __init__(self, conn: Any) -> None:
        self._index = KnowledgeIndex(conn)

    async def setup(self, config: dict) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(self, message: str, context: dict) -> CommandMatch:
        lower = message.lower()
        for phrase in _TRIGGER_PHRASES:
            if phrase in lower:
                return CommandMatch(matched=True, intent="knowledge_search", confidence=0.8)
        return CommandMatch(matched=False)

    async def handle(self, message: str, match: CommandMatch, context: dict) -> CommandResult:
        user_id = context.get("user_id", "")
        results = self._index.search(message, user_id=user_id)
        if not results:
            return CommandResult(success=True, response="I couldn't find anything relevant in your documents.")
        snippets = "\n".join(
            f"• [{r['title']}] {r['text'][:200]}..." for r in results[:3]
        )
        return CommandResult(success=True, response=f"Here's what I found:\n{snippets}")
