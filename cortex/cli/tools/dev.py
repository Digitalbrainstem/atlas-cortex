"""Development tools: test runner, build, lint, docker, database, packages,
refactor, diff, process manager, env management, code analysis, benchmarks,
documentation generation, changelog.

Provides project development workflow tools for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — development workflow
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import signal
import sqlite3
import statistics
import textwrap
import time
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


def _resolve_cwd(params: dict[str, Any], context: dict[str, Any] | None) -> str:
    """Determine working directory from params/context/os."""
    return params.get("cwd") or (context or {}).get("cwd") or os.getcwd()


def _detect_file(cwd: str, *candidates: str) -> str | None:
    """Return the first candidate filename that exists in cwd."""
    for name in candidates:
        if (Path(cwd) / name).is_file():
            return name
    return None


async def _run_cmd(
    cmd_parts: list[str],
    cwd: str,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd_parts,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace") if stdout else "",
        stderr.decode(errors="replace") if stderr else "",
    )


# ---------------------------------------------------------------------------
# Test Runner
# ---------------------------------------------------------------------------

_PYTEST_SUMMARY_RE = re.compile(
    r"(\d+) passed(?:.*?(\d+) failed)?(?:.*?(\d+) error)?", re.I
)
_JEST_SUMMARY_RE = re.compile(
    r"Tests:\s+(?:(\d+) failed,\s+)?(\d+) passed", re.I
)


def _detect_test_framework(cwd: str) -> str:
    """Guess the test framework from project files."""
    if _detect_file(cwd, "pytest.ini", "pyproject.toml", "setup.cfg", "setup.py"):
        return "pytest"
    if _detect_file(cwd, "jest.config.js", "jest.config.ts", "jest.config.mjs"):
        return "jest"
    if _detect_file(cwd, "package.json"):
        return "jest"
    if _detect_file(cwd, "go.mod"):
        return "go"
    if _detect_file(cwd, "Cargo.toml"):
        return "cargo"
    return "pytest"


def _parse_pytest_output(output: str) -> dict[str, Any]:
    """Extract pass/fail counts from pytest output."""
    summary: dict[str, Any] = {"passed": 0, "failed": 0, "errors": 0, "failed_tests": []}
    m = _PYTEST_SUMMARY_RE.search(output)
    if m:
        summary["passed"] = int(m.group(1))
        summary["failed"] = int(m.group(2) or 0)
        summary["errors"] = int(m.group(3) or 0)
    # Collect FAILED test names
    for line in output.splitlines():
        if line.startswith("FAILED "):
            summary["failed_tests"].append(line.removeprefix("FAILED ").strip())
    return summary


class TestRunTool(AgentTool):
    """Run tests and return a results summary."""

    tool_id = "test_run"
    description = "Run tests and return results summary"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Test path or file (default: tests/)",
            },
            "pattern": {
                "type": "string",
                "description": "Filter pattern (e.g. test_auth)",
            },
            "verbose": {"type": "boolean", "description": "Verbose output"},
            "framework": {
                "type": "string",
                "enum": ["pytest", "jest", "go", "cargo"],
                "description": "Test framework (auto-detected if omitted)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        cwd = _resolve_cwd(params, context)
        framework = params.get("framework") or _detect_test_framework(cwd)
        test_path = params.get("path", "tests/")
        pattern = params.get("pattern", "")
        verbose = params.get("verbose", False)

        cmd: list[str] = []
        if framework == "pytest":
            cmd = ["python", "-m", "pytest", test_path]
            if pattern:
                cmd.extend(["-k", pattern])
            if verbose:
                cmd.append("-v")
            else:
                cmd.append("-q")
        elif framework == "jest":
            cmd = ["npx", "jest", test_path]
            if pattern:
                cmd.extend(["-t", pattern])
            if verbose:
                cmd.append("--verbose")
        elif framework == "go":
            cmd = ["go", "test", f"./{test_path}..."]
            if pattern:
                cmd.extend(["-run", pattern])
            if verbose:
                cmd.append("-v")
        elif framework == "cargo":
            cmd = ["cargo", "test"]
            if pattern:
                cmd.append(pattern)
            if not verbose:
                cmd.append("-q")
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown test framework: {framework}",
            )

        try:
            rc, out, err = await _run_cmd(cmd, cwd, timeout=300)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Test run timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + "\n" + err
        summary = _parse_pytest_output(combined) if framework == "pytest" else {}

        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"Tests exited with code {rc}",
            metadata={"framework": framework, "returncode": rc, **summary},
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _detect_build_system(cwd: str) -> tuple[str, list[str]]:
    """Return (name, command) for the detected build system."""
    if _detect_file(cwd, "package.json"):
        return "npm", ["npm", "run", "build"]
    if _detect_file(cwd, "Makefile"):
        return "make", ["make"]
    if _detect_file(cwd, "Cargo.toml"):
        return "cargo", ["cargo", "build"]
    if _detect_file(cwd, "go.mod"):
        return "go", ["go", "build", "./..."]
    if _detect_file(cwd, "setup.py", "pyproject.toml"):
        return "pip", ["python", "-m", "build"]
    return "unknown", []


class BuildTool(AgentTool):
    """Build a project using its build system."""

    tool_id = "build"
    description = "Build a project using its build system"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Override build command (optional)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        cwd = _resolve_cwd(params, context)
        override = params.get("command", "").strip()

        if override:
            cmd_parts = override.split()
            build_name = "custom"
        else:
            build_name, cmd_parts = _detect_build_system(cwd)

        if not cmd_parts:
            return ToolResult(
                success=False, output="",
                error="Could not detect build system. Provide a command override.",
            )

        try:
            rc, out, err = await _run_cmd(cmd_parts, cwd, timeout=300)
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Build timed out")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + ("\n" + err if err else "")
        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"Build failed (exit {rc})",
            metadata={"build_system": build_name, "returncode": rc},
        )


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def _detect_linter(cwd: str) -> tuple[str, list[str]]:
    """Return (name, command) for the detected linter."""
    if _detect_file(cwd, "pyproject.toml"):
        # Prefer ruff if pyproject.toml exists
        return "ruff", ["ruff", "check"]
    if _detect_file(cwd, ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml"):
        return "eslint", ["npx", "eslint"]
    if _detect_file(cwd, "eslint.config.js", "eslint.config.mjs"):
        return "eslint", ["npx", "eslint"]
    if _detect_file(cwd, ".golangci.yml", ".golangci.yaml"):
        return "golangci-lint", ["golangci-lint", "run"]
    if _detect_file(cwd, "Cargo.toml"):
        return "clippy", ["cargo", "clippy"]
    return "unknown", []


class LintTool(AgentTool):
    """Run linters and formatters on code."""

    tool_id = "lint"
    description = "Run linters and formatters on code"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to lint (default: current dir)",
            },
            "fix": {
                "type": "boolean",
                "description": "Auto-fix issues (default: false)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        cwd = _resolve_cwd(params, context)
        lint_path = params.get("path", ".")
        fix = params.get("fix", False)

        linter_name, cmd_parts = _detect_linter(cwd)
        if not cmd_parts:
            return ToolResult(
                success=False, output="",
                error="Could not detect linter. Install ruff, eslint, or golangci-lint.",
            )

        # Add fix flags
        if fix:
            if linter_name == "ruff":
                cmd_parts.append("--fix")
            elif linter_name == "eslint":
                cmd_parts.append("--fix")
            elif linter_name == "clippy":
                cmd_parts.extend(["--fix", "--allow-dirty"])

        cmd_parts.append(lint_path)

        try:
            rc, out, err = await _run_cmd(cmd_parts, cwd, timeout=120)
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Lint timed out")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + ("\n" + err if err else "")
        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"Lint issues found (exit {rc})",
            metadata={"linter": linter_name, "returncode": rc},
        )


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

_DOCKER_OPS = frozenset({"build", "run", "ps", "logs", "stop", "images"})


class DockerTool(AgentTool):
    """Docker operations: build, run, ps, logs, stop, images."""

    tool_id = "docker"
    description = "Docker operations: build, run, ps, logs, stop, images"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": sorted(_DOCKER_OPS),
                "description": "Docker operation",
            },
            "args": {
                "type": "string",
                "description": "Additional arguments for the docker command",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": ["operation"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        operation = params.get("operation", "")
        if operation not in _DOCKER_OPS:
            return ToolResult(
                success=False, output="",
                error=f"Unknown docker operation: {operation}",
            )

        cwd = _resolve_cwd(params, context)
        args_str = params.get("args", "")

        cmd_parts = ["docker", operation]
        if args_str:
            cmd_parts.extend(args_str.split())

        try:
            rc, out, err = await _run_cmd(cmd_parts, cwd, timeout=300)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Docker command timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + ("\n" + err if err else "")
        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"Docker {operation} failed (exit {rc})",
            metadata={"operation": operation, "returncode": rc},
        )


# ---------------------------------------------------------------------------
# Database (SQLite)
# ---------------------------------------------------------------------------

_READ_ONLY_PREFIXES = ("SELECT", "PRAGMA", "EXPLAIN")


class DBQueryTool(AgentTool):
    """Execute SQL queries against a SQLite database."""

    tool_id = "db_query"
    description = "Execute SQL queries against a SQLite database"
    # Read-only queries don't need confirmation; writes do (checked at runtime)
    requires_confirmation = False
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "SQL query to execute"},
            "db_path": {
                "type": "string",
                "description": "Path to SQLite database (default: cortex data dir)",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        query = params.get("query", "").strip()
        if not query:
            return ToolResult(success=False, output="", error="query is required")

        db_path = params.get("db_path", "")
        if not db_path:
            data_dir = os.environ.get("CORTEX_DATA_DIR", "data")
            db_path = os.path.join(data_dir, "cortex.db")

        # Check if query is read-only
        normalised = query.lstrip().upper()
        is_read_only = any(normalised.startswith(p) for p in _READ_ONLY_PREFIXES)

        if not is_read_only:
            log.info(
                "db_query: write query detected — confirmation required"
            )
            return ToolResult(
                success=False,
                output="",
                error=(
                    "Write queries require confirmation. Only SELECT, PRAGMA, "
                    "and EXPLAIN queries are allowed without confirmation."
                ),
            )

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            conn.close()
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if not rows:
            return ToolResult(
                success=True, output="(no rows returned)",
                metadata={"row_count": 0, "columns": columns},
            )

        # Format as JSON array for structured consumption
        result_dicts = [dict(row) for row in rows]
        output = json.dumps(result_dicts, indent=2, default=str)

        return ToolResult(
            success=True,
            output=output,
            metadata={"row_count": len(rows), "columns": columns},
        )


# ---------------------------------------------------------------------------
# Package Management
# ---------------------------------------------------------------------------

_PKG_OPS = frozenset({"install", "update", "audit", "list"})


def _detect_package_manager(cwd: str) -> tuple[str, str]:
    """Return (manager_name, config_file) for the detected package manager."""
    if _detect_file(cwd, "requirements.txt", "pyproject.toml", "setup.py"):
        return "pip", "requirements.txt"
    if _detect_file(cwd, "package.json"):
        return "npm", "package.json"
    if _detect_file(cwd, "Cargo.toml"):
        return "cargo", "Cargo.toml"
    if _detect_file(cwd, "go.mod"):
        return "go", "go.mod"
    return "unknown", ""


class PackageManageTool(AgentTool):
    """Install, update, or audit project dependencies."""

    tool_id = "package_manage"
    description = "Install, update, or audit project dependencies"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": sorted(_PKG_OPS),
                "description": "Package operation",
            },
            "package": {
                "type": "string",
                "description": "Specific package name (optional)",
            },
            "manager": {
                "type": "string",
                "enum": ["pip", "npm", "cargo", "go"],
                "description": "Package manager (auto-detected if omitted)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": ["operation"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        operation = params.get("operation", "")
        if operation not in _PKG_OPS:
            return ToolResult(
                success=False, output="",
                error=f"Unknown operation: {operation}",
            )

        cwd = _resolve_cwd(params, context)
        package = params.get("package", "")
        manager = params.get("manager", "")
        if not manager:
            manager, _ = _detect_package_manager(cwd)

        if manager == "unknown":
            return ToolResult(
                success=False, output="",
                error="Could not detect package manager. Specify one explicitly.",
            )

        cmd: list[str] = _build_pkg_command(manager, operation, package)
        if not cmd:
            return ToolResult(
                success=False, output="",
                error=f"Unsupported operation '{operation}' for {manager}",
            )

        try:
            rc, out, err = await _run_cmd(cmd, cwd, timeout=300)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Package command timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + ("\n" + err if err else "")
        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"{manager} {operation} failed (exit {rc})",
            metadata={"manager": manager, "operation": operation, "returncode": rc},
        )


def _build_pkg_command(manager: str, operation: str, package: str) -> list[str]:
    """Build the CLI command for a package manager operation."""
    if manager == "pip":
        if operation == "install":
            return ["pip", "install"] + ([package] if package else ["-r", "requirements.txt"])
        if operation == "update":
            if package:
                return ["pip", "install", "--upgrade", package]
            return ["pip", "install", "--upgrade", "-r", "requirements.txt"]
        if operation == "audit":
            return ["pip", "audit"]
        if operation == "list":
            return ["pip", "list"]
    elif manager == "npm":
        if operation == "install":
            return ["npm", "install"] + ([package] if package else [])
        if operation == "update":
            return ["npm", "update"] + ([package] if package else [])
        if operation == "audit":
            return ["npm", "audit"]
        if operation == "list":
            return ["npm", "list", "--depth=0"]
    elif manager == "cargo":
        if operation == "install":
            return ["cargo", "install"] + ([package] if package else [])
        if operation == "update":
            return ["cargo", "update"] + (["-p", package] if package else [])
        if operation == "audit":
            return ["cargo", "audit"]
        if operation == "list":
            return ["cargo", "install", "--list"]
    elif manager == "go":
        if operation == "install":
            return ["go", "get"] + ([package] if package else ["./..."])
        if operation == "update":
            return ["go", "get", "-u"] + ([package] if package else ["./..."])
        if operation == "audit":
            return ["go", "vet", "./..."]
        if operation == "list":
            return ["go", "list", "-m", "all"]
    return []


# ---------------------------------------------------------------------------
# Refactor (multi-file find & replace)
# ---------------------------------------------------------------------------


class RefactorTool(AgentTool):
    """Multi-file find and replace with regex support and preview."""

    tool_id = "refactor"
    description = "Multi-file find and replace with regex support and preview"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to find"},
            "replacement": {
                "type": "string",
                "description": "Replacement string (supports \\1 backrefs)",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search",
                "default": ".",
            },
            "glob_filter": {
                "type": "string",
                "description": "File glob pattern (e.g., '*.py')",
                "default": "*.py",
            },
            "preview": {
                "type": "boolean",
                "description": "Preview changes without applying",
                "default": True,
            },
        },
        "required": ["pattern", "replacement"],
    }

    @property
    def requires_confirmation(self) -> bool:  # type: ignore[override]
        return True

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        pattern_str = params.get("pattern", "")
        replacement = params.get("replacement", "")
        if not pattern_str:
            return ToolResult(success=False, output="", error="pattern is required")

        try:
            regex = re.compile(pattern_str)
        except re.error as exc:
            return ToolResult(
                success=False, output="", error=f"Invalid regex: {exc}"
            )

        search_path = Path(params.get("path", "."))
        glob_filter = params.get("glob_filter", "*.py")
        preview = params.get("preview", True)

        if search_path.is_file():
            files = [search_path]
        elif search_path.is_dir():
            files = sorted(search_path.rglob(glob_filter))
        else:
            return ToolResult(
                success=False, output="", error=f"Path not found: {search_path}"
            )

        total_matches = 0
        changed_files: list[str] = []
        preview_lines: list[str] = []

        for fpath in files:
            if not fpath.is_file():
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            matches = list(regex.finditer(content))
            if not matches:
                continue

            total_matches += len(matches)
            changed_files.append(str(fpath))

            # Build preview with context
            lines = content.splitlines(keepends=True)
            for match in matches[:5]:  # Limit preview per file
                line_num = content[:match.start()].count("\n") + 1
                original = match.group(0)
                replaced = regex.sub(replacement, original)
                preview_lines.append(
                    f"  {fpath}:{line_num}: {original!r} → {replaced!r}"
                )
            if len(matches) > 5:
                preview_lines.append(
                    f"  ... and {len(matches) - 5} more in {fpath}"
                )

            if not preview:
                new_content = regex.sub(replacement, content)
                fpath.write_text(new_content, encoding="utf-8")

        if not total_matches:
            return ToolResult(
                success=True,
                output="No matches found.",
                metadata={"matches": 0, "files_changed": 0},
            )

        header = (
            f"{'Preview — ' if preview else ''}"
            f"{total_matches} match(es) in {len(changed_files)} file(s)"
        )
        output = header + "\n" + "\n".join(preview_lines)

        return ToolResult(
            success=True,
            output=output,
            metadata={
                "matches": total_matches,
                "files_changed": len(changed_files),
                "preview": preview,
                "files": changed_files,
            },
        )


# ---------------------------------------------------------------------------
# Diff Preview
# ---------------------------------------------------------------------------


class DiffPreviewTool(AgentTool):
    """Show pending git changes (staged and unstaged)."""

    tool_id = "diff_preview"
    description = "Show pending git changes (staged and unstaged)"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "Show staged changes",
                "default": False,
            },
            "file": {
                "type": "string",
                "description": "Specific file to diff (optional)",
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        cwd = _resolve_cwd(params, context)
        staged = params.get("staged", False)

        cmd = ["git", "--no-pager", "diff"]
        if staged:
            cmd.append("--staged")

        file_path = params.get("file", "")
        if file_path:
            cmd.extend(["--", file_path])

        try:
            rc, out, err = await _run_cmd(cmd, cwd, timeout=30)
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Git diff timed out")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if rc != 0:
            return ToolResult(success=False, output="", error=err or "git diff failed")

        if not out.strip():
            label = "staged" if staged else "unstaged"
            return ToolResult(
                success=True,
                output=f"No {label} changes.",
                metadata={"staged": staged, "has_changes": False},
            )

        return ToolResult(
            success=True,
            output=out,
            metadata={"staged": staged, "has_changes": True},
        )


# ---------------------------------------------------------------------------
# Process Manager
# ---------------------------------------------------------------------------

# Module-level dict tracking background processes
_TRACKED_PROCESSES: dict[int, dict[str, Any]] = {}


class ProcessRunTool(AgentTool):
    """Start, stop, or list background processes (dev servers, watchers)."""

    tool_id = "process_run"
    description = "Start, stop, or list background processes (dev servers, watchers)"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "list", "logs"],
                "description": "Action to perform",
            },
            "command": {
                "type": "string",
                "description": "Command to run (for start)",
            },
            "name": {"type": "string", "description": "Process name/label"},
            "pid": {
                "type": "integer",
                "description": "PID to stop (for stop action)",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action = params.get("action", "")
        if action == "start":
            return await self._start(params, context)
        if action == "stop":
            return await self._stop(params)
        if action == "list":
            return self._list()
        if action == "logs":
            return await self._logs(params)
        return ToolResult(
            success=False, output="", error=f"Unknown action: {action}"
        )

    async def _start(
        self, params: dict[str, Any], context: dict[str, Any] | None
    ) -> ToolResult:
        command = params.get("command", "").strip()
        if not command:
            return ToolResult(
                success=False, output="", error="command is required for start"
            )

        name = params.get("name", command.split()[0])
        cwd = _resolve_cwd(params, context)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        pid = proc.pid
        _TRACKED_PROCESSES[pid] = {
            "name": name,
            "command": command,
            "process": proc,
            "started": time.time(),
        }

        return ToolResult(
            success=True,
            output=f"Started '{name}' (PID {pid}): {command}",
            metadata={"pid": pid, "name": name},
        )

    async def _stop(self, params: dict[str, Any]) -> ToolResult:
        pid = params.get("pid")
        if pid is None:
            return ToolResult(
                success=False, output="", error="pid is required for stop"
            )

        entry = _TRACKED_PROCESSES.pop(pid, None)
        name = entry["name"] if entry else str(pid)

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return ToolResult(
                success=True,
                output=f"Process {name} (PID {pid}) already exited.",
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        return ToolResult(
            success=True,
            output=f"Sent SIGTERM to {name} (PID {pid}).",
            metadata={"pid": pid},
        )

    def _list(self) -> ToolResult:
        if not _TRACKED_PROCESSES:
            return ToolResult(success=True, output="No tracked processes.")

        lines: list[str] = []
        for pid, info in _TRACKED_PROCESSES.items():
            proc = info.get("process")
            running = proc and proc.returncode is None
            status = "running" if running else "exited"
            elapsed = int(time.time() - info.get("started", 0))
            lines.append(
                f"  PID {pid} [{status}] {info['name']} — "
                f"{info['command']} ({elapsed}s)"
            )

        return ToolResult(
            success=True,
            output=f"Tracked processes ({len(_TRACKED_PROCESSES)}):\n"
            + "\n".join(lines),
            metadata={"count": len(_TRACKED_PROCESSES)},
        )

    async def _logs(self, params: dict[str, Any]) -> ToolResult:
        pid = params.get("pid")
        if pid is None:
            return ToolResult(
                success=False, output="", error="pid is required for logs"
            )

        entry = _TRACKED_PROCESSES.get(pid)
        if not entry:
            return ToolResult(
                success=False, output="",
                error=f"No tracked process with PID {pid}",
            )

        proc = entry.get("process")
        if not proc or not proc.stdout:
            return ToolResult(
                success=True, output="(no output captured)",
                metadata={"pid": pid},
            )

        # Read available output without blocking
        try:
            data = await asyncio.wait_for(proc.stdout.read(65536), timeout=1)
            text = data.decode(errors="replace") if data else "(no output)"
        except asyncio.TimeoutError:
            text = "(no new output)"

        return ToolResult(
            success=True, output=text, metadata={"pid": pid}
        )


# ---------------------------------------------------------------------------
# Environment Management
# ---------------------------------------------------------------------------


class EnvManageTool(AgentTool):
    """Read, write, or list environment variables and .env files."""

    tool_id = "env_manage"
    description = "Read, write, or list environment variables and .env files"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "list", "read_file", "write_file"],
            },
            "key": {
                "type": "string",
                "description": "Variable name (for get/set)",
            },
            "value": {"type": "string", "description": "Value to set"},
            "file": {
                "type": "string",
                "description": ".env file path",
                "default": ".env",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action = params.get("action", "")

        if action == "get":
            key = params.get("key", "")
            if not key:
                return ToolResult(
                    success=False, output="", error="key is required for get"
                )
            value = os.environ.get(key)
            if value is None:
                return ToolResult(success=True, output=f"{key} is not set")
            return ToolResult(
                success=True, output=f"{key}={value}",
                metadata={"key": key, "value": value},
            )

        if action == "set":
            key = params.get("key", "")
            value = params.get("value", "")
            if not key:
                return ToolResult(
                    success=False, output="", error="key is required for set"
                )
            os.environ[key] = value
            return ToolResult(
                success=True, output=f"Set {key}={value}",
                metadata={"key": key, "value": value},
            )

        if action == "list":
            env_vars = sorted(os.environ.items())
            lines = [f"{k}={v}" for k, v in env_vars]
            return ToolResult(
                success=True,
                output="\n".join(lines),
                metadata={"count": len(env_vars)},
            )

        if action == "read_file":
            env_file = Path(params.get("file", ".env"))
            if not env_file.is_file():
                return ToolResult(
                    success=False, output="",
                    error=f"File not found: {env_file}",
                )
            content = env_file.read_text(encoding="utf-8", errors="replace")
            return ToolResult(
                success=True, output=content,
                metadata={"file": str(env_file)},
            )

        if action == "write_file":
            key = params.get("key", "")
            value = params.get("value", "")
            if not key:
                return ToolResult(
                    success=False, output="",
                    error="key is required for write_file",
                )
            env_file = Path(params.get("file", ".env"))
            # Read existing, update or append
            lines: list[str] = []
            found = False
            if env_file.is_file():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith(f"{key}="):
                        lines.append(f"{key}={value}")
                        found = True
                    else:
                        lines.append(line)
            if not found:
                lines.append(f"{key}={value}")
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"Wrote {key}={value} to {env_file}",
                metadata={"file": str(env_file), "key": key},
            )

        return ToolResult(
            success=False, output="", error=f"Unknown action: {action}"
        )


# ---------------------------------------------------------------------------
# Code Analysis
# ---------------------------------------------------------------------------


def _analyze_python_file(file_path: Path) -> dict[str, Any]:
    """Parse a single Python file and extract metrics."""
    source = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return {"error": f"Syntax error in {file_path}"}

    functions: list[str] = []
    classes: list[dict[str, Any]] = []
    imports: list[str] = []
    complexity = 0
    lines = len(source.splitlines())

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            classes.append({"name": node.name, "methods": methods})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
        # Cyclomatic complexity: count branching statements
        if isinstance(
            node,
            ast.If | ast.For | ast.While | ast.ExceptHandler
            | ast.With | ast.Assert,
        ):
            complexity += 1

    return {
        "file": str(file_path),
        "lines": lines,
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "complexity": complexity + 1,  # Base complexity is 1
    }


class CodeAnalyzeTool(AgentTool):
    """Analyze Python code: functions, classes, complexity, imports."""

    tool_id = "code_analyze"
    description = "Analyze Python code: functions, classes, complexity, imports"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to analyze",
            },
            "metrics": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "functions", "classes", "imports", "complexity", "summary",
                    ],
                },
                "default": ["summary"],
            },
        },
        "required": ["path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        target = Path(params.get("path", ""))
        if not target.exists():
            return ToolResult(
                success=False, output="", error=f"Path not found: {target}"
            )

        metrics = params.get("metrics") or ["summary"]

        if target.is_file():
            files = [target]
        else:
            files = sorted(target.rglob("*.py"))

        if not files:
            return ToolResult(
                success=True, output="No Python files found.",
                metadata={"file_count": 0},
            )

        all_results = [_analyze_python_file(f) for f in files]

        # Aggregate
        total_lines = sum(r.get("lines", 0) for r in all_results)
        total_funcs = sum(len(r.get("functions", [])) for r in all_results)
        total_classes = sum(len(r.get("classes", [])) for r in all_results)
        total_complexity = sum(r.get("complexity", 0) for r in all_results)

        output_parts: list[str] = []

        if "summary" in metrics:
            output_parts.append(
                f"Files: {len(files)}  |  Lines: {total_lines}  |  "
                f"Functions: {total_funcs}  |  Classes: {total_classes}  |  "
                f"Complexity: {total_complexity}"
            )

        if "functions" in metrics:
            func_list = []
            for r in all_results:
                for fn in r.get("functions", []):
                    func_list.append(f"  {r.get('file', '?')}::{fn}")
            output_parts.append("Functions:\n" + "\n".join(func_list))

        if "classes" in metrics:
            cls_lines = []
            for r in all_results:
                for cls in r.get("classes", []):
                    methods = ", ".join(cls["methods"][:10])
                    cls_lines.append(
                        f"  {r.get('file', '?')}::{cls['name']} "
                        f"({len(cls['methods'])} methods: {methods})"
                    )
            output_parts.append("Classes:\n" + "\n".join(cls_lines))

        if "imports" in metrics:
            all_imports: set[str] = set()
            for r in all_results:
                all_imports.update(r.get("imports", []))
            output_parts.append(
                "Imports:\n  " + "\n  ".join(sorted(all_imports))
            )

        if "complexity" in metrics and "summary" not in metrics:
            for r in all_results:
                output_parts.append(
                    f"  {r.get('file', '?')}: complexity={r.get('complexity', 0)}"
                )

        return ToolResult(
            success=True,
            output="\n\n".join(output_parts),
            metadata={
                "file_count": len(files),
                "total_lines": total_lines,
                "total_functions": total_funcs,
                "total_classes": total_classes,
                "total_complexity": total_complexity,
            },
        )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


class BenchmarkTool(AgentTool):
    """Run and compare performance benchmarks."""

    tool_id = "benchmark"
    description = "Run and compare performance benchmarks"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Benchmark command to run",
            },
            "iterations": {
                "type": "integer",
                "description": "Number of iterations",
                "default": 3,
            },
            "label": {
                "type": "string",
                "description": "Label for this benchmark run",
            },
        },
        "required": ["command"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        command = params.get("command", "").strip()
        if not command:
            return ToolResult(
                success=False, output="", error="command is required"
            )

        iterations = max(1, params.get("iterations", 3))
        label = params.get("label", command.split()[0])
        cwd = _resolve_cwd(params, context)

        timings: list[float] = []
        last_output = ""

        for i in range(iterations):
            start = time.monotonic()
            try:
                rc, out, err = await _run_cmd(command.split(), cwd, timeout=600)
            except asyncio.TimeoutError:
                return ToolResult(
                    success=False, output="",
                    error=f"Iteration {i + 1} timed out",
                )
            except OSError as exc:
                return ToolResult(success=False, output="", error=str(exc))

            elapsed = time.monotonic() - start
            timings.append(elapsed)
            last_output = out + err
            if rc != 0:
                return ToolResult(
                    success=False,
                    output=last_output,
                    error=f"Command failed on iteration {i + 1} (exit {rc})",
                    metadata={"iteration": i + 1, "returncode": rc},
                )

        avg = statistics.mean(timings)
        med = statistics.median(timings)
        mn, mx = min(timings), max(timings)

        lines = [
            f"Benchmark: {label}",
            f"Command:   {command}",
            f"Iterations: {iterations}",
            f"  Min:    {mn:.3f}s",
            f"  Max:    {mx:.3f}s",
            f"  Avg:    {avg:.3f}s",
            f"  Median: {med:.3f}s",
        ]

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "label": label,
                "iterations": iterations,
                "min": round(mn, 4),
                "max": round(mx, 4),
                "avg": round(avg, 4),
                "median": round(med, 4),
                "timings": [round(t, 4) for t in timings],
            },
        )


# ---------------------------------------------------------------------------
# Documentation Generation
# ---------------------------------------------------------------------------


def _find_undocumented(source: str, filepath: str) -> list[dict[str, Any]]:
    """Find functions and classes without docstrings using ast."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    items: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            has_doc = (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            )
            if not has_doc:
                # Build signature for functions
                sig = ""
                if not isinstance(node, ast.ClassDef):
                    args = []
                    for arg in node.args.args:
                        name = arg.arg
                        annotation = ""
                        if arg.annotation:
                            annotation = ast.unparse(arg.annotation)
                        args.append(f"{name}: {annotation}" if annotation else name)
                    ret = ""
                    if node.returns:
                        ret = f" -> {ast.unparse(node.returns)}"
                    sig = f"({', '.join(args)}){ret}"
                items.append({
                    "name": node.name,
                    "kind": kind,
                    "line": node.lineno,
                    "signature": sig,
                })
    return items


