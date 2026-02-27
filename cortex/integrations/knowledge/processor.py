"""Document processor — extracts and chunks text from files (Phase I5.3).

Supports: plain text, markdown, CSV, JSON.
Intentionally minimal — external parsers (PDF, DOCX) are optional future extensions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONTENT_TYPE_MAP = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/toml",
}


@dataclass
class DocumentChunk:
    doc_id: str
    chunk_index: int
    total_chunks: int
    text: str
    title: str = ""
    tags: list[str] = field(default_factory=list)


class DocumentProcessor:
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 100

    def process_text(
        self,
        text: str,
        doc_id: str,
        title: str = "",
        tags: list[str] | None = None,
    ) -> list[DocumentChunk]:
        return self._split_text(text, doc_id, title, tags or [])

    def process_file(
        self,
        path: Path | str,
        owner_id: str,
        access_level: str = "private",
    ) -> tuple[list[DocumentChunk], dict]:
        path = Path(path)
        suffix = path.suffix.lower()
        content_type = _CONTENT_TYPE_MAP.get(suffix)
        if content_type is None:
            raise ValueError(f"Unsupported file type: {suffix!r}")

        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="utf-8", errors="replace")
            logger.warning("File %s had encoding errors — replaced invalid bytes", path)

        if suffix == ".json":
            try:
                text = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                text = raw
        elif suffix == ".csv":
            rows = list(csv.reader(raw.splitlines()))
            text = "\n".join(", ".join(row) for row in rows)
        else:
            text = raw

        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        doc_id = content_hash[:16]
        title = path.stem

        chunks = self._split_text(text, doc_id, title, [])
        metadata = {
            "doc_id": doc_id,
            "owner_id": owner_id,
            "access_level": access_level,
            "source": "file",
            "source_path": str(path),
            "content_type": content_type,
            "title": title,
            "content_hash": content_hash,
            "total_chunks": len(chunks),
        }
        return chunks, metadata

    def _split_text(
        self,
        text: str,
        doc_id: str,
        title: str,
        tags: list[str],
    ) -> list[DocumentChunk]:
        if not text:
            return [DocumentChunk(doc_id=doc_id, chunk_index=0, total_chunks=1, text="", title=title, tags=tags)]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self.CHUNK_SIZE
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start = end - self.CHUNK_OVERLAP

        total = len(chunks)
        return [
            DocumentChunk(
                doc_id=doc_id,
                chunk_index=i,
                total_chunks=total,
                text=chunk,
                title=title,
                tags=tags,
            )
            for i, chunk in enumerate(chunks)
        ]
