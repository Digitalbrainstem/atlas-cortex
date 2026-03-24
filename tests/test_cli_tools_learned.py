"""Tests for the self-learning tool system.

Module ownership: Tests for cortex.cli.tools.learned
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from cortex.cli.tools import ToolRegistry, ToolResult
from cortex.cli.tools.learned import (
    DynamicTool,
    DynamicToolLoader,
    ToolCleanupTool,
    ToolDefinition,
    ToolForgetTool,
    ToolLearnTool,
    ToolListLearnedTool,
    ToolProposeTool,
    reset_tool_loader,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Ensure each test starts with a fresh loader singleton."""
    reset_tool_loader()


# ── ToolDefinition ──────────────────────────────────────────────────


class TestToolDefinition:
    def test_defaults(self) -> None:
        defn = ToolDefinition(
            id="test", description="desc", command_template="echo hi"
        )
        assert defn.id == "test"
        assert defn.use_count == 0
        assert defn.last_used == 0
        assert defn.auto_discovered is False
        assert defn.tags == []
        assert defn.parameters == {}
        assert defn.requires_confirmation is False

    def test_custom_params(self) -> None:
        defn = ToolDefinition(
            id="x",
            description="d",
            command_template="ls {dir}",
            parameters={"dir": {"type": "string", "description": "Target dir"}},
            tags=["infra"],
        )
        assert "dir" in defn.parameters
        assert defn.tags == ["infra"]


# ── DynamicTool ─────────────────────────────────────────────────────


class TestDynamicTool:
    def test_tool_id_prefix(self) -> None:
        defn = ToolDefinition(id="pods", description="d", command_template="echo ok")
        tool = DynamicTool(defn)
        assert tool.tool_id == "learned_pods"

    def test_parameters_schema(self) -> None:
        defn = ToolDefinition(
            id="t",
            description="d",
            command_template="echo {name}",
            parameters={
                "name": {
                    "type": "string",
                    "description": "A name",
                    "required": True,
                },
                "opt": {
                    "type": "integer",
                    "description": "Optional val",
                    "default": 5,
                },
            },
        )
        tool = DynamicTool(defn)
        schema = tool.parameters_schema
        assert schema["properties"]["name"]["type"] == "string"
        assert "name" in schema["required"]
        assert "opt" not in schema["required"]
        assert schema["properties"]["opt"]["default"] == 5

    async def test_execute_success(self) -> None:
        defn = ToolDefinition(id="hw", description="d", command_template="echo hello")
        tool = DynamicTool(defn)
        result = await tool.execute({})
        assert result.success
        assert "hello" in result.output
        assert defn.use_count == 1
        assert defn.last_used > 0

    async def test_execute_param_substitution(self) -> None:
        defn = ToolDefinition(
            id="greet",
            description="d",
            command_template="echo hello {who}",
            parameters={"who": {"type": "string", "description": "name"}},
        )
        tool = DynamicTool(defn)
        result = await tool.execute({"who": "Atlas"})
        assert result.success
        assert "hello Atlas" in result.output

    async def test_execute_default_param(self) -> None:
        defn = ToolDefinition(
            id="def",
            description="d",
            command_template="echo {msg}",
            parameters={"msg": {"type": "string", "default": "fallback"}},
        )
        tool = DynamicTool(defn)
        result = await tool.execute({})
        assert result.success
        assert "fallback" in result.output

    async def test_execute_failure(self) -> None:
        defn = ToolDefinition(
            id="fail", description="d", command_template="exit 1"
        )
        tool = DynamicTool(defn)
        result = await tool.execute({})
        assert not result.success
        assert "Exit code" in result.error

    async def test_execute_timeout(self) -> None:
        defn = ToolDefinition(
            id="slow", description="d", command_template="sleep 60"
        )
        tool = DynamicTool(defn)
        result = await tool.execute({})
        assert not result.success
        assert "timed out" in result.error.lower()

    async def test_execute_stderr(self) -> None:
        defn = ToolDefinition(
            id="err", description="d", command_template="echo oops >&2"
        )
        tool = DynamicTool(defn)
        result = await tool.execute({})
        assert result.success
        assert "oops" in result.output
        assert "[stderr]" in result.output

    def test_to_function_schema(self) -> None:
        defn = ToolDefinition(id="s", description="Schema test", command_template="ls")
        tool = DynamicTool(defn)
        schema = tool.to_function_schema()
        assert schema["function"]["name"] == "learned_s"
        assert schema["function"]["description"] == "Schema test"


# ── DynamicToolLoader ───────────────────────────────────────────────


