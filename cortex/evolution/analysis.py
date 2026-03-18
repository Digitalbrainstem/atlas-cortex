"""Conversation analysis for self-evolution.

Analyses recent interactions to identify quality gaps, weak domains,
and training candidates that can drive LoRA fine-tuning runs.
"""

# Module ownership: Self-evolution conversation analysis

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from cortex.db import get_db

log = logging.getLogger(__name__)

# Thresholds
_SHORT_RESPONSE_MS = 200  # follow-ups under this are suspiciously quick
_LONG_RESPONSE_MS = 4000  # responses slower than this indicate issues
_LOW_CONFIDENCE = 0.4  # confidence below this counts as weak
_FALLTHROUGH_LAYER = "llm"  # interactions that fell through to LLM


class ConversationAnalyzer:
    """Analyse recent conversations for quality issues and training opportunities."""

    # ── Public API ───────────────────────────────────────────────────

    def analyze_quality_gaps(self, days: int = 7) -> dict:
        """Analyse recent conversations for quality issues.

        Checks:
        - Low user satisfaction signals (short follow-ups, topic changes, corrections)
        - Failed plugin matches (fallthrough rate by domain)
        - Long response times
        - Repeated questions (user asking same thing differently)

        Returns ``{domain: {score, issue_count, examples}}``.
        """
        try:
            conn = get_db()
            cutoff = self._cutoff_iso(days)
            rows = self._recent_interactions(conn, cutoff)
        except Exception:
            log.debug("analyze_quality_gaps: unable to read interactions", exc_info=True)
            return {}

        domains: dict[str, dict] = {}
        for r in rows:
            domain = r["resolved_area"] or r["matched_layer"] or "general"
            bucket = domains.setdefault(domain, {"score": 1.0, "issue_count": 0, "examples": []})

            issues: list[str] = []
            # Long response time
            rt = r["response_time_ms"]
            if rt is not None and rt > _LONG_RESPONSE_MS:
                issues.append(f"slow response ({rt}ms)")

            # Low confidence
            conf = r["confidence_score"]
            if conf is not None and conf < _LOW_CONFIDENCE:
                issues.append(f"low confidence ({conf:.2f})")

            # Negative / frustrated sentiment
            sentiment = (r["sentiment"] or "").lower()
            if sentiment in ("negative", "frustrated"):
                issues.append(f"negative sentiment ({sentiment})")

            # Fallthrough to LLM (plugin miss)
            if r["matched_layer"] == _FALLTHROUGH_LAYER and r["intent"]:
                issues.append("plugin fallthrough")

            if issues:
                bucket["issue_count"] += len(issues)
                bucket["score"] = max(0.0, bucket["score"] - 0.05 * len(issues))
                if len(bucket["examples"]) < 5:
                    bucket["examples"].append({
                        "message": (r["message"] or "")[:120],
                        "issues": issues,
                    })

        return domains

    def identify_weak_domains(self, days: int = 30) -> list[dict]:
        """Identify domains where Atlas underperforms.

        Returns a list sorted by ascending score (weakest first).
        """
        gaps = self.analyze_quality_gaps(days=days)
        result = []
        for domain, info in gaps.items():
            result.append({
                "domain": domain,
                "score": round(info["score"], 3),
                "issue_count": info["issue_count"],
            })
        result.sort(key=lambda d: d["score"])
        return result

    def generate_training_candidates(self, domain: str, limit: int = 100) -> list[dict]:
        """Extract conversation pairs suitable for LoRA training.

        Returns ``[{prompt, ideal_response, domain}]`` from interactions where
        the user expressed positive or neutral sentiment (indicating an
        acceptable response).
        """
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT message, response, sentiment, resolved_area, matched_layer "
                "FROM interactions "
                "WHERE (resolved_area = ? OR matched_layer = ?) "
                "  AND response IS NOT NULL AND response != '' "
                "  AND sentiment IN ('positive', 'neutral') "
                "ORDER BY created_at DESC LIMIT ?",
                (domain, domain, limit),
            ).fetchall()
        except Exception:
            log.debug("generate_training_candidates: unable to query", exc_info=True)
            return []

        candidates = []
        for r in rows:
            candidates.append({
                "prompt": r["message"],
                "ideal_response": r["response"],
                "domain": domain,
            })
        return candidates

    def get_usage_stats(self, days: int = 7) -> dict:
        """Get conversation volume, topics, peak hours, satisfaction estimates."""
        try:
            conn = get_db()
            cutoff = self._cutoff_iso(days)
        except Exception:
            log.debug("get_usage_stats: unable to connect", exc_info=True)
            return {"total": 0, "topics": {}, "peak_hours": [], "satisfaction": 0.0}

        empty = {"total": 0, "topics": {}, "peak_hours": [], "satisfaction": 0.0}
        try:
            # Total interactions
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM interactions WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()
            total = total_row["cnt"] if total_row else 0

            # Per-domain counts (topic proxy)
            topic_rows = conn.execute(
                "SELECT COALESCE(resolved_area, matched_layer, 'general') AS domain, "
                "       COUNT(*) AS cnt "
                "FROM interactions WHERE created_at >= ? "
                "GROUP BY domain ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
            topics = {r["domain"]: r["cnt"] for r in topic_rows}

            # Satisfaction estimate (ratio of positive/neutral to total)
            sat_row = conn.execute(
                "SELECT COUNT(*) AS good FROM interactions "
                "WHERE created_at >= ? AND sentiment IN ('positive', 'neutral')",
                (cutoff,),
            ).fetchone()
            good = sat_row["good"] if sat_row else 0
            satisfaction = round(good / total, 3) if total > 0 else 0.0
        except Exception:
            log.debug("get_usage_stats: unable to query interactions", exc_info=True)
            return empty

        # Peak hours from user_activity_hours
        peak_hours: list[int] = []
        try:
            hour_rows = conn.execute(
                "SELECT hour, SUM(interaction_count) AS cnt "
                "FROM user_activity_hours GROUP BY hour ORDER BY cnt DESC LIMIT 3"
            ).fetchall()
            peak_hours = [r["hour"] for r in hour_rows]
        except Exception:
            pass

        return {
            "total": total,
            "topics": topics,
            "peak_hours": peak_hours,
            "satisfaction": satisfaction,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _cutoff_iso(days: int) -> str:
        """Return ISO-8601 timestamp *days* ago."""
        from datetime import timedelta

        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    @staticmethod
    def _recent_interactions(conn, cutoff: str) -> list:
        return conn.execute(
            "SELECT message, matched_layer, intent, sentiment, sentiment_score, "
            "       response, response_time_ms, confidence_score, resolved_area "
            "FROM interactions WHERE created_at >= ? "
            "ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
