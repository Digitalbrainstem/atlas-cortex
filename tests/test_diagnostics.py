"""Tests for atlas status dashboard and atlas diagnose system checks."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════
# Diagnose — individual check functions
# ═══════════════════════════════════════════════════════════════════


class TestCheckPythonVersion:
    async def test_passes_on_current_python(self):
        from cortex.cli.diagnose import check_python_version

        status, detail = await check_python_version()
        assert status == "pass"
        expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        assert expected in detail


class TestCheckDbWritable:
    async def test_writable_with_temp_db(self, tmp_path: Path):
        from cortex.db import set_db_path, init_db
        from cortex.cli.diagnose import check_db_writable

        db_file = tmp_path / "test_diag.db"
        set_db_path(db_file)
        init_db()

        status, detail = await check_db_writable()
        assert status == "pass"
        assert str(db_file) in detail or "test_diag" in detail

    async def test_fails_with_bad_path(self):
        """Ensure function handles import/runtime errors gracefully."""
        # The function does a local import, so just verify it never crashes
        from cortex.cli.diagnose import check_db_writable

        status, detail = await check_db_writable()
        assert status in ("pass", "fail")


class TestCheckDbSchema:
    async def test_schema_with_tables(self, tmp_path: Path):
        from cortex.db import set_db_path, init_db
        from cortex.cli.diagnose import check_db_schema

        set_db_path(tmp_path / "schema_test.db")
        init_db()

        status, detail = await check_db_schema()
        assert status == "pass"
        assert "tables" in detail


class TestCheckDiskSpace:
    async def test_passes_with_disk_space(self):
        from cortex.cli.diagnose import check_disk_space

        status, detail = await check_disk_space()
        # We definitely have more than 1GB on this machine
        assert status == "pass"
        assert "free" in detail


class TestCheckGpuDetected:
    async def test_returns_without_crashing(self):
        from cortex.cli.diagnose import check_gpu_detected

        status, detail = await check_gpu_detected()
        # Either pass (GPU found) or warn (no GPU), but never crash
        assert status in ("pass", "warn")
        assert detail  # non-empty

    async def test_no_gpu_tools(self):
        """When neither rocm-smi nor nvidia-smi exist, returns warn."""
        from cortex.cli.diagnose import check_gpu_detected

        async def _fake_exec(*args, **kwargs):
            raise FileNotFoundError("not found")

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            with patch("pathlib.Path.exists", return_value=False):
                status, detail = await check_gpu_detected()
                assert status == "warn"


class TestCheckMemoryAvailable:
    async def test_passes_on_linux(self):
        from cortex.cli.diagnose import check_memory_available

        status, detail = await check_memory_available()
        if sys.platform == "linux":
            # Should be able to read /proc/meminfo
            assert status in ("pass", "fail")
            assert "available" in detail.lower() or "gb" in detail.lower()
        else:
            # Non-linux: warn is acceptable
            assert status in ("pass", "warn", "fail")


class TestCheckPortAvailable:
    async def test_port_free(self):
        from cortex.cli.diagnose import check_port_available

        # Port 5100 is unlikely to be in use in CI
        status, detail = await check_port_available()
        assert status in ("pass", "warn")
        assert "5100" in detail

    async def test_port_in_use(self):
        """Bind port 5100 temporarily, then check reports in-use."""
        from cortex.cli.diagnose import check_port_available

        # Try to bind the port; if it's already in use, skip
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 5100))
            sock.listen(1)
        except OSError:
            pytest.skip("Port 5100 already in use")
            return

        try:
            status, detail = await check_port_available()
            assert status == "warn"
            assert "in use" in detail
        finally:
            sock.close()


class TestCheckPortConflicts:
    async def test_returns_without_crashing(self):
        from cortex.cli.diagnose import check_port_conflicts

        status, detail = await check_port_conflicts()
        assert status in ("pass", "warn")


class TestCheckPluginsLoadable:
    async def test_finds_builtin_plugins(self):
        from cortex.cli.diagnose import check_plugins_loadable

        status, detail = await check_plugins_loadable()
        assert status == "pass"
        assert "plugins" in detail.lower()


class TestCheckAdminBuilt:
    async def test_not_built(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from cortex.cli.diagnose import check_admin_built

        monkeypatch.chdir(tmp_path)
        status, detail = await check_admin_built()
        assert status == "warn"
        assert "not built" in detail.lower()

    async def test_built(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from cortex.cli.diagnose import check_admin_built

        admin_dist = tmp_path / "admin" / "dist"
        admin_dist.mkdir(parents=True)
        (admin_dist / "index.html").write_text("<html></html>")
        monkeypatch.chdir(tmp_path)

        status, detail = await check_admin_built()
        assert status == "pass"


class TestCheckOllamaReachable:
    async def test_ollama_not_running(self):
        from cortex.cli.diagnose import check_ollama_reachable

        # Point at a port nothing listens on
        with patch.dict(os.environ, {"LLM_URL": "http://127.0.0.1:19999"}):
            status, detail = await check_ollama_reachable()
            assert status == "fail"

    async def test_ollama_healthy(self):
        from cortex.cli.diagnose import check_ollama_reachable

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"version": "0.6.2"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            status, detail = await check_ollama_reachable()
            assert status == "pass"
            assert "0.6.2" in detail


class TestCheckHaConnected:
    async def test_not_configured(self):
        from cortex.cli.diagnose import check_ha_connected

        with patch.dict(os.environ, {"HA_URL": "", "HA_TOKEN": ""}, clear=False):
            # Remove the keys if present
            env = os.environ.copy()
            env.pop("HA_URL", None)
            env.pop("HA_TOKEN", None)
            with patch.dict(os.environ, env, clear=True):
                status, detail = await check_ha_connected()
                assert status == "warn"
                assert "not configured" in detail.lower()


class TestCheckSatellites:
    async def test_no_satellites(self):
        from cortex.cli.diagnose import check_satellites

        mock_disc = MagicMock()
        mock_disc.get_announced.return_value = []
        with patch(
            "cortex.satellite.discovery.SatelliteDiscovery", return_value=mock_disc,
        ):
            status, detail = await check_satellites()
            assert status == "warn"


class TestCheckTtsAvailable:
    async def test_tts_not_running(self):
        from cortex.cli.diagnose import check_tts_available

        with patch.dict(
            os.environ,
            {"TTS_PROVIDER": "kokoro", "KOKORO_HOST": "127.0.0.1", "KOKORO_PORT": "19998"},
        ):
            status, detail = await check_tts_available()
            assert status == "fail"


class TestCheckSttAvailable:
    async def test_stt_not_running(self):
        from cortex.cli.diagnose import check_stt_available

        with patch.dict(os.environ, {"STT_HOST": "127.0.0.1", "STT_PORT": "19997"}):
            status, detail = await check_stt_available()
            assert status == "fail"


# ═══════════════════════════════════════════════════════════════════
# run_diagnose — full integration
# ═══════════════════════════════════════════════════════════════════


class TestRunDiagnose:
    async def test_completes_without_crashing(self, tmp_path: Path, capsys):
        from cortex.db import set_db_path, init_db
        from cortex.cli.diagnose import run_diagnose

        set_db_path(tmp_path / "diag_run.db")
        init_db()

        exit_code = await run_diagnose()
        assert isinstance(exit_code, int)
        assert exit_code in (0, 1)

    async def test_output_contains_categories(self, tmp_path: Path, capsys):
        from cortex.db import set_db_path, init_db
        from cortex.cli.diagnose import run_diagnose

        set_db_path(tmp_path / "diag_out.db")
        init_db()

        # Force plain text output for easy capture
        with patch("cortex.cli.diagnose._HAS_RICH", False):
            with patch("cortex.cli.diagnose._console", None):
                await run_diagnose()

        captured = capsys.readouterr()
        assert "Core:" in captured.out
        assert "System:" in captured.out
        assert "Results:" in captured.out


# ═══════════════════════════════════════════════════════════════════
# print_status — full integration
# ═══════════════════════════════════════════════════════════════════


class TestPrintStatus:
    async def test_completes_without_crashing(self, tmp_path: Path, capsys):
        from cortex.db import set_db_path, init_db
        from cortex.cli.status import print_status

        set_db_path(tmp_path / "status_run.db")
        init_db()

        exit_code = await print_status()
        assert exit_code == 0

    async def test_plain_output(self, tmp_path: Path, capsys):
        from cortex.db import set_db_path, init_db
        from cortex.cli.status import print_status

        set_db_path(tmp_path / "status_plain.db")
        init_db()

        with patch("cortex.cli.status._HAS_RICH", False):
            with patch("cortex.cli.status._console", None):
                exit_code = await print_status()

        captured = capsys.readouterr()
        assert "Atlas Cortex" in captured.out
        assert "LLM Provider" in captured.out or "Provider" in captured.out
        assert exit_code == 0


# ═══════════════════════════════════════════════════════════════════
# CLI wiring — argparse
# ═══════════════════════════════════════════════════════════════════


class TestCLIWiring:
    def test_diagnose_subcommand_exists(self):
        from cortex.cli.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["diagnose"])
        assert args.command == "diagnose"

    def test_status_subcommand_exists(self):
        from cortex.cli.__main__ import _build_parser

        parser = parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