class DocGenerateTool(AgentTool):
    """Generate or update documentation: docstrings, README, changelogs."""

    tool_id = "doc_generate"
    description = (
        "Generate or update documentation: docstrings, README sections, changelogs"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["docstrings", "readme", "changelog"],
                "description": "Documentation action",
            },
            "path": {
                "type": "string",
                "description": "File or directory to document",
            },
            "output_path": {
                "type": "string",
                "description": "Output file path (optional)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": ["action", "path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action = params.get("action", "")
        path = params.get("path", "")

        if action == "docstrings":
            return self._scan_docstrings(path)
        if action == "readme":
            return self._generate_readme(path)
        if action == "changelog":
            cwd = _resolve_cwd(params, context)
            return await self._generate_changelog(cwd, params)

        return ToolResult(
            success=False, output="", error=f"Unknown action: {action}"
        )

    def _scan_docstrings(self, path: str) -> ToolResult:
        """Scan Python files for missing docstrings and generate stubs."""
        target = Path(path)
        if not target.exists():
            return ToolResult(
                success=False, output="", error=f"Path not found: {path}"
            )

        if target.is_file():
            files = [target]
        else:
            files = sorted(target.rglob("*.py"))

        all_missing: list[dict[str, Any]] = []
        for fpath in files:
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            missing = _find_undocumented(source, str(fpath))
            for item in missing:
                item["file"] = str(fpath)
            all_missing.extend(missing)

        if not all_missing:
            return ToolResult(
                success=True,
                output="All functions and classes have docstrings.",
                metadata={"files_scanned": len(files), "missing": 0},
            )

        lines: list[str] = [
            f"Missing docstrings ({len(all_missing)} items in {len(files)} files):\n"
        ]
        for item in all_missing:
            sig = item.get("signature", "")
            lines.append(
                f"  {item['file']}:{item['line']} "
                f"{item['kind']} {item['name']}{sig}"
            )

        # Generate stub docstrings
        lines.append("\nSuggested stubs:")
        for item in all_missing[:20]:
            if item["kind"] == "function":
                lines.append(
                    f'  {item["name"]}: """TODO: Document {item["name"]}."""'
                )
            else:
                lines.append(
                    f'  {item["name"]}: """TODO: Document the {item["name"]} class."""'
                )

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "files_scanned": len(files),
                "missing": len(all_missing),
            },
        )

    def _generate_readme(self, path: str) -> ToolResult:
        """Scan project structure and generate README sections."""
        target = Path(path)
        if not target.is_dir():
            return ToolResult(
                success=False, output="",
                error=f"Not a directory: {path}",
            )

        # Collect project info
        py_files = list(target.rglob("*.py"))
        js_files = list(target.rglob("*.js")) + list(target.rglob("*.ts"))
        test_files = [f for f in py_files if "test" in f.name.lower()]
        has_docker = (target / "Dockerfile").is_file()
        has_requirements = (target / "requirements.txt").is_file()
        has_package_json = (target / "package.json").is_file()

        # Top-level directories
        dirs = sorted(
            d.name for d in target.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )

        sections: list[str] = [f"# {target.name}\n"]

        # Description placeholder
        sections.append("> TODO: Project description\n")

        # Project structure
        sections.append("## Project Structure\n")
        sections.append("```")
        for d in dirs[:20]:
            sections.append(f"├── {d}/")
        sections.append("```\n")

        # Setup
        sections.append("## Setup\n")
        if has_requirements:
            sections.append("```bash")
            sections.append("pip install -r requirements.txt")
            sections.append("```\n")
        if has_package_json:
            sections.append("```bash")
            sections.append("npm install")
            sections.append("```\n")
        if has_docker:
            sections.append("```bash")
            sections.append("docker build -t project .")
            sections.append("```\n")

        # Testing
        if test_files:
            sections.append("## Testing\n")
            sections.append("```bash")
            sections.append("python -m pytest tests/ -v")
            sections.append("```\n")

        # Stats
        sections.append("## Stats\n")
        sections.append(f"- Python files: {len(py_files)}")
        if js_files:
            sections.append(f"- JS/TS files: {len(js_files)}")
        sections.append(f"- Test files: {len(test_files)}")
        sections.append(f"- Directories: {len(dirs)}")

        return ToolResult(
            success=True,
            output="\n".join(sections),
            metadata={
                "py_files": len(py_files),
                "test_files": len(test_files),
                "directories": len(dirs),
            },
        )

    async def _generate_changelog(
        self, cwd: str, params: dict[str, Any]
    ) -> ToolResult:
        """Delegate to ChangelogGenerateTool logic."""
        # Use git log for changelog
        try:
            rc, out, err = await _run_cmd(
                ["git", "--no-pager", "log", "--oneline", "-50"],
                cwd, timeout=30,
            )
        except (asyncio.TimeoutError, OSError) as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if rc != 0:
            return ToolResult(
                success=False, output="",
                error=err or "git log failed",
            )

        return ToolResult(
            success=True,
            output=f"Recent commits:\n{out}",
            metadata={"source": "git log"},
        )


