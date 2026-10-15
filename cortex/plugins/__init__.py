"""Plugin registry for Atlas Cortex Layer 2.

Plugins register themselves here. The pipeline calls
:meth:`PluginRegistry.dispatch` for every message.
"""

# Module ownership: Plugin framework: registry, base class

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from .base import CommandMatch, CommandResult, ConfigField, CortexPlugin
from .intent import command_scope, correction_fragment

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Central registry of active Layer 2 plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, CortexPlugin] = {}
        self._recent: OrderedDict[tuple[str, ...], tuple[float, str, bool]] = OrderedDict()
        self._recent_lock = threading.RLock()

    def _remember(self, scope: tuple[str, ...] | None, plugin_id: str) -> float | None:
        if scope is None:
            return None
        with self._recent_lock:
            stamp = time.monotonic()
            self._recent[scope] = (stamp, plugin_id, False)
            self._recent.move_to_end(scope)
            while len(self._recent) > 256:
                self._recent.popitem(last=False)
            return stamp

    def _mark_failed(self, scope: tuple[str, ...] | None, stamp: float | None) -> None:
        if scope is not None:
            with self._recent_lock:
                recent = self._recent.get(scope)
                if recent is not None and recent[0] == stamp:
                    self._recent[scope] = (recent[0], recent[1], True)

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
        scope = command_scope(context)
        with self._recent_lock:
            recent = self._recent.get(scope) if scope is not None else None
        previous_id = recent[1] if recent and time.monotonic() - recent[0] <= 120 else None
        context["recent_plugin_id"] = previous_id
        context["recent_command_failed"] = bool(recent and recent[2])
        for plugin in self._plugins.values():
            match: CommandMatch | None = None
            handler_started = False
            stamp: float | None = None
            try:
                match = await plugin.match(message, context)
                if not match.matched:
                    continue
                if match.intent or not match.metadata.get("clarification"):
                    stamp = self._remember(scope, plugin.plugin_id)
                healthy = await plugin.health()
                if not healthy:
                    logger.warning("Plugin %s matched but is unhealthy", plugin.plugin_id)
                    self._mark_failed(scope, stamp)
                    return plugin, match, CommandResult(
                        success=False,
                        response=f"{plugin.display_name or plugin.plugin_id} is unavailable. Please check its configuration.",
                        metadata={"action_outcome": "none"},
                    )
                handler_started = True
                result = await plugin.handle(message, match, context)
                if not result.handled:
                    logger.info("Plugin %s explicitly declined the request", plugin.plugin_id)
                    continue
                if not result.success and not result.clarification_required:
                    self._mark_failed(scope, stamp)
                return plugin, match, result
            except Exception as exc:
                logger.exception("Plugin %s raised an error: %s", plugin.plugin_id, exc)
                if match is not None and match.matched:
                    self._mark_failed(scope, stamp)
                    return plugin, match, CommandResult(
                        success=False,
                        response=f"I couldn't complete the request using {plugin.display_name or plugin.plugin_id}. "
                        + ("The outcome is uncertain; please check before retrying."
                           if handler_started else "No action was started."),
                        metadata={"action_outcome": "uncertain" if handler_started else "none"},
                    )
        if previous_id in self._plugins and correction_fragment(message) is not None:
            return self._plugins[previous_id], CommandMatch(True, "clarification"), CommandResult(
                False, "Please restate the full command and target so I don't change the wrong item.",
                clarification_required=True,
            )
        self._remember(scope, "_llm")
        return None, None, None


# Module-level singleton — the pipeline imports this
_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    """Return the global plugin registry singleton."""
    return _registry
