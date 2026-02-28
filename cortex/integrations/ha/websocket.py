"""Home Assistant WebSocket listener (Phase I2.3).

Maintains a persistent WebSocket connection to Home Assistant, subscribes to
``state_changed`` events, and caches the latest state of every entity for
fast synchronous lookups.

Uses :mod:`aiohttp` for the WebSocket transport and reconnects with
exponential backoff on any disconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable

import aiohttp

logger = logging.getLogger(__name__)

_BACKOFF_BASE = 1
_BACKOFF_MAX = 60


class HAWebSocketError(Exception):
    """Raised on unrecoverable WebSocket errors (e.g. bad auth)."""


class HAWebSocketListener:
    """Real-time event listener for Home Assistant via WebSocket.

    Parameters
    ----------
    base_url:
        HA base URL (e.g. ``http://homeassistant.local:8123``).
        Falls back to the ``HA_URL`` environment variable.
    token:
        Long-lived access token. Falls back to ``HA_TOKEN``.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self._base_url = (base_url or os.getenv("HA_URL", "")).rstrip("/")
        self._token = token or os.getenv("HA_TOKEN", "")
        self._callbacks: list[Callable] = []
        self._states: dict[str, dict] = {}
        self._connected = False
        self._running = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._msg_id = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start the listener loop in a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._run_loop())

    async def stop(self) -> None:
        """Gracefully shut down the listener."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._connected = False

    def on_state_change(self, callback: Callable) -> None:
        """Register a callback invoked on every ``state_changed`` event.

        The callback receives ``(entity_id: str, new_state: dict, old_state: dict | None)``.
        """
        self._callbacks.append(callback)

    def get_state(self, entity_id: str) -> dict | None:
        """Return the last-known state dict for *entity_id*, or ``None``."""
        return self._states.get(entity_id)

    @property
    def connected(self) -> bool:
        """``True`` while the WebSocket is authenticated and listening."""
        return self._connected

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _run_loop(self) -> None:
        """Outer reconnect loop with exponential backoff."""
        backoff = _BACKOFF_BASE
        while self._running:
            try:
                await self._connect_and_listen()
            except HAWebSocketError:
                # Auth failure — stop entirely.
                self._running = False
                raise
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                logger.warning("HA WS disconnected: %s — retrying in %ss", exc, backoff)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected HA WS error: %s — retrying in %ss", exc, backoff)

            self._connected = False
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

        self._connected = False

    async def _connect_and_listen(self) -> None:
        """Single connection lifecycle: connect → auth → subscribe → read."""
        ws_url = self._ws_url()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        async with self._session.ws_connect(ws_url) as ws:
            self._ws = ws
            await self._authenticate(ws)
            self._connected = True
            self._msg_id = 0
            logger.info("HA WebSocket connected and authenticated")

            sub_id = self._next_id()
            await ws.send_json({
                "id": sub_id,
                "type": "subscribe_events",
                "event_type": "state_changed",
            })

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    self._handle_message(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

        self._connected = False

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Perform the HA auth handshake."""
        # Step 1 — receive auth_required
        auth_req = await ws.receive_json()
        if auth_req.get("type") != "auth_required":
            raise HAWebSocketError(f"Expected auth_required, got {auth_req.get('type')}")

        # Step 2 — send token
        await ws.send_json({"type": "auth", "access_token": self._token})

        # Step 3 — receive auth_ok / auth_invalid
        auth_resp = await ws.receive_json()
        if auth_resp.get("type") == "auth_invalid":
            raise HAWebSocketError("HA WebSocket authentication failed — check token")
        if auth_resp.get("type") != "auth_ok":
            raise HAWebSocketError(f"Unexpected auth response: {auth_resp.get('type')}")

    def _handle_message(self, data: dict) -> None:
        """Process a single incoming WebSocket message."""
        if data.get("type") != "event":
            return
        event = data.get("event", {})
        event_data = event.get("data", {})

        entity_id = event_data.get("entity_id")
        new_state = event_data.get("new_state")
        old_state = event_data.get("old_state")

        if entity_id is None or new_state is None:
            return

        self._states[entity_id] = new_state

        for cb in self._callbacks:
            try:
                cb(entity_id, new_state, old_state)
            except Exception:  # noqa: BLE001
                logger.exception("Error in state-change callback")

    def _ws_url(self) -> str:
        """Derive the WebSocket URL from the base HTTP URL."""
        url = self._base_url
        if url.startswith("https://"):
            return url.replace("https://", "wss://", 1) + "/api/websocket"
        url = url.replace("http://", "ws://", 1)
        if not url.startswith("ws"):
            url = "ws://" + url
        return url + "/api/websocket"
