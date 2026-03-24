"""Tracks hypotheses about improvements and their experimental results.

Module ownership: Curiosity hypothesis management
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Hypothesis:
    """A testable theory about a potential improvement."""

    id: str
    statement: str  # "Caching API responses would reduce latency by 50%"
    category: str  # "performance", "reliability", "ux", "automation", "architecture"
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    status: str = "proposed"  # "proposed", "testing", "validated", "rejected", "implemented"
    proposed_experiment: str = ""  # "Benchmark with and without cache"
    result: str = ""  # After testing: "Reduced latency from 200ms to 45ms"
    confidence: float = 0.5  # 0-1
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "id": self.id,
            "statement": self.statement,
            "category": self.category,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "status": self.status,
            "proposed_experiment": self.proposed_experiment,
            "result": self.result,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Hypothesis:
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            statement=data["statement"],
            category=data.get("category", ""),
            evidence_for=data.get("evidence_for", []),
            evidence_against=data.get("evidence_against", []),
            status=data.get("status", "proposed"),
            proposed_experiment=data.get("proposed_experiment", ""),
            result=data.get("result", ""),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )


class HypothesisTracker:
    """Manage hypotheses about system improvements."""

    def __init__(self) -> None:
        self._hypotheses: dict[str, Hypothesis] = {}

    def propose(
        self,
        statement: str,
        category: str,
        evidence: list[str] | None = None,
        experiment: str = "",
    ) -> Hypothesis:
        """Create a new hypothesis from observed patterns."""
        hid = hashlib.sha256(statement.encode()).hexdigest()[:12]

        # Merge evidence if we already have this hypothesis
        if hid in self._hypotheses:
            existing = self._hypotheses[hid]
            for e in evidence or []:
                if e not in existing.evidence_for:
                    existing.evidence_for.append(e)
            existing.updated_at = time.time()
            return existing

        now = time.time()
        h = Hypothesis(
            id=hid,
            statement=statement,
            category=category,
            evidence_for=list(evidence or []),
            status="proposed",
            proposed_experiment=experiment,
            confidence=min(0.3 + len(evidence or []) * 0.1, 0.9),
            created_at=now,
            updated_at=now,
        )
        self._hypotheses[hid] = h
        return h

    def add_evidence(
        self, hypothesis_id: str, evidence: str, *, supports: bool = True,
    ) -> None:
        """Add supporting or contradicting evidence."""
        h = self._hypotheses.get(hypothesis_id)
        if h is None:
            return
        if supports:
            h.evidence_for.append(evidence)
            h.confidence = min(h.confidence + 0.1, 0.99)
        else:
            h.evidence_against.append(evidence)
            h.confidence = max(h.confidence - 0.1, 0.01)
        h.updated_at = time.time()

    def start_experiment(self, hypothesis_id: str) -> None:
        """Mark hypothesis as being tested."""
        h = self._hypotheses.get(hypothesis_id)
        if h is None:
            return
        h.status = "testing"
        h.updated_at = time.time()

    def record_result(
        self, hypothesis_id: str, result: str, *, validated: bool,
    ) -> None:
        """Record experimental result."""
        h = self._hypotheses.get(hypothesis_id)
        if h is None:
            return
        h.result = result
        h.status = "validated" if validated else "rejected"
        h.confidence = 0.95 if validated else 0.05
        h.updated_at = time.time()

    def mark_implemented(self, hypothesis_id: str) -> None:
        """Mark a validated hypothesis as implemented."""
        h = self._hypotheses.get(hypothesis_id)
        if h is None:
            return
        h.status = "implemented"
        h.updated_at = time.time()

    def get_actionable(self) -> list[Hypothesis]:
        """Return hypotheses ready to test (high confidence, not yet tested)."""
        return [
            h
            for h in self._hypotheses.values()
            if h.status == "proposed" and h.confidence >= 0.5
        ]

    def get_validated(self) -> list[Hypothesis]:
        """Return validated hypotheses that should be implemented."""
        return [
            h
            for h in self._hypotheses.values()
            if h.status == "validated"
        ]

    def get_all(self) -> list[Hypothesis]:
        """Return all hypotheses."""
        return list(self._hypotheses.values())

    def save(self, path: Path) -> None:
        """Persist hypotheses to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [h.to_dict() for h in self._hypotheses.values()]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: Path) -> None:
        """Load hypotheses from disk."""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                h = Hypothesis.from_dict(item)
                self._hypotheses[h.id] = h
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # Corrupt file — start fresh
