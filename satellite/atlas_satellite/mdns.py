"""mDNS service announcement and server discovery.

On boot, the satellite:
  1. Broadcasts _atlas-satellite._tcp.local (so the server finds us)
  2. Browses for _atlas-cortex._tcp.local (so we find the server)

This enables fully zero-config operation — no server URL needed.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
except ImportError:
    Zeroconf = None  # type: ignore[assignment,misc]
    ServiceInfo = None  # type: ignore[assignment,misc]
    ServiceBrowser = None  # type: ignore[assignment,misc]
    logger.warning("zeroconf not installed — mDNS disabled")


SATELLITE_SERVICE_TYPE = "_atlas-satellite._tcp.local."
SERVER_SERVICE_TYPE = "_atlas-cortex._tcp.local."


class SatelliteAnnouncer:
    """Announces this satellite on the local network via mDNS."""

    def __init__(
        self,
        satellite_id: str,
        port: int = 5110,
        room: str = "",
        hw_type: str = "",
    ):
        self.satellite_id = satellite_id
        self.port = port
        self.room = room
        self.hw_type = hw_type
        self._zeroconf: Optional[Zeroconf] = None
        self._info: Optional[ServiceInfo] = None

    def start(self) -> None:
        if Zeroconf is None:
            logger.warning("Cannot announce — zeroconf not installed")
            return

        local_ip = _get_local_ip()
        hostname = socket.gethostname()

        self._info = ServiceInfo(
            SATELLITE_SERVICE_TYPE,
            f"{self.satellite_id}.{SATELLITE_SERVICE_TYPE}",
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties={
                "id": self.satellite_id,
                "room": self.room,
                "hw_type": self.hw_type,
                "hostname": hostname,
            },
        )

        self._zeroconf = Zeroconf()
        self._zeroconf.register_service(self._info)
        logger.info(
            "mDNS: announcing %s at %s:%d",
            self.satellite_id,
            local_ip,
            self.port,
        )

    def stop(self) -> None:
        if self._zeroconf and self._info:
            self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
            self._zeroconf = None
            self._info = None
            logger.info("mDNS: stopped announcing")


class ServerDiscovery:
    """Discovers the Atlas server on the local network via mDNS.

    Browses for _atlas-cortex._tcp.local and calls on_found when
    a server is detected, providing ws://ip:port/ws/satellite.
    """

    def __init__(self, on_found: Callable[[str], None]):
        self.on_found = on_found
        self._zeroconf: Optional[Zeroconf] = None
        self._browser = None
        self._found = False

    def start(self) -> None:
        if Zeroconf is None or ServiceBrowser is None:
            logger.warning("Cannot browse for server — zeroconf not installed")
            return

        self._zeroconf = Zeroconf()
        self._browser = ServiceBrowser(
            self._zeroconf,
            SERVER_SERVICE_TYPE,
            handlers=[self._on_state_change],
        )
        logger.info("mDNS: browsing for Atlas server (%s)", SERVER_SERVICE_TYPE)

    def _on_state_change(
        self, zeroconf: Zeroconf, service_type: str,
        name: str, state_change: ServiceStateChange,
    ) -> None:
        if state_change != ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port or 5100
            ws_path = (info.properties or {}).get(b"ws_path", b"/ws/satellite").decode()
            server_url = f"ws://{ip}:{port}{ws_path}"
            logger.info("mDNS: discovered Atlas server at %s", server_url)
            if not self._found:
                self._found = True
                self.on_found(server_url)

    def stop(self) -> None:
        if self._browser:
            self._browser.cancel()
        if self._zeroconf:
            self._zeroconf.close()
            self._zeroconf = None

    @property
    def found(self) -> bool:
        return self._found


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
