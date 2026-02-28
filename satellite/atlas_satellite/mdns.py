"""mDNS service announcement for satellite auto-discovery.

On boot, the satellite broadcasts _atlas-satellite._tcp.local so the
Atlas server's passive mDNS listener can detect it automatically.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:
    Zeroconf = None  # type: ignore[assignment,misc]
    ServiceInfo = None  # type: ignore[assignment,misc]
    logger.warning("zeroconf not installed — mDNS disabled")


SERVICE_TYPE = "_atlas-satellite._tcp.local."


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
            SERVICE_TYPE,
            f"{self.satellite_id}.{SERVICE_TYPE}",
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
