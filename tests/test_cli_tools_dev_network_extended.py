"""Tests for extended dev tools and network engineering tools."""

from __future__ import annotations

import json
import os
import socket
import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.tools import ToolResult, get_default_registry
from cortex.cli.tools.dev import (
    BenchmarkTool,
    CodeAnalyzeTool,
    DiffPreviewTool,
    EnvManageTool,
    ProcessRunTool,
    RefactorTool,
    _analyze_python_file,
    _TRACKED_PROCESSES,
)
from cortex.cli.tools.network_ops import (
    ContainerLogsTool,
    FirewallReadTool,
    HTTPDebugTool,
    NetworkScanTool,
    SSLCheckTool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    text: str = "",
    headers: dict[str, str] | None = None,
    history: list | None = None,
    http_version: str = "1.1",
) -> MagicMock:
    """Create a mock httpx.Response."""
    import httpx

    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    resp.headers = headers or {"content-type": "text/html"}
    resp.history = history or []
    resp.http_version = http_version
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


def _async_client_mock(response: MagicMock) -> MagicMock:
    """Create a mock httpx.AsyncClient context manager."""
    client = AsyncMock()
    client.request = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ===========================================================================
# Part 1: Extended Dev Tools
# ===========================================================================


# ---------------------------------------------------------------------------
# RefactorTool
# ---------------------------------------------------------------------------


class TestRefactorTool:
    @pytest.fixture()
    def tool(self):
        return RefactorTool()

    async def test_preview_matches(self, tool: RefactorTool, tmp_path: Path):
        (tmp_path / "a.py").write_text("old_name = 1\nold_name = 2\n")
        result = await tool.execute({
            "pattern": r"old_name",
            "replacement": "new_name",
            "path": str(tmp_path),
            "glob_filter": "*.py",
            "preview": True,
        })
        assert result.success
        assert "2 match" in result.output
        assert result.metadata["preview"] is True
        # File should NOT be modified
        assert "old_name" in (tmp_path / "a.py").read_text()

    async def test_apply_changes(self, tool: RefactorTool, tmp_path: Path):
        (tmp_path / "b.py").write_text("foo_bar = 1\n")
        result = await tool.execute({
            "pattern": r"foo_bar",
            "replacement": "baz_qux",
            "path": str(tmp_path),
            "glob_filter": "*.py",
            "preview": False,
        })
        assert result.success
        assert result.metadata["preview"] is False
        assert "baz_qux" in (tmp_path / "b.py").read_text()

    async def test_regex_backrefs(self, tool: RefactorTool, tmp_path: Path):
        (tmp_path / "c.py").write_text("def calc_sum():\n    pass\n")
        result = await tool.execute({
            "pattern": r"def (calc_\w+)",
            "replacement": r"def compute_\1",
            "path": str(tmp_path),
            "glob_filter": "*.py",
            "preview": False,
        })
        assert result.success
        assert "compute_calc_sum" in (tmp_path / "c.py").read_text()

    async def test_no_matches(self, tool: RefactorTool, tmp_path: Path):
        (tmp_path / "d.py").write_text("nothing here\n")
        result = await tool.execute({
            "pattern": r"nonexistent_xyz",
            "replacement": "new",
            "path": str(tmp_path),
            "glob_filter": "*.py",
        })
        assert result.success
        assert "No matches" in result.output

    async def test_invalid_regex(self, tool: RefactorTool):
        result = await tool.execute({
            "pattern": r"[invalid(",
            "replacement": "new",
        })
        assert not result.success
        assert "Invalid regex" in result.error

    async def test_missing_pattern(self, tool: RefactorTool):
        result = await tool.execute({"pattern": "", "replacement": "x"})
        assert not result.success

    async def test_path_not_found(self, tool: RefactorTool):
        result = await tool.execute({
            "pattern": "x",
            "replacement": "y",
            "path": "/nonexistent_path_xyz",
        })
        assert not result.success

    async def test_single_file(self, tool: RefactorTool, tmp_path: Path):
        f = tmp_path / "single.py"
        f.write_text("alpha = 1\nalpha = 2\n")
        result = await tool.execute({
            "pattern": r"alpha",
            "replacement": "beta",
            "path": str(f),
            "preview": True,
        })
        assert result.success
        assert "2 match" in result.output

    def test_requires_confirmation(self, tool: RefactorTool):
        assert tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# DiffPreviewTool
