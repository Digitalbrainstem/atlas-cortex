"""Development tools: test runner, build, lint, docker, database, packages.

Provides project development workflow tools for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — development workflow
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
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