class TestDynamicToolLoader:
    def test_create_and_list(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("pods", "List k8s pods", "kubectl get pods")
        tools = loader.list_tools()
        assert len(tools) == 1
        assert tools[0]["id"] == "pods"
        assert tools[0]["use_count"] == 0

    def test_save_and_load(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("svc", "List services", "kubectl get svc")
        loader.create_tool("ns", "List namespaces", "kubectl get ns")

        # New loader reads from same directory
        loader2 = DynamicToolLoader(tmp_path)
        loaded = loader2.load_all()
        assert len(loaded) == 2
        ids = {t.tool_id for t in loaded}
        assert "learned_svc" in ids
        assert "learned_ns" in ids

    def test_json_format(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("x", "desc", "echo x", tags=["test"])
        data = json.loads((tmp_path / "definitions.json").read_text())
        assert data["version"] == 1
        assert len(data["tools"]) == 1
        assert data["tools"][0]["tags"] == ["test"]

    def test_remove_tool(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("tmp", "Temporary", "echo tmp")
        assert loader.remove_tool("tmp") is True
        assert loader.remove_tool("tmp") is False
        assert loader.list_tools() == []

    def test_get_definition(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("abc", "ABC tool", "echo abc")
        defn = loader.get_definition("abc")
        assert defn is not None
        assert defn.description == "ABC tool"
        assert loader.get_definition("nonexistent") is None

    def test_cleanup_unused_old(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("old", "Old unused tool", "echo old")
        # Backdate creation to 60 days ago
        loader._definitions["old"].created_at = time.time() - 60 * 86400
        removed = loader.cleanup_unused(max_age_days=30)
        assert "old" in removed
        assert loader.list_tools() == []

    def test_cleanup_keeps_recent(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("new", "Brand new", "echo new")
        removed = loader.cleanup_unused(max_age_days=30)
        assert removed == []
        assert len(loader.list_tools()) == 1

    def test_cleanup_removes_rarely_used(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("rare", "Rarely used", "echo rare")
        defn = loader._definitions["rare"]
        defn.use_count = 2
        defn.last_used = time.time() - 60 * 86400  # Last used 60 days ago
        removed = loader.cleanup_unused(max_age_days=30)
        assert "rare" in removed

    def test_cleanup_keeps_frequently_used(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("freq", "Frequently used", "echo freq")
        defn = loader._definitions["freq"]
        defn.use_count = 10
        defn.last_used = time.time() - 60 * 86400
        removed = loader.cleanup_unused(max_age_days=30)
        assert removed == []

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loaded = loader.load_all()
        assert loaded == []

    def test_load_corrupted_file(self, tmp_path: Path) -> None:
        (tmp_path / "definitions.json").write_text("NOT JSON")
        loader = DynamicToolLoader(tmp_path)
        loaded = loader.load_all()
        assert loaded == []

    def test_auto_discovered_flag(self, tmp_path: Path) -> None:
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool(
            "auto", "Auto tool", "echo auto", auto_discovered=True
        )
        defn = loader.get_definition("auto")
        assert defn is not None
        assert defn.auto_discovered is True


# ── ToolLearnTool ───────────────────────────────────────────────────


class TestToolLearnTool:
    async def test_learn_basic(self, tmp_path: Path) -> None:
        reset_tool_loader()
        from cortex.cli.tools.learned import _loader, get_tool_loader

        # Inject a fresh loader with tmp_path
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolLearnTool()
        result = await tool.execute(
            {
                "name": "pods",
                "description": "List Kubernetes pods",
                "command": "kubectl get pods",
            }
        )
        assert result.success
        assert "Learned new tool: pods" in result.output
        assert "kubectl get pods" in result.output

        # Verify persisted
        data = json.loads((tmp_path / "definitions.json").read_text())
        assert any(t["id"] == "pods" for t in data["tools"])

    async def test_learn_with_registry(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        registry = ToolRegistry()
        tool = ToolLearnTool()
        result = await tool.execute(
            {
                "name": "svc",
                "description": "List services",
                "command": "kubectl get svc",
            },
            context={"registry": registry},
        )
        assert result.success
        assert registry.get("learned_svc") is not None

    async def test_learn_dangerous(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolLearnTool()
        result = await tool.execute(
            {
                "name": "reboot",
                "description": "Reboot server",
                "command": "sudo reboot",
                "dangerous": True,
            }
        )
        assert result.success
        loader = mod._loader
        defn = loader.get_definition("reboot")
        assert defn is not None
        assert defn.requires_confirmation is True


# ── ToolForgetTool ──────────────────────────────────────────────────


class TestToolForgetTool:
    async def test_forget_existing(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)
        mod._loader.create_tool("tmp", "Temp", "echo tmp")

        registry = ToolRegistry()
        registry.register(DynamicTool(mod._loader.get_definition("tmp")))  # type: ignore[arg-type]

        tool = ToolForgetTool()
        result = await tool.execute(
            {"name": "tmp"}, context={"registry": registry}
        )
        assert result.success
        assert "Forgot tool: tmp" in result.output
        assert registry.get("learned_tmp") is None

    async def test_forget_nonexistent(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolForgetTool()
        result = await tool.execute({"name": "nope"})
        assert not result.success
        assert "not found" in result.error.lower()


# ── ToolListLearnedTool ─────────────────────────────────────────────


class TestToolListLearnedTool:
    async def test_list_empty(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolListLearnedTool()
        result = await tool.execute({})
        assert result.success
        assert "No learned tools" in result.output

    async def test_list_with_tools(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)
        mod._loader.create_tool("aaa", "Tool A", "echo a")
        mod._loader.create_tool("bbb", "Tool B", "echo b")

        tool = ToolListLearnedTool()
        result = await tool.execute({})
        assert result.success
        assert "aaa" in result.output
        assert "bbb" in result.output
        assert "Tool A" in result.output


# ── ToolCleanupTool ─────────────────────────────────────────────────


class TestToolCleanupTool:
    async def test_cleanup_nothing(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolCleanupTool()
        result = await tool.execute({})
        assert result.success
        assert "No unused tools" in result.output

    async def test_cleanup_old_tools(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)
        mod._loader.create_tool("stale", "Stale tool", "echo stale")
        mod._loader._definitions["stale"].created_at = time.time() - 60 * 86400

        tool = ToolCleanupTool()
        result = await tool.execute({"max_age_days": 30})
        assert result.success
        assert "stale" in result.output
        assert "Cleaned up 1" in result.output


# ── ToolProposeTool ─────────────────────────────────────────────────


class TestToolProposeTool:
    async def test_propose_creates_auto_discovered(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        registry = ToolRegistry()
        tool = ToolProposeTool()
        result = await tool.execute(
            {
                "need": "Check disk usage",
                "suggested_name": "disk_usage",
                "suggested_command": "df -h",
                "reason": "Frequently need to check disk space",
            },
            context={"registry": registry},
        )
        assert result.success
        assert "disk_usage" in result.output
        assert "Auto-discovered" in result.output

        defn = mod._loader.get_definition("disk_usage")
        assert defn is not None
        assert defn.auto_discovered is True
        assert "auto-discovered" in defn.tags

        # Registered in the active registry
        assert registry.get("learned_disk_usage") is not None

    async def test_propose_without_reason(self, tmp_path: Path) -> None:
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        tool = ToolProposeTool()
        result = await tool.execute(
            {
                "need": "List docker images",
                "suggested_name": "docker_ls",
                "suggested_command": "docker images",
            }
        )
        assert result.success
        assert "Auto-discovered need" in result.output


# ── Integration: registry wiring ────────────────────────────────────


class TestRegistryIntegration:
    async def test_learned_tools_in_default_registry(self, tmp_path: Path) -> None:
        """Management tools are registered in the default registry."""
        import cortex.cli.tools.learned as mod

        mod._loader = DynamicToolLoader(tmp_path)

        from cortex.cli.tools import get_default_registry

        registry = get_default_registry()
        for tid in [
            "tool_learn",
            "tool_forget",
            "tool_list_learned",
            "tool_cleanup",
            "tool_propose",
        ]:
            assert registry.get(tid) is not None, f"{tid} not registered"

    async def test_persisted_tools_loaded_on_startup(self, tmp_path: Path) -> None:
        """Tools saved to disk are loaded when a new registry is created."""
        import cortex.cli.tools.learned as mod

        # Create and persist a tool
        loader = DynamicToolLoader(tmp_path)
        loader.create_tool("my_tool", "My custom tool", "echo custom")

        # Reset and point singleton at same dir
        reset_tool_loader()
        mod._loader = DynamicToolLoader(tmp_path)

        from cortex.cli.tools import get_default_registry

        registry = get_default_registry()
        assert registry.get("learned_my_tool") is not None

    def test_unregister(self) -> None:
        registry = ToolRegistry()

        class FakeTool(ToolLearnTool):
            tool_id = "fake"

        registry.register(FakeTool())
        assert registry.get("fake") is not None
        assert registry.unregister("fake") is True
        assert registry.get("fake") is None
        assert registry.unregister("fake") is False
