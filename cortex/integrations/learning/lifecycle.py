"""Pattern lifecycle — prunes stale patterns, boosts high-hit patterns (Phase I4.3)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PatternLifecycle:
    """Manage the lifecycle of command_patterns: prune stale, boost frequent."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get_stats(self) -> dict:
        """Return pattern counts grouped by source."""
        rows = self._conn.execute(
            "SELECT source, count(*) FROM command_patterns GROUP BY source"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def prune_zero_hit_patterns(self, older_than_days: int = 30) -> int:
        """Delete learned patterns with zero hits older than N days."""
        cursor = self._conn.execute(
            """DELETE FROM command_patterns
               WHERE hit_count = 0
                 AND source = 'learned'
                 AND created_at < datetime('now', ?)""",
            (f"-{older_than_days} days",),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        logger.info("prune_zero_hit_patterns: deleted %d patterns", deleted)
        return deleted

    def boost_frequent_patterns(self, min_hits: int = 10) -> int:
        """Increase confidence by 0.05 (capped at 1.0) for patterns with >= min_hits."""
        cursor = self._conn.execute(
            """UPDATE command_patterns
               SET confidence = min(1.0, confidence + 0.05)
               WHERE hit_count >= ?""",
            (min_hits,),
        )
        self._conn.commit()
        updated = cursor.rowcount
        logger.info("boost_frequent_patterns: updated %d patterns", updated)
        return updated

    def weekly_report(self) -> dict:
        """Return a summary of the current pattern table state."""
        stats = self._conn.execute(
            """SELECT
                 count(*) as total,
                 sum(case when source='seed' then 1 else 0 end) as seed,
                 sum(case when source='learned' then 1 else 0 end) as learned,
                 sum(case when source='discovered' then 1 else 0 end) as discovered,
                 sum(case when hit_count=0 then 1 else 0 end) as zero_hit
               FROM command_patterns"""
        ).fetchone()

        # Eligible for pruning: learned, zero hit, older than 30 days
        pruned_eligible = self._conn.execute(
            """SELECT count(*) FROM command_patterns
               WHERE hit_count = 0
                 AND source = 'learned'
                 AND created_at < datetime('now', '-30 days')"""
        ).fetchone()[0]

        return {
            "total_patterns": stats[0] or 0,
            "seed_patterns": stats[1] or 0,
            "learned_patterns": stats[2] or 0,
            "discovered_patterns": stats[3] or 0,
            "zero_hit_patterns": stats[4] or 0,
            "pruned_eligible": pruned_eligible,
        }
