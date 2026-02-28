"""Tests for HA WebSocket listener (I2.3)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.integrations.ha.websocket import (
    HAWebSocketError,
    HAWebSocketListener,
)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_ws_msg(data: dict) -> MagicMock:
    """Create a mock aiohttp WSMessage with TEXT type."""
    import aiohttp
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.TEXT
    msg.data = json.dumps(data)
    return msg


def _make_close_msg() -> MagicMock:
    import aiohttp
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.CLOSED
    return msg


class FakeWS:
    """Minimal fake WebSocket that replays a scripted conversation."""

    def __init__(self, recv_messages: list[dict], stream_messages: list | None = None):
        self._recv = [json.dumps(m) for m in recv_messages]
        self._recv_idx = 0
        self._stream = stream_messages or []
        self.sent: list[dict] = []
        self.closed = False

    async def receive_json(self) -> dict:
        msg = json.loads(self._recv[self._recv_idx])
        self._recv_idx += 1
        return msg

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._stream:
            raise StopAsyncIteration
        return self._stream.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #

class TestWebSocketURL:
    """Test WS URL derivation."""

    def test_http(self):
        ws = HAWebSocketListener(base_url="http://ha.local:8123", token="tok")
        assert ws._ws_url() == "ws://ha.local:8123/api/websocket"

    def test_https(self):
        ws = HAWebSocketListener(base_url="https://ha.example.com", token="tok")
        assert ws._ws_url() == "wss://ha.example.com/api/websocket"

    def test_trailing_slash(self):
        ws = HAWebSocketListener(base_url="http://ha.local:8123/", token="tok")
        assert ws._ws_url() == "ws://ha.local:8123/api/websocket"


class TestAuthentication:
    """Test the auth handshake."""

    @pytest.mark.asyncio
    async def test_successful_auth(self):
        ws = FakeWS([
            {"type": "auth_required"},
            {"type": "auth_ok"},
        ])
        listener = HAWebSocketListener(base_url="http://ha:8123", token="my-token")
        await listener._authenticate(ws)
        assert ws.sent == [{"type": "auth", "access_token": "my-token"}]

    @pytest.mark.asyncio
    async def test_auth_invalid_raises(self):
        ws = FakeWS([
            {"type": "auth_required"},
            {"type": "auth_invalid"},
        ])
        listener = HAWebSocketListener(base_url="http://ha:8123", token="bad")
        with pytest.raises(HAWebSocketError, match="authentication failed"):
            await listener._authenticate(ws)

    @pytest.mark.asyncio
    async def test_unexpected_first_message_raises(self):
        ws = FakeWS([
            {"type": "something_else"},
        ])
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        with pytest.raises(HAWebSocketError, match="Expected auth_required"):
            await listener._authenticate(ws)


class TestStateTracking:
    """Test in-memory state cache via _handle_message."""

    def test_state_update(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        listener._handle_message({
            "type": "event",
            "event": {
                "data": {
                    "entity_id": "light.kitchen",
                    "new_state": {"state": "on", "attributes": {"brightness": 255}},
                    "old_state": {"state": "off"},
                }
            },
        })
        state = listener.get_state("light.kitchen")
        assert state is not None
        assert state["state"] == "on"

    def test_unknown_entity_returns_none(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        assert listener.get_state("sensor.nonexistent") is None

    def test_callback_invoked(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        received = []
        listener.on_state_change(lambda eid, ns, os_: received.append((eid, ns["state"])))
        listener._handle_message({
            "type": "event",
            "event": {
                "data": {
                    "entity_id": "switch.fan",
                    "new_state": {"state": "off"},
                    "old_state": {"state": "on"},
                }
            },
        })
        assert received == [("switch.fan", "off")]

    def test_callback_error_does_not_propagate(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        listener.on_state_change(lambda *_: 1 / 0)
        # Should not raise
        listener._handle_message({
            "type": "event",
            "event": {
                "data": {
                    "entity_id": "light.x",
                    "new_state": {"state": "on"},
                    "old_state": None,
                }
            },
        })

    def test_non_event_ignored(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        listener._handle_message({"type": "result", "success": True})
        assert listener.get_state("anything") is None


class TestStartStop:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        await listener.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_connected_property_default(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        assert listener.connected is False

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")

        # Simulate the full connection lifecycle via _connect_and_listen
        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            # Simulate auth + one event, then set connected and inject state
            await listener._authenticate(FakeWS([
                {"type": "auth_required"},
                {"type": "auth_ok"},
            ]))
            listener._connected = True
            listener._states["light.test"] = {"state": "on"}
            # Simulate disconnect
            listener._running = False

        listener._connect_and_listen = fake_connect

        await listener.start()
        await asyncio.sleep(0.1)
        await listener.stop()

        assert listener.get_state("light.test") is not None


class TestReconnectBackoff:
    """Test that the reconnect loop uses exponential backoff."""

    @pytest.mark.asyncio
    async def test_backoff_escalation(self):
        listener = HAWebSocketListener(base_url="http://ha:8123", token="tok")
        listener._running = True

        call_count = 0
        sleep_values = []

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                listener._running = False
            raise OSError("refused")

        listener._connect_and_listen = fake_connect

        original_sleep = asyncio.sleep

        async def capture_sleep(t):
            sleep_values.append(t)
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=capture_sleep):
            await listener._run_loop()

        assert len(sleep_values) >= 2
        assert sleep_values[0] == 1
        assert sleep_values[1] == 2
