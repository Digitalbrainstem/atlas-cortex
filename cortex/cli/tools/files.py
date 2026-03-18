"""File operation tools: read, write, edit, list.

Provides safe file manipulation for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — file operations
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


def _resolve(path_str: str, context: dict[str, Any] | None) -> Path:
    """Resolve a path relative to context cwd (or real cwd)."""
    p = Path(path_str)
    if not p.is_absolute():
        cwd = (context or {}).get("cwd", os.getcwd())
        p = Path(cwd) / p
    return p.resolve()


class FileReadTool(AgentTool):
    """Read the contents of a file, optionally a specific line range."""

    tool_id = "file_read"
    description = "Read the contents of a file, optionally a specific line range"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to file"},
            "start_line": {
                "type": "integer",
                "description": "Start line (1-indexed, optional)",
            },
            "end_line": {
                "type": "integer",
                "description": "End line (inclusive, optional)",
            },
        },
        "required": ["path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        filepath = _resolve(params["path"], context)
        if not filepath.is_file():
            return ToolResult(
                success=False, output="", error=f"File not found: {filepath}"
            )
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        lines = text.splitlines(keepends=True)
        start = params.get("start_line")
        end = params.get("end_line")

        if start is not None or end is not None:
            s = max((start or 1) - 1, 0)
            e = end if end is not None else len(lines)
            lines = lines[s:e]
            offset = s
        else:
            offset = 0

        numbered = "".join(
            f"{offset + i + 1}. {line}" for i, line in enumerate(lines)
        )
        return ToolResult(
            success=True,
            output=numbered,
            metadata={"path": str(filepath), "total_lines": offset + len(lines)},
        )


class FileWriteTool(AgentTool):
    """Create a new file with specified content."""

    tool_id = "file_write"
    description = "Create a new file with specified content"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to new file"},
            "content": {"type": "string", "description": "File content to write"},
        },
        "required": ["path", "content"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        filepath = _resolve(params["path"], context)
        if filepath.exists():
            return ToolResult(
                success=False,
                output="",
                error=f"File already exists: {filepath} — use file_edit instead",
            )
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(params["content"], encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))
        return ToolResult(
            success=True,
            output=f"Created {filepath}",
            metadata={"path": str(filepath)},
        )


class FileEditTool(AgentTool):
    """Make a surgical string replacement in an existing file."""

    tool_id = "file_edit"
    description = "Make a surgical string replacement in an existing file"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to file"},
            "old_str": {"type": "string", "description": "Exact text to find"},
            "new_str": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_str", "new_str"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        filepath = _resolve(params["path"], context)
        if not filepath.is_file():
            return ToolResult(
                success=False, output="", error=f"File not found: {filepath}"
            )

        old_str: str = params["old_str"]
        new_str: str = params["new_str"]

        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        count = text.count(old_str)
        if count == 0:
            return ToolResult(
                success=False,
                output="",
                error="old_str not found in file",
            )
        if count > 1:
            return ToolResult(
                success=False,
                output="",
                error=f"old_str matches {count} locations — be more specific",
            )

        new_text = text.replace(old_str, new_str, 1)
        filepath.write_text(new_text, encoding="utf-8")

        # Show context around the edit
        pos = new_text.find(new_str)
        start = max(pos - 200, 0)
        end = min(pos + len(new_str) + 200, len(new_text))
        snippet = new_text[start:end]

        return ToolResult(
            success=True,
            output=f"Edited {filepath}\n\n...{snippet}...",
            metadata={"path": str(filepath)},
        )


class FileListTool(AgentTool):
    """List files and directories at a path."""

    tool_id = "file_list"
    description = "List files and directories at a path"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path (default: current directory)",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum depth to list (default: 2)",
            },
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        dirpath = _resolve(params.get("path", "."), context)
        if not dirpath.is_dir():
            return ToolResult(
                success=False, output="", error=f"Not a directory: {dirpath}"
            )

        max_depth = params.get("max_depth", 2)
        entries: list[str] = []

        def _walk(p: Path, depth: int, prefix: str = "") -> None:
            if depth > max_depth:
                return
            try:
                items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return
            for item in items:
                if item.name.startswith("."):
                    continue
                marker = "/" if item.is_dir() else ""
                entries.append(f"{prefix}{item.name}{marker}")
                if item.is_dir() and depth < max_depth:
                    _walk(item, depth + 1, prefix + "  ")

        _walk(dirpath, 1)
        return ToolResult(
            success=True,
            output="\n".join(entries) if entries else "(empty directory)",
            metadata={"path": str(dirpath), "count": len(entries)},
        )
