"""Elegance scoring — measures how "beautiful" a solution is.

Based on Derek Thomas's original Curiosity Engine design.  Lower score = more
elegant.  The intuition: fewer free parameters, random (not structured)
residuals, symmetry, matching known constants, and low complexity all indicate
a solution that captured the *real* structure of the problem.

Module ownership: Curiosity elegance measurement
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Sequence


# ── Known constants database ───────────────────────────────────────

_KNOWN_CONSTANTS: dict[str, float] = {
    # Mathematical
    "pi": math.pi,
    "e": math.e,
    "ln2": math.log(2),
    "ln10": math.log(10),
    "sqrt2": math.sqrt(2),
    "sqrt3": math.sqrt(3),
    "phi": (1 + math.sqrt(5)) / 2,  # Golden ratio
    "euler_mascheroni": 0.5772156649,
    # Simple fractions
    "1/2": 0.5,
    "1/3": 0.333333,
    "2/3": 0.666667,
    "1/4": 0.25,
    "3/4": 0.75,
    "1/6": 0.166667,
    "1/8": 0.125,
    # Common combinations
    "pi/2": math.pi / 2,
    "pi/4": math.pi / 4,
    "2pi": 2 * math.pi,
    "e^-1": 1 / math.e,
    "e^-pi": math.exp(-math.pi),
    "ln_pi": math.log(math.pi),
    # Physical-ish (dimensionless)
    "alpha_fine_structure": 1 / 137.036,
    "feigenbaum_delta": 4.6692016,
}

_NUMERIC_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


@dataclass
class EleganceBreakdown:
    """Detailed breakdown of an elegance score."""

    free_params: float = 1.0      # 0 = no arbitrary constants (perfect)
    residual_structure: float = 0.0  # 0 = random residuals (good)
    symmetry: float = 1.0         # 0 = fully symmetric (good)
    constant_match: float = 1.0   # 0 = all constants recognised (good)
    complexity: float = 1.0       # 0 = minimal complexity (good)
    total: float = 1.0
    matched_constants: list[str] = field(default_factory=list)
    unmatched_values: list[float] = field(default_factory=list)


class EleganceScorer:
    """Scores solutions on elegance.  High scores trigger continued exploration.

    Metrics (all normalised to 0–1, lower = better):
    - ``free_params``:        Number of fitted/arbitrary constants
    - ``residual_structure``: Are residuals random or structured?
    - ``symmetry``:           Does the solution have symmetry properties?
    - ``constant_match``:     Do numbers match known mathematical constants?
    - ``complexity``:         Solution complexity / problem complexity
    """

    WEIGHTS: dict[str, float] = {
        "free_params": 0.30,
        "residual_structure": 0.25,
        "symmetry": 0.15,
        "constant_match": 0.15,
        "complexity": 0.15,
    }

    # ── Public API ────────────────────────────────────────────────

    def score(
        self,
        solution_text: str,
        residuals: Sequence[float] | None = None,
        problem_text: str = "",
    ) -> EleganceBreakdown:
        """Score a solution's elegance.  Returns a detailed breakdown."""
        fp_score, matched, unmatched = self._score_free_params(solution_text)
        res_score = self._score_residual_structure(residuals) if residuals else 0.0
        sym_score = self._score_symmetry(solution_text)
        const_score = 1.0 - (len(matched) / max(len(matched) + len(unmatched), 1))
        comp_score = self._score_complexity(solution_text, problem_text)

        scores = {
            "free_params": fp_score,
            "residual_structure": res_score,
            "symmetry": 1.0 - sym_score,
            "constant_match": const_score,
            "complexity": comp_score,
        }
        total = sum(scores[k] * self.WEIGHTS[k] for k in scores)

        return EleganceBreakdown(
            free_params=fp_score,
            residual_structure=res_score,
            symmetry=1.0 - sym_score,
            constant_match=const_score,
            complexity=comp_score,
            total=total,
            matched_constants=matched,
            unmatched_values=unmatched,
        )

    # ── Internal scorers ──────────────────────────────────────────

    def _score_free_params(
        self, text: str,
    ) -> tuple[float, list[str], list[float]]:
        """Extract numeric literals and check against known constants.

        Returns ``(normalised_score, matched_names, unmatched_values)``.
        """
        numbers = [float(m) for m in _NUMERIC_RE.findall(text)]
        if not numbers:
            return 0.0, [], []

        matched: list[str] = []
        unmatched: list[float] = []
        for val in numbers:
            name = self._match_constant(val)
            if name:
                matched.append(name)
            else:
                unmatched.append(val)

        # Normalise: proportion of unmatched out of total, capped at 1
        score = len(unmatched) / max(len(numbers), 1)
        return min(score, 1.0), matched, unmatched

    def _score_residual_structure(self, residuals: Sequence[float]) -> float:
        """Check if residuals have structure (bad) or are random (good).

        Uses a simple runs-test proxy: count sign changes.  Many sign
        changes → random → good (low score).  Few → structured → bad.
        """
        if len(residuals) < 3:
            return 0.0
        sign_changes = sum(
            1
            for a, b in zip(residuals, residuals[1:])
            if (a >= 0) != (b >= 0)
        )
        expected = (len(residuals) - 1) / 2
        if expected == 0:
            return 1.0
        # ratio > 1 means more random than expected → good
        ratio = sign_changes / expected
        return max(0.0, min(1.0, 1.0 - ratio))

    def _score_symmetry(self, text: str) -> float:
        """Heuristic symmetry score (0 = no symmetry, 1 = very symmetric).

        Looks for symmetry keywords and structural indicators.
        """
        indicators = [
            "symmetric", "symmetry", "invariant", "conserved",
            "commut", "palindrom", "mirror", "dual",
            "= -", "f(x) = f(-x)", "even function", "odd function",
        ]
        hits = sum(1 for ind in indicators if ind in text.lower())
        return min(hits / 3.0, 1.0)

    def _score_complexity(self, solution: str, problem: str) -> float:
        """Complexity ratio: solution tokens / problem tokens.

        Simpler solutions to complex problems are more elegant.
        """
        sol_len = max(len(solution.split()), 1)
        prob_len = max(len(problem.split()), 1) if problem else sol_len
        ratio = sol_len / prob_len
        # Normalise: ratio of 1 → 0.5, ratio of 3+ → ~1.0, ratio of 0.3 → ~0.15
        return min(ratio / 3.0, 1.0)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _match_constant(value: float, tolerance: float = 0.01) -> str | None:
        """Check *value* against database of known constants.

        Returns the constant name if matched within *tolerance* (relative),
        or ``None`` if it appears to be an arbitrary fitted value.
        """
        if value == 0:
            return "zero"
        for name, known in _KNOWN_CONSTANTS.items():
            if known == 0:
                continue
            if abs(value - known) / abs(known) < tolerance:
                return name
        # Check small integers
        if value == int(value) and abs(value) <= 20:
            return f"int({int(value)})"
        return None
