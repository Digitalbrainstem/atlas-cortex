"""Perspective rotation — systematically apply different scientific lenses.

Based on Derek Thomas's original Curiosity Engine design.  The key innovation:
when stuck, don't just retry — rotate to the *most different* scientific
framework to maximize cognitive distance.

Module ownership: Curiosity perspective rotation
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Lens:
    """A scientific framework for viewing problems."""

    name: str
    prompt: str
    concepts: list[str] = field(default_factory=list)


# ── The 8 canonical lenses ────────────────────────────────────────

LENSES: list[Lens] = [
    Lens(
        name="classical_physics",
        prompt=(
            "Analyze this as a wave/resonance problem. "
            "What are the natural frequencies? What are the "
            "boundary conditions? Is there impedance matching?"
        ),
        concepts=[
            "standing waves", "resonance", "impedance",
            "damping", "coupled oscillators",
        ],
    ),
    Lens(
        name="quantum_mechanics",
        prompt=(
            "Analyze this as a quantum system. "
            "What is the Hilbert space? What are the observables? "
            "Is there entanglement? What does the Bloch sphere look like?"
        ),
        concepts=[
            "superposition", "measurement", "entanglement",
            "transition amplitude", "Bloch sphere",
        ],
    ),
    Lens(
        name="fluid_dynamics",
        prompt=(
            "Analyze this as fluid flow. "
            "What is the Reynolds number? Where are the eddies? "
            "Is the flow laminar or turbulent? Where are the constrictions?"
        ),
        concepts=[
            "Reynolds number", "turbulence", "eddy currents",
            "Venturi effect", "Bernoulli equation",
        ],
    ),
    Lens(
        name="chaos_theory",
        prompt=(
            "Analyze this as a dynamical system. "
            "What is the Lyapunov exponent? Is there a strange attractor? "
            "Where is the edge of chaos? What are the bifurcation points?"
        ),
        concepts=[
            "Lyapunov exponent", "strange attractor",
            "bifurcation", "Feigenbaum", "logistic map",
        ],
    ),
    Lens(
        name="information_theory",
        prompt=(
            "Analyze this as information flow. "
            "What is the channel capacity? Where is information lost? "
            "What is the entropy at each stage?"
        ),
        concepts=[
            "entropy", "mutual information", "channel capacity",
            "rate-distortion", "Kolmogorov complexity",
        ],
    ),
    Lens(
        name="thermodynamics",
        prompt=(
            "Analyze this as a thermal system. "
            "What is the temperature? What is the partition function? "
            "Is there a phase transition? What is the free energy?"
        ),
        concepts=[
            "Boltzmann distribution", "partition function",
            "phase transition", "critical point", "free energy",
        ],
    ),
    Lens(
        name="string_theory",
        prompt=(
            "Analyze this with extra dimensions. "
            "What are the compact dimensions? Is there T-duality? "
            "What are the winding modes vs momentum modes?"
        ),
        concepts=[
            "compactification", "T-duality", "winding modes",
            "moduli space", "string tension",
        ],
    ),
    Lens(
        name="biology",
        prompt=(
            "Analyze this as a biological system. "
            "Is there homeostasis? What are the feedback loops? "
            "Is there adaptation or evolution happening?"
        ),
        concepts=[
            "homeostasis", "feedback", "adaptation",
            "predator-prey", "fitness landscape",
        ],
    ),
]

_LENS_BY_NAME: dict[str, Lens] = {lens.name: lens for lens in LENSES}


class PerspectiveRotator:
    """Maintains a library of scientific lenses to view problems through.

    When the current approach stalls, rotate to the next lens.
    Strategy: pick the lens **most different** from everything tried so far
    to maximize cognitive distance.
    """

    def __init__(self) -> None:
        self.tried_lenses: list[str] = []
        self.best_lens: str | None = None
        self.best_score: float = float("inf")

    def next_lens(
        self, current_score: float = 0.0, current_lens: str | None = None,
    ) -> Lens | None:
        """Select the next perspective to try.

        - Never repeats a lens in the same exploration.
        - Picks the lens most *different* from everything already tried
          (maximises cognitive distance via concept-overlap metric).
        - Updates ``best_lens`` / ``best_score`` tracking.
        """
        if current_lens:
            if current_lens not in self.tried_lenses:
                self.tried_lenses.append(current_lens)
            if current_score < self.best_score:
                self.best_score = current_score
                self.best_lens = current_lens

        available = [
            lens for lens in LENSES if lens.name not in self.tried_lenses
        ]
        if not available:
            return None  # Exhausted all perspectives

        return max(available, key=lambda l: self._distance_from_tried(l))

    def reset(self) -> None:
        """Reset for a new exploration."""
        self.tried_lenses.clear()
        self.best_lens = None
        self.best_score = float("inf")

    def get_lens(self, name: str) -> Lens | None:
        """Look up a lens by name."""
        return _LENS_BY_NAME.get(name)

    # ── Internal ──────────────────────────────────────────────────

    def _distance_from_tried(self, lens: Lens) -> float:
        """How different is *lens* from everything already tried?

        Measured by concept overlap: 0 = identical, 1 = no overlap.
        """
        if not self.tried_lenses:
            return 1.0

        tried_concepts: set[str] = set()
        for name in self.tried_lenses:
            other = _LENS_BY_NAME.get(name)
            if other:
                tried_concepts.update(other.concepts)

        if not lens.concepts:
            return 1.0

        overlap = len(set(lens.concepts) & tried_concepts)
        return 1.0 - overlap / len(lens.concepts)
