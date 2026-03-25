"""Plugin base class for Atlas Cortex Layer 2.

Each integration (Home Assistant, lists, knowledge, etc.) implements this
interface and registers itself with the PluginRegistry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConfigField:
    """Describes one configuration field for a plugin."""

    key: str
    label: str
    field_type: str = "text"  # text, password, url, toggle, select, number
    required: bool = False
    placeholder: str = ""
    help_text: str = ""
    default: Any = None
    options: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the admin API response."""
        d: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "field_type": self.field_type,
            "required": self.required,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "default": self.default,
        }
        if self.options:
            d["options"] = self.options
        return d


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

    #: If True, intent detection enables learn mode for this plugin
    supports_learning: bool = False

    #: Semantic version string
    version: str = "0.0.0"

    #: Author name / organisation
    author: str = ""

    #: JSON Schema describing accepted config keys (for admin UI)
    config_schema: dict[str, Any] = {}

    #: Structured config field descriptors for the admin UI
    config_fields: list[ConfigField] = []

    @property
    def health_message(self) -> str:
        """Human-readable health status message for the admin UI."""
        return "OK"

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