# ---------------------------------------------------------------------------


class TestDiffPreviewTool:
    @pytest.fixture()
    def tool(self):
        return DiffPreviewTool()

    async def test_unstaged_diff(self, tool: DiffPreviewTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"diff --git a/f.py\n+new line\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({}, context={"cwd": "/tmp"})

        assert result.success
        assert "new line" in result.output
        call_args = mock_exec.call_args[0]
        assert "--staged" not in call_args

    async def test_staged_diff(self, tool: DiffPreviewTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"diff --git staged\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({"staged": True}, context={"cwd": "/tmp"})

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "--staged" in call_args

    async def test_no_changes(self, tool: DiffPreviewTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({}, context={"cwd": "/tmp"})

        assert result.success
        assert "No" in result.output
        assert result.metadata["has_changes"] is False

    async def test_specific_file(self, tool: DiffPreviewTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"diff output", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute(
                {"file": "src/main.py"}, context={"cwd": "/tmp"}
            )

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "src/main.py" in call_args

    async def test_git_error(self, tool: DiffPreviewTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"not a git repo")
        )
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({}, context={"cwd": "/tmp"})

        assert not result.success


# ---------------------------------------------------------------------------
# ProcessRunTool
# ---------------------------------------------------------------------------


class TestProcessRunTool:
    @pytest.fixture()
    def tool(self):
        return ProcessRunTool()

    @pytest.fixture(autouse=True)
    def _clear_tracked(self):
        _TRACKED_PROCESSES.clear()
        yield
        _TRACKED_PROCESSES.clear()

    async def test_start_process(self, tool: ProcessRunTool):
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await tool.execute({
                "action": "start",
                "command": "python -m http.server",
                "name": "http-server",
            })

        assert result.success
        assert "12345" in result.output
        assert 12345 in _TRACKED_PROCESSES

    async def test_start_missing_command(self, tool: ProcessRunTool):
        result = await tool.execute({"action": "start"})
        assert not result.success

    async def test_stop_process(self, tool: ProcessRunTool):
        _TRACKED_PROCESSES[999] = {
            "name": "test",
            "command": "sleep 100",
            "process": MagicMock(),
            "started": time.time(),
        }

        with patch("os.kill") as mock_kill:
            result = await tool.execute({"action": "stop", "pid": 999})

        assert result.success
        mock_kill.assert_called_once_with(999, 15)  # SIGTERM
        assert 999 not in _TRACKED_PROCESSES

    async def test_stop_already_exited(self, tool: ProcessRunTool):
        _TRACKED_PROCESSES[888] = {
            "name": "gone",
            "command": "exit",
            "process": MagicMock(),
            "started": time.time(),
        }

        with patch("os.kill", side_effect=ProcessLookupError):
            result = await tool.execute({"action": "stop", "pid": 888})

        assert result.success
        assert "already exited" in result.output

    async def test_stop_missing_pid(self, tool: ProcessRunTool):
        result = await tool.execute({"action": "stop"})
        assert not result.success

    async def test_list_empty(self, tool: ProcessRunTool):
        result = await tool.execute({"action": "list"})
        assert result.success
        assert "No tracked" in result.output

    async def test_list_with_processes(self, tool: ProcessRunTool):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        _TRACKED_PROCESSES[111] = {
            "name": "dev-server",
            "command": "npm run dev",
            "process": mock_proc,
            "started": time.time(),
        }

        result = await tool.execute({"action": "list"})
        assert result.success
        assert "dev-server" in result.output
        assert "running" in result.output

    async def test_logs_no_output(self, tool: ProcessRunTool):
        mock_proc = MagicMock()
        mock_proc.stdout = None
        _TRACKED_PROCESSES[222] = {
            "name": "quiet",
            "command": "true",
            "process": mock_proc,
            "started": time.time(),
        }

        result = await tool.execute({"action": "logs", "pid": 222})
        assert result.success
        assert "no output" in result.output

    async def test_logs_missing_pid(self, tool: ProcessRunTool):
        result = await tool.execute({"action": "logs", "pid": 9999})
        assert not result.success

    async def test_unknown_action(self, tool: ProcessRunTool):
        result = await tool.execute({"action": "restart"})
        assert not result.success

    def test_requires_confirmation(self, tool: ProcessRunTool):
        assert tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# EnvManageTool
# ---------------------------------------------------------------------------


class TestEnvManageTool:
    @pytest.fixture()
    def tool(self):
        return EnvManageTool()

    async def test_get_existing(self, tool: EnvManageTool):
        with patch.dict(os.environ, {"MY_VAR": "hello"}):
            result = await tool.execute({"action": "get", "key": "MY_VAR"})
        assert result.success
        assert "hello" in result.output

    async def test_get_missing(self, tool: EnvManageTool):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NONEXISTENT_VAR_XYZ", None)
            result = await tool.execute({"action": "get", "key": "NONEXISTENT_VAR_XYZ"})
        assert result.success
        assert "not set" in result.output

    async def test_get_no_key(self, tool: EnvManageTool):
        result = await tool.execute({"action": "get"})
        assert not result.success

    async def test_set(self, tool: EnvManageTool):
        result = await tool.execute({
            "action": "set", "key": "TEST_SET_VAR", "value": "42"
        })
        assert result.success
        assert os.environ.get("TEST_SET_VAR") == "42"
        os.environ.pop("TEST_SET_VAR", None)

    async def test_set_no_key(self, tool: EnvManageTool):
        result = await tool.execute({"action": "set", "value": "x"})
        assert not result.success

    async def test_list(self, tool: EnvManageTool):
        result = await tool.execute({"action": "list"})
        assert result.success
        assert "PATH" in result.output

    async def test_read_file(self, tool: EnvManageTool, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=val1\nKEY2=val2\n")
        result = await tool.execute({
            "action": "read_file", "file": str(env_file)
        })
        assert result.success
        assert "KEY1=val1" in result.output

    async def test_read_file_missing(self, tool: EnvManageTool):
        result = await tool.execute({
            "action": "read_file", "file": "/nonexistent/.env"
        })
        assert not result.success

    async def test_write_file_new(self, tool: EnvManageTool, tmp_path: Path):
        env_file = tmp_path / ".env"
        result = await tool.execute({
            "action": "write_file",
            "key": "NEW_KEY",
            "value": "new_val",
            "file": str(env_file),
        })
        assert result.success
        content = env_file.read_text()
        assert "NEW_KEY=new_val" in content

    async def test_write_file_update(self, tool: EnvManageTool, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=old\nOTHER=keep\n")
        result = await tool.execute({
            "action": "write_file",
            "key": "EXISTING",
            "value": "new",
            "file": str(env_file),
        })
        assert result.success
        content = env_file.read_text()
        assert "EXISTING=new" in content
        assert "OTHER=keep" in content
        assert "EXISTING=old" not in content

    async def test_write_file_no_key(self, tool: EnvManageTool):
        result = await tool.execute({"action": "write_file", "value": "x"})
        assert not result.success

    async def test_unknown_action(self, tool: EnvManageTool):
        result = await tool.execute({"action": "delete"})
        assert not result.success


# ---------------------------------------------------------------------------
# CodeAnalyzeTool
# ---------------------------------------------------------------------------


class TestCodeAnalyzeTool:
    @pytest.fixture()
    def tool(self):
        return CodeAnalyzeTool()

    async def test_analyze_file(self, tool: CodeAnalyzeTool, tmp_path: Path):
        code = textwrap.dedent("""\
            import os
            from pathlib import Path

            class MyClass:
                def method_a(self):
                    pass
                def method_b(self):
                    pass

            def standalone():
                if True:
                    pass
        """)
        f = tmp_path / "sample.py"
        f.write_text(code)
        result = await tool.execute({
            "path": str(f), "metrics": ["summary"]
        })
        assert result.success
        assert "Functions: 3" in result.output
        assert "Classes: 1" in result.output

    async def test_analyze_directory(self, tool: CodeAnalyzeTool, tmp_path: Path):
        (tmp_path / "a.py").write_text("def fa():\n    pass\n")
        (tmp_path / "b.py").write_text("def fb():\n    pass\ndef fc():\n    pass\n")
        result = await tool.execute({
            "path": str(tmp_path), "metrics": ["summary"]
        })
        assert result.success
        assert result.metadata["file_count"] == 2
        assert result.metadata["total_functions"] == 3

    async def test_functions_metric(self, tool: CodeAnalyzeTool, tmp_path: Path):
        (tmp_path / "m.py").write_text("def alpha():\n    pass\ndef beta():\n    pass\n")
        result = await tool.execute({
            "path": str(tmp_path), "metrics": ["functions"]
        })
        assert result.success
        assert "alpha" in result.output
        assert "beta" in result.output

    async def test_classes_metric(self, tool: CodeAnalyzeTool, tmp_path: Path):
        code = "class Foo:\n    def bar(self):\n        pass\n"
        (tmp_path / "c.py").write_text(code)
        result = await tool.execute({
            "path": str(tmp_path), "metrics": ["classes"]
        })
        assert result.success
        assert "Foo" in result.output
        assert "bar" in result.output

    async def test_imports_metric(self, tool: CodeAnalyzeTool, tmp_path: Path):
        (tmp_path / "i.py").write_text("import os\nfrom sys import argv\n")
        result = await tool.execute({
            "path": str(tmp_path), "metrics": ["imports"]
        })
        assert result.success
        assert "os" in result.output
        assert "sys.argv" in result.output

    async def test_path_not_found(self, tool: CodeAnalyzeTool):
        result = await tool.execute({"path": "/nonexistent_xyz"})
        assert not result.success

    async def test_no_python_files(self, tool: CodeAnalyzeTool, tmp_path: Path):
        (tmp_path / "readme.md").write_text("# hello\n")
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert "No Python files" in result.output

    def test_analyze_helper_syntax_error(self, tmp_path: Path):
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(\n")
        info = _analyze_python_file(bad)
        assert "error" in info


# ---------------------------------------------------------------------------
# BenchmarkTool
# ---------------------------------------------------------------------------


class TestBenchmarkTool:
    @pytest.fixture()
    def tool(self):
        return BenchmarkTool()

    async def test_successful_benchmark(self, tool: BenchmarkTool):
        call_count = 0

        async def mock_run_cmd(cmd, cwd, timeout=600):
            nonlocal call_count
            call_count += 1
            return (0, "ok\n", "")

        with patch("cortex.cli.tools.dev._run_cmd", side_effect=mock_run_cmd):
            result = await tool.execute({
                "command": "echo hello",
                "iterations": 3,
                "label": "echo-test",
            })

        assert result.success
        assert call_count == 3
        assert "echo-test" in result.output
        assert "Min" in result.output
        assert "Max" in result.output
        assert "Avg" in result.output
        assert "Median" in result.output
        assert result.metadata["iterations"] == 3

    async def test_command_failure(self, tool: BenchmarkTool):
        async def mock_run_cmd(cmd, cwd, timeout=600):
            return (1, "", "error\n")

        with patch("cortex.cli.tools.dev._run_cmd", side_effect=mock_run_cmd):
            result = await tool.execute({"command": "false"})

        assert not result.success
        assert "iteration 1" in result.error

    async def test_missing_command(self, tool: BenchmarkTool):
        result = await tool.execute({"command": ""})
        assert not result.success

    async def test_single_iteration(self, tool: BenchmarkTool):
        async def mock_run_cmd(cmd, cwd, timeout=600):
            return (0, "done\n", "")

        with patch("cortex.cli.tools.dev._run_cmd", side_effect=mock_run_cmd):
            result = await tool.execute({
                "command": "echo x",
                "iterations": 1,
            })

        assert result.success
        assert result.metadata["iterations"] == 1


# ===========================================================================
# Part 2: Network Engineering Tools
# ===========================================================================


# ---------------------------------------------------------------------------
# NetworkScanTool
# ---------------------------------------------------------------------------


class TestNetworkScanTool:
    @pytest.fixture()
    def tool(self):
        return NetworkScanTool()

    async def test_ping(self, tool: NetworkScanTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"64 bytes from 1.1.1.1\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"action": "ping", "host": "1.1.1.1"})

        assert result.success
        assert "64 bytes" in result.output

    async def test_ping_timeout(self, tool: NetworkScanTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"action": "ping", "host": "10.0.0.1"})

        assert not result.success

    async def test_port_scan_single(self, tool: NetworkScanTool):
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_cls.return_value = mock_sock

            result = await tool.execute({
                "action": "port_scan", "host": "localhost", "port": 80,
            })

        assert result.success
        assert 80 in result.metadata["open_ports"]

    async def test_port_scan_range(self, tool: NetworkScanTool):
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            # Port 80 open, 81 closed
            mock_sock.connect_ex.side_effect = [0, 1]
            mock_sock_cls.return_value = mock_sock

            result = await tool.execute({
                "action": "port_scan",
                "host": "localhost",
                "port_range": "80-81",
            })

        assert result.success
        assert 80 in result.metadata["open_ports"]

    async def test_port_scan_range_too_large(self, tool: NetworkScanTool):
        result = await tool.execute({
            "action": "port_scan",
            "host": "localhost",
            "port_range": "1-2000",
        })
        assert not result.success
        assert "1024" in result.error

    async def test_port_scan_missing_port(self, tool: NetworkScanTool):
        result = await tool.execute({
            "action": "port_scan", "host": "localhost",
        })
        assert not result.success

    async def test_port_scan_invalid_range(self, tool: NetworkScanTool):
        result = await tool.execute({
            "action": "port_scan",
            "host": "localhost",
            "port_range": "abc",
        })
        assert not result.success

    async def test_dns_lookup(self, tool: NetworkScanTool):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
                (socket.AF_INET6, 0, 0, "", ("2606:2800:220:1::248", 0, 0, 0)),
            ]
            result = await tool.execute({
                "action": "dns", "host": "example.com",
            })

        assert result.success
        assert "93.184.216.34" in result.output
        assert "IPv4" in result.output

    async def test_dns_failure(self, tool: NetworkScanTool):
        with patch(
            "socket.getaddrinfo", side_effect=socket.gaierror("not found")
        ):
            result = await tool.execute({
                "action": "dns", "host": "invalid.zzz",
            })
        assert not result.success

    async def test_traceroute(self, tool: NetworkScanTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"1  gateway  1ms\n2  next  5ms\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "action": "traceroute", "host": "example.com",
            })

        assert result.success
        assert "gateway" in result.output

    async def test_missing_host(self, tool: NetworkScanTool):
        result = await tool.execute({"action": "ping", "host": ""})
        assert not result.success

    async def test_unknown_action(self, tool: NetworkScanTool):
        result = await tool.execute({"action": "nmap", "host": "x"})
        assert not result.success


# ---------------------------------------------------------------------------
# HTTPDebugTool
# ---------------------------------------------------------------------------


class TestHTTPDebugTool:
    @pytest.fixture()
    def tool(self):
        return HTTPDebugTool()

    async def test_basic_get(self, tool: HTTPDebugTool):
        resp = _mock_response(
            status_code=200,
            text='{"ok":true}',
            headers={"content-type": "application/json", "server": "nginx"},
        )
        cm = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=cm):
            result = await tool.execute({"url": "https://api.example.com"})

        assert result.success
        assert "200" in result.output
        assert "Total time" in result.output

    async def test_missing_url(self, tool: HTTPDebugTool):
        result = await tool.execute({"url": ""})
        assert not result.success

    async def test_timeout(self, tool: HTTPDebugTool):
        import httpx

        client = AsyncMock()
        client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=cm):
            result = await tool.execute({"url": "https://slow.example.com"})

        assert not result.success
        assert "timed out" in result.error

    async def test_redirect_tracking(self, tool: HTTPDebugTool):
        redirect_resp = MagicMock()
        redirect_resp.status_code = 301
        redirect_resp.headers = {"location": "https://example.com/new"}

        resp = _mock_response(status_code=200, text="final")
        resp.history = [redirect_resp]
        cm = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=cm):
            result = await tool.execute({"url": "https://example.com/old"})

        assert result.success
        assert "301" in result.output
        assert result.metadata["redirects"] == 1


# ---------------------------------------------------------------------------
# ContainerLogsTool
# ---------------------------------------------------------------------------


class TestContainerLogsTool:
    @pytest.fixture()
    def tool(self):
        return ContainerLogsTool()

    async def test_docker_logs(self, tool: ContainerLogsTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"2024-01-01 log line 1\n2024-01-01 log line 2\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({
                "source": "docker", "name": "my-app", "lines": 100,
            })

        assert result.success
        assert "log line 1" in result.output
        call_args = mock_exec.call_args[0]
        assert "docker" in call_args
        assert "logs" in call_args
        assert "100" in call_args

    async def test_systemd_logs(self, tool: ContainerLogsTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"systemd output\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({
                "source": "systemd", "name": "nginx",
            })

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "journalctl" in call_args
        assert "nginx" in call_args

    async def test_grep_filter(self, tool: ContainerLogsTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"ERROR something\nINFO ok\nERROR another\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "source": "docker",
                "name": "app",
                "filter": "ERROR",
            })

        assert result.success
        assert "ERROR something" in result.output
        assert "INFO ok" not in result.output

    async def test_missing_name(self, tool: ContainerLogsTool):
        result = await tool.execute({"source": "docker", "name": ""})
        assert not result.success

    async def test_unknown_source(self, tool: ContainerLogsTool):
        result = await tool.execute({"source": "podman", "name": "x"})
        assert not result.success


