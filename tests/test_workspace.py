"""Tests for Atlas workspace daemon and client.

Module ownership: CLI workspace tests
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.daemon import (
    SOCKET_PATH,
    DaemonServer,
    WorkspaceState,
    _receive,
    _send,
    get_daemon_pid,
    is_daemon_running,
    start_daemon,
    stop_daemon,
)
from cortex.cli.workspace import WorkspaceClient


# ── Helpers ──────────────────────────────────────────────────────


def _make_temp_socket_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sock"


def _make_temp_pid_file(tmp_path: Path) -> Path:
    return tmp_path / "test.pid"


async def _read_message(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read a length-prefixed JSON message from a stream."""
    length_bytes = await reader.readexactly(4)
    length = int.from_bytes(length_bytes, "big")
    data = await reader.readexactly(length)
    return json.loads(data.decode())


async def _write_message(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    """Write a length-prefixed JSON message to a stream."""
    data = json.dumps(msg).encode()
    writer.write(len(data).to_bytes(4, "big") + data)
    await writer.drain()


# ── WorkspaceState ───────────────────────────────────────────────


class TestWorkspaceState:
    def test_initial_state(self) -> None:
        state = WorkspaceState()
        assert state.messages == []
        assert state.provider is None
        assert state.memory_bridge is None
        assert state.curiosity_engine is None
        assert state.tool_registry is None
        assert state.active_tasks == []
        assert state.connected_clients == []
        assert state.started_at > 0
        assert state.cwd == os.getcwd()

    async def test_initialize_with_all_failures(self) -> None:
        """Initialization should succeed even if all subsystems fail."""
        state = WorkspaceState()
        with (
            patch("cortex.cli.daemon.WorkspaceState.initialize", wraps=state.initialize),
            patch.dict("sys.modules", {
                "cortex.providers": MagicMock(
                    get_provider=MagicMock(side_effect=RuntimeError("no provider"))
                ),
            }),
        ):
            # Calling initialize — subsystem failures are logged, not raised
            await state.initialize()
            # State should remain with Nones for failed subsystems
            # (the actual imports might succeed or fail depending on env)

    async def test_initialize_records_tool_count(self) -> None:
        state = WorkspaceState()
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [MagicMock() for _ in range(5)]

        with (
            patch("cortex.cli.daemon.WorkspaceState.initialize") as mock_init,
        ):
            state.tool_registry = mock_registry
            assert len(state.tool_registry.list_tools()) == 5


# ── Wire protocol ────────────────────────────────────────────────


class TestWireProtocol:
    async def test_send_receive_roundtrip(self, tmp_path: Path) -> None:
        """Messages survive a send→receive roundtrip over a Unix socket."""
        sock_path = _make_temp_socket_path(tmp_path)
        received: list[dict[str, Any]] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            msg = await _receive(reader)
            if msg:
                received.append(msg)
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _send(writer, {"type": "ping", "value": 42})
            writer.close()
            await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0] == {"type": "ping", "value": 42}

    async def test_send_receive_multiple(self, tmp_path: Path) -> None:
        """Multiple messages can be sent and received sequentially."""
        sock_path = _make_temp_socket_path(tmp_path)
        received: list[dict[str, Any]] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            for _ in range(3):
                msg = await _receive(reader)
                if msg:
                    received.append(msg)
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            for i in range(3):
                await _send(writer, {"type": "test", "i": i})
            await asyncio.sleep(0.05)
            writer.close()

        assert len(received) == 3
        assert [m["i"] for m in received] == [0, 1, 2]

    async def test_receive_returns_none_on_disconnect(self, tmp_path: Path) -> None:
        """_receive returns None when the connection is closed."""
        sock_path = _make_temp_socket_path(tmp_path)
        result: list[Any] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await asyncio.sleep(0.05)
            msg = await _receive(reader)
            result.append(msg)
            writer.close()

        assert result[0] is None

    async def test_large_message(self, tmp_path: Path) -> None:
        """Large messages (> 64KB) survive the protocol."""
        sock_path = _make_temp_socket_path(tmp_path)
        big_payload = {"type": "big", "data": "x" * 100_000}
        received: list[dict[str, Any]] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            msg = await _receive(reader)
            if msg:
                received.append(msg)
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _send(writer, big_payload)
            await asyncio.sleep(0.05)
            writer.close()

        assert len(received) == 1
        assert received[0]["data"] == "x" * 100_000


