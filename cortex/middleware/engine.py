"""Middleware engine — runs registered plugins at hook points.

The engine is a singleton that collects active middleware plugins and
invokes their hooks during model inference. Integrates with the
Atlas pipeline without coupling to any specific experiment.

Usage in pipeline::

    from cortex.middleware import get_engine

    engine = get_engine()
    engine.pre_inference({"messages": messages, "model": model_name})
    # ... run model ...
    engine.post_inference({"messages": messages, "response": response})
"""

from __future__ import annotations

import logging
from typing import Any

from .plugins import MiddlewarePlugin, get_active_middleware

logger = logging.getLogger(__name__)

_engine: MiddlewareEngine | None = None


class MiddlewareEngine:
    """Runs middleware plugins at each hook point."""

    def __init__(self) -> None:
        self._plugins: list[MiddlewarePlugin] = []
        self._initialized = False

    def initialize(self) -> None:
        """Load and activate all enabled middleware plugins."""
        if self._initialized:
            return
        self._plugins = get_active_middleware()
        self._initialized = True
        if self._plugins:
            logger.info(
                "Middleware engine: %d active plugin(s)", len(self._plugins)
            )

    def reload(self) -> None:
        """Reload plugins (e.g., after flag changes)."""
        for p in self._plugins:
            try:
                p.on_deactivate()
            except Exception:
                pass
        self._plugins = []
        self._initialized = False
        self.initialize()

    def pre_inference(self, context: dict[str, Any]) -> None:
        """Run all pre-inference hooks."""
        for plugin in self._plugins:
            try:
                plugin.on_pre_inference(context)
            except Exception as e:
                logger.error(
                    "Middleware slot %d pre_inference error: %s",
                    plugin.slot, e,
                )

    def post_inference(self, context: dict[str, Any]) -> None:
        """Run all post-inference hooks."""
        for plugin in self._plugins:
            try:
                plugin.on_post_inference(context)
            except Exception as e:
                logger.error(
                    "Middleware slot %d post_inference error: %s",
                    plugin.slot, e,
                )

    def model_loaded(self, context: dict[str, Any]) -> None:
        """Notify plugins that a model was loaded."""
        for plugin in self._plugins:
            try:
                plugin.on_model_load(context)
            except Exception as e:
                logger.error(
                    "Middleware slot %d model_load error: %s",
                    plugin.slot, e,
                )

    def lora_loaded(self, context: dict[str, Any]) -> None:
        """Notify plugins that a LoRA was loaded."""
        for plugin in self._plugins:
            try:
                plugin.on_lora_load(context)
            except Exception as e:
                logger.error(
                    "Middleware slot %d lora_load error: %s",
                    plugin.slot, e,
                )

    @property
    def active_count(self) -> int:
        return len(self._plugins)

    @property
    def active_slots(self) -> list[int]:
        return [p.slot for p in self._plugins]


def get_engine() -> MiddlewareEngine:
    """Return the singleton middleware engine."""
    global _engine
    if _engine is None:
        _engine = MiddlewareEngine()
    return _engine
