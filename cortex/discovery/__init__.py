"""cortex.discovery — Service Discovery module for Atlas Cortex (Phase I1).

Exports:
    DiscoveredService   — dataclass representing a probed service
    ServiceScanner      — async HTTP/TCP probe scanner
    ServiceRegistry     — SQLite-backed service persistence
    ServiceConfig       — alias for the service_config table dict shape
"""

from __future__ import annotations

from cortex.discovery.registry import ServiceRegistry
from cortex.discovery.scanner import DiscoveredService, ServiceScanner

# Thin type alias kept for callers that want to type-annotate config dicts.
ServiceConfig = dict

__all__ = [
    "DiscoveredService",
    "ServiceScanner",
    "ServiceRegistry",
    "ServiceConfig",
]
