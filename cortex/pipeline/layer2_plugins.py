"""Layer 2: Plugin dispatch.

Tries executable plugins before informational learned patterns. If either matches, its result is returned
and the pipeline short-circuits (no LLM needed).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins import get_registry
from .turns import ActionOutcome, DispatchResult, RouteStatus

logger = logging.getLogger(__name__)


def _try_learned_patterns(message: str) -> tuple[str | None, float]:
    """Check message against learned command_patterns in DB.

    Returns (response, confidence) or (None, 0.0) if no match.
    """
    try:
        from cortex.db import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT * "
            "FROM command_patterns WHERE confidence >= 0.5 "
            "ORDER BY confidence DESC, hit_count DESC"
        ).fetchall()
        for row in rows:
            # Templates are answers, never substitutes for a device/list/timer effect.
            if (row["intent"] not in ("greet", "greeting", "name", "inform", "definition", "fact")
                    and not row["intent"].startswith("greet_")):
                continue
            if "entity_domain" in row.keys() and row["entity_domain"]:
                continue
            try:
                m = re.search(row["pattern"], message, re.IGNORECASE)
            except re.error as exc:
                logger.warning("Invalid learned command pattern %s: %s", row["id"], exc)
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
                    except re.error as exc:
                        logger.warning("Invalid learned response template %s: %s", row["id"], exc)
                        continue
                    logger.info("Layer 2 learned pattern hit: id=%d intent=%s",
                                row["id"], row["intent"])
                    return response, row["confidence"]
    except Exception as e:
        logger.warning("Learned pattern check failed: %s", e)
    return None, 0.0


async def try_plugin_dispatch(
    message: str,
    context: dict[str, Any],
) -> tuple[str | None, float, list[str]]:
    """Dispatch to the Layer 2 plugin registry.

    Returns ``(response_text, confidence, entities_used)`` where
    *response_text* is ``None`` if no plugin matched.
    """
    result = await dispatch_plugins(message, context)
    return result.response, result.confidence, result.entities


async def dispatch_plugins(message: str, context: dict[str, Any]) -> DispatchResult:
    """Only an unmatched request falls through to generation."""
    # Standard plugin dispatch
    registry = get_registry()
    plugin, match, result = await registry.dispatch(message, context)

    if result is None:
        learned_response, learned_conf = _try_learned_patterns(message)
        if learned_response is not None:
            return DispatchResult(RouteStatus.HANDLED, learned_response, learned_conf)
        return DispatchResult(RouteStatus.NOT_MATCHED)

    status = RouteStatus.HANDLED
    if result.clarification_required:
        status = RouteStatus.CLARIFICATION
    elif not result.success:
        status = RouteStatus.FAILED
        logger.warning("Recognized plugin request failed: %s", plugin.plugin_id if plugin else "?")
    response = result.response
    if not response:
        logger.error("Plugin returned an empty response: %s", plugin.plugin_id if plugin else "?")
        status = RouteStatus.FAILED
        response = "I couldn't complete that request. Please check the plugin configuration."

    logger.info(
        "Layer 2 hit: plugin=%s intent=%s",
        plugin.plugin_id if plugin else "?",
        match.intent if match else "?",
    )
    outcome = ActionOutcome.NONE
    if (plugin is not None and (
        plugin.plugin_type == "action"
        or plugin.plugin_type == "list_backend" and match and match.intent in ("add_item", "remove_item")
    ) and status != RouteStatus.CLARIFICATION):
        outcome = ActionOutcome.SUCCEEDED if status == RouteStatus.HANDLED else ActionOutcome.FAILED
        reported = result.metadata.get("action_outcome")
        if reported in {item.value for item in ActionOutcome}:
            outcome = ActionOutcome(reported)
    return DispatchResult(
        status=status, response=response, confidence=match.confidence if match else 0.9,
        plugin_id=plugin.plugin_id if plugin else None,
        intent=match.intent if match else "", entities=result.entities_used,
        action_outcome=outcome,
    )
