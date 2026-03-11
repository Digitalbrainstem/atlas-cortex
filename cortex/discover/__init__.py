"""cortex.discover — entry-point for ``python -m cortex.discover``.

Re-exports core discovery classes from :mod:`cortex.discovery` for convenience.
"""

# Module ownership: Network service discovery (legacy)

from __future__ import annotations

from cortex.discovery import (
    DiscoveredService,
    ServiceConfig,
    ServiceRegistry,
    ServiceScanner,
)

__all__ = [
    "DiscoveredService",
    "ServiceScanner",
    "ServiceRegistry",
    "ServiceConfig",
]
