"""Observes Atlas's behavior and identifies patterns worth investigating.

Module ownership: Curiosity pattern observation
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """A detected pattern that may warrant investigation."""

    pattern_type: str  # "repeated_task", "error_pattern", "slow_operation", "manual_step", "tool_sequence"
    description: str
    frequency: int  # How many times observed
    examples: list[str] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    confidence: float = 0.0  # 0-1 how confident this is a real pattern


class PatternObserver:
    """Watches Atlas's interactions and identifies improvable patterns."""

    def __init__(self) -> None:
        self._observations: dict[str, Observation] = {}
        self._tool_usage: dict[str, int] = defaultdict(int)
        self._tool_params_log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._error_counts: dict[str, int] = defaultdict(int)
        self._error_contexts: dict[str, list[str]] = defaultdict(list)
        self._task_durations: list[tuple[str, float, int]] = []
        self._tool_sequence: list[tuple[str, float]] = []
        self._corrections: list[tuple[str, str]] = []

    # ── Observation hooks ──────────────────────────────────────────

    def observe_tool_use(
        self, tool_id: str, params: dict[str, Any], result: Any, duration: float,
    ) -> None:
        """Record a tool execution. Look for:
        - Tools used repeatedly with similar params (automation opportunity)
        - Tools that frequently fail (improvement opportunity)
        - Sequences of tools always used together (macro opportunity)
        """
        now = time.time()
        self._tool_usage[tool_id] += 1
        self._tool_params_log[tool_id].append(params)
        self._tool_sequence.append((tool_id, now))

        # Keep a sliding window of recent tool calls
        if len(self._tool_sequence) > 500:
            self._tool_sequence = self._tool_sequence[-500:]

        # Detect repeated tool+params combos
        count = self._tool_usage[tool_id]
        if count >= 3:
            key = f"repeated_tool:{tool_id}"
            param_summaries = [
                str(p)[:120] for p in self._tool_params_log[tool_id][-3:]
            ]
            self._upsert_observation(
                key=key,
                pattern_type="repeated_task",
                description=f"Tool '{tool_id}' used {count} times",
                examples=param_summaries,
                confidence=min(0.3 + count * 0.1, 0.95),
            )

        # Detect slow operations
        if duration > 5.0:
            key = f"slow_op:{tool_id}"
            self._upsert_observation(
                key=key,
                pattern_type="slow_operation",
                description=f"Tool '{tool_id}' took {duration:.1f}s",
                examples=[f"{tool_id}({str(params)[:100]}) → {duration:.1f}s"],
                confidence=0.6 if duration > 10.0 else 0.4,
            )

    def observe_error(self, error_type: str, context: str) -> None:
        """Record an error. Look for:
        - Repeated errors (systematic issue)
        - Errors after specific tool sequences (fragile workflow)
        """
        self._error_counts[error_type] += 1
        self._error_contexts[error_type].append(context[:200])

        count = self._error_counts[error_type]
        if count >= 2:
            key = f"error:{error_type}"
            self._upsert_observation(
                key=key,
                pattern_type="error_pattern",
                description=f"Error '{error_type}' occurred {count} times",
                examples=self._error_contexts[error_type][-3:],
                confidence=min(0.4 + count * 0.15, 0.95),
            )

    def observe_task_completion(
        self, task: str, duration: float, iterations: int,
    ) -> None:
        """Record task completion metrics. Look for:
        - Tasks that take many iterations (complexity hotspot)
        - Similar tasks with different durations (optimization opportunity)
        """
        self._task_durations.append((task, duration, iterations))

        if iterations >= 5:
            key = f"complex_task:{task[:60]}"
            self._upsert_observation(
                key=key,
                pattern_type="slow_operation",
                description=(
                    f"Task took {iterations} iterations ({duration:.1f}s): "
                    f"{task[:80]}"
                ),
                examples=[f"{iterations} iters, {duration:.1f}s"],
                confidence=min(0.3 + iterations * 0.05, 0.9),
            )

    def observe_user_correction(self, original: str, correction: str) -> None:
        """Record when the user corrects Atlas. This is gold:
        - Pattern in corrections → systematic misunderstanding
        - Correction matches a known approach → should have known this
        """
        self._corrections.append((original[:200], correction[:200]))

        if len(self._corrections) >= 2:
            key = "user_corrections"
            self._upsert_observation(
                key=key,
                pattern_type="manual_step",
                description=(
                    f"User has corrected Atlas {len(self._corrections)} times"
                ),
                examples=[
                    f"'{o[:60]}' → '{c[:60]}'"
                    for o, c in self._corrections[-3:]
                ],
                confidence=min(0.4 + len(self._corrections) * 0.1, 0.95),
            )

    # ── Analysis ──────────────────────────────────────────────────

    def get_notable_patterns(
        self, min_frequency: int = 3, min_confidence: float = 0.5,
    ) -> list[Observation]:
        """Return patterns worth investigating."""
        # Also run detectors to surface any new patterns
        for obs in self._detect_repeated_sequences():
            self._observations[f"seq:{obs.description[:40]}"] = obs
        for obs in self._detect_slow_operations():
            self._observations[f"slow_agg:{obs.description[:40]}"] = obs

        return [
            obs
            for obs in self._observations.values()
            if obs.frequency >= min_frequency and obs.confidence >= min_confidence
        ]

    def _detect_repeated_sequences(self) -> list[Observation]:
        """Detect tool call sequences that repeat (macro candidates)."""
        if len(self._tool_sequence) < 4:
            return []

        tool_ids = [t for t, _ in self._tool_sequence]
        seq_counts: dict[tuple[str, ...], int] = defaultdict(int)

        # Look for 2- and 3-tool sequences
        for window in (2, 3):
            for i in range(len(tool_ids) - window + 1):
                seq = tuple(tool_ids[i : i + window])
                seq_counts[seq] += 1

        results: list[Observation] = []
        now = time.time()
        for seq, count in seq_counts.items():
            if count >= 3:
                desc = " → ".join(seq)
                results.append(
                    Observation(
                        pattern_type="tool_sequence",
                        description=f"Sequence '{desc}' repeated {count} times",
                        frequency=count,
                        examples=[desc],
                        first_seen=self._tool_sequence[0][1],
                        last_seen=now,
                        confidence=min(0.3 + count * 0.1, 0.95),
                    ),
                )
        return results

    def _detect_slow_operations(self) -> list[Observation]:
        """Detect operations that are consistently slow."""
        if not self._task_durations:
            return []

        slow = [
            (task, dur, iters)
            for task, dur, iters in self._task_durations
            if dur > 10.0
        ]
        if len(slow) < 2:
            return []

        return [
            Observation(
                pattern_type="slow_operation",
                description=(
                    f"{len(slow)} tasks exceeded 10s "
                    f"(avg {sum(d for _, d, _ in slow) / len(slow):.1f}s)"
                ),
                frequency=len(slow),
                examples=[f"{t[:60]}: {d:.1f}s" for t, d, _ in slow[:3]],
                first_seen=time.time(),
                last_seen=time.time(),
                confidence=0.7,
            ),
        ]

    # ── Internal helpers ──────────────────────────────────────────

    def _upsert_observation(
        self,
        *,
        key: str,
        pattern_type: str,
        description: str,
        examples: list[str],
        confidence: float,
    ) -> None:
        """Create or update an observation."""
        now = time.time()
        if key in self._observations:
            obs = self._observations[key]
            obs.frequency += 1
            obs.last_seen = now
            obs.confidence = max(obs.confidence, confidence)
            # Keep the 5 most recent examples
            obs.examples = (obs.examples + examples)[-5:]
            obs.description = description
        else:
            self._observations[key] = Observation(
                pattern_type=pattern_type,
                description=description,
                frequency=1,
                examples=examples,
                first_seen=now,
                last_seen=now,
                confidence=confidence,
            )
