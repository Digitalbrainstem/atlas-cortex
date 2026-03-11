"""Atlas Cortex Memory System — HOT/COLD architecture.

Sub-modules:
  pii            — PII redaction (email, phone, SSN, CC)
  classification — Memory type classification (keep/drop heuristics)
  types          — MemoryEntry, MemoryHit dataclasses
  vector         — Optional ChromaDB wrapper + embedding helper
  hot            — HOT path: BM25 + vector search + RRF fusion
  cold           — COLD path: async write queue
  controller     — MemorySystem unified interface

See docs/memory-system.md for full design.
"""

# Module ownership: Memory system: HOT recall, COLD write
from __future__ import annotations

# Re-export public API from submodules for backward compatibility
from cortex.memory.pii import redact_pii
from cortex.memory.classification import classify_memory, KEEP_TYPES, DROP_TYPES
from cortex.memory.types import MemoryEntry, MemoryHit
from cortex.memory.vector import VectorStore, get_embedding
from cortex.memory.hot import rrf_fuse, hot_query, format_memory_context
from cortex.memory.cold import MemoryWriter
from cortex.memory.controller import MemorySystem, get_memory_system, set_memory_system

# Backward compat aliases (old private names used in tests)
_classify_memory = classify_memory
_KEEP_TYPES = KEEP_TYPES
_DROP_TYPES = DROP_TYPES

__all__ = [
    "redact_pii",
    "classify_memory",
    "KEEP_TYPES",
    "DROP_TYPES",
    "MemoryEntry",
    "MemoryHit",
    "VectorStore",
    "get_embedding",
    "rrf_fuse",
    "hot_query",
    "format_memory_context",
    "MemoryWriter",
    "MemorySystem",
    "get_memory_system",
    "set_memory_system",
]
