"""Layer 2: Plugin dispatch.

Iterates learned command patterns first (fast-path), then falls back to
the registered plugin registry.  If either matches, its result is returned
and the pipeline short-circuits (no LLM needed).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins import get_registry
from cortex.plugins.base import CommandResult

logger = logging.getLogger(__name__)


def _try_learned_patterns(message: str) -> tuple[str | None, float]:
    """Check message against learned command_patterns in DB.

    Returns (response, confidence) or (None, 0.0) if no match.
    """
    try:
        from cortex.db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT id, pattern, intent, response_template, confidence "
            "FROM command_patterns WHERE confidence >= 0.5 "
            "ORDER BY confidence DESC, hit_count DESC"
        ).fetchall()
        for row in rows:
            try:
                m = re.search(row["pattern"], message, re.IGNORECASE)
            except re.error:
                continue
            if m:
                # Update hit stats
                conn.execute(
                    "UPDATE command_patterns SET hit_count = hit_count + 1, "
                    "last_hit = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()
                template = row["response_template"]
                if template:
                    try:
                        response = m.expand(template)
                    except Exception:
                        response = template
                    logger.info("Layer 2 learned pattern hit: id=%d intent=%s",
                                row["id"], row["intent"])
                    return response, row["confidence"]
    except Exception as e:
        logger.debug("Learned pattern check failed: %s", e)
    return None, 0.0


async def try_plugin_dispatch(
    message: str,
    context: dict[str, Any],
) -> tuple[str | None, float, list[str]]:
    """Dispatch to the Layer 2 plugin registry.

    Returns ``(response_text, confidence, entities_used)`` where
    *response_text* is ``None`` if no plugin matched.
    """
    # Fast-path: check learned patterns first
    learned_response, learned_conf = _try_learned_patterns(message)
    if learned_response is not None:
        return learned_response, learned_conf, []

    # Standard plugin dispatch
    registry = get_registry()
    plugin, match, result = await registry.dispatch(message, context)

    if result is None or not result.success:
        return None, 0.0, []

    logger.info(
        "Layer 2 hit: plugin=%s intent=%s",
        plugin.plugin_id if plugin else "?",
        match.intent if match else "?",
    )
    return result.response, match.confidence if match else 0.9, result.entities_used
