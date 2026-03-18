"""Shell, grep, and glob tools.

Provides safe shell execution and file search for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — shell / search
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)

# Patterns that indicate destructive intent
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-\w*r\w*f|--force|--recursive)\s+/\s*$", re.I),
    re.compile(r"\brm\s+-\w*rf?\s+/\s*$", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bdd\s+.*of=/dev/", re.I),
    re.compile(r":\(\)\{\s*:\|:&\s*\};:", re.I),  # fork bomb
    re.compile(r"\b>\s*/dev/sd[a-z]", re.I),
    re.compile(r"\bchmod\s+(-R\s+)?777\s+/\s*$", re.I),
]


def _is_dangerous(command: str) -> str | None:
    """Return a reason string if the command looks dangerous, else None."""
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return f"Blocked: command matches dangerous pattern ({pat.pattern})"
    return None


class ShellExecTool(AgentTool):
    """Execute a shell command and return its output."""

    tool_id = "shell_exec"
    description = "Execute a shell command and return its output"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30)",
            },
            "cwd": {"type": "string", "description": "Working directory (optional)"},
        },
        "required": ["command"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        command = params["command"]
        reason = _is_dangerous(command)
        if reason:
            return ToolResult(success=False, output="", error=reason)

        timeout = params.get("timeout", 30)
        cwd = params.get("cwd") or (context or {}).get("cwd") or os.getcwd()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return ToolResult(
                success=False,
                output="",
                error=f"Command timed out after {timeout}s",
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        out = stdout.decode(errors="replace") if stdout else ""
        err = stderr.decode(errors="replace") if stderr else ""
        ok = proc.returncode == 0

        return ToolResult(
            success=ok,
            output=out,
            error=err if not ok else "",
            metadata={"returncode": proc.returncode},
        )


class GrepTool(AgentTool):
    """Search file contents for a pattern using ripgrep (or grep fallback)."""

    tool_id = "grep"
    description = "Search file contents for a pattern using ripgrep"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {
                "type": "string",
                "description": "File or directory to search (default: current dir)",
            },
            "glob_filter": {
                "type": "string",
                "description": "Glob to filter files (e.g. '*.py')",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Case-insensitive search",
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of context around each match",
            },
            "max_results": {
                "type": "integer",
                "description": "Max number of matching lines to return",
            },
        },
        "required": ["pattern"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        pattern = params["pattern"]
        search_path = params.get("path", ".")
        if not Path(search_path).is_absolute():
            cwd = (context or {}).get("cwd", os.getcwd())
            search_path = str(Path(cwd) / search_path)

        rg = shutil.which("rg")
        cmd_parts: list[str] = []

        if rg:
            cmd_parts = [rg, "--no-heading", "--line-number", "--color=never"]
            if params.get("case_insensitive"):
                cmd_parts.append("-i")
            ctx = params.get("context_lines")
            if ctx is not None:
                cmd_parts.extend(["-C", str(ctx)])
            if params.get("glob_filter"):
                cmd_parts.extend(["-g", params["glob_filter"]])
            max_r = params.get("max_results")
            if max_r is not None:
                cmd_parts.extend(["-m", str(max_r)])
            cmd_parts.extend(["--", pattern, search_path])
        else:
            cmd_parts = ["grep", "-rn", "--color=never"]
            if params.get("case_insensitive"):
                cmd_parts.append("-i")
            ctx = params.get("context_lines")
            if ctx is not None:
                cmd_parts.extend(["-C", str(ctx)])
            if params.get("glob_filter"):
                cmd_parts.extend(["--include", params["glob_filter"]])
            max_r = params.get("max_results")
            if max_r is not None:
                cmd_parts.extend(["-m", str(max_r)])
            cmd_parts.extend(["--", pattern, search_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return ToolResult(success=False, output="", error="Grep timed out")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        out = stdout.decode(errors="replace") if stdout else ""
        # ripgrep returns 1 for "no matches" — that's not an error
        if proc.returncode not in (0, 1):
            return ToolResult(
                success=False,
                output=out,
                error=stderr.decode(errors="replace") if stderr else "",
            )
        return ToolResult(success=True, output=out or "(no matches)")


class GlobTool(AgentTool):
    """Find files matching a glob pattern."""

    tool_id = "glob"
    description = "Find files matching a glob pattern"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py')",
            },
            "path": {
                "type": "string",
                "description": "Base directory (default: current dir)",
            },
        },
        "required": ["pattern"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        base = params.get("path", ".")
        if not Path(base).is_absolute():
            cwd = (context or {}).get("cwd", os.getcwd())
            base = str(Path(cwd) / base)

        base_path = Path(base)
        if not base_path.is_dir():
            return ToolResult(
                success=False, output="", error=f"Not a directory: {base}"
            )

        try:
            matches = sorted(str(p) for p in base_path.glob(params["pattern"]))
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))

        return ToolResult(
            success=True,
            output="\n".join(matches) if matches else "(no matches)",
            metadata={"count": len(matches)},
        )