# ---------------------------------------------------------------------------
# SSLCheckTool
# ---------------------------------------------------------------------------


class TestSSLCheckTool:
    @pytest.fixture()
    def tool(self):
        return SSLCheckTool()

    async def test_valid_cert(self, tool: SSLCheckTool):
        cert_info = {
            "subject": ((("commonName", "example.com"),),),
            "issuer": (
                (("organizationName", "Let's Encrypt"),),
                (("commonName", "R3"),),
            ),
            "notBefore": "Jan  1 00:00:00 2024 GMT",
            "notAfter": "Dec 31 23:59:59 2025 GMT",
            "subjectAltName": (("DNS", "example.com"), ("DNS", "*.example.com")),
        }

        with patch.object(
            SSLCheckTool,
            "_get_cert_info",
            return_value=(cert_info, "TLSv1.3", "TLS_AES_256_GCM_SHA384 (TLSv1.3)"),
        ):
            result = await tool.execute({"host": "example.com"})

        assert result.success
        assert "example.com" in result.output
        assert "Let's Encrypt" in result.output
        assert "TLSv1.3" in result.output
        assert result.metadata["days_left"] is not None

    async def test_missing_host(self, tool: SSLCheckTool):
        result = await tool.execute({"host": ""})
        assert not result.success

    async def test_connection_error(self, tool: SSLCheckTool):
        with patch.object(
            SSLCheckTool,
            "_get_cert_info",
            side_effect=ConnectionRefusedError("refused"),
        ):
            result = await tool.execute({"host": "nossl.example.com"})

        assert not result.success

    async def test_custom_port(self, tool: SSLCheckTool):
        cert_info = {
            "subject": ((("commonName", "mail.example.com"),),),
            "issuer": ((("organizationName", "DigiCert"),),),
            "notBefore": "Jan  1 00:00:00 2024 GMT",
            "notAfter": "Jan  1 00:00:00 2026 GMT",
            "subjectAltName": (),
        }

        with patch.object(
            SSLCheckTool,
            "_get_cert_info",
            return_value=(cert_info, "TLSv1.2", "ECDHE-RSA-AES256 (TLSv1.2)"),
        ):
            result = await tool.execute({"host": "mail.example.com", "port": 993})

        assert result.success
        assert result.metadata["port"] == 993


