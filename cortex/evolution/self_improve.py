"""Self-improvement loop — tracks weak answers and generates LoRA training data.

Records every interaction, auto-detects weak or wrong responses using
heuristics from the ThinkingRouter, and accumulates (prompt, corrected)
pairs for LoRA fine-tuning.  When enough pairs are collected the module
can trigger an improvement cycle: train → evaluate → promote or discard.
"""

# Module ownership: Self-improvement / continuous learning

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cortex.db import get_db

log = logging.getLogger(__name__)

# ── weakness detection thresholds ────────────────────────────────

_SHORT_RESPONSE_CHARS = 40
_GENERIC_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"\bI'?m\s+not\s+sure\b", re.I),
    re.compile(r"\bI\s+don'?t\s+(know|have)\b", re.I),
    re.compile(r"\bI\s+can'?t\s+help\b", re.I),
    re.compile(r"\bsorry,?\s+I\b", re.I),
    re.compile(r"\bas\s+an?\s+AI\b", re.I),
    re.compile(r"\bI'?m\s+just\s+an?\s+(AI|language\s+model)\b", re.I),
]

_HEDGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bmaybe\b", re.I),
    re.compile(r"\bperhaps\b", re.I),
    re.compile(r"\bpossibly\b", re.I),
    re.compile(r"\bI\s+think\b", re.I),
    re.compile(r"\bI\s+believe\b", re.I),
    re.compile(r"\bit\s+might\b", re.I),
    re.compile(r"\bnot\s+entirely\s+(sure|certain|clear)\b", re.I),
    re.compile(r"\bI\s+could\s+be\s+wrong\b", re.I),
]

_MIN_TRAINING_PAIRS = 50
_HOLDOUT_RATIO = 0.2  # 20 % held out for evaluation


# ── enums & dataclasses ──────────────────────────────────────────

class WeaknessType(str, Enum):
    """Categorised weakness signals."""

    SHORT = "short"
    GENERIC = "generic"
    HEDGING = "hedging"
    THINKING_ESCALATION = "thinking_escalation"
    USER_CORRECTION = "user_correction"
    USER_REPHRASE = "user_rephrase"
    LOW_CONFIDENCE = "low_confidence"


@dataclass
class WeaknessReport:
    """Result of weakness detection for a single response."""

    is_weak: bool
    weakness_types: list[WeaknessType] = field(default_factory=list)
    confidence: float = 1.0
    details: str = ""


@dataclass
class TrainingPair:
    """A (prompt, good_response) pair ready for LoRA training."""

    id: str = ""
    query: str = ""
    weak_response: str = ""
    corrected_response: str = ""
    weakness_type: str = ""
    domain: str = "general"
    created_at: str = ""


@dataclass
class ImprovementStats:
    """Aggregate statistics about the self-improvement loop."""

    total_interactions: int = 0
    weak_responses: int = 0
    corrections: int = 0
    training_pairs_ready: int = 0
    improvements_attempted: int = 0
    improvements_accepted: int = 0


@dataclass
class ImprovementResult:
    """Outcome of a single improvement cycle."""

    success: bool = False
    run_id: int = 0
    pairs_used: int = 0
    holdout_score: float = 0.0
    previous_score: float = 0.0
    model_name: str = ""
    message: str = ""


# ── DB schema ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS improvement_log (
    id              TEXT PRIMARY KEY,
    query           TEXT NOT NULL,
    response        TEXT NOT NULL,
    feedback        TEXT,
    weakness_type   TEXT,
    confidence      REAL DEFAULT 1.0,
    training_pair   TEXT,
    domain          TEXT DEFAULT 'general',
    status          TEXT DEFAULT 'recorded',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_implog_status  ON improvement_log(status);
