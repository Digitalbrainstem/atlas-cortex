"""Plugin registry for Atlas Cortex Layer 2.

Plugins register themselves here. The pipeline calls
:meth:`PluginRegistry.dispatch` for every message.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Central registry of active Layer 2 plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, CortexPlugin] = {}

    def register(self, plugin: CortexPlugin) -> None:
        """Register (or replace) a plugin instance."""
        self._plugins[plugin.plugin_id] = plugin
        logger.info("Plugin registered: %s (%s)", plugin.plugin_id, plugin.display_name)

    def unregister(self, plugin_id: str) -> None:
        """Remove a plugin from the registry."""
        self._plugins.pop(plugin_id, None)

    def list_plugins(self) -> list[CortexPlugin]:
        return list(self._plugins.values())

    async def dispatch(
        self,
        message: str,
        context: dict[str, Any],
    ) -> tuple[CortexPlugin | None, CommandMatch | None, CommandResult | None]:
        """Try each plugin in registration order; return first match.

        Returns ``(plugin, match, result)`` on success, or ``(None, None, None)``
        if no plugin matched.
        """
        for plugin in self._plugins.values():
            try:
                match = await plugin.match(message, context)
                if not match.matched:
                    continue
                healthy = await plugin.health()
                if not healthy:
                    logger.warning("Plugin %s matched but is unhealthy, skipping", plugin.plugin_id)
                    continue
                result = await plugin.handle(message, match, context)
                return plugin, match, result
            except Exception as exc:
                logger.exception("Plugin %s raised an error: %s", plugin.plugin_id, exc)
        return None, None, None


# Module-level singleton — the pipeline imports this
_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Return the global plugin registry singleton."""
    return _registry
