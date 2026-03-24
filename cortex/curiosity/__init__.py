"""Atlas Curiosity Engine — analytical scientist mindset for continuous improvement.

The curiosity engine runs as a background process that:
- Observes patterns in Atlas's work (tool usage, repeated tasks, error rates)
- Forms hypotheses about improvements
- Proposes experiments to validate hypotheses
- Learns from results and stores insights
- Surfaces novel approaches from cross-domain knowledge

Architecture based on Derek Thomas's original design:
- EleganceScorer — measures solution beauty (fewer free params, random residuals)
- ResidualAnalyzer — examines what models get wrong to find missing physics
- PerspectiveRotator — 8 scientific lenses, maximizes cognitive distance
- DriveController — research/practical/urgent modes control exploration depth
- CrossDomainConnector — archetype-based analogy graph across domains

Module ownership: Curiosity engine — autonomous improvement and discovery
"""
from __future__ import annotations

from cortex.curiosity.connector import Analogy, CrossDomainConnector
from cortex.curiosity.drive import DriveController, DriveThresholds
from cortex.curiosity.elegance import EleganceBreakdown, EleganceScorer
from cortex.curiosity.engine import CuriosityEngine
from cortex.curiosity.hypothesis import Hypothesis, HypothesisTracker
from cortex.curiosity.observer import PatternObserver
from cortex.curiosity.perspectives import Lens, PerspectiveRotator
from cortex.curiosity.residuals import ResidualAnalyzer, ResidualFinding

__all__ = [
    "Analogy",
    "CrossDomainConnector",
    "CuriosityEngine",
    "DriveController",
    "DriveThresholds",
    "EleganceBreakdown",
    "EleganceScorer",
    "Hypothesis",
    "HypothesisTracker",
    "Lens",
    "PatternObserver",
    "PerspectiveRotator",
    "ResidualAnalyzer",
    "ResidualFinding",
]
