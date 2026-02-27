"""Layer 2: Plugin dispatch.

Iterates the registered plugin registry.  If a plugin matches, its result
is returned and the pipeline short-circuits (no LLM needed).
"""

from __future__ import annotations

import logging
from typing import Any

from cortex.plugins import get_registry
from cortex.plugins.base import CommandResult

logger = logging.getLogger(__name__)


async def try_plugin_dispatch(
    message: str,
    context: dict[str, Any],
) -> tuple[str | None, float, list[str]]:
    """Dispatch to the Layer 2 plugin registry.

    Returns ``(response_text, confidence, entities_used)`` where
    *response_text* is ``None`` if no plugin matched.
    """
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