# ---------------------------------------------------------------------------
# Changelog Generation
# ---------------------------------------------------------------------------

# Conventional commit pattern: type(scope): description
_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|chore|ci|build|revert)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?"
    r":\s*(?P<desc>.+)$",
    re.IGNORECASE,
)

_TYPE_LABELS = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "docs": "Documentation",
    "style": "Styling",
    "refactor": "Refactoring",
    "perf": "Performance",
    "test": "Tests",
    "chore": "Chores",
    "ci": "CI/CD",
    "build": "Build",
    "revert": "Reverts",
}


class ChangelogGenerateTool(AgentTool):
    """Generate changelog from git commits since a tag or date."""

    tool_id = "changelog_generate"
    description = "Generate changelog from git commits since a tag or date"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": "Git tag or date to start from (e.g., 'v1.0.0' or '2024-01-01')",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "keep-a-changelog"],
                "description": "Output format (default: markdown)",
            },
            "group_by": {
                "type": "string",
                "enum": ["type", "scope"],
                "description": "Group commits by type or scope (default: type)",
            },
            "cwd": {"type": "string", "description": "Working directory"},
        },
        "required": ["since"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        since = params.get("since", "")
        if not since:
            return ToolResult(
                success=False, output="",
                error="since is required (tag or date)",
            )

        cwd = _resolve_cwd(params, context)
        fmt = params.get("format", "markdown")
        group_by = params.get("group_by", "type")

        # Get commits since the reference
        cmd = [
            "git", "--no-pager", "log",
            f"{since}..HEAD",
            "--format=%H|%an|%aI|%s",
        ]

        try:
            rc, out, err = await _run_cmd(cmd, cwd, timeout=30)
        except (asyncio.TimeoutError, OSError) as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if rc != 0:
            return ToolResult(
                success=False, output="",
                error=err or f"git log failed (is '{since}' a valid ref?)",
            )

        if not out.strip():
            return ToolResult(
                success=True,
                output=f"No commits found since {since}.",
                metadata={"commit_count": 0},
            )

        # Parse commits
        commits: list[dict[str, str]] = []
        for line in out.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            sha, author, date, subject = parts
            commits.append({
                "sha": sha[:8],
                "author": author,
                "date": date[:10],
                "subject": subject,
            })

        # Parse conventional commits
        grouped: dict[str, list[dict[str, str]]] = {}
        ungrouped: list[dict[str, str]] = []

        for commit in commits:
            m = _CONVENTIONAL_RE.match(commit["subject"])
            if m:
                ctype = m.group("type").lower()
                scope = m.group("scope") or ""
                desc = m.group("desc")
                key = scope if group_by == "scope" else ctype
                grouped.setdefault(key, []).append({
                    **commit,
                    "type": ctype,
                    "scope": scope,
                    "desc": desc,
                    "breaking": bool(m.group("breaking")),
                })
            else:
                ungrouped.append(commit)

        # Format output
        if fmt == "keep-a-changelog":
            output = self._format_kac(since, grouped, ungrouped)
        else:
            output = self._format_markdown(since, grouped, ungrouped)

        return ToolResult(
            success=True,
            output=output,
            metadata={
                "commit_count": len(commits),
                "conventional_count": len(commits) - len(ungrouped),
                "groups": list(grouped.keys()),
            },
        )

    def _format_markdown(
        self,
        since: str,
        grouped: dict[str, list[dict[str, str]]],
        ungrouped: list[dict[str, str]],
    ) -> str:
        """Format as simple markdown."""
        lines: list[str] = [f"# Changelog (since {since})\n"]

        for key in sorted(grouped.keys()):
            label = _TYPE_LABELS.get(key, key.title())
            lines.append(f"## {label}\n")
            for c in grouped[key]:
                scope_prefix = f"**{c['scope']}:** " if c.get("scope") else ""
                breaking = "⚠ BREAKING: " if c.get("breaking") else ""
                lines.append(
                    f"- {breaking}{scope_prefix}{c['desc']} "
                    f"({c['sha']}) — {c['author']}"
                )
            lines.append("")

        if ungrouped:
            lines.append("## Other\n")
            for c in ungrouped:
                lines.append(
                    f"- {c['subject']} ({c['sha']}) — {c['author']}"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_kac(
        self,
        since: str,
        grouped: dict[str, list[dict[str, str]]],
        ungrouped: list[dict[str, str]],
    ) -> str:
        """Format using Keep a Changelog convention."""
        lines: list[str] = [
            f"## [Unreleased] - since {since}\n",
        ]

        # KAC categories
        kac_map = {
            "feat": "Added",
            "fix": "Fixed",
            "docs": "Changed",
            "refactor": "Changed",
            "perf": "Changed",
            "revert": "Removed",
        }

        kac_groups: dict[str, list[str]] = {}
        for key, commits in grouped.items():
            kac_category = kac_map.get(key, "Changed")
            for c in commits:
                scope_prefix = f"**{c['scope']}:** " if c.get("scope") else ""
                entry = f"{scope_prefix}{c['desc']}"
                kac_groups.setdefault(kac_category, []).append(entry)

        for category in ["Added", "Changed", "Deprecated", "Removed",
                         "Fixed", "Security"]:
            entries = kac_groups.get(category, [])
            if entries:
                lines.append(f"### {category}\n")
                for entry in entries:
                    lines.append(f"- {entry}")
                lines.append("")

        return "\n".join(lines)
