"""Serial bridge — communicate with legacy devices via serial port."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    import serial  # pyserial
    import serial.tools.list_ports

    _HAS_SERIAL = True
except ImportError:  # pragma: no cover
    _HAS_SERIAL = False


@dataclass
class SerialPort:
    """Describes a discovered serial port."""

    device: str
    description: str = ""
    manufacturer: str = ""
    hwid: str = ""


@dataclass
class SerialConfig:
    """Configuration for a serial connection."""

    port: str
    baud: int = 9600
    timeout: float = 2.0
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0


class SerialBridge:
    """Communicate with legacy devices via serial port.

    Used for Arduino sensors, legacy home automation controllers,
    industrial equipment, X10, and other serial-connected devices.
    """

    def __init__(self) -> None:
        self._connections: dict[str, Any] = {}

    async def discover_ports(self) -> list[SerialPort]:
        """Discover available serial ports on the system."""
        if not _HAS_SERIAL:
            logger.warning("pyserial not installed — serial discovery unavailable")
            return []
        try:
            ports = await asyncio.to_thread(self._discover_sync)
            return ports
        except Exception:
            logger.exception("Serial port discovery failed")
            return []

    async def send_command(
        self,
        port: str,
        command: str,
        baud: int = 9600,
        timeout: float = 2.0,
        expect_response: bool = True,
    ) -> str:
        """Send a command to a serial device and optionally read response."""
        if not _HAS_SERIAL:
            logger.error("pyserial not installed — cannot send serial commands")
            return ""
        try:
            return await asyncio.to_thread(
                self._send_sync, port, command, baud, timeout, expect_response
            )
        except Exception:
            logger.exception("Serial command failed on %s", port)
            return ""

    async def send_raw(
        self,
        port: str,
        data: bytes,
        baud: int = 9600,
        timeout: float = 2.0,
    ) -> bytes:
        """Send raw bytes to a serial device and read response."""
        if not _HAS_SERIAL:
            logger.error("pyserial not installed")
            return b""
        try:
            return await asyncio.to_thread(
                self._send_raw_sync, port, data, baud, timeout
            )
        except Exception:
            logger.exception("Serial raw send failed on %s", port)
            return b""

    # ── Synchronous Helpers ──────────────────────────────────────

    def _discover_sync(self) -> list[SerialPort]:
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append(
                SerialPort(
                    device=p.device,
                    description=p.description or "",
                    manufacturer=p.manufacturer or "",
                    hwid=p.hwid or "",
                )
            )
        return ports

    def _send_sync(
        self,
        port: str,
        command: str,
        baud: int,
        timeout: float,
        expect_response: bool,
    ) -> str:
        with serial.Serial(port, baud, timeout=timeout) as ser:
            ser.write((command + "\r\n").encode())
            ser.flush()
            if not expect_response:
                return ""
            response = ser.readline().decode("utf-8", errors="replace").strip()
            return response

    def _send_raw_sync(
        self,
        port: str,
        data: bytes,
        baud: int,
        timeout: float,
    ) -> bytes:
        with serial.Serial(port, baud, timeout=timeout) as ser:
            ser.write(data)
            ser.flush()
            return ser.read(1024)
