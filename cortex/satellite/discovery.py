"""Satellite discovery — passive mDNS listener + on-demand scan.

Primary method: satellites announce themselves via mDNS
  (_atlas-satellite._tcp.local). Atlas runs a passive listener — zero network
  traffic until a satellite broadcasts.

Fallback: admin clicks "Scan Now" which does a one-time mDNS query + ARP
  table scan for atlas-satellite hosts.

Manual: admin enters IP directly (for shared-mode or mDNS-blocked networks).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

ATLAS_SATELLITE_SERVICE = "_atlas-satellite._tcp.local."
ATLAS_SERVER_SERVICE = "_atlas-cortex._tcp.local."
DEFAULT_HOSTNAME = "atlas-satellite"


@dataclass
class DiscoveredSatellite:
    """A satellite found via mDNS announcement, scan, or manual entry."""

    ip_address: str
    hostname: str = ""
    mac_address: str = ""
    port: int = 5110
    properties: dict = field(default_factory=dict)
    discovery_method: str = "mdns"  # "mdns", "scan", "manual"
    discovered_at: float = field(default_factory=time.time)


class SatelliteDiscovery:
    """Passive mDNS listener + on-demand scan fallback."""

    def __init__(self) -> None:
        self._announced: dict[str, DiscoveredSatellite] = {}
        self._listener: _MdnsListener | None = None
        self._zeroconf = None
        self._browser = None
        self._callbacks: list[Callable[[DiscoveredSatellite], None]] = []
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the passive mDNS listener in a background thread."""
        if self._running:
            return
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            self._listener = _MdnsListener(self._on_service_found)
            self._zeroconf = Zeroconf()
            self._browser = ServiceBrowser(
                self._zeroconf, ATLAS_SATELLITE_SERVICE, self._listener
            )
            self._running = True
            logger.info("Satellite mDNS listener started for %s", ATLAS_SATELLITE_SERVICE)
        except ImportError:
            logger.warning("zeroconf not installed — mDNS discovery disabled")
        except Exception:
            logger.exception("Failed to start mDNS listener")

    async def stop(self) -> None:
        """Stop the mDNS listener and clean up."""
        if self._zeroconf:
            self._zeroconf.close()
            self._zeroconf = None
        self._browser = None
        self._listener = None
        self._running = False
        logger.info("Satellite mDNS listener stopped")

    # ── Passive discovery ──────────────────────────────────────────

    def on_discovered(self, callback: Callable[[DiscoveredSatellite], None]) -> None:
        """Register a callback for when a new satellite is discovered."""
        self._callbacks.append(callback)

    def get_announced(self) -> list[DiscoveredSatellite]:
        """Return all satellites that have self-announced."""
        return list(self._announced.values())

    def get_announced_since(self, since: float) -> list[DiscoveredSatellite]:
        """Return satellites announced after the given timestamp."""
        return [s for s in self._announced.values() if s.discovered_at > since]

    def clear_announced(self, ip: str) -> None:
        """Remove a satellite from the announced list (after provisioning)."""
        self._announced.pop(ip, None)

    # ── On-demand scan (fallback) ──────────────────────────────────

    async def scan_now(self, timeout: float = 5.0) -> list[DiscoveredSatellite]:
        """Admin-triggered one-time scan. Combines mDNS query + ARP table.

        This is a fallback for networks where passive mDNS doesn't work.
        """
        results: dict[str, DiscoveredSatellite] = {}

        # Method 1: Active mDNS query
        mdns_results = await self._mdns_query(timeout)
        for sat in mdns_results:
            results[sat.ip_address] = sat

        # Method 2: ARP table scan for atlas-satellite hostnames
        arp_results = await self._arp_hostname_scan()
        for sat in arp_results:
            if sat.ip_address not in results:
                results[sat.ip_address] = sat

        return list(results.values())

    async def check_host(self, ip: str, port: int = 5110) -> DiscoveredSatellite | None:
        """Probe a specific IP for a satellite service (manual add)."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=3.0
            )
            writer.close()
            await writer.wait_closed()

            hostname = await self._reverse_dns(ip)
            return DiscoveredSatellite(
                ip_address=ip,
                hostname=hostname,
                port=port,
                discovery_method="manual",
            )
        except (asyncio.TimeoutError, OSError):
            return None

    async def check_ssh(self, ip: str, port: int = 22) -> bool:
        """Check if SSH is reachable on a host."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=3.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, OSError):
            return False

    # ── Internal ───────────────────────────────────────────────────

    def _on_service_found(self, name: str, ip: str, port: int, properties: dict) -> None:
        """Called by the mDNS listener when a satellite announces."""
        mac = properties.get("mac", "")
        sat = DiscoveredSatellite(
            ip_address=ip,
            hostname=name.replace(f".{ATLAS_SATELLITE_SERVICE}", ""),
            mac_address=mac,
            port=port,
            properties=properties,
            discovery_method="mdns",
        )

        is_new = ip not in self._announced
        self._announced[ip] = sat

        if is_new:
            logger.info("New satellite announced: %s at %s:%d", sat.hostname, ip, port)
            for cb in self._callbacks:
                try:
                    cb(sat)
                except Exception:
                    logger.exception("Error in discovery callback")

    async def _mdns_query(self, timeout: float) -> list[DiscoveredSatellite]:
        """Active mDNS query for atlas-satellite services."""
        results = []
        try:
            from zeroconf import Zeroconf, ServiceBrowser
            import threading

            found = []
            event = threading.Event()

            class _Collector:
                def add_service(self, zc, type_, name):
                    info = zc.get_service_info(type_, name)
                    if info and info.parsed_addresses():
                        ip = info.parsed_addresses()[0]
                        props = {
                            k.decode() if isinstance(k, bytes) else k:
                            v.decode() if isinstance(v, bytes) else v
                            for k, v in (info.properties or {}).items()
                        }
                        found.append(DiscoveredSatellite(
                            ip_address=ip,
                            hostname=name.replace(f".{ATLAS_SATELLITE_SERVICE}", ""),
                            port=info.port or 5110,
                            properties=props,
                            discovery_method="scan",
                        ))

                def remove_service(self, zc, type_, name):
                    pass

                def update_service(self, zc, type_, name):
                    pass

            zc = Zeroconf()
            browser = ServiceBrowser(zc, ATLAS_SATELLITE_SERVICE, _Collector())
            await asyncio.sleep(timeout)
            zc.close()
            results = found
        except ImportError:
            logger.warning("zeroconf not installed — skipping mDNS scan")
        except Exception:
            logger.exception("mDNS query failed")
        return results

    async def _arp_hostname_scan(self) -> list[DiscoveredSatellite]:
        """Parse ARP table and check hostnames for atlas-satellite pattern."""
        results = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "neigh", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 5 or parts[3] == "FAILED":
                    continue
                ip = parts[0]
                mac = parts[4] if len(parts) > 4 else ""

                hostname = await self._reverse_dns(ip)
                if hostname and DEFAULT_HOSTNAME in hostname.lower():
                    results.append(DiscoveredSatellite(
                        ip_address=ip,
                        hostname=hostname,
                        mac_address=mac,
                        discovery_method="scan",
                    ))
        except Exception:
            logger.exception("ARP scan failed")
        return results

    @staticmethod
    async def _reverse_dns(ip: str) -> str:
        """Attempt reverse DNS lookup."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: socket.gethostbyaddr(ip)
            )
            return result[0]
        except (socket.herror, socket.gaierror, OSError):
            return ""


class _MdnsListener:
    """Zeroconf service listener for atlas-satellite announcements."""

    def __init__(self, callback: Callable) -> None:
        self._callback = callback

    def add_service(self, zc, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info and info.parsed_addresses():
            ip = info.parsed_addresses()[0]
            port = info.port or 5110
            properties = {
                k.decode() if isinstance(k, bytes) else k:
                v.decode() if isinstance(v, bytes) else v
                for k, v in (info.properties or {}).items()
            }
            self._callback(name, ip, port, properties)

    def remove_service(self, zc, type_: str, name: str) -> None:
        logger.debug("Satellite service removed: %s", name)

    def update_service(self, zc, type_: str, name: str) -> None:
        # Re-announce (IP change, etc.)
        self.add_service(zc, type_, name)


class ServerAnnouncer:
    """Announces the Atlas server via mDNS so satellites can auto-discover it.

    Broadcasts _atlas-cortex._tcp.local with the WebSocket endpoint info.
    Satellites browse for this service to find their server URL automatically.
    """

    def __init__(self, port: int = 5100, ws_path: str = "/ws/satellite") -> None:
        self.port = port
        self.ws_path = ws_path
        self._zeroconf = None
        self._info = None

    async def start(self) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf

            local_ip = _get_local_ip()
            hostname = socket.gethostname()

            self._info = ServiceInfo(
                ATLAS_SERVER_SERVICE,
                f"atlas-cortex.{ATLAS_SERVER_SERVICE}",
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={
                    "hostname": hostname,
                    "ws_path": self.ws_path,
                    "version": "0.1.0",
                },
            )
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(self._info)
            logger.info(
                "Server mDNS: announcing _atlas-cortex._tcp at %s:%d",
                local_ip, self.port,
            )
        except ImportError:
            logger.warning("zeroconf not installed — server mDNS announcement disabled")
        except Exception:
            logger.exception("Failed to start server mDNS announcement")

    async def stop(self) -> None:
        if self._zeroconf and self._info:
            self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
            self._zeroconf = None
            self._info = None


def _get_local_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