# ---------------------------------------------------------------------------
# FirewallReadTool
# ---------------------------------------------------------------------------


class TestFirewallReadTool:
    @pytest.fixture()
    def tool(self):
        return FirewallReadTool()

    async def test_iptables(self, tool: FirewallReadTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Chain INPUT (policy ACCEPT)\nACCEPT tcp 0.0.0.0/0\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({"backend": "iptables"})

        assert result.success
        assert "Chain INPUT" in result.output
        call_args = mock_exec.call_args[0]
        assert "iptables" in call_args

    async def test_nftables(self, tool: FirewallReadTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"table inet filter {\n  chain input {\n  }\n}\n", b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"backend": "nftables"})

        assert result.success
        assert "table inet" in result.output

    async def test_auto_detect_nftables(self, tool: FirewallReadTool):
        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:
                # nft --version succeeds
                proc.communicate = AsyncMock(return_value=(b"nftables v1.0", b""))
                proc.returncode = 0
            elif "nft" in args and "list" in args:
                proc.communicate = AsyncMock(return_value=(b"table inet {}\n", b""))
                proc.returncode = 0
            else:
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            return proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            result = await tool.execute({"backend": "auto"})

        assert result.success
        assert result.metadata["backend"] == "nftables"

    async def test_auto_detect_fallback_iptables(self, tool: FirewallReadTool):
        call_count = 0

        async def mock_create_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count <= 2:
                # nft --version fails, iptables --version succeeds
                if call_count == 1:
                    proc.communicate = AsyncMock(
                        side_effect=OSError("not found")
                    )
                    proc.returncode = 1
                else:
                    proc.communicate = AsyncMock(
                        return_value=(b"iptables v1.8", b"")
                    )
                    proc.returncode = 0
            else:
                # iptables -L command
                proc.communicate = AsyncMock(
                    return_value=(b"Chain INPUT\n", b"")
                )
                proc.returncode = 0
            return proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            result = await tool.execute({"backend": "auto"})

        assert result.success

    async def test_command_failure(self, tool: FirewallReadTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"permission denied")
        )
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"backend": "iptables"})

        assert not result.success

    async def test_unknown_backend(self, tool: FirewallReadTool):
        result = await tool.execute({"backend": "ufw"})
        assert not result.success


# ===========================================================================
# Registry Integration
# ===========================================================================


class TestExtendedRegistryIntegration:
    def test_new_dev_tools_registered(self):
        registry = get_default_registry()
        tools = registry.list_tools()
        tool_ids = {t.tool_id for t in tools}
        new_dev = {
            "refactor", "diff_preview", "process_run",
            "env_manage", "code_analyze", "benchmark",
        }
        assert new_dev.issubset(tool_ids), f"Missing dev tools: {new_dev - tool_ids}"

    def test_new_network_tools_registered(self):
        registry = get_default_registry()
        tools = registry.list_tools()
        tool_ids = {t.tool_id for t in tools}
        new_net = {
            "network_scan", "http_debug", "container_logs",
            "ssl_check", "firewall_read",
        }
        assert new_net.issubset(tool_ids), f"Missing net tools: {new_net - tool_ids}"

    def test_total_tool_count(self):
        registry = get_default_registry()
        tools = registry.list_tools()
        # 18 original core + 6 new dev + 5 new network = 29 minimum
        assert len(tools) >= 29

    def test_schemas_valid(self):
        registry = get_default_registry()
        schemas = registry.get_function_schemas()
        for schema in schemas:
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "description" in schema["function"]
