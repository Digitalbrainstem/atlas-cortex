"""Residual analysis — examines what the current model gets WRONG.

Based on Derek Thomas's original Curiosity Engine design.  The key insight:
errors that correlate with metadata properties indicate missing terms in the
model.  Structured residuals mean there's physics left to capture.

Module ownership: Curiosity residual analysis
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class ResidualFinding:
    """One finding from residual analysis."""

    finding_type: str  # "correlation", "group_difference", "monotonic_trend"
    field: str         # Which metadata field is implicated
    detail: str        # Human-readable description
    strength: float    # 0-1 how strong the signal is


class ResidualAnalyzer:
    """Examines prediction errors to identify missing physics.

    Key insight from research: errors that correlate with architecture
    properties (GQA, sliding window, norm count) indicate missing terms
    in the equation.
    """

    def analyze(
        self,
        predictions: Sequence[float],
        actuals: Sequence[float],
        metadata: dict[str, Sequence[Any]] | None = None,
    ) -> list[ResidualFinding]:
        """Run full residual analysis.

        Parameters
        ----------
        predictions:
            Model predictions (numeric).
        actuals:
            Ground-truth values (numeric).
        metadata:
            Optional mapping of ``field_name → values`` for correlation
            analysis.  Numeric fields get correlation + monotonic checks;
            categorical fields get group-difference checks.

        Returns
        -------
        list[ResidualFinding]
            Ordered by strength descending.
        """
        if len(predictions) != len(actuals):
            return []

        residuals = [p - a for p, a in zip(predictions, actuals)]
        if not residuals:
            return []

        findings: list[ResidualFinding] = []

        # Overall structure check (even without metadata)
        struct = self._structure_score(residuals)
        if struct > 0.3:
            findings.append(ResidualFinding(
                finding_type="structure",
                field="(overall)",
                detail=(
                    f"Residuals show structure (score {struct:.2f}) "
                    "— missing variable or wrong model form"
                ),
                strength=struct,
            ))

        if not metadata:
            return findings

        for field_name, values in metadata.items():
            if len(values) != len(residuals):
                continue

            if _is_numeric(values):
                # Correlation check
                corr = _pearson(residuals, [float(v) for v in values])
                if abs(corr) > 0.3:
                    findings.append(ResidualFinding(
                        finding_type="correlation",
                        field=field_name,
                        detail=(
                            f"Residuals correlate with {field_name} "
                            f"(r={corr:.2f})"
                        ),
                        strength=abs(corr),
                    ))

                # Monotonic trend check
                mono = self._monotonic_score(
                    residuals, [float(v) for v in values],
                )
                if mono > 0.7:
                    findings.append(ResidualFinding(
                        finding_type="monotonic_trend",
                        field=field_name,
                        detail=(
                            f"Monotonic residual trend with {field_name} "
                            f"(score {mono:.2f}) — suggests missing power law"
                        ),
                        strength=mono,
                    ))
            else:
                # Categorical: group-difference check
                diff = self._group_difference(
                    residuals, [str(v) for v in values],
                )
                if diff > 0.3:
                    findings.append(ResidualFinding(
                        finding_type="group_difference",
                        field=field_name,
                        detail=(
                            f"Residuals differ by {field_name} "
                            f"(effect {diff:.2f})"
                        ),
                        strength=diff,
                    ))

        findings.sort(key=lambda f: f.strength, reverse=True)
        return findings

    # ── Internal helpers ──────────────────────────────────────────

    def _structure_score(self, residuals: list[float]) -> float:
        """Runs-test proxy for randomness.  0 = random, 1 = structured."""
        if len(residuals) < 4:
            return 0.0
        sign_changes = sum(
            1
            for a, b in zip(residuals, residuals[1:])
            if (a >= 0) != (b >= 0)
        )
        expected = (len(residuals) - 1) / 2
        if expected == 0:
            return 1.0
        ratio = sign_changes / expected
        return max(0.0, min(1.0, 1.0 - ratio))

    def _monotonic_score(
        self, residuals: list[float], sort_key: list[float],
    ) -> float:
        """Check if residuals are monotonic when sorted by *sort_key*."""
        if len(residuals) < 4:
            return 0.0
        paired = sorted(zip(sort_key, residuals), key=lambda x: x[0])
        sorted_res = [r for _, r in paired]
        n = len(sorted_res) - 1
        if n == 0:
            return 0.0
        inc = sum(1 for a, b in zip(sorted_res, sorted_res[1:]) if b >= a)
        dec = sum(1 for a, b in zip(sorted_res, sorted_res[1:]) if b <= a)
        return max(inc, dec) / n

    def _group_difference(
        self, residuals: list[float], categories: list[str],
    ) -> float:
        """Measure how much residual means differ across groups.

        Returns a normalised effect size (0–1).
        """
        groups: dict[str, list[float]] = {}
        for res, cat in zip(residuals, categories):
            groups.setdefault(cat, []).append(res)

        if len(groups) < 2:
            return 0.0

        means = [sum(v) / len(v) for v in groups.values() if v]
        if not means:
            return 0.0

        spread = max(means) - min(means)
        overall_std = _std(residuals)
        if overall_std == 0:
            return 0.0

        # Cohen's d analog: spread / overall std, capped at 1
        return min(spread / overall_std, 1.0)


# ── Utility functions ─────────────────────────────────────────────


def _is_numeric(values: Sequence[Any]) -> bool:
    """Check if a sequence is numeric."""
    try:
        for v in values[:5]:
            float(v)
        return True
    except (ValueError, TypeError):
        return False


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)
