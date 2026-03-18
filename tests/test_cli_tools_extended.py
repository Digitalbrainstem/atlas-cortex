"""Tests for CLI network and development tools."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cortex.cli.tools import get_default_registry
from cortex.cli.tools.dev import (
    BuildTool,
    DBQueryTool,
    DockerTool,
    LintTool,
    PackageManageTool,
    TestRunTool,
    _detect_build_system,
    _detect_linter,
    _detect_package_manager,
    _detect_test_framework,
)
from cortex.cli.tools.network import (
    APICallTool,
    SSHTool,
    WebFetchTool,
    WebSearchTool,
    _strip_html,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    headers: dict[str, str] | None = None,
    content_type: str = "text/html",
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text or json.dumps(json_data or {})
    resp.headers = {"content-type": content_type, **(headers or {})}
    resp.json.return_value = json_data
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
    client.get = AsyncMock(return_value=response)
    client.request = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_basic(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_script_removal(self):
        html = "<script>alert(1)</script><p>Safe</p>"
        assert "alert" not in _strip_html(html)
        assert "Safe" in _strip_html(html)

    def test_entities(self):
        assert "&amp;" in _strip_html("&amp;") or "&" in _strip_html("&amp;")
        result = _strip_html("&lt;tag&gt;")
        assert "<tag>" in result


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class TestWebSearchTool:
    @pytest.fixture()
    def tool(self):
        return WebSearchTool()

    async def test_no_query(self, tool: WebSearchTool):
        result = await tool.execute({"query": ""})
        assert not result.success

    async def test_no_searxng_url(self, tool: WebSearchTool):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure SEARXNG_URL is not set
            os.environ.pop("SEARXNG_URL", None)
            result = await tool.execute({"query": "test"})
        assert not result.success
        assert "SEARXNG_URL" in result.error

    async def test_successful_search(self, tool: WebSearchTool):
        mock_data = {
            "results": [
                {"title": "Result 1", "url": "https://example.com", "content": "Snippet"},
                {"title": "Result 2", "url": "https://example.org", "content": "Another"},
            ]
        }
        resp = _mock_response(json_data=mock_data)
        client_mock = _async_client_mock(resp)

        with (
            patch.dict(os.environ, {"SEARXNG_URL": "http://localhost:8888"}),
            patch("httpx.AsyncClient", return_value=client_mock),
        ):
            result = await tool.execute({"query": "python async"})

        assert result.success
        assert "Result 1" in result.output
        assert "Result 2" in result.output

    async def test_timeout(self, tool: WebSearchTool):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.dict(os.environ, {"SEARXNG_URL": "http://localhost:8888"}),
            patch("httpx.AsyncClient", return_value=cm),
        ):
            result = await tool.execute({"query": "test"})

        assert not result.success
        assert "timed out" in result.error


# ---------------------------------------------------------------------------
# WebFetchTool
# ---------------------------------------------------------------------------


class TestWebFetchTool:
    @pytest.fixture()
    def tool(self):
        return WebFetchTool()

    async def test_no_url(self, tool: WebFetchTool):
        result = await tool.execute({"url": ""})
        assert not result.success

    async def test_fetch_html(self, tool: WebFetchTool):
        html = "<html><body><p>Hello World</p></body></html>"
        resp = _mock_response(text=html, content_type="text/html")
        client_mock = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=client_mock):
            result = await tool.execute({"url": "https://example.com"})

        assert result.success
        assert "Hello World" in result.output

    async def test_truncation(self, tool: WebFetchTool):
        resp = _mock_response(text="A" * 10000, content_type="text/plain")
        client_mock = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=client_mock):
            result = await tool.execute({"url": "https://example.com", "max_length": 100})

        assert result.success
        assert "truncated" in result.output
        assert len(result.output) < 200

    async def test_http_error(self, tool: WebFetchTool):
        resp = _mock_response(status_code=404)
        client_mock = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=client_mock):
            result = await tool.execute({"url": "https://example.com/404"})

        assert not result.success
        assert "404" in result.error


# ---------------------------------------------------------------------------
# APICallTool
# ---------------------------------------------------------------------------


class TestAPICallTool:
    @pytest.fixture()
    def tool(self):
        return APICallTool()

    async def test_get_request(self, tool: APICallTool):
        resp = _mock_response(
            status_code=200, text='{"ok": true}', content_type="application/json"
        )
        client_mock = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=client_mock):
            result = await tool.execute({"method": "GET", "url": "https://api.example.com"})

        assert result.success
        assert "200" in result.output

    async def test_post_request_with_body(self, tool: APICallTool):
        resp = _mock_response(status_code=201, text='{"id": 1}')
        client_mock = _async_client_mock(resp)

        with patch("httpx.AsyncClient", return_value=client_mock):
            result = await tool.execute({
                "method": "POST",
                "url": "https://api.example.com/items",
                "body": {"name": "test"},
            })

        assert result.success
        assert "201" in result.output

    async def test_requires_confirmation(self, tool: APICallTool):
        assert tool.requires_confirmation is True

    async def test_missing_url(self, tool: APICallTool):
        result = await tool.execute({"method": "GET", "url": ""})
        assert not result.success


# ---------------------------------------------------------------------------
# SSHTool
# ---------------------------------------------------------------------------


class TestSSHTool:
    @pytest.fixture()
    def tool(self):
        return SSHTool()

    async def test_requires_confirmation(self, tool: SSHTool):
        assert tool.requires_confirmation is True

    async def test_missing_params(self, tool: SSHTool):
        result = await tool.execute({"host": "", "command": "ls"})
        assert not result.success

    async def test_command_construction(self, tool: SSHTool):
        """Verify SSH command is constructed correctly without actually connecting."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute({
                "host": "server.example.com",
                "command": "uptime",
                "user": "deploy",
                "port": 2222,
            })

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "ssh" in call_args
        assert "server.example.com" in call_args
        assert "uptime" in call_args
        assert "-p" in call_args
        assert "2222" in call_args
        assert "-l" in call_args
        assert "deploy" in call_args

    async def test_timeout(self, tool: SSHTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "host": "slow.example.com",
                "command": "sleep 999",
                "timeout": 1,
            })

        assert not result.success


