"""Tests for the Atlas CLI tool system — registry, file, shell, grep, git tools."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from cortex.cli.tools import AgentTool, ToolRegistry, ToolResult, get_default_registry
from cortex.cli.tools.files import FileEditTool, FileListTool, FileReadTool, FileWriteTool
from cortex.cli.tools.git import GitTool
from cortex.cli.tools.shell import GlobTool, GrepTool, ShellExecTool


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        tool = FileReadTool()
        reg.register(tool)
        assert reg.get("file_read") is tool

    def test_get_unknown_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("no_such_tool") is None

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(FileReadTool())
        reg.register(FileWriteTool())
        assert len(reg.list_tools()) == 2

    def test_get_function_schemas(self):
        reg = ToolRegistry()
        reg.register(FileReadTool())
        schemas = reg.get_function_schemas()
        assert len(schemas) == 1
        s = schemas[0]
        assert s["type"] == "function"
        assert s["function"]["name"] == "file_read"
        assert "properties" in s["function"]["parameters"]

    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = await reg.execute("nope", {})
        assert not result.success
        assert "Unknown tool" in result.error

    async def test_execute_delegates_to_tool(self, tmp_path: Path):
        reg = ToolRegistry()
        reg.register(FileReadTool())
        f = tmp_path / "hello.txt"
        f.write_text("hi\n")
        result = await reg.execute("file_read", {"path": str(f)})
        assert result.success
        assert "hi" in result.output

    def test_register_no_id_raises(self):
        class BadTool(AgentTool):
            async def execute(self, params, context=None):
                return ToolResult(success=True, output="")

        with pytest.raises(ValueError):
            ToolRegistry().register(BadTool())


# ---------------------------------------------------------------------------
# to_function_schema
# ---------------------------------------------------------------------------

class TestFunctionSchema:
    def test_schema_structure(self):
        schema = FileReadTool().to_function_schema()
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "file_read"
        assert isinstance(fn["description"], str) and len(fn["description"]) > 0
        assert fn["parameters"]["type"] == "object"
        assert "path" in fn["parameters"]["properties"]


# ---------------------------------------------------------------------------
# FileReadTool
# ---------------------------------------------------------------------------

class TestFileReadTool:
    async def test_read_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        result = await FileReadTool().execute({"path": str(f)})
        assert result.success
        assert "1. line1" in result.output
        assert "3. line3" in result.output

    async def test_read_with_line_range(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = await FileReadTool().execute(
            {"path": str(f), "start_line": 2, "end_line": 4}
        )
        assert result.success
        assert "2. b" in result.output
        assert "4. d" in result.output
        assert "1. a" not in result.output
        assert "5. e" not in result.output

    async def test_file_not_found(self):
        result = await FileReadTool().execute({"path": "/no/such/file.txt"})
        assert not result.success
        assert "not found" in result.error.lower()


# ---------------------------------------------------------------------------
# FileWriteTool
# ---------------------------------------------------------------------------

class TestFileWriteTool:
    async def test_create_new_file(self, tmp_path: Path):
        target = tmp_path / "new.txt"
        result = await FileWriteTool().execute(
            {"path": str(target), "content": "hello world"}
        )
        assert result.success
        assert target.read_text() == "hello world"

    async def test_refuse_overwrite(self, tmp_path: Path):
        target = tmp_path / "existing.txt"
        target.write_text("old")
        result = await FileWriteTool().execute(
            {"path": str(target), "content": "new"}
        )
        assert not result.success
        assert "already exists" in result.error
        assert target.read_text() == "old"

    async def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c.txt"
        result = await FileWriteTool().execute(
            {"path": str(target), "content": "deep"}
        )
        assert result.success
        assert target.read_text() == "deep"


# ---------------------------------------------------------------------------
# FileEditTool
# ---------------------------------------------------------------------------

class TestFileEditTool:
    async def test_successful_edit(self, tmp_path: Path):
        f = tmp_path / "edit.txt"
        f.write_text("foo bar baz\n")
        result = await FileEditTool().execute(
            {"path": str(f), "old_str": "bar", "new_str": "REPLACED"}
        )
        assert result.success
        assert "REPLACED" in f.read_text()

    async def test_old_str_not_found(self, tmp_path: Path):
        f = tmp_path / "edit.txt"
        f.write_text("foo\n")
        result = await FileEditTool().execute(
            {"path": str(f), "old_str": "MISSING", "new_str": "x"}
        )
        assert not result.success
        assert "not found" in result.error

    async def test_ambiguous_match(self, tmp_path: Path):
        f = tmp_path / "edit.txt"
        f.write_text("aaa\naaa\n")
        result = await FileEditTool().execute(
            {"path": str(f), "old_str": "aaa", "new_str": "bbb"}
        )
        assert not result.success
        assert "2 locations" in result.error


# ---------------------------------------------------------------------------
# FileListTool
# ---------------------------------------------------------------------------

class TestFileListTool:
    async def test_list_directory(self, tmp_path: Path):
        (tmp_path / "file.txt").write_text("x")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "inner.txt").write_text("y")
        result = await FileListTool().execute({"path": str(tmp_path)})
        assert result.success
        assert "subdir/" in result.output
        assert "file.txt" in result.output
        assert "inner.txt" in result.output

    async def test_not_a_directory(self, tmp_path: Path):
        f = tmp_path / "notdir.txt"
        f.write_text("x")
        result = await FileListTool().execute({"path": str(f)})
        assert not result.success


# ---------------------------------------------------------------------------
# ShellExecTool
# ---------------------------------------------------------------------------

class TestShellExecTool:
    async def test_simple_command(self):
        result = await ShellExecTool().execute({"command": "echo hello"})
        assert result.success
        assert "hello" in result.output

    async def test_timeout(self):
        result = await ShellExecTool().execute(
            {"command": "sleep 60", "timeout": 1}
        )
        assert not result.success
        assert "timed out" in result.error.lower()

    async def test_dangerous_command_rejected(self):
        result = await ShellExecTool().execute({"command": "rm -rf / "})
        assert not result.success
        assert "Blocked" in result.error

    async def test_nonzero_exit(self):
        result = await ShellExecTool().execute({"command": "false"})
        assert not result.success

    def test_requires_confirmation_flag(self):
        assert ShellExecTool.requires_confirmation is True


# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------

class TestGrepTool:
    async def test_pattern_search(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        (tmp_path / "b.py").write_text("x = 1\n")
        result = await GrepTool().execute(
            {"pattern": "hello", "path": str(tmp_path)}
        )
        assert result.success
        assert "hello" in result.output

    async def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "c.txt").write_text("Hello World\n")
        result = await GrepTool().execute(
            {"pattern": "hello", "path": str(tmp_path), "case_insensitive": True}
        )
        assert result.success
        assert "Hello" in result.output

    async def test_with_glob_filter(self, tmp_path: Path):
        (tmp_path / "match.py").write_text("target\n")
        (tmp_path / "skip.txt").write_text("target\n")
        result = await GrepTool().execute(
            {"pattern": "target", "path": str(tmp_path), "glob_filter": "*.py"}
        )
        assert result.success
        assert "match.py" in result.output

    async def test_no_matches(self, tmp_path: Path):
        (tmp_path / "empty.txt").write_text("nothing here\n")
        result = await GrepTool().execute(
            {"pattern": "XYZZY_NOT_HERE", "path": str(tmp_path)}
        )
        assert result.success
        assert "no matches" in result.output.lower()


# ---------------------------------------------------------------------------
# GlobTool
# ---------------------------------------------------------------------------

class TestGlobTool:
    async def test_pattern_matching(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = await GlobTool().execute(
            {"pattern": "*.py", "path": str(tmp_path)}
        )
        assert result.success
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output

    async def test_recursive_pattern(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        result = await GlobTool().execute(
            {"pattern": "**/*.py", "path": str(tmp_path)}
        )
        assert result.success
        assert "deep.py" in result.output


# ---------------------------------------------------------------------------
# GitTool
# ---------------------------------------------------------------------------

class TestGitTool:
    async def test_status(self, tmp_path: Path):
        # Init a git repo in tmp_path
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        result = await GitTool().execute(
            {"operation": "status"}, context={"cwd": str(tmp_path)}
        )
        assert result.success

    async def test_diff(self, tmp_path: Path):
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        result = await GitTool().execute(
            {"operation": "diff"}, context={"cwd": str(tmp_path)}
        )
        assert result.success

    async def test_log_empty_repo(self, tmp_path: Path):
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        result = await GitTool().execute(
            {"operation": "log"}, context={"cwd": str(tmp_path)}
        )
        # Empty repo has no commits → git log returns non-zero
        assert not result.success

    async def test_unknown_operation(self):
        result = await GitTool().execute({"operation": "push"})
        assert not result.success
        assert "Unknown" in result.error


# ---------------------------------------------------------------------------
# get_default_registry
# ---------------------------------------------------------------------------

class TestDefaultRegistry:
    def test_all_tools_registered(self):
        reg = get_default_registry()
        expected = {
            "file_read", "file_write", "file_edit", "file_list",
            "shell_exec", "grep", "glob", "git",
            "web_search", "web_fetch", "api_call", "ssh_exec",
            "test_run", "build", "lint", "docker", "db_query", "package_manage",
        }
        actual = {t.tool_id for t in reg.list_tools()}
        assert expected == actual

    def test_schemas_are_valid(self):
        reg = get_default_registry()
        schemas = reg.get_function_schemas()
        assert len(schemas) == 8
        for s in schemas:
            assert s["type"] == "function"
            fn = s["function"]
            assert isinstance(fn["name"], str)
            assert isinstance(fn["description"], str)
            assert isinstance(fn["parameters"], dict)


# Needed for TestGitTool's async subprocess calls
import asyncio
