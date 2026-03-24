"""Middleware plugin base class and registry.

Plugins implement hook methods that run at defined points in the
model inference pipeline. Each plugin is associated with an experiment
slot and only runs when that slot is enabled.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from .flags import is_experiment_enabled, get_experiment_config

logger = logging.getLogger(__name__)

# Registry: slot -> plugin class
_registry: dict[int, type[MiddlewarePlugin]] = {}


class MiddlewarePlugin(abc.ABC):
    """Base class for model middleware plugins.

    Subclasses implement any combination of hook methods. All hooks
    receive a mutable *context* dict with request/response data.

    Hook points:
      - on_pre_inference: before model receives input
      - on_post_inference: after model produces output
      - on_model_load: when a model is loaded/swapped
      - on_lora_load: when a LoRA adapter is loaded
    """

    def __init__(self, slot: int, config: dict[str, Any] | None = None):
        self.slot = slot
        self.config = config or {}

    def on_pre_inference(self, context: dict[str, Any]) -> None:
        """Called before model inference. Modify context in place."""

    def on_post_inference(self, context: dict[str, Any]) -> None:
        """Called after model inference. Modify context in place."""

    def on_model_load(self, context: dict[str, Any]) -> None:
        """Called when a model is loaded or swapped."""

    def on_lora_load(self, context: dict[str, Any]) -> None:
        """Called when a LoRA adapter is loaded."""

    def on_activate(self) -> None:
        """Called when the plugin slot is enabled."""

    def on_deactivate(self) -> None:
        """Called when the plugin slot is disabled."""


def register_plugin(slot: int, plugin_cls: type[MiddlewarePlugin]) -> None:
    """Register a middleware plugin class for an experiment slot."""
    if slot in _registry:
        logger.warning(
            "Overwriting middleware slot %d: %s -> %s",
            slot, _registry[slot].__name__, plugin_cls.__name__,
        )
    _registry[slot] = plugin_cls
    logger.debug("Registered middleware slot %d: %s", slot, plugin_cls.__name__)


def get_active_middleware() -> list[MiddlewarePlugin]:
    """Return instantiated plugins for all enabled experiment slots."""
    active = []
    for slot, cls in sorted(_registry.items()):
        if is_experiment_enabled(slot):
            try:
                config = get_experiment_config(slot)
                plugin = cls(slot=slot, config=config)
                plugin.on_activate()
                active.append(plugin)
                logger.info("Activated middleware slot %d: %s", slot, cls.__name__)
            except Exception as e:
                logger.error("Failed to activate slot %d: %s", slot, e)
    return active


def get_registered_slots() -> dict[int, str]:
    """Return map of registered slot numbers to plugin class names."""
    return {slot: cls.__name__ for slot, cls in sorted(_registry.items())}
