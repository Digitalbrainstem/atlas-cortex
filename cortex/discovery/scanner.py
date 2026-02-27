"""Service scanner for Atlas Cortex (Phase I1.1).

Probes common local network endpoints for known services:
  - Home Assistant  (:8123)
  - MQTT broker     (:1883)
  - Nextcloud       (:80/:443)
  - CalDAV          (:5232)
  - IMAP            (:993/:143) — TCP only

Does NOT require zeroconf — uses HTTP probes. Falls back gracefully if
nothing responds.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HOSTS: list[str] = [
    "localhost",
    "127.0.0.1",
    "homeassistant.local",
    "hassio.local",
]


@dataclass
class DiscoveredService:
    service_type: str
    name: str
    url: str
    discovery_method: str
    is_configured: bool = False
    is_active: bool = False
    health_status: str = "unknown"


class ServiceScanner:
    """HTTP/TCP probe-based service discovery.

    No mDNS dependency — scans a list of hosts against known service
    signatures and returns :class:`DiscoveredService` objects for every
    endpoint that responds.
    """

    PROBE_TARGETS: list[dict[str, Any]] = [
        {
            "service_type": "home_assistant",
            "name": "Home Assistant",
            "ports": [8123],
            "path": "/api/",
            "scheme": "http",
        },
        {
            "service_type": "mqtt",
            "name": "MQTT Broker",
            "ports": [1883, 8883],
            "path": None,
            "scheme": "tcp",
        },
        {
            "service_type": "nextcloud",
            "name": "Nextcloud",
            "ports": [80, 443, 8080],
            "path": "/status.php",
            "scheme": "http",
        },
        {
            "service_type": "caldav",
            "name": "CalDAV Server",
            "ports": [5232, 80, 443],
            "path": "/.well-known/caldav",
            "scheme": "http",
        },
    ]

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def scan(
        self,
        hosts: list[str] | None = None,
        timeout: float = 2.0,
    ) -> list[DiscoveredService]:
        """Probe all *hosts* × *PROBE_TARGETS* combinations concurrently.

        Args:
            hosts:   Override the default host list.
            timeout: Per-probe timeout in seconds.

        Returns:
            Deduplicated list of services that responded.
        """
        if hosts is None:
            hosts = DEFAULT_HOSTS

        tasks = [
            self.probe_host(host, target, timeout)
            for host in hosts
            for target in self.PROBE_TARGETS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        services: list[DiscoveredService] = []
        for r in results:
            if isinstance(r, DiscoveredService) and r.url not in seen:
                seen.add(r.url)
                services.append(r)
        logger.info("scan complete — %d service(s) discovered", len(services))
        return services

    async def probe_host(
        self,
        host: str,
        target: dict[str, Any],
        timeout: float,
    ) -> DiscoveredService | None:
        """Probe a single *host* / *target* pair.

        Returns a :class:`DiscoveredService` on success, ``None`` otherwise.
        """
        scheme = target["scheme"]
        if scheme == "tcp":
            return await self._probe_tcp(host, target, timeout)
        return await self._probe_http(host, target, timeout)

    async def rescan(
        self,
        conn: sqlite3.Connection,
        timeout: float = 2.0,
    ) -> list[DiscoveredService]:
        """Re-run :meth:`scan` and persist results via :class:`ServiceRegistry`.

        Args:
            conn:    Open SQLite connection (WAL-mode recommended).
            timeout: Per-probe timeout forwarded to :meth:`scan`.

        Returns:
            Freshly discovered services (same as :meth:`scan`).
        """
        from cortex.discovery.registry import ServiceRegistry

        services = await self.scan(timeout=timeout)
        registry = ServiceRegistry(conn)
        for svc in services:
            registry.upsert_service(svc)
        return services

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _probe_http(
        self,
        host: str,
        target: dict[str, Any],
        timeout: float,
    ) -> DiscoveredService | None:
        """Try every port in *target['ports']* with an HTTP GET.

        Any non-5xx response is treated as a live service: 401 is expected
        for authenticated endpoints (e.g. HA /api/ without a token), and
        3xx may indicate a redirect to HTTPS.  SSL verification is disabled
        because local services often use self-signed certificates.
        """
        path = target.get("path") or "/"
        for port in target["ports"]:
            scheme = "https" if port == 443 else "http"
            url = f"{scheme}://{host}:{port}{path}"
            try:
                async with httpx.AsyncClient(
                    verify=False,  # local network — self-signed certs are common
                    follow_redirects=True,
                    timeout=timeout,
                ) as client:
                    resp = await client.get(url)
                # 2xx = healthy, 3xx = redirect (already followed), 4xx = running
                # but needs auth/different path.  5xx = unhealthy server, skip.
                if resp.status_code < 500:
                    logger.debug("HTTP probe OK: %s (%d)", url, resp.status_code)
                    return DiscoveredService(
                        service_type=target["service_type"],
                        name=target["name"],
                        url=f"{scheme}://{host}:{port}",
                        discovery_method="http_probe",
                        health_status="ok",
                    )
            except Exception:
                pass
        return None

    async def _probe_tcp(
        self,
        host: str,
        target: dict[str, Any],
        timeout: float,
    ) -> DiscoveredService | None:
        """Try a bare TCP connect for each port in *target['ports']*."""
        for port in target["ports"]:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=timeout,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                url = f"tcp://{host}:{port}"
                logger.debug("TCP probe OK: %s", url)
                return DiscoveredService(
                    service_type=target["service_type"],
                    name=target["name"],
                    url=url,
                    discovery_method="tcp_probe",
                    health_status="ok",
                )
            except Exception:
                pass
        return None
