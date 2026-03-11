"""Learning engine — self-learning from interactions.

Module ownership: Pattern learning, fallthrough analysis, nightly evolution.

Re-exports from cortex.integrations.learning for the new module layout.
New code should import from cortex.learning, not cortex.integrations.learning.
"""
from __future__ import annotations

from cortex.integrations.learning.analyzer import FallthroughAnalyzer
from cortex.integrations.learning.lifecycle import PatternLifecycle
from cortex.integrations.learning.evolution import NightlyEvolution

__all__ = [
    "FallthroughAnalyzer",
    "PatternLifecycle",
    "NightlyEvolution",
]