# ---------------------------------------------------------------------------
# TestRunTool
# ---------------------------------------------------------------------------


class TestTestRunTool:
    @pytest.fixture()
    def tool(self):
        return TestRunTool()

    def test_framework_detection_pytest(self, tmp_path: Path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert _detect_test_framework(str(tmp_path)) == "pytest"

    def test_framework_detection_jest(self, tmp_path: Path):
        (tmp_path / "jest.config.js").write_text("module.exports = {};\n")
        assert _detect_test_framework(str(tmp_path)) == "jest"

    def test_framework_detection_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module test\n")
        assert _detect_test_framework(str(tmp_path)) == "go"

    def test_framework_detection_cargo(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert _detect_test_framework(str(tmp_path)) == "cargo"

    async def test_pytest_run(self, tool: TestRunTool):
        pytest_output = "5 passed, 2 failed in 1.23s\nFAILED tests/test_foo.py::test_bar"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(pytest_output.encode(), b""))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(
                {"framework": "pytest", "path": "tests/"},
                context={"cwd": "/tmp"},
            )

        assert not result.success
        assert result.metadata.get("framework") == "pytest"
        assert result.metadata.get("passed") == 5
        assert result.metadata.get("failed") == 2


# ---------------------------------------------------------------------------
# BuildTool
# ---------------------------------------------------------------------------


class TestBuildTool:
    @pytest.fixture()
    def tool(self):
        return BuildTool()

    def test_detect_npm(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}\n")
        name, cmd = _detect_build_system(str(tmp_path))
        assert name == "npm"
        assert "npm" in cmd

    def test_detect_make(self, tmp_path: Path):
        (tmp_path / "Makefile").write_text("all:\n")
        name, cmd = _detect_build_system(str(tmp_path))
        assert name == "make"

    def test_detect_cargo(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        name, cmd = _detect_build_system(str(tmp_path))
        assert name == "cargo"

    def test_detect_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module test\n")
        name, cmd = _detect_build_system(str(tmp_path))
        assert name == "go"

    async def test_custom_command(self, tool: BuildTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"Built OK", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(
                {"command": "make release"}, context={"cwd": "/tmp"}
            )

        assert result.success
        assert result.metadata["build_system"] == "custom"

    async def test_no_build_system(self, tool: BuildTool, tmp_path: Path):
        result = await tool.execute({}, context={"cwd": str(tmp_path)})
        assert not result.success
        assert "detect" in result.error.lower()


# ---------------------------------------------------------------------------
# LintTool
# ---------------------------------------------------------------------------


class TestLintTool:
    def test_detect_ruff(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
        name, cmd = _detect_linter(str(tmp_path))
        assert name == "ruff"

    def test_detect_eslint(self, tmp_path: Path):
        (tmp_path / ".eslintrc.json").write_text("{}\n")
        name, cmd = _detect_linter(str(tmp_path))
        assert name == "eslint"

    def test_detect_golangci(self, tmp_path: Path):
        (tmp_path / ".golangci.yml").write_text("linters:\n")
        name, cmd = _detect_linter(str(tmp_path))
        assert name == "golangci-lint"

    def test_detect_clippy(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        name, cmd = _detect_linter(str(tmp_path))
        assert name == "clippy"


# ---------------------------------------------------------------------------
# DockerTool
# ---------------------------------------------------------------------------


class TestDockerTool:
    @pytest.fixture()
    def tool(self):
        return DockerTool()

    async def test_requires_confirmation(self, tool: DockerTool):
        assert tool.requires_confirmation is True

    async def test_invalid_operation(self, tool: DockerTool):
        result = await tool.execute({"operation": "nuke"})
        assert not result.success

    async def test_ps(self, tool: DockerTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"CONTAINER ID\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute(
                {"operation": "ps", "args": "-a"}, context={"cwd": "/tmp"}
            )

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "docker" in call_args
        assert "ps" in call_args
        assert "-a" in call_args


# ---------------------------------------------------------------------------
# DBQueryTool
# ---------------------------------------------------------------------------


class TestDBQueryTool:
    @pytest.fixture()
    def tool(self):
        return DBQueryTool()

    async def test_select_query(self, tool: DBQueryTool, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'alpha')")
        conn.execute("INSERT INTO items VALUES (2, 'beta')")
        conn.commit()
        conn.close()

        result = await tool.execute({
            "query": "SELECT * FROM items",
            "db_path": db_path,
        })

        assert result.success
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["name"] == "alpha"

    async def test_write_rejected(self, tool: DBQueryTool):
        result = await tool.execute({
            "query": "DROP TABLE items",
            "db_path": ":memory:",
        })
        assert not result.success
        assert "confirmation" in result.error.lower()

    async def test_empty_query(self, tool: DBQueryTool):
        result = await tool.execute({"query": ""})
        assert not result.success

    async def test_pragma_allowed(self, tool: DBQueryTool, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        sqlite3.connect(db_path).close()

        result = await tool.execute({
            "query": "PRAGMA table_info('sqlite_master')",
            "db_path": db_path,
        })
        assert result.success


# ---------------------------------------------------------------------------
# PackageManageTool
# ---------------------------------------------------------------------------


class TestPackageManageTool:
    @pytest.fixture()
    def tool(self):
        return PackageManageTool()

    def test_detect_pip(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("httpx\n")
        mgr, _ = _detect_package_manager(str(tmp_path))
        assert mgr == "pip"

    def test_detect_npm(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}\n")
        mgr, _ = _detect_package_manager(str(tmp_path))
        assert mgr == "npm"

    def test_detect_cargo(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        mgr, _ = _detect_package_manager(str(tmp_path))
        assert mgr == "cargo"

    def test_detect_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module test\n")
        mgr, _ = _detect_package_manager(str(tmp_path))
        assert mgr == "go"

    async def test_requires_confirmation(self, tool: PackageManageTool):
        assert tool.requires_confirmation is True

    async def test_unknown_manager(self, tool: PackageManageTool, tmp_path: Path):
        result = await tool.execute(
            {"operation": "install"}, context={"cwd": str(tmp_path)}
        )
        assert not result.success

    async def test_pip_install(self, tool: PackageManageTool):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"Successfully installed\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await tool.execute(
                {"operation": "install", "package": "httpx", "manager": "pip"},
                context={"cwd": "/tmp"},
            )

        assert result.success
        call_args = mock_exec.call_args[0]
        assert "pip" in call_args
        assert "install" in call_args
        assert "httpx" in call_args


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_all_tools_registered(self):
        registry = get_default_registry()
        tools = registry.list_tools()
        tool_ids = {t.tool_id for t in tools}
        expected = {
            "file_read", "file_write", "file_edit", "file_list",
            "shell_exec", "grep", "glob", "git",
            "web_search", "web_fetch", "api_call", "ssh_exec",
            "test_run", "build", "lint", "docker", "db_query", "package_manage",
        }
        assert expected.issubset(tool_ids), f"Missing: {expected - tool_ids}"

    def test_function_schemas(self):
        registry = get_default_registry()
        schemas = registry.get_function_schemas()
        assert len(schemas) >= 18
        for schema in schemas:
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "description" in schema["function"]
