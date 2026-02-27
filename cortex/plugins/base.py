"""Plugin base class for Atlas Cortex Layer 2.

Each integration (Home Assistant, lists, knowledge, etc.) implements this
interface and registers itself with the PluginRegistry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandMatch:
    """Result returned by :meth:`CortexPlugin.match`."""
    matched: bool
    intent: str = ""
    entities: list[str] = field(default_factory=list)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    """Result returned by :meth:`CortexPlugin.handle`."""
    success: bool
    response: str
    entities_used: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CortexPlugin(abc.ABC):
    """Abstract base class for all Atlas Cortex Layer 2 plugins."""

    #: Unique identifier, e.g. ``"ha_commands"``
    plugin_id: str = ""

    #: Human-readable name shown in installer and logs
    display_name: str = ""

    #: Plugin type: ``"action"`` | ``"knowledge"`` | ``"list_backend"``
    plugin_type: str = "action"

    @abc.abstractmethod
    async def setup(self, config: dict[str, Any]) -> bool:
        """Configure and activate the plugin.

        Returns ``True`` on success, ``False`` if setup failed (plugin stays
        inactive).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health(self) -> bool:
        """Return ``True`` if the plugin's back-end service is reachable."""
        raise NotImplementedError

    @abc.abstractmethod
    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        """Determine whether this plugin can handle *message*.

        Args:
            message: The user's message text.
            context: Layer 0 context dict (user_id, sentiment, room, etc.).

        Returns a :class:`CommandMatch` with ``matched=True`` if applicable.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        """Execute the command and return a natural-language response."""
        raise NotImplementedError

    async def discover_entities(self) -> list[dict[str, Any]]:
        """Optional: return discoverable entities (devices, lists, etc.)."""
        return []
