"""Tests for the browser chat WebSocket endpoint and admin chat route."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

logger = logging.getLogger(__name__)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_server_deps():
    """Patch heavy server dependencies so we can import cortex.server."""
    mock_provider = MagicMock()
    mock_provider.health = AsyncMock(return_value=True)

    with (
        patch("cortex.server.init_db"),
        patch("cortex.server.get_db", return_value=MagicMock()),
        patch("cortex.server._get_provider", return_value=mock_provider),
        patch("cortex.server._get_db", return_value=MagicMock()),
    ):
        yield


@pytest.fixture()
def client(_patch_server_deps):
    """TestClient wrapping the FastAPI app with server deps patched."""
    from cortex.server import app

    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────


async def _fake_pipeline(**kwargs):
    """Simulate run_pipeline yielding token chunks."""
    for word in ["Hello", " ", "World", "!"]:
        yield word


# ── WebSocket Tests ───────────────────────────────────────────────


class TestChatWebSocket:
    def test_connect_and_send(self, client):
        """Basic send → start/token/end flow."""
        with patch("cortex.server.run_pipeline", side_effect=_fake_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": "hi", "user_id": "tester"})

                msg = ws.receive_json()
                assert msg["type"] == "start"

                tokens = []
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "token":
                        tokens.append(msg["text"])
                    elif msg["type"] == "end":
                        break

                assert tokens == ["Hello", " ", "World", "!"]
                assert msg["full_text"] == "Hello World!"

    def test_empty_message_returns_error(self, client):
        """Sending an empty message yields an error frame, not a crash."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"message": "", "user_id": "tester"})
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_missing_message_key_returns_error(self, client):
        """Missing 'message' key treated as empty."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"user_id": "tester"})
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_default_user_id(self, client):
        """user_id defaults to 'web_user' when omitted."""
        captured = {}

        async def _capture_pipeline(**kwargs):
            captured.update(kwargs)
            yield "ok"

        with patch("cortex.server.run_pipeline", side_effect=_capture_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": "hey"})
                ws.receive_json()  # start
                ws.receive_json()  # token
                ws.receive_json()  # end

        assert captured.get("user_id") == "web_user"

    def test_history_passed_through(self, client):
        """Conversation history from the client is forwarded to the pipeline."""
        captured = {}

        async def _capture_pipeline(**kwargs):
            captured.update(kwargs)
            yield "ok"

        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        with patch("cortex.server.run_pipeline", side_effect=_capture_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": "again", "history": history})
                ws.receive_json()  # start
                ws.receive_json()  # token
                ws.receive_json()  # end

        assert captured.get("conversation_history") == history

    def test_multiple_messages(self, client):
        """Multiple sequential messages on the same connection work."""
        with patch("cortex.server.run_pipeline", side_effect=_fake_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                for _ in range(3):
                    ws.send_json({"message": "hi"})

                    msg = ws.receive_json()
                    assert msg["type"] == "start"

                    while True:
                        msg = ws.receive_json()
                        if msg["type"] == "end":
                            break

    def test_token_order_preserved(self, client):
        """Tokens arrive in the exact order the pipeline yields them."""
        expected = ["one", " ", "two", " ", "three"]

        async def _ordered_pipeline(**kwargs):
            for t in expected:
                yield t

        with patch("cortex.server.run_pipeline", side_effect=_ordered_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": "count"})
                ws.receive_json()  # start

                received = []
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "token":
                        received.append(msg["text"])
                    elif msg["type"] == "end":
                        break

                assert received == expected


# ── Route / SPA Tests ─────────────────────────────────────────────


class TestChatRoute:
    def test_chat_route_in_router(self):
        """Chat route is registered in the admin Vue router config."""
        import pathlib

        router_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "router"
            / "index.js"
        )
        content = router_path.read_text()
        assert "'/chat'" in content or '"/chat"' in content
        assert "'chat'" in content or '"chat"' in content
        assert "ChatView" in content

    def test_navbar_has_chat(self):
        """NavBar includes a Chat item."""
        import pathlib

        navbar_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "components"
            / "NavBar.vue"
        )
        content = navbar_path.read_text()
        assert "'Chat'" in content or '"Chat"' in content
        assert "'chat'" in content or '"chat"' in content
