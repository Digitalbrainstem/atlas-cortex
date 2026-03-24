"""Drive controller — decides when to keep exploring vs when to stop.

Based on Derek Thomas's original Curiosity Engine design.  The "dissatisfaction"
module: it determines whether to keep pushing for a more elegant answer or
accept the current one.

Module ownership: Curiosity drive control
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DriveThresholds:
    """Thresholds that control when exploration stops."""

    elegance_threshold: float    # Score below this → beautiful enough
    max_iterations: int          # Hard cap on exploration iterations
    improvement_threshold: float # Minimum improvement to avoid plateau
    residual_threshold: float    # Unexplained residual fraction limit


# Pre-configured modes
_MODE_THRESHOLDS: dict[str, DriveThresholds] = {
    "research": DriveThresholds(
        elegance_threshold=0.2,
        max_iterations=20,
        improvement_threshold=0.01,
        residual_threshold=0.05,
    ),
    "practical": DriveThresholds(
        elegance_threshold=0.5,
        max_iterations=5,
        improvement_threshold=0.05,
        residual_threshold=0.15,
    ),
    "urgent": DriveThresholds(
        elegance_threshold=0.8,
        max_iterations=2,
        improvement_threshold=0.10,
        residual_threshold=0.30,
    ),
}

VALID_MODES = frozenset(_MODE_THRESHOLDS)


class DriveController:
    """The 'dissatisfaction' module.

    Determines whether to keep pushing or accept the current answer.
    Three modes calibrate the exploration depth:

    - **research**: high drive — keep going until elegant (20 iterations,
      very high elegance bar).
    - **practical**: moderate drive — good enough for production
      (5 iterations, moderate bar).
    - **urgent**: low drive — first working answer (2 iterations).
    """

    def __init__(self, mode: str = "practical") -> None:
        if mode not in _MODE_THRESHOLDS:
            raise ValueError(
                f"Unknown mode {mode!r}, must be one of {sorted(VALID_MODES)}",
            )
        self.mode = mode
        self.thresholds = _MODE_THRESHOLDS[mode]
        self.iteration = 0
        self.score_history: list[float] = []

    def should_continue(self, current_score: float) -> bool:
        """Return ``True`` if exploration should keep going.

        Stop conditions (checked in order):
        1. Score below elegance threshold (beautiful enough).
        2. Exceeded max iterations.
        3. Score stopped improving (plateau over last 3).
        """
        self.iteration += 1
        self.score_history.append(current_score)

        # 1. Beautiful enough?
        if current_score < self.thresholds.elegance_threshold:
            return False

        # 2. Too many iterations?
        if self.iteration >= self.thresholds.max_iterations:
            return False

        # 3. Plateau? (last 3 scores similar)
        if len(self.score_history) >= 3:
            recent = self.score_history[-3:]
            improvement = max(recent) - min(recent)
            if improvement < self.thresholds.improvement_threshold:
                return False

        return True  # Keep going!

    def suggest_action(
        self,
        elegance_score: float,
        residual_findings: list[Any] | None = None,
        free_param_count: int = 0,
        residual_structure: float = 0.0,
    ) -> tuple[str, str]:
        """Suggest what to do next based on the current state.

        Returns ``(action, detail)`` where action is one of:
        ``"investigate_residuals"``, ``"derive_constants"``,
        ``"change_perspective"``, or ``"accept"``.
        """
        if residual_findings:
            first = residual_findings[0]
            detail = getattr(first, "detail", str(first))
            return "investigate_residuals", detail

        if free_param_count > 0:
            return "derive_constants", f"{free_param_count} unmatched constants"

        if residual_structure > 0.3:
            return "change_perspective", "structured residuals suggest missing physics"

        return "accept", "solution meets elegance criteria"

    def reset(self) -> None:
        """Reset for a new exploration."""
        self.iteration = 0
        self.score_history.clear()
