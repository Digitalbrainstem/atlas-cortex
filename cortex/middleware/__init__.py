"""Model middleware framework for Atlas Cortex.

Provides a plugin-based middleware system for model inference. Middleware
plugins can modify inputs, outputs, or model behavior at well-defined
hook points. Useful for monitoring, A/B testing, caching, and
experimental feature evaluation.

Middleware slots are numbered EXP_001 through EXP_010 and toggled via
environment variables or the experiment_flags DB table. All slots are
disabled by default.

Usage::

    from cortex.middleware import get_active_middleware, MiddlewarePlugin

    # Register a plugin
    class MyPlugin(MiddlewarePlugin):
        def on_pre_inference(self, context):
            ...
        def on_post_inference(self, context):
            ...

    # Load active middleware
    plugins = get_active_middleware()
"""

# Module ownership: Model middleware and experiment framework

from __future__ import annotations

from .engine import MiddlewareEngine, get_engine
from .plugins import MiddlewarePlugin, get_active_middleware
from .flags import is_experiment_enabled, get_experiment_config

__all__ = [
    "MiddlewareEngine",
    "MiddlewarePlugin",
    "get_engine",
    "get_active_middleware",
    "is_experiment_enabled",
    "get_experiment_config",
]
