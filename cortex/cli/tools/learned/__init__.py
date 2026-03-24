"""Self-learning tool system — Atlas creates, uses, and cleans up tools autonomously.

Module ownership: Dynamic tool learning and lifecycle
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

logger = logging.getLogger(__name__)

TOOLS_DIR = Path(os.path.expanduser("~/.atlas/tools"))


@dataclass
class ToolDefinition:
    """A user-taught or auto-discovered tool."""

    id: str
    description: str
    command_template: str  # Shell command with {param} placeholders
    parameters: dict[str, dict] = field(default_factory=dict)
    requires_confirmation: bool = False
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_used: float = 0
    use_count: int = 0
    auto_discovered: bool = False


class DynamicTool(AgentTool):
    """A tool created at runtime from a ToolDefinition."""

    def __init__(self, definition: ToolDefinition) -> None:
        self.tool_id = f"learned_{definition.id}"
        self.description = definition.description
        self.requires_confirmation = definition.requires_confirmation
        self._definition = definition

        self.parameters_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                name: {
                    "type": info.get("type", "string"),
                    "description": info.get("description", ""),
                    **({"default": info["default"]} if "default" in info else {}),
                }
                for name, info in definition.parameters.items()
            },
            "required": [
                name
                for name, info in definition.parameters.items()
                if info.get("required", False)
            ],
        }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute the learned tool by filling in the command template."""
        try:
            cmd = self._definition.command_template
            for key, value in params.items():
                cmd = cmd.replace(f"{{{key}}}", str(value))

            # Fill defaults for missing params
            for name, info in self._definition.parameters.items():
                placeholder = f"{{{name}}}"
                if placeholder in cmd and "default" in info:
                    cmd = cmd.replace(placeholder, str(info["default"]))

            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            output = stdout.decode(errors="replace")
            if stderr:
                output += f"\n[stderr]\n{stderr.decode(errors='replace')}"

            self._definition.last_used = time.time()
            self._definition.use_count += 1

            return ToolResult(
                success=proc.returncode == 0,
                output=output[:10000],
                error="" if proc.returncode == 0 else f"Exit code: {proc.returncode}",
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Command timed out (30s)")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class DynamicToolLoader:
    """Manages the lifecycle of learned tools."""

    def __init__(self, tools_dir: Path = TOOLS_DIR) -> None:
        self.tools_dir = tools_dir
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self._definitions: dict[str, ToolDefinition] = {}

    def load_all(self) -> list[DynamicTool]:
        """Load all tool definitions from disk."""
        defs_file = self.tools_dir / "definitions.json"
        if not defs_file.exists():
            return []

        try:
            data = json.loads(defs_file.read_text())
            tools: list[DynamicTool] = []
            for d in data.get("tools", []):
                defn = ToolDefinition(**d)
                self._definitions[defn.id] = defn
                tools.append(DynamicTool(defn))
            logger.info("Loaded %d learned tools", len(tools))
            return tools
        except Exception as e:
            logger.warning("Failed to load learned tools: %s", e)
            return []

    def save_all(self) -> None:
        """Persist all tool definitions to disk."""
        defs_file = self.tools_dir / "definitions.json"
        data = {
            "version": 1,
            "tools": [
                {
                    "id": d.id,
                    "description": d.description,
                    "command_template": d.command_template,
                    "parameters": d.parameters,
                    "requires_confirmation": d.requires_confirmation,
                    "tags": d.tags,
                    "created_at": d.created_at,
                    "last_used": d.last_used,
                    "use_count": d.use_count,
                    "auto_discovered": d.auto_discovered,
                }
                for d in self._definitions.values()
            ],
        }
        defs_file.write_text(json.dumps(data, indent=2))

    def create_tool(
        self,
        tool_id: str,
        description: str,
        command_template: str,
        parameters: dict[str, dict] | None = None,
        requires_confirmation: bool = False,
        tags: list[str] | None = None,
        auto_discovered: bool = False,
    ) -> DynamicTool:
        """Create and persist a new learned tool."""
        defn = ToolDefinition(
            id=tool_id,
            description=description,
            command_template=command_template,
            parameters=parameters or {},
            requires_confirmation=requires_confirmation,
            tags=tags or [],
            auto_discovered=auto_discovered,
        )
        self._definitions[tool_id] = defn
        self.save_all()
        tool = DynamicTool(defn)
        logger.info("Created learned tool: %s", tool_id)
        return tool

    def remove_tool(self, tool_id: str) -> bool:
        """Remove a learned tool."""
        if tool_id in self._definitions:
            del self._definitions[tool_id]
            self.save_all()
            return True
        return False

    def list_tools(self) -> list[dict[str, Any]]:
        """List all learned tools with usage stats."""
        return [
            {
                "id": d.id,
                "description": d.description,
                "use_count": d.use_count,
                "last_used": d.last_used,
                "auto_discovered": d.auto_discovered,
                "created_at": d.created_at,
            }
            for d in self._definitions.values()
        ]

    def cleanup_unused(self, max_age_days: int = 30) -> list[str]:
        """Remove tools unused for more than *max_age_days*.

        Removal criteria:
        - Never used and created more than *max_age_days* ago.
        - Used fewer than 3 times and last used more than *max_age_days* ago.

        Returns list of removed tool IDs.
        """
        cutoff = time.time() - (max_age_days * 86400)
        removed: list[str] = []
        for tool_id, defn in list(self._definitions.items()):
            if defn.use_count == 0 and defn.created_at < cutoff:
                del self._definitions[tool_id]
                removed.append(tool_id)
            elif defn.last_used > 0 and defn.last_used < cutoff and defn.use_count < 3:
                del self._definitions[tool_id]
                removed.append(tool_id)
        if removed:
            self.save_all()
            logger.info("Cleaned up %d unused tools: %s", len(removed), removed)
        return removed

    def get_definition(self, tool_id: str) -> ToolDefinition | None:
        return self._definitions.get(tool_id)


# ── Management tools ────────────────────────────────────────────────


class ToolLearnTool(AgentTool):
    """Teach Atlas a new tool from a description."""

    tool_id = "tool_learn"
    description = "Teach Atlas a new tool by providing a command template and description"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short tool name (lowercase, no spaces)",
            },
            "description": {
                "type": "string",
                "description": "What the tool does",
            },
            "command": {
                "type": "string",
                "description": "Shell command template with {param} placeholders",
            },
            "params": {
                "type": "object",
                "description": "Parameter definitions: {name: {type, description, default}}",
            },
            "dangerous": {
                "type": "boolean",
                "description": "Requires user confirmation before running",
                "default": False,
            },
        },
        "required": ["name", "description", "command"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        loader = get_tool_loader()
        tool = loader.create_tool(
            tool_id=params["name"],
            description=params["description"],
            command_template=params["command"],
            parameters=params.get("params", {}),
            requires_confirmation=params.get("dangerous", False),
        )
        if context and "registry" in context:
            context["registry"].register(tool)
        return ToolResult(
            success=True,
            output=(
                f"Learned new tool: {params['name']}\n"
                f"Command: {params['command']}\n"
                f"Description: {params['description']}\n"
                f"Saved to ~/.atlas/tools/definitions.json"
            ),
        )


class ToolForgetTool(AgentTool):
    """Remove a learned tool."""

    tool_id = "tool_forget"
    description = "Remove a previously learned tool"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tool name to remove"},
        },
        "required": ["name"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        loader = get_tool_loader()
        removed = loader.remove_tool(params["name"])
        if removed:
            if context and "registry" in context:
                context["registry"].unregister(f"learned_{params['name']}")
            return ToolResult(success=True, output=f"Forgot tool: {params['name']}")
        return ToolResult(
            success=False, output="", error=f"Tool not found: {params['name']}"
        )


class ToolListLearnedTool(AgentTool):
    """List all learned tools with usage stats."""

    tool_id = "tool_list_learned"
    description = "List all tools Atlas has learned, with usage statistics"
    parameters_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        loader = get_tool_loader()
        tools = loader.list_tools()
        if not tools:
            return ToolResult(
                success=True, output="No learned tools yet. Use tool_learn to teach me!"
            )

        lines = [
            f"{'Name':20s} {'Uses':>5s} {'Last Used':20s} {'Auto':>4s} Description"
        ]
        lines.append("-" * 80)
        for t in tools:
            last = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(t["last_used"]))
                if t["last_used"]
                else "never"
            )
            auto = "yes" if t["auto_discovered"] else "no"
            lines.append(
                f"{t['id']:20s} {t['use_count']:5d} {last:20s} {auto:>4s} "
                f"{t['description'][:30]}"
            )

        return ToolResult(success=True, output="\n".join(lines))


