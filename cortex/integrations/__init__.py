"""Atlas Cortex integrations package.

Provides concrete plugin implementations for Layer 2 dispatch:
  - Home Assistant (smart home control)
  - Knowledge (document indexing & search)
  - Learning (fallthrough analysis, pattern lifecycle, nightly evolution)
  - Lists (multi-backend list management)
"""

from __future__ import annotations

from cortex.integrations.ha import HAPlugin
from cortex.integrations.knowledge import AccessGate, DocumentProcessor, KnowledgeIndex
from cortex.integrations.learning import (
    FallthroughAnalyzer,
    NightlyEvolution,
    PatternLifecycle,
)
from cortex.integrations.lists import ListPlugin

__all__ = [
    "HAPlugin",
    "KnowledgeIndex",
    "DocumentProcessor",
    "AccessGate",
    "FallthroughAnalyzer",
    "PatternLifecycle",
    "NightlyEvolution",
    "ListPlugin",
]
