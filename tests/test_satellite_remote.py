"""Tests for satellite remote management — commands, configs, scripts via WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    yield


@pytest.fixture()
def _seed_satellite():
    """Insert a test satellite into the DB."""
    db = get_db()
    db.execute(
        "INSERT INTO satellites (id, display_name, mode, status) VALUES (?, ?, ?, ?)",
        ("sat-kitchen", "Kitchen", "dedicated", "online"),
    )
    db.commit()


# ── Schema Tests ──────────────────────────────────────────────────


class TestSatelliteCommandsSchema:
    def test_table_exists(self):
        db = get_db()
        cur = db.execute("PRAGMA table_info(satellite_commands)")
        cols = [r[1] for r in cur.fetchall()]
        assert "id" in cols
        assert "satellite_id" in cols
        assert "command_type" in cols
        assert "payload" in cols
        assert "status" in cols
        assert "result" in cols
        assert "created_at" in cols
        assert "completed_at" in cols

    def test_insert_command(self, _seed_satellite):
        db = get_db()
        db.execute(
            "INSERT INTO satellite_commands (satellite_id, command_type, payload) "
            "VALUES (?, ?, ?)",
            ("sat-kitchen", "REBOOT", "{}"),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM satellite_commands WHERE satellite_id = 'sat-kitchen'"
        ).fetchone()
        assert row is not None

    def test_default_status_is_pending(self, _seed_satellite):
        db = get_db()
        db.execute(
            "INSERT INTO satellite_commands (satellite_id, command_type) "
            "VALUES (?, ?)",
            ("sat-kitchen", "REBOOT"),
        )
        db.commit()
        row = db.execute("SELECT status FROM satellite_commands").fetchone()
        assert row[0] == "pending"


# ── send_remote_command Tests ─────────────────────────────────────


class TestSendRemoteCommand:
    async def test_stores_in_db(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command("sat-kitchen", "REBOOT")
        assert result["command_type"] == "REBOOT"
        assert result["status"] == "pending"  # no connection
        assert result["id"] is not None

        db = get_db()
        row = db.execute("SELECT * FROM satellite_commands WHERE id = ?", (result["id"],)).fetchone()
        assert row is not None

    async def test_sends_to_connected_satellite(self, _seed_satellite):
        from cortex.satellite.websocket import (
            SatelliteConnection,
            _connected_satellites,
            send_remote_command,
        )

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")
        _connected_satellites["sat-kitchen"] = conn

        try:
            result = await send_remote_command("sat-kitchen", "REBOOT")
            assert result["status"] == "sent"
            mock_ws.send_json.assert_called_once()
            sent_msg = mock_ws.send_json.call_args[0][0]
            assert sent_msg["type"] == "REBOOT"
            assert sent_msg["cmd_id"] == result["id"]
        finally:
            _connected_satellites.pop("sat-kitchen", None)

    async def test_unknown_command_type_raises(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        with pytest.raises(ValueError, match="Unknown command type"):
            await send_remote_command("sat-kitchen", "INVALID_TYPE")

    async def test_config_update_stores_payload(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        payload = {"volume": 0.9, "wake_word": "hey buddy"}
        result = await send_remote_command("sat-kitchen", "CONFIG_UPDATE", payload)
        assert result["payload"] == payload

        db = get_db()
        row = db.execute("SELECT payload FROM satellite_commands WHERE id = ?", (result["id"],)).fetchone()
        stored = json.loads(row[0])
        assert stored["volume"] == 0.9

    async def test_exec_script_timeout_clamped(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command(
            "sat-kitchen", "EXEC_SCRIPT",
            {"script": "echo hi", "timeout": 9999},
        )
        assert result["payload"]["timeout"] == 300

    async def test_exec_script_min_timeout(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command(
            "sat-kitchen", "EXEC_SCRIPT",
            {"script": "echo hi", "timeout": -5},
        )
        assert result["payload"]["timeout"] == 1


# ── CMD_ACK Handler Tests ─────────────────────────────────────────


class TestCmdAck:
    async def test_ack_updates_db(self, _seed_satellite):
        from cortex.satellite.websocket import (
            SatelliteConnection,
            _handle_cmd_ack,
            send_remote_command,
        )

        result = await send_remote_command("sat-kitchen", "REBOOT")
        cmd_id = result["id"]

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")

        await _handle_cmd_ack(conn, {"cmd_id": cmd_id, "result": "rebooting"})

        db = get_db()
        row = db.execute("SELECT status, result, completed_at FROM satellite_commands WHERE id = ?", (cmd_id,)).fetchone()
        assert row[0] == "ack"
        assert row[1] == "rebooting"
        assert row[2] is not None

    async def test_ack_missing_cmd_id_is_noop(self):
        from cortex.satellite.websocket import SatelliteConnection, _handle_cmd_ack

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")
        # Should not raise
        await _handle_cmd_ack(conn, {"result": "ok"})


# ── Log Upload Handler Tests ──────────────────────────────────────


class TestLogUpload:
    async def test_log_upload_stores_in_db(self, _seed_satellite):
        from cortex.satellite.websocket import (
            SatelliteConnection,
            _handle_log_upload,
            send_remote_command,
        )

        result = await send_remote_command("sat-kitchen", "LOG_REQUEST", {"lines": 50})
        cmd_id = result["id"]

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")
        logs = "Jan 01 12:00:00 atlas-satellite started\nJan 01 12:00:01 ready"

        await _handle_log_upload(conn, {"cmd_id": cmd_id, "logs": logs})

        db = get_db()
        row = db.execute("SELECT status, result FROM satellite_commands WHERE id = ?", (cmd_id,)).fetchone()
        assert row[0] == "ack"
        assert "atlas-satellite started" in row[1]


# ── Command History Tests ─────────────────────────────────────────


class TestCommandHistory:
    async def test_get_command_history(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        await send_remote_command("sat-kitchen", "REBOOT")
        await send_remote_command("sat-kitchen", "CONFIG_UPDATE", {"volume": 0.5})
        await send_remote_command("sat-kitchen", "EXEC_SCRIPT", {"script": "uptime"})

        history = get_command_history("sat-kitchen")
        assert len(history) == 3
        # Most recent first
        assert history[0]["command_type"] == "EXEC_SCRIPT"
        assert history[1]["command_type"] == "CONFIG_UPDATE"
        assert history[2]["command_type"] == "REBOOT"

    async def test_history_with_limit(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        for _ in range(5):
            await send_remote_command("sat-kitchen", "REBOOT")

        history = get_command_history("sat-kitchen", limit=2)
        assert len(history) == 2

    async def test_history_with_offset(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        for i in range(5):
            await send_remote_command("sat-kitchen", "REBOOT")

        history = get_command_history("sat-kitchen", limit=2, offset=3)
        assert len(history) == 2

    def test_empty_history(self):
        from cortex.satellite.websocket import get_command_history

        history = get_command_history("sat-nonexistent")
        assert history == []


# ── Satellite Agent Handler Tests ─────────────────────────────────


class TestAgentConfigUpdate:
    async def test_config_update_merges_file(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        # Pre-create config
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"volume": 0.5, "room": "kitchen"}))

        # Mock websocket
        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_config_update({
            "cmd_id": 1,
            "payload": {"volume": 0.9, "wake_word": "hey buddy"},
        })

        # File should be merged
        data = json.loads(config_path.read_text())
        assert data["volume"] == 0.9
        assert data["room"] == "kitchen"
        assert data["wake_word"] == "hey buddy"

        # Live config should be updated
        assert config.volume == 0.9
        assert config.wake_word == "hey buddy"

        agent.ws.send_cmd_ack.assert_called_once_with(1, "ok")

    async def test_config_update_creates_file(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_config_update({
            "cmd_id": 2,
            "payload": {"volume": 0.3},
        })

        config_path = tmp_path / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["volume"] == 0.3


class TestAgentExecScript:
    async def test_exec_script_success(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_exec_script({
            "cmd_id": 10,
            "payload": {"script": "echo hello_world", "timeout": 5},
        })

        agent.ws.send_cmd_ack.assert_called_once()
        call_args = agent.ws.send_cmd_ack.call_args[0]
        assert call_args[0] == 10
        result = json.loads(call_args[1])
        assert result["exit_code"] == 0
        assert "hello_world" in result["stdout"]

    async def test_exec_script_empty_script(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_exec_script({
            "cmd_id": 11,
            "payload": {"script": ""},
        })

        agent.ws.send_cmd_ack.assert_called_once_with(11, "error: empty script")

    async def test_exec_script_timeout(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_exec_script({
            "cmd_id": 12,
            "payload": {"script": "sleep 60", "timeout": 1},
        })

        agent.ws.send_cmd_ack.assert_called_once_with(12, "error: timeout")

    async def test_exec_script_captures_stderr(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_exec_script({
            "cmd_id": 13,
            "payload": {"script": "echo err >&2; exit 1", "timeout": 5},
        })

        call_args = agent.ws.send_cmd_ack.call_args[0]
        result = json.loads(call_args[1])
        assert result["exit_code"] == 1
        assert "err" in result["stderr"]


class TestAgentRestartService:
    async def test_restart_service_sends_ack(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        with patch("satellite.atlas_satellite.agent.subprocess.Popen") as mock_popen:
            await agent._on_remote_restart_service({
                "cmd_id": 20,
                "payload": {"service": "my-service"},
            })
            mock_popen.assert_called_once_with(["sudo", "systemctl", "restart", "my-service"])
            agent.ws.send_cmd_ack.assert_called_once_with(20, "restarting")

    async def test_restart_default_service(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        with patch("satellite.atlas_satellite.agent.subprocess.Popen") as mock_popen:
            await agent._on_remote_restart_service({
                "cmd_id": 21,
                "payload": {},
            })
            mock_popen.assert_called_once_with(["sudo", "systemctl", "restart", "atlas-satellite"])


class TestAgentReboot:
    async def test_reboot_acks_then_reboots(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        with patch("satellite.atlas_satellite.agent.subprocess.Popen") as mock_popen:
            with patch("satellite.atlas_satellite.agent.asyncio.sleep", new_callable=AsyncMock):
                await agent._on_remote_reboot({"cmd_id": 30})
                agent.ws.send_cmd_ack.assert_called_once_with(30, "rebooting")
                mock_popen.assert_called_once_with(["sudo", "reboot"])


class TestAgentKioskUrl:
    async def test_kiosk_url_updates_config(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"room": "kitchen"}))

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        with patch("satellite.atlas_satellite.agent.subprocess.Popen"):
            with patch("satellite.atlas_satellite.agent.asyncio.sleep", new_callable=AsyncMock):
                await agent._on_remote_kiosk_url({
                    "cmd_id": 40,
                    "payload": {"url": "https://example.com/dashboard"},
                })

        data = json.loads(config_path.read_text())
        assert data["kiosk_url"] == "https://example.com/dashboard"
        assert data["room"] == "kitchen"
        agent.ws.send_cmd_ack.assert_called_once_with(40, "ok")

    async def test_kiosk_url_missing_url(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        await agent._on_remote_kiosk_url({"cmd_id": 41, "payload": {}})
        agent.ws.send_cmd_ack.assert_called_once_with(41, "error: missing url")


class TestAgentLogRequest:
    async def test_log_request_collects_logs(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()
        agent.ws.send_log_upload = AsyncMock()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"log line 1\nlog line 2\n", b"")
        mock_proc.returncode = 0

        with patch("satellite.atlas_satellite.agent.asyncio.create_subprocess_exec", return_value=mock_proc):
            await agent._on_remote_log_request({
                "cmd_id": 50,
                "payload": {"lines": 100},
            })

        agent.ws.send_log_upload.assert_called_once()
        call_args = agent.ws.send_log_upload.call_args[0]
        assert call_args[0] == 50
        assert "log line 1" in call_args[1]


class TestAgentUpdateAgent:
    async def test_update_agent_runs_git_and_pip(self, tmp_path):
        from satellite.atlas_satellite.agent import SatelliteAgent
        from satellite.atlas_satellite.config import SatelliteConfig

        config = SatelliteConfig(satellite_id="sat-test")
        agent = SatelliteAgent(config)
        agent._base_dir = tmp_path

        # Create requirements.txt so pip step runs
        (tmp_path / "requirements.txt").write_text("websockets\n")

        agent.ws = MagicMock()
        agent.ws.send_cmd_ack = AsyncMock()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok\n", b"")
        mock_proc.returncode = 0

        with patch("satellite.atlas_satellite.agent.asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("satellite.atlas_satellite.agent.subprocess.Popen") as mock_popen:
                with patch("satellite.atlas_satellite.agent.asyncio.sleep", new_callable=AsyncMock):
                    await agent._on_remote_update_agent({"cmd_id": 60})

        agent.ws.send_cmd_ack.assert_called_once()
        call_args = agent.ws.send_cmd_ack.call_args[0]
        result = json.loads(call_args[1])
        assert "git pull" in result["steps"][0]
        assert "pip install" in result["steps"][1]
        assert result["restarting"] is True


# ── WS Client send_cmd_ack Tests ─────────────────────────────────


class TestWSClientAck:
    async def test_send_cmd_ack(self):
        from satellite.atlas_satellite.ws_client import SatelliteWSClient

        client = SatelliteWSClient("ws://localhost/ws", "sat-test")
        client._ws = AsyncMock()
        client._connected = True

        await client.send_cmd_ack(42, "ok")

        client._ws.send.assert_called_once()
        sent = json.loads(client._ws.send.call_args[0][0])
        assert sent["type"] == "CMD_ACK"
        assert sent["cmd_id"] == 42
        assert sent["result"] == "ok"

    async def test_send_log_upload(self):
        from satellite.atlas_satellite.ws_client import SatelliteWSClient

        client = SatelliteWSClient("ws://localhost/ws", "sat-test")
        client._ws = AsyncMock()
        client._connected = True

        await client.send_log_upload(99, "some log content")

        client._ws.send.assert_called_once()
        sent = json.loads(client._ws.send.call_args[0][0])
        assert sent["type"] == "LOG_UPLOAD"
        assert sent["cmd_id"] == 99
        assert sent["logs"] == "some log content"


# ── Admin API Tests ───────────────────────────────────────────────


class TestAdminRemoteAPI:
    """Test admin endpoints for remote management.

    Uses direct function calls since we don't start a full server.
    """

    async def test_remote_command_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        result = await send_remote_command("sat-kitchen", "REBOOT")
        assert result["status"] == "pending"
        assert result["command_type"] == "REBOOT"

        history = get_command_history("sat-kitchen")
        assert len(history) == 1
        assert history[0]["command_type"] == "REBOOT"

    async def test_config_push_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        result = await send_remote_command("sat-kitchen", "CONFIG_UPDATE", {"volume": 0.3})
        assert result["payload"]["volume"] == 0.3

        history = get_command_history("sat-kitchen")
        assert len(history) == 1
        payload = history[0]["payload"]
        assert payload["volume"] == 0.3

    async def test_update_agent_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command("sat-kitchen", "UPDATE_AGENT")
        assert result["command_type"] == "UPDATE_AGENT"
        assert result["status"] == "pending"

    async def test_kiosk_url_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command("sat-kitchen", "KIOSK_URL", {"url": "https://ha.local"})
        assert result["payload"]["url"] == "https://ha.local"

    async def test_exec_script_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command(
            "sat-kitchen", "EXEC_SCRIPT",
            {"script": "df -h", "timeout": 10},
        )
        assert result["payload"]["script"] == "df -h"
        assert result["payload"]["timeout"] == 10

    async def test_log_request_via_admin(self, _seed_satellite):
        from cortex.satellite.websocket import send_remote_command

        result = await send_remote_command("sat-kitchen", "LOG_REQUEST", {"lines": 200})
        assert result["payload"]["lines"] == 200


# ── Full Flow Tests ───────────────────────────────────────────────


class TestFullCommandFlow:
    async def test_send_then_ack_flow(self, _seed_satellite):
        """Simulate: admin sends command → satellite receives → satellite acks → DB updated."""
        from cortex.satellite.websocket import (
            SatelliteConnection,
            _connected_satellites,
            _handle_cmd_ack,
            send_remote_command,
        )

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")
        _connected_satellites["sat-kitchen"] = conn

        try:
            # Admin sends command
            result = await send_remote_command("sat-kitchen", "EXEC_SCRIPT", {"script": "uptime"})
            assert result["status"] == "sent"
            cmd_id = result["id"]

            # Verify it was sent to satellite
            mock_ws.send_json.assert_called_once()
            sent_msg = mock_ws.send_json.call_args[0][0]
            assert sent_msg["type"] == "EXEC_SCRIPT"
            assert sent_msg["cmd_id"] == cmd_id

            # Satellite sends ack back
            await _handle_cmd_ack(conn, {
                "cmd_id": cmd_id,
                "result": json.dumps({"exit_code": 0, "stdout": "up 5 days"}),
            })

            # DB should reflect completion
            db = get_db()
            row = db.execute(
                "SELECT status, result, completed_at FROM satellite_commands WHERE id = ?",
                (cmd_id,),
            ).fetchone()
            assert row[0] == "ack"
            assert "up 5 days" in row[1]
            assert row[2] is not None
        finally:
            _connected_satellites.pop("sat-kitchen", None)

    async def test_send_then_log_upload_flow(self, _seed_satellite):
        """Simulate: admin requests logs → satellite collects → satellite uploads."""
        from cortex.satellite.websocket import (
            SatelliteConnection,
            _connected_satellites,
            _handle_log_upload,
            send_remote_command,
        )

        mock_ws = AsyncMock()
        conn = SatelliteConnection(mock_ws, "sat-kitchen")
        _connected_satellites["sat-kitchen"] = conn

        try:
            result = await send_remote_command("sat-kitchen", "LOG_REQUEST", {"lines": 50})
            cmd_id = result["id"]

            await _handle_log_upload(conn, {
                "cmd_id": cmd_id,
                "logs": "systemd log line 1\nsystemd log line 2",
            })

            db = get_db()
            row = db.execute(
                "SELECT status, result FROM satellite_commands WHERE id = ?",
                (cmd_id,),
            ).fetchone()
            assert row[0] == "ack"
            assert "systemd log line 1" in row[1]
        finally:
            _connected_satellites.pop("sat-kitchen", None)

    async def test_multiple_commands_tracked(self, _seed_satellite):
        from cortex.satellite.websocket import get_command_history, send_remote_command

        await send_remote_command("sat-kitchen", "REBOOT")
        await send_remote_command("sat-kitchen", "CONFIG_UPDATE", {"volume": 0.8})
        await send_remote_command("sat-kitchen", "LOG_REQUEST", {"lines": 100})
        await send_remote_command("sat-kitchen", "KIOSK_URL", {"url": "http://dash"})
        await send_remote_command("sat-kitchen", "UPDATE_AGENT")

        history = get_command_history("sat-kitchen")
        assert len(history) == 5
        types = {h["command_type"] for h in history}
        assert types == {"REBOOT", "CONFIG_UPDATE", "LOG_REQUEST", "KIOSK_URL", "UPDATE_AGENT"}


# ── REMOTE_CMD_TYPES validation ───────────────────────────────────


class TestRemoteCmdTypes:
    def test_all_types_defined(self):
        from cortex.satellite.websocket import REMOTE_CMD_TYPES

        expected = {
            "CONFIG_UPDATE", "EXEC_SCRIPT", "RESTART_SERVICE",
            "UPDATE_AGENT", "KIOSK_URL", "REBOOT", "LOG_REQUEST",
        }
        assert REMOTE_CMD_TYPES == expected
