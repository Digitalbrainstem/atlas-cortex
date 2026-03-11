"""Memory data types — shared across HOT and COLD paths."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryEntry:
    """In-flight memory item queued for COLD-path processing."""
    doc_id: str
    user_id: str
    text: str
    memory_type: str = "fact"
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "interaction"


@dataclass
class MemoryHit:
    """Search result from the HOT path (BM25 or vector or fused)."""
    doc_id: str
    user_id: str
    text: str
    score: float
    source: str = "fts5"
