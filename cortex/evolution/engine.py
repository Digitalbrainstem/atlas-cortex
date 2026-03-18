"""Self-evolution engine — orchestrates analysis, training, and model promotion.

Coordinates the full evolution loop: conversation analysis → gap
identification → training scheduling → model evaluation → promotion.
"""

# Module ownership: Self-evolution orchestrator

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from cortex.db import get_db
from cortex.evolution.analysis import ConversationAnalyzer
from cortex.evolution.registry import ModelRegistry

log = logging.getLogger(__name__)

# A domain qualifies for retraining when its score drops below this.
_RETRAIN_THRESHOLD = 0.6
_MIN_ISSUES_FOR_RETRAIN = 3


class EvolutionEngine:
    """Top-level self-evolution orchestrator."""

    def __init__(self) -> None:
        self.analyzer = ConversationAnalyzer()
        self.registry = ModelRegistry()

    # ── Run management helpers ───────────────────────────────────────

    def _create_run(self, run_type: str, config: dict | None = None) -> int:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO evolution_runs (run_type, status, config, started_at) "
            "VALUES (?, 'running', ?, ?)",
            (run_type, json.dumps(config or {}), now),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def _complete_run(self, run_id: int, results: dict, status: str = "completed") -> None:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE evolution_runs SET status = ?, results = ?, completed_at = ? "
            "WHERE id = ?",
            (status, json.dumps(results), now, run_id),
        )
        conn.commit()

    def _record_metrics(self, run_id: int, metrics: dict[str, float], domain: str = "general") -> None:
        conn = get_db()
        for name, value in metrics.items():
            conn.execute(
                "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain) "
                "VALUES (?, ?, ?, ?)",
                (run_id, name, value, domain),
            )
        conn.commit()

    # ── Public API ───────────────────────────────────────────────────

    async def run_analysis(self) -> dict:
        """Run full conversation analysis. Returns quality report."""
        run_id = self._create_run("analysis")
        try:
            gaps = self.analyzer.analyze_quality_gaps()
            weak = self.analyzer.identify_weak_domains()
            stats = self.analyzer.get_usage_stats()

            report = {
                "quality_gaps": {
                    domain: {"score": info["score"], "issue_count": info["issue_count"]}
                    for domain, info in gaps.items()
                },
                "weak_domains": weak,
                "usage_stats": stats,
            }

            # Record aggregate metrics
            flat_metrics = {"total_interactions": float(stats.get("total", 0))}
            if weak:
                flat_metrics["weakest_domain_score"] = weak[0]["score"]
            self._record_metrics(run_id, flat_metrics)

            self._complete_run(run_id, report)
            return report
        except Exception as exc:
            log.error("run_analysis failed: %s", exc, exc_info=True)
            self._complete_run(run_id, {"error": str(exc)}, status="failed")
            return {"error": str(exc)}

    async def schedule_training(self, domain: str, config: dict | None = None) -> int:
        """Schedule a LoRA training run. Returns run_id."""
        training_config = {
            "domain": domain,
            **(config or {}),
        }
        candidates = self.analyzer.generate_training_candidates(domain)
        training_config["candidate_count"] = len(candidates)
        run_id = self._create_run("training", training_config)

        # In a real system this would launch an async training job.
        # For now we record the intent and mark as pending.
        conn = get_db()
        conn.execute(
            "UPDATE evolution_runs SET status = 'pending' WHERE id = ?",
            (run_id,),
        )
        conn.commit()
        log.info("Scheduled training run %d for domain '%s' (%d candidates)", run_id, domain, len(candidates))
        return run_id

    async def check_for_improvements(self) -> list[dict]:
        """Check if any domain has degraded enough to warrant retraining."""
        weak = self.analyzer.identify_weak_domains()
        actionable = [
            d for d in weak
            if d["score"] < _RETRAIN_THRESHOLD and d["issue_count"] >= _MIN_ISSUES_FOR_RETRAIN
        ]
        return actionable

    async def run_nightly_evolution(self) -> dict:
        """Full nightly pipeline: analyse → identify gaps → schedule training if needed."""
        report = await self.run_analysis()
        if "error" in report:
            return report

        scheduled: list[int] = []
        improvements = await self.check_for_improvements()
        for domain_info in improvements:
            run_id = await self.schedule_training(domain_info["domain"])
            scheduled.append(run_id)

        report["scheduled_training_runs"] = scheduled
        return report

    def get_evolution_history(self, limit: int = 20) -> list[dict]:
        """Get recent evolution runs with results."""
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM evolution_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            log.debug("get_evolution_history: unable to query", exc_info=True)
            return []