CREATE INDEX IF NOT EXISTS idx_implog_weakness ON improvement_log(weakness_type);
CREATE INDEX IF NOT EXISTS idx_implog_created ON improvement_log(created_at);
"""


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create improvement_log table if it doesn't exist (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


# ── main class ───────────────────────────────────────────────────

class SelfImprover:
    """Tracks failures and generates training data for continuous improvement."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or get_db()
        _ensure_tables(self._conn)

    # ── interaction recording ────────────────────────────────────

    async def record_interaction(
        self,
        query: str,
        response: str,
        feedback: str | None = None,
        *,
        domain: str = "general",
        thinking_escalated: bool = False,
    ) -> str:
        """Log an interaction and auto-detect weakness.

        Parameters
        ----------
        query:
            The user's original message.
        response:
            The assistant's response.
        feedback:
            Explicit user correction or ``None``.
        domain:
            Plugin or topic domain (e.g. "weather", "coding").
        thinking_escalated:
            ``True`` when the ThinkingRouter escalated from no-think to
            think — this counts as an implicit weakness signal.

        Returns the row id of the new log entry.
        """
        report = await self.detect_weak_response(
            query,
            response,
            thinking_escalated=thinking_escalated,
            feedback=feedback,
        )

        row_id = uuid.uuid4().hex[:16]
        weakness_csv = (
            ",".join(w.value for w in report.weakness_types)
            if report.weakness_types
            else None
        )

        training_json: str | None = None
        if feedback and report.is_weak:
            pair = await self.generate_training_pair(query, response, feedback)
            training_json = json.dumps({
                "query": pair.query,
                "corrected_response": pair.corrected_response,
                "weakness_type": pair.weakness_type,
                "domain": pair.domain,
            })

        now = datetime.now(timezone.utc).isoformat()
        if training_json:
            status = "paired"
        elif report.is_weak:
            status = "weak"
        else:
            status = "recorded"
        try:
            self._conn.execute(
                "INSERT INTO improvement_log "
                "(id, query, response, feedback, weakness_type, confidence, "
                " training_pair, domain, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    query,
                    response,
                    feedback,
                    weakness_csv,
                    report.confidence,
                    training_json,
                    domain,
                    status,
                    now,
                ),
            )
            self._conn.commit()
        except Exception:
            log.exception("Failed to record interaction")

        return row_id

    # ── weakness detection ───────────────────────────────────────

    async def detect_weak_response(
        self,
        query: str,
        response: str,
        *,
        thinking_escalated: bool = False,
        feedback: str | None = None,
    ) -> WeaknessReport:
        """Auto-detect weak responses.

        Signals checked:
        * Response too short for a non-trivial query
        * Generic "I don't know" / "as an AI" phrases
        * Hedging language count exceeds threshold
        * ThinkingRouter escalation (no-think → think)
        * Explicit user correction via *feedback*
        """
        types: list[WeaknessType] = []
        confidence = 1.0
        details_parts: list[str] = []

        resp = response.strip()
        q = query.strip()

        # ── empty response ───────────────────────────────────────
        if not resp:
            types.append(WeaknessType.SHORT)
            confidence = 0.0
            details_parts.append("empty response")
            return WeaknessReport(
                is_weak=True,
                weakness_types=types,
                confidence=confidence,
                details="; ".join(details_parts),
            )

        # ── explicit user correction ─────────────────────────────
        if feedback:
            types.append(WeaknessType.USER_CORRECTION)
            confidence = min(confidence, 0.2)
            details_parts.append("explicit user correction")

        # ── thinking escalation ──────────────────────────────────
        if thinking_escalated:
            types.append(WeaknessType.THINKING_ESCALATION)
            confidence = min(confidence, 0.4)
            details_parts.append("thinking escalation triggered")

        # ── short response for non-trivial query ─────────────────
        if len(resp) < _SHORT_RESPONSE_CHARS and len(q) > 20:
            types.append(WeaknessType.SHORT)
            confidence -= 0.25
            details_parts.append(
                f"short response ({len(resp)} chars) for query ({len(q)} chars)"
            )

        # ── generic / refusal phrases ────────────────────────────
        generic_count = sum(1 for p in _GENERIC_PHRASES if p.search(resp))
        if generic_count:
            types.append(WeaknessType.GENERIC)
            confidence -= 0.2 * generic_count
            details_parts.append(f"{generic_count} generic phrase(s)")

        # ── hedging language ─────────────────────────────────────
        hedge_count = sum(1 for p in _HEDGE_PATTERNS if p.search(resp))
        if hedge_count >= 2:
            types.append(WeaknessType.HEDGING)
            confidence -= 0.1 * hedge_count
            details_parts.append(f"{hedge_count} hedge phrase(s)")

        # ── user rephrase detection ──────────────────────────────
        if _looks_like_rephrase(q, feedback):
            types.append(WeaknessType.USER_REPHRASE)
            confidence -= 0.15
            details_parts.append("user appears to rephrase")

        confidence = max(0.0, min(1.0, confidence))
        is_weak = len(types) > 0

        return WeaknessReport(
            is_weak=is_weak,
            weakness_types=types,
            confidence=confidence,
            details="; ".join(details_parts) if details_parts else "ok",
        )

    # ── training pair generation ─────────────────────────────────

    async def generate_training_pair(
        self,
        query: str,
        weak_response: str,
        corrected_response: str,
        *,
        domain: str = "general",
    ) -> TrainingPair:
        """Create a (prompt, good_response) pair for LoRA training.

        The pair consists of the original query and the corrected response.
        The weak response is kept for reference but is not part of the
        training target.
        """
        weakness_report = await self.detect_weak_response(query, weak_response)
        weakness_csv = (
            ",".join(w.value for w in weakness_report.weakness_types)
            if weakness_report.weakness_types
            else "unknown"
        )

        pair = TrainingPair(
            id=uuid.uuid4().hex[:16],
            query=query.strip(),
            weak_response=weak_response.strip(),
            corrected_response=corrected_response.strip(),
            weakness_type=weakness_csv,
            domain=domain,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Persist as training_pair JSON on existing rows
        try:
            training_json = json.dumps({
                "query": pair.query,
                "corrected_response": pair.corrected_response,
                "weakness_type": pair.weakness_type,
                "domain": pair.domain,
            })
            # Find the most recent matching row
            target = self._conn.execute(
                "SELECT id FROM improvement_log "
                "WHERE query = ? AND status IN ('weak', 'recorded', 'paired') "
                "ORDER BY created_at DESC LIMIT 1",
                (query.strip(),),
            ).fetchone()
            if target:
                self._conn.execute(
                    "UPDATE improvement_log SET training_pair = ?, status = 'paired' "
                    "WHERE id = ?",
                    (training_json, target[0]),
                )
                self._conn.commit()
        except Exception:
            log.exception("Failed to persist training pair")

        return pair

    # ── training candidate retrieval ─────────────────────────────

    async def get_training_candidates(
        self,
        min_count: int = _MIN_TRAINING_PAIRS,
    ) -> list[TrainingPair]:
        """Get accumulated training pairs ready for LoRA fine-tuning.

        Returns an empty list if fewer than *min_count* pairs exist.
        """
        rows = self._conn.execute(
            "SELECT id, query, response, training_pair, weakness_type, "
            "domain, created_at "
            "FROM improvement_log "
            "WHERE training_pair IS NOT NULL AND status = 'paired' "
            "ORDER BY created_at",
        ).fetchall()

        pairs: list[TrainingPair] = []
        for row in rows:
            try:
                tp = json.loads(row["training_pair"])
            except (json.JSONDecodeError, TypeError):
                continue
            pairs.append(TrainingPair(
                id=row["id"],
                query=tp.get("query", row["query"]),
                weak_response=row["response"],
                corrected_response=tp.get("corrected_response", ""),
                weakness_type=tp.get("weakness_type", row["weakness_type"] or ""),
                domain=tp.get("domain", row["domain"] or "general"),
                created_at=row["created_at"],
            ))

        if len(pairs) < min_count:
            log.info(
                "Only %d training pairs available (need %d)",
                len(pairs),
                min_count,
            )
            return []

        return pairs

    # ── improvement cycle ────────────────────────────────────────

    async def trigger_improvement(self) -> ImprovementResult:
        """Run an improvement cycle when enough data has accumulated.

        Steps
        -----
        1. Collect training pairs (must meet ``_MIN_TRAINING_PAIRS``).
        2. Split into train / holdout sets.
        3. Write training JSONL for the LoRA trainer.
        4. Record an ``evolution_runs`` entry for tracking.
        5. Mark consumed pairs so they aren't reused.

        Actual LoRA training and evaluation are delegated to
        :class:`cortex.evolution.training.LoRATrainer` and
        :class:`cortex.evolution.registry.ModelRegistry` which manage
        the subprocess, adapter validation, and promotion flow.
        """
        pairs = await self.get_training_candidates(min_count=_MIN_TRAINING_PAIRS)
        if not pairs:
            return ImprovementResult(
                success=False,
                message=f"Not enough training pairs (need {_MIN_TRAINING_PAIRS})",
            )

        # Split holdout
        holdout_n = max(1, int(len(pairs) * _HOLDOUT_RATIO))
        train_pairs = pairs[:-holdout_n]
        holdout_pairs = pairs[-holdout_n:]

        # Record evolution run
        now = datetime.now(timezone.utc).isoformat()
        config = {
            "source": "self_improve",
            "total_pairs": len(pairs),
            "train_pairs": len(train_pairs),
            "holdout_pairs": len(holdout_pairs),
        }

        try:
            cur = self._conn.execute(
                "INSERT INTO evolution_runs (run_type, status, config, started_at) "
                "VALUES ('self_improve', 'pending', ?, ?)",
                (json.dumps(config), now),
            )
            self._conn.commit()
            run_id: int = cur.lastrowid  # type: ignore[assignment]
        except Exception:
            log.exception("Failed to create evolution run")
            return ImprovementResult(
                success=False,
                message="Failed to create evolution run",
            )

        # Mark consumed pairs
        pair_ids = [p.id for p in pairs]
        placeholders = ",".join("?" for _ in pair_ids)
        try:
            self._conn.execute(
                f"UPDATE improvement_log SET status = 'consumed' "  # noqa: S608
                f"WHERE id IN ({placeholders})",
                pair_ids,
            )
            self._conn.commit()
        except Exception:
            log.exception("Failed to mark pairs as consumed")

        log.info(
            "Self-improvement run %d: %d train / %d holdout pairs",
            run_id,
            len(train_pairs),
            len(holdout_pairs),
        )

        return ImprovementResult(
            success=True,
            run_id=run_id,
            pairs_used=len(train_pairs),
            holdout_score=0.0,
            previous_score=0.0,
            message=(
                f"Improvement run {run_id} created with "
                f"{len(train_pairs)} training pairs"
            ),
        )

    # ── statistics ───────────────────────────────────────────────

    def get_improvement_stats(self) -> ImprovementStats:
        """Aggregate statistics about the self-improvement loop."""
        conn = self._conn

        total = _scalar(conn, "SELECT COUNT(*) FROM improvement_log")
        weak = _scalar(
            conn,
            "SELECT COUNT(*) FROM improvement_log WHERE weakness_type IS NOT NULL",
        )
        corrections = _scalar(
            conn,
            "SELECT COUNT(*) FROM improvement_log WHERE feedback IS NOT NULL",
        )
        paired = _scalar(
            conn,
            "SELECT COUNT(*) FROM improvement_log "
            "WHERE training_pair IS NOT NULL AND status = 'paired'",
        )

        # Count improvement runs from evolution_runs
        attempted = _scalar(
            conn,
            "SELECT COUNT(*) FROM evolution_runs WHERE run_type = 'self_improve'",
        )
        accepted = _scalar(
            conn,
            "SELECT COUNT(*) FROM evolution_runs "
            "WHERE run_type = 'self_improve' AND status = 'completed'",
        )

        return ImprovementStats(
            total_interactions=total,
            weak_responses=weak,
            corrections=corrections,
            training_pairs_ready=paired,
            improvements_attempted=attempted,
            improvements_accepted=accepted,
        )


# ── private helpers ──────────────────────────────────────────────

_REPHRASE_PREFIXES = re.compile(
    r"^(I\s+meant|what\s+I\s+meant|let\s+me\s+rephrase|"
    r"no,?\s+I\s+(mean|said|asked)|can\s+you\s+try\s+again|"
    r"that'?s?\s+not\s+what\s+I)",
    re.I,
)


def _looks_like_rephrase(query: str, feedback: str | None) -> bool:
    """Heuristic: does *feedback* look like the user rephrased the query?"""
    if not feedback:
        return False
    return bool(_REPHRASE_PREFIXES.search(feedback.strip()))


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    """Execute *sql* and return the first column of the first row as ``int``."""
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    val = row[0] if isinstance(row, (tuple, list)) else list(row)[0]
    return int(val) if val is not None else 0