class ToolCleanupTool(AgentTool):
    """Clean up unused learned tools."""

    tool_id = "tool_cleanup"
    description = "Remove learned tools that haven't been used in 30+ days"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "max_age_days": {
                "type": "integer",
                "description": "Remove tools unused for this many days",
                "default": 30,
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        loader = get_tool_loader()
        removed = loader.cleanup_unused(params.get("max_age_days", 30))
        if removed:
            return ToolResult(
                success=True,
                output=f"Cleaned up {len(removed)} tools: {', '.join(removed)}",
            )
        return ToolResult(success=True, output="No unused tools to clean up.")


class ToolProposeTool(AgentTool):
    """Propose creating a new tool when Atlas identifies a repeated need."""

    tool_id = "tool_propose"
    description = (
        "Propose creating a new tool for a capability Atlas doesn't have yet. "
        "Use this when you find yourself repeatedly needing to do something "
        "that no existing tool covers."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "need": {
                "type": "string",
                "description": "What capability is needed",
            },
            "suggested_command": {
                "type": "string",
                "description": "Suggested shell command template",
            },
            "suggested_name": {
                "type": "string",
                "description": "Suggested tool name",
            },
            "reason": {
                "type": "string",
                "description": "Why this tool would be useful",
            },
        },
        "required": ["need", "suggested_name", "suggested_command"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        loader = get_tool_loader()
        tool = loader.create_tool(
            tool_id=params["suggested_name"],
            description=params["need"],
            command_template=params["suggested_command"],
            auto_discovered=True,
            tags=["auto-discovered"],
        )
        if context and "registry" in context:
            context["registry"].register(tool)

        return ToolResult(
            success=True,
            output=(
                f"Created new tool: {params['suggested_name']}\n"
                f"Command: {params['suggested_command']}\n"
                f"Reason: {params.get('reason', 'Auto-discovered need')}\n"
                f"Note: Auto-discovered tools are cleaned up if unused for 30 days."
            ),
        )


# ── Module-level singleton ──────────────────────────────────────────

_loader: DynamicToolLoader | None = None


def get_tool_loader(tools_dir: Path | None = None) -> DynamicToolLoader:
    """Return the module-level loader singleton.

    Pass *tools_dir* to override the default ``~/.atlas/tools`` path (useful
    for testing).  Once created the singleton is reused for subsequent calls.
    """
    global _loader
    if _loader is None:
        _loader = DynamicToolLoader(tools_dir or TOOLS_DIR)
    return _loader


def reset_tool_loader() -> None:
    """Reset the singleton — primarily for test isolation."""
    global _loader
    _loader = None
