"""Nightly evolution job orchestrator (Phase I4.1).

Runs all nightly tasks in sequence:
  1. Device discovery diff (if HA client available)
  2. Fallthrough analysis → new patterns
  3. Pattern lifecycle (prune + boost)
  4. Log to evolution_log table
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .analyzer import FallthroughAnalyzer
from .lifecycle import PatternLifecycle

logger = logging.getLogger(__name__)


class NightlyEvolution:
    """Orchestrate nightly learning and evolution tasks."""

    def __init__(
        self,
        conn: Any,
        ha_bootstrap: Any = None,
        analyzer: FallthroughAnalyzer | None = None,
        lifecycle: PatternLifecycle | None = None,
    ) -> None:
        self._conn = conn
        self._ha_bootstrap = ha_bootstrap
        self._analyzer = analyzer or FallthroughAnalyzer(conn)
        self._lifecycle = lifecycle or PatternLifecycle(conn)

    async def run(self) -> dict:
        """Execute all evolution phases and return combined stats."""
        stats: dict[str, Any] = {
            "devices_discovered": 0,
            "devices_removed": 0,
            "patterns_generated": 0,
            "patterns_learned": 0,
            "patterns_pruned": 0,
            "profiles_evolved": 0,
        }

        # Phase 1: Device discovery (optional — only if HA bootstrap available)
        if self._ha_bootstrap is not None:
            try:
                sync_result = await self._ha_bootstrap.full_bootstrap()
                stats["devices_discovered"] = sync_result.get("added", 0) + sync_result.get("updated", 0)
                stats["devices_removed"] = sync_result.get("removed", 0)
                stats["patterns_generated"] = sync_result.get("patterns_generated", 0)
                logger.info("Nightly: device discovery complete — %s", sync_result)
            except Exception as exc:
                logger.error("Nightly: device discovery failed: %s", exc)

        # Phase 2: Fallthrough analysis → new learned patterns
        try:
            analysis = self._analyzer.run()
            stats["patterns_learned"] = analysis.get("patterns_saved", 0)
            stats["fallthroughs_analyzed"] = analysis.get("fallthroughs_analyzed", 0)
            stats["patterns_proposed"] = analysis.get("patterns_proposed", 0)
            logger.info("Nightly: fallthrough analysis — %s", analysis)
        except Exception as exc:
            logger.error("Nightly: fallthrough analysis failed: %s", exc)

        # Phase 3: Pattern lifecycle — prune + boost
        try:
            pruned = self._lifecycle.prune_zero_hit_patterns()
            boosted = self._lifecycle.boost_frequent_patterns()
            stats["patterns_pruned"] = pruned
            stats["patterns_boosted"] = boosted
            logger.info("Nightly: lifecycle — pruned=%d boosted=%d", pruned, boosted)
        except Exception as exc:
            logger.error("Nightly: lifecycle management failed: %s", exc)

        # Phase 4: Log run
        try:
            self.log_run(stats)
        except Exception as exc:
            logger.error("Nightly: failed to write evolution_log: %s", exc)

        return stats

    def log_run(self, stats: dict) -> None:
        """Write an entry to the evolution_log table."""
        self._conn.execute(
            """INSERT INTO evolution_log
               (devices_discovered, devices_removed, patterns_generated,
                patterns_learned, patterns_pruned, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                stats.get("devices_discovered", 0),
                stats.get("devices_removed", 0),
                stats.get("patterns_generated", 0),
                stats.get("patterns_learned", 0),
                stats.get("patterns_pruned", 0),
                json.dumps({k: v for k, v in stats.items() if k not in (
                    "devices_discovered", "devices_removed",
                    "patterns_generated", "patterns_learned", "patterns_pruned",
                )}),
            ),
        )
        self._conn.commit()
        logger.info("evolution_log entry written")
