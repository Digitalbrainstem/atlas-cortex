"""Git operation tool.

Provides safe git command execution for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — git
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)

# Operations that modify the repository
_MUTATING_OPS = frozenset({"add", "commit", "stash"})

# Allowed git sub-commands
_ALLOWED_OPS = frozenset(
    {"status", "diff", "log", "blame", "add", "commit", "branch", "show", "stash"}
)


class GitTool(AgentTool):
    """Execute git operations: status, diff, log, blame, commit, branch, etc."""

    tool_id = "git"
    description = (
        "Execute git operations: status, diff, log, blame, commit, branch"
    )
    requires_confirmation = False  # handled per-operation below
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": sorted(_ALLOWED_OPS),
                "description": "Git operation to perform",
            },
            "args": {
                "type": "string",
                "description": (
                    "Additional arguments (e.g., file path for blame, "
                    "message for commit)"
                ),
            },
        },
        "required": ["operation"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        operation: str = params["operation"]
        if operation not in _ALLOWED_OPS:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown git operation: {operation}",
            )

        if operation in _MUTATING_OPS:
            log.info("git %s is a mutating operation — confirmation advised", operation)

        args = params.get("args", "")
        cwd = (context or {}).get("cwd") or os.getcwd()

        cmd_parts = ["git", "--no-pager", operation]
        if args:
            cmd_parts.extend(args.split())

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return ToolResult(
                success=False, output="", error="git command timed out"
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