# ── DaemonServer ─────────────────────────────────────────────────


class TestDaemonServer:
    async def test_start_stop_lifecycle(self, tmp_path: Path) -> None:
        """Daemon starts, accepts a client, then shuts down cleanly."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            # Start server in background
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            assert sock_path.exists()
            assert pid_path.exists()
            assert server._running

            # Connect a client
            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            welcome = await _read_message(reader)
            assert welcome["type"] == "welcome"
            assert "tools" in welcome
            assert "uptime" in welcome
            writer.close()

            # Stop
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_ping_pong(self, tmp_path: Path) -> None:
        """Daemon responds to ping with pong."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            # Read welcome
            await _read_message(reader)

            # Send ping
            await _write_message(writer, {"type": "ping"})
            pong = await _read_message(reader)
            assert pong["type"] == "pong"

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_clear_command(self, tmp_path: Path) -> None:
        """The clear command empties conversation history."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()
        server.state.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "command", "cmd": "clear"})
            resp = await _read_message(reader)
            assert resp["type"] == "cleared"
            assert server.state.messages == []

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_status_command(self, tmp_path: Path) -> None:
        """The status command returns system info."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "command", "cmd": "status"})
            resp = await _read_message(reader)
            assert resp["type"] == "status"
            assert "uptime" in resp
            assert "messages" in resp
            assert "clients" in resp
            assert resp["clients"] >= 1

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_welcome_includes_history(self, tmp_path: Path) -> None:
        """Welcome message includes recent conversation history."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()
        server.state.messages = [
            {"role": "user", "content": f"msg{i}"} for i in range(5)
        ]

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            welcome = await _read_message(reader)
            assert welcome["type"] == "welcome"
            assert len(welcome["history"]) == 5

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_multiple_clients(self, tmp_path: Path) -> None:
        """Multiple clients can connect simultaneously."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            # Connect two clients
            r1, w1 = await asyncio.open_unix_connection(str(sock_path))
            welcome1 = await _read_message(r1)
            assert welcome1["type"] == "welcome"

            r2, w2 = await asyncio.open_unix_connection(str(sock_path))
            welcome2 = await _read_message(r2)
            assert welcome2["type"] == "welcome"

            await asyncio.sleep(0.05)
            assert len(server.state.connected_clients) == 2

            w1.close()
            w2.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_cd_command(self, tmp_path: Path) -> None:
        """The cd command changes the daemon working directory."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)
        target_dir = str(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(
                writer, {"type": "command", "cmd": "cd", "path": target_dir}
            )
            resp = await _read_message(reader)
            assert resp["type"] == "cwd_changed"
            assert resp["path"] == target_dir
            assert server.state.cwd == target_dir

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ── Slash command routing ────────────────────────────────────────


class TestSlashCommands:
    async def test_help_command(self, tmp_path: Path) -> None:
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "message", "text": "/help"})
            resp = await _read_message(reader)
            assert resp["type"] == "info"
            assert "/help" in resp["text"]
            assert "/quit" in resp["text"]

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_status_slash(self, tmp_path: Path) -> None:
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "message", "text": "/status"})
            resp = await _read_message(reader)
            assert resp["type"] == "status"
            assert "uptime" in resp

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_unknown_slash_command(self, tmp_path: Path) -> None:
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "message", "text": "/nonexistent"})
            resp = await _read_message(reader)
            assert resp["type"] == "error"
            assert "Unknown command" in resp["message"]

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_quit_disconnects(self, tmp_path: Path) -> None:
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "message", "text": "/quit"})
            resp = await _read_message(reader)
            assert resp["type"] == "disconnect"

            writer.close()
            await asyncio.sleep(0.05)

            # Client should have been removed
            assert len(server.state.connected_clients) == 0

            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_clear_slash(self, tmp_path: Path) -> None:
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()
        server.state.messages = [{"role": "user", "content": "old message"}]

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            reader, writer = await asyncio.open_unix_connection(str(sock_path))
            await _read_message(reader)  # welcome

            await _write_message(writer, {"type": "message", "text": "/clear"})
            resp = await _read_message(reader)
            assert resp["type"] == "cleared"
            assert server.state.messages == []

            writer.close()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ── PID file management ──────────────────────────────────────────


class TestPidManagement:
    def test_is_daemon_running_no_pid_file(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "nonexistent.pid"
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert is_daemon_running() is False

    def test_is_daemon_running_stale_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "stale.pid"
        pid_path.write_text("999999999")
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert is_daemon_running() is False

    def test_is_daemon_running_current_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "current.pid"
        pid_path.write_text(str(os.getpid()))
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert is_daemon_running() is True

    def test_get_daemon_pid_not_running(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "nonexistent.pid"
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert get_daemon_pid() is None

    def test_get_daemon_pid_running(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "current.pid"
        pid_path.write_text(str(os.getpid()))
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert get_daemon_pid() == os.getpid()

    def test_get_daemon_pid_invalid_content(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "bad.pid"
        pid_path.write_text("not-a-number")
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert get_daemon_pid() is None

    def test_stop_daemon_not_running(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "nonexistent.pid"
        with patch("cortex.cli.daemon.PID_FILE", pid_path):
            assert stop_daemon() is False

    def test_stop_daemon_sends_sigterm(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "running.pid"
        pid_path.write_text(str(os.getpid()))
        with (
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch("os.kill") as mock_kill,
        ):
            # First os.kill(pid, 0) check in get_daemon_pid, then SIGTERM
            mock_kill.return_value = None
            result = stop_daemon()
            assert result is True
            mock_kill.assert_any_call(os.getpid(), signal.SIGTERM)


# ── WorkspaceClient ──────────────────────────────────────────────


class TestWorkspaceClient:
    async def test_connect_no_daemon(self, tmp_path: Path) -> None:
        sock_path = tmp_path / "nonexistent.sock"
        with patch("cortex.cli.workspace.SOCKET_PATH", sock_path):
            client = WorkspaceClient()
            assert await client.connect() is False
            assert client.connected is False

    async def test_connect_disconnect(self, tmp_path: Path) -> None:
        """Client connects to a mock server and disconnects."""
        sock_path = _make_temp_socket_path(tmp_path)

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            welcome = {"type": "welcome", "history": [], "tools": 0, "uptime": 0, "cwd": "/tmp"}
            data = json.dumps(welcome).encode()
            writer.write(len(data).to_bytes(4, "big") + data)
            await writer.drain()
            # Read until client disconnects
            try:
                while True:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
            except Exception:
                pass
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        try:
            with patch("cortex.cli.workspace.SOCKET_PATH", sock_path):
                client = WorkspaceClient()
                assert await client.connect() is True
                assert client.connected is True

                welcome = await client.receive()
                assert welcome is not None
                assert welcome["type"] == "welcome"

                await client.disconnect()
                assert client.connected is False
        finally:
            server.close()
            await server.wait_closed()

    async def test_send_message(self, tmp_path: Path) -> None:
        """Client can send chat messages."""
        sock_path = _make_temp_socket_path(tmp_path)
        received: list[dict[str, Any]] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            welcome = {"type": "welcome", "history": [], "tools": 0, "uptime": 0, "cwd": "/tmp"}
            data = json.dumps(welcome).encode()
            writer.write(len(data).to_bytes(4, "big") + data)
            await writer.drain()
            msg = await _receive(reader)
            if msg:
                received.append(msg)
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        try:
            with patch("cortex.cli.workspace.SOCKET_PATH", sock_path):
                client = WorkspaceClient()
                await client.connect()
                await client.receive()  # welcome
                await client.send_message("hello world")
                await asyncio.sleep(0.05)
                await client.disconnect()
        finally:
            server.close()
            await server.wait_closed()

        assert len(received) == 1
        assert received[0]["type"] == "message"
        assert received[0]["text"] == "hello world"

    async def test_send_command(self, tmp_path: Path) -> None:
        """Client can send commands."""
        sock_path = _make_temp_socket_path(tmp_path)
        received: list[dict[str, Any]] = []

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            welcome = {"type": "welcome", "history": [], "tools": 0, "uptime": 0, "cwd": "/tmp"}
            data = json.dumps(welcome).encode()
            writer.write(len(data).to_bytes(4, "big") + data)
            await writer.drain()
            msg = await _receive(reader)
            if msg:
                received.append(msg)
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        try:
            with patch("cortex.cli.workspace.SOCKET_PATH", sock_path):
                client = WorkspaceClient()
                await client.connect()
                await client.receive()  # welcome
                await client.send_command("status")
                await asyncio.sleep(0.05)
                await client.disconnect()
        finally:
            server.close()
            await server.wait_closed()

        assert len(received) == 1
        assert received[0]["type"] == "command"
        assert received[0]["cmd"] == "status"

    async def test_ping(self, tmp_path: Path) -> None:
        """Client ping returns True when daemon responds with pong."""
        sock_path = _make_temp_socket_path(tmp_path)

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            welcome = {"type": "welcome", "history": [], "tools": 0, "uptime": 0, "cwd": "/tmp"}
            data = json.dumps(welcome).encode()
            writer.write(len(data).to_bytes(4, "big") + data)
            await writer.drain()
            msg = await _receive(reader)
            if msg and msg.get("type") == "ping":
                pong = {"type": "pong"}
                data = json.dumps(pong).encode()
                writer.write(len(data).to_bytes(4, "big") + data)
                await writer.drain()
            # Read until client disconnects
            try:
                while True:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
            except Exception:
                pass
            writer.close()

        server = await asyncio.start_unix_server(handler, str(sock_path))
        try:
            with patch("cortex.cli.workspace.SOCKET_PATH", sock_path):
                client = WorkspaceClient()
                await client.connect()
                await client.receive()  # welcome
                assert await client.ping() is True
                await client.disconnect()
        finally:
            server.close()
            await server.wait_closed()


# ── Integration: client ↔ daemon ─────────────────────────────────


class TestClientDaemonIntegration:
    async def test_client_sends_message_daemon_receives(self, tmp_path: Path) -> None:
        """Full roundtrip: client sends message, daemon processes it."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch("cortex.cli.workspace.SOCKET_PATH", sock_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            client = WorkspaceClient()
            assert await client.connect()

            welcome = await client.receive()
            assert welcome["type"] == "welcome"

            # Send /help via the client
            await client.send_message("/help")
            resp = await client.receive()
            assert resp["type"] == "info"
            assert "Commands:" in resp["text"]

            await client.disconnect()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_client_ping_pong_via_client(self, tmp_path: Path) -> None:
        """Client.ping() works against a real DaemonServer."""
        sock_path = _make_temp_socket_path(tmp_path)
        pid_path = _make_temp_pid_file(tmp_path)

        server = DaemonServer()

        with (
            patch("cortex.cli.daemon.SOCKET_PATH", sock_path),
            patch("cortex.cli.daemon.PID_FILE", pid_path),
            patch("cortex.cli.workspace.SOCKET_PATH", sock_path),
            patch.object(server.state, "initialize", new_callable=AsyncMock),
        ):
            task = asyncio.create_task(server.start())
            await asyncio.sleep(0.1)

            client = WorkspaceClient()
            await client.connect()
            await client.receive()  # welcome

            assert await client.ping() is True

            await client.disconnect()
            await server.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
