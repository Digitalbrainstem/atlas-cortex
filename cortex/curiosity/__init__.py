"""Atlas Curiosity Engine — analytical scientist mindset for continuous improvement.

The curiosity engine runs as a background process that:
- Observes patterns in Atlas's work (tool usage, repeated tasks, error rates)
- Forms hypotheses about improvements
- Proposes experiments to validate hypotheses
- Learns from results and stores insights
- Surfaces novel approaches from cross-domain knowledge

Module ownership: Curiosity engine — autonomous improvement and discovery
"""
from __future__ import annotations

from cortex.curiosity.engine import CuriosityEngine
from cortex.curiosity.hypothesis import Hypothesis, HypothesisTracker
from cortex.curiosity.observer import PatternObserver

__all__ = ["CuriosityEngine", "Hypothesis", "HypothesisTracker", "PatternObserver"]
