"""WebSocket client for connecting to Atlas server.

Handles the satellite side of the protocol:
  Satellite → Server: ANNOUNCE, WAKE, AUDIO_START/CHUNK/END, STATUS, HEARTBEAT
  Server → Satellite: ACCEPTED, TTS_START/CHUNK/END, PLAY_FILLER, COMMAND, CONFIG, SYNC_FILLERS
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import time
from typing import Any, Awaitable, Callable, Optional

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict], Awaitable[None]]


class SatelliteWSClient:
    """WebSocket client connecting a satellite to the Atlas server."""

    def __init__(
        self,
        server_url: str,
        satellite_id: str,
        room: str = "",
        capabilities: list[str] | None = None,
        hw_info: dict | None = None,
    ):
        self.server_url = server_url
        self.satellite_id = satellite_id
        self.room = room
        self.capabilities = capabilities or []
        self.hw_info = hw_info or {}

        self._ws: Optional[ClientConnection] = None
        self._session_id: Optional[str] = None
        self._handlers: dict[str, MessageHandler] = {}
        self._connected = False
        self._reconnect_delay = 2
        self._max_reconnect_delay = 60

    def on(self, msg_type: str, handler: MessageHandler) -> None:
        """Register a handler for a server message type."""
        self._handlers[msg_type] = handler

    async def connect(self) -> bool:
        """Connect to the Atlas server and complete handshake."""
        try:
            self._ws = await websockets.connect(
                self.server_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            # Send ANNOUNCE
            await self._send({
                "type": "ANNOUNCE",
                "satellite_id": self.satellite_id,
                "hostname": socket.gethostname(),
                "room": self.room,
                "capabilities": self.capabilities,
                "hw_info": self.hw_info,
            })

            # Wait for ACCEPTED (with timeout)
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            response = json.loads(raw)

            if response.get("type") == "ACCEPTED":
                self._session_id = response.get("session_id")
                self._connected = True
                self._reconnect_delay = 2
                logger.info(
                    "Connected to server (session: %s)", self._session_id
                )
                return True
            else:
                logger.error("Server rejected connection: %s", response)
                return False

        except Exception:
            logger.exception("Failed to connect to %s", self.server_url)
            return False

    async def _send(self, message: dict) -> None:
        """Send a JSON message."""
        if self._ws:
            await self._ws.send(json.dumps(message))

    async def send_wake(self, confidence: float) -> None:
        await self._send({
            "type": "WAKE",
            "satellite_id": self.satellite_id,
            "wake_word_confidence": confidence,
        })

    async def send_audio_start(self) -> None:
        await self._send({
            "type": "AUDIO_START",
            "satellite_id": self.satellite_id,
            "format": "pcm_16k_16bit_mono",
        })

    async def send_audio_chunk(self, audio_data: bytes) -> None:
        """Send an audio chunk (base64-encoded in JSON)."""
        await self._send({
            "type": "AUDIO_CHUNK",
            "satellite_id": self.satellite_id,
            "audio": base64.b64encode(audio_data).decode("ascii"),
        })

    async def send_audio_end(self, reason: str = "vad_silence") -> None:
        await self._send({
            "type": "AUDIO_END",
            "satellite_id": self.satellite_id,
            "reason": reason,
        })

    async def send_heartbeat(
        self, uptime: float = 0, cpu_temp: float = 0, wifi_rssi: int = 0
    ) -> None:
        await self._send({
            "type": "HEARTBEAT",
            "satellite_id": self.satellite_id,
            "uptime": uptime,
            "cpu_temp": cpu_temp,
            "wifi_rssi": wifi_rssi,
        })

    async def send_status(self, status: str) -> None:
        await self._send({
            "type": "STATUS",
            "satellite_id": self.satellite_id,
            "status": status,
        })

    async def listen(self) -> None:
        """Listen for messages from server. Blocks until disconnected."""
        if not self._ws:
            return
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                handler = self._handlers.get(msg_type)
                if handler:
                    try:
                        await handler(msg)
                    except Exception:
                        logger.exception("Handler error for %s", msg_type)
                else:
                    logger.debug("Unhandled message type: %s", msg_type)
        except websockets.ConnectionClosed:
            logger.info("Server connection closed")
        except Exception:
            logger.exception("WebSocket listen error")
        finally:
            self._connected = False

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False

    async def reconnect_loop(self) -> None:
        """Keep trying to reconnect with exponential backoff."""
        while True:
            if not self._connected:
                logger.info(
                    "Reconnecting in %ds...", self._reconnect_delay
                )
                await asyncio.sleep(self._reconnect_delay)
                success = await self.connect()
                if not success:
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )
            else:
                await asyncio.sleep(5)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id
