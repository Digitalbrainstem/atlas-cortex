"""Project management tools: task tracking, time estimation, risk assessment.

Provides project management capabilities for the agent tool system.
"""

# Module ownership: CLI project management tools
from __future__ import annotations

import ast
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'todo',
    priority TEXT DEFAULT 'P3',
    assignee TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _get_tasks_db(db_path: str = "") -> sqlite3.Connection:
    """Open (or create) the tasks database."""
    if not db_path:
        atlas_dir = Path(os.environ.get("ATLAS_DIR", "~/.atlas")).expanduser()
        atlas_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(atlas_dir / "tasks.db")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_TASKS_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Task Track
# ---------------------------------------------------------------------------


class TaskTrackTool(AgentTool):
    """Track tasks in a local SQLite database."""

    tool_id = "task_track"
    description = "Track tasks in a local SQLite database (create, list, update, complete)"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "update", "complete", "delete"],
                "description": "Task operation",
            },
            "id": {
                "type": "integer",
                "description": "Task ID (for update/complete/delete)",
            },
            "title": {
                "type": "string",
                "description": "Task title (for create)",
            },
            "description": {
                "type": "string",
                "description": "Task description (optional)",
            },
            "status": {
                "type": "string",
                "enum": ["todo", "in_progress", "done", "blocked"],
                "description": "Task status (for update)",
            },
            "priority": {
                "type": "string",
                "enum": ["P1", "P2", "P3", "P4"],
                "description": "Priority (P1=critical, P4=low)",
            },
            "assignee": {
                "type": "string",
                "description": "Assignee name",
            },
            "db_path": {
                "type": "string",
                "description": "Custom database path (optional)",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action = params.get("action", "")
        db_path = params.get("db_path", "")

        try:
            conn = _get_tasks_db(db_path)
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            if action == "create":
                return self._create(conn, params)
            if action == "list":
                return self._list(conn, params)
            if action == "update":
                return self._update(conn, params)
            if action == "complete":
                return self._complete(conn, params)
            if action == "delete":
                return self._delete(conn, params)
            return ToolResult(
                success=False, output="", error=f"Unknown action: {action}"
            )
        finally:
            conn.close()

    def _create(
        self, conn: sqlite3.Connection, params: dict[str, Any]
    ) -> ToolResult:
        title = params.get("title", "").strip()
        if not title:
            return ToolResult(
                success=False, output="", error="title is required for create"
            )

        desc = params.get("description", "")
        priority = params.get("priority", "P3")
        assignee = params.get("assignee", "")

        cursor = conn.execute(
            "INSERT INTO tasks (title, description, priority, assignee) "
            "VALUES (?, ?, ?, ?)",
            (title, desc, priority, assignee),
        )
        conn.commit()
        task_id = cursor.lastrowid

        return ToolResult(
            success=True,
            output=f"Created task #{task_id}: {title} [{priority}]",
            metadata={"id": task_id, "title": title, "priority": priority},
        )

    def _list(
        self, conn: sqlite3.Connection, params: dict[str, Any]
    ) -> ToolResult:
        status = params.get("status", "")
        priority = params.get("priority", "")

        query = "SELECT * FROM tasks WHERE 1=1"
        bind: list[str] = []
        if status:
            query += " AND status = ?"
            bind.append(status)
        if priority:
            query += " AND priority = ?"
            bind.append(priority)
        query += " ORDER BY priority, created_at DESC"

        rows = conn.execute(query, bind).fetchall()

        if not rows:
            return ToolResult(
                success=True, output="No tasks found.",
                metadata={"count": 0},
            )

        lines: list[str] = [f"Tasks ({len(rows)}):"]
        for row in rows:
            lines.append(
                f"  #{row['id']} [{row['priority']}] {row['title']} "
                f"({row['status']})"
                + (f" @{row['assignee']}" if row["assignee"] else "")
            )

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"count": len(rows)},
        )

    def _update(
        self, conn: sqlite3.Connection, params: dict[str, Any]
    ) -> ToolResult:
        task_id = params.get("id")
        if task_id is None:
            return ToolResult(
                success=False, output="", error="id is required for update"
            )

        updates: list[str] = []
        values: list[Any] = []
        for field in ("title", "description", "status", "priority", "assignee"):
            if field in params and params[field] is not None:
                updates.append(f"{field} = ?")
                values.append(params[field])

        if not updates:
            return ToolResult(
                success=False, output="",
                error="No fields to update",
            )

        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(task_id)

        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        conn.commit()

        return ToolResult(
            success=True,
            output=f"Updated task #{task_id}",
            metadata={"id": task_id},
        )

    def _complete(
        self, conn: sqlite3.Connection, params: dict[str, Any]
    ) -> ToolResult:
        task_id = params.get("id")
        if task_id is None:
            return ToolResult(
                success=False, output="", error="id is required for complete"
            )

        conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (task_id,),
        )
        conn.commit()

        return ToolResult(
            success=True,
            output=f"Completed task #{task_id}",
            metadata={"id": task_id},
        )

    def _delete(
        self, conn: sqlite3.Connection, params: dict[str, Any]
    ) -> ToolResult:
        task_id = params.get("id")
        if task_id is None:
            return ToolResult(
                success=False, output="", error="id is required for delete"
            )

        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()

        return ToolResult(
            success=True,
            output=f"Deleted task #{task_id}",
            metadata={"id": task_id},
        )


# ---------------------------------------------------------------------------
# Time Estimate
# ---------------------------------------------------------------------------


class TimeEstimateTool(AgentTool):
    """Estimate implementation time based on code complexity and scope."""

    tool_id = "time_estimate"
    description = "Estimate implementation time based on code complexity and scope"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Task description",
            },
            "path": {
                "type": "string",
                "description": "Path to relevant code (file or directory)",
            },
        },
        "required": ["description"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        desc = params.get("description", "").strip()
        if not desc:
            return ToolResult(
                success=False, output="",
                error="description is required",
            )

        code_path = params.get("path", "")
        metrics = self._analyze_scope(code_path) if code_path else {}

        # Heuristic estimation
        base_hours = 2.0
        assumptions: list[str] = []

        # Scale by file count
        file_count = metrics.get("file_count", 0)
        if file_count > 20:
            base_hours += file_count * 0.3
            assumptions.append(f"{file_count} files in scope — large change set")
        elif file_count > 5:
            base_hours += file_count * 0.2
            assumptions.append(f"{file_count} files in scope — medium change set")
        elif file_count > 0:
            base_hours += file_count * 0.5
            assumptions.append(f"{file_count} files in scope — small change set")

        # Scale by complexity
        complexity = metrics.get("complexity", 0)
        if complexity > 50:
            base_hours *= 1.5
            assumptions.append("High code complexity")
        elif complexity > 20:
            base_hours *= 1.2
            assumptions.append("Moderate code complexity")

        # Scale by LOC
        total_lines = metrics.get("total_lines", 0)
        if total_lines > 5000:
            base_hours *= 1.3
            assumptions.append(f"Large codebase ({total_lines} lines)")

        # Test coverage factor
        test_files = metrics.get("test_files", 0)
        if test_files == 0 and file_count > 0:
            base_hours *= 1.2
            assumptions.append("No test files detected — add time for test writing")

        # Description keywords
        desc_lower = desc.lower()
        if any(kw in desc_lower for kw in ("refactor", "migration", "rewrite")):
            base_hours *= 1.5
            assumptions.append("Refactoring/migration work — higher risk")
        if any(kw in desc_lower for kw in ("security", "auth", "encrypt")):
            base_hours *= 1.3
            assumptions.append("Security-sensitive — extra review time")
        if any(kw in desc_lower for kw in ("fix", "bug", "patch")):
            base_hours *= 0.7
            assumptions.append("Bug fix — typically faster than new features")

        # Confidence
        if metrics:
            confidence = "medium" if file_count > 10 else "high"
        else:
            confidence = "low"
            assumptions.append("No code path provided — estimate is rough")

        # Format
        if base_hours < 4:
            estimate = f"{base_hours:.1f} hours"
        elif base_hours < 16:
            estimate = f"{base_hours / 8:.1f} days"
        else:
            estimate = f"{base_hours / 8:.1f} days ({base_hours:.0f} hours)"

        lines = [
            f"Estimate: {estimate}",
            f"Confidence: {confidence}",
            "",
            "Assumptions:",
        ]
        for a in assumptions:
            lines.append(f"  • {a}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "hours": round(base_hours, 1),
                "confidence": confidence,
                **metrics,
            },
        )

    def _analyze_scope(self, code_path: str) -> dict[str, Any]:
        """Analyze the code scope for estimation."""
        path = Path(code_path)
        if not path.exists():
            return {}

        if path.is_file():
            files = [path]
        else:
            files = list(path.rglob("*.py"))

        total_lines = 0
        complexity = 0
        test_files = 0

        for fpath in files:
            if "test" in fpath.name.lower():
                test_files += 1
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
                total_lines += len(source.splitlines())
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(
                        node,
                        ast.If | ast.For | ast.While | ast.ExceptHandler,
                    ):
                        complexity += 1
            except (SyntaxError, OSError):
                continue

        return {
            "file_count": len(files),
            "total_lines": total_lines,
            "complexity": complexity,
            "test_files": test_files,
        }


# ---------------------------------------------------------------------------
# Risk Assess
# ---------------------------------------------------------------------------


class RiskAssessTool(AgentTool):
    """Assess risks of a code change: blast radius, test coverage, dependency impact."""

    tool_id = "risk_assess"
    description = (
        "Assess risks of a code change: blast radius, "
        "test coverage, dependency impact"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of files being changed",
            },
            "description": {
                "type": "string",
                "description": "Description of the change",
            },
        },
        "required": ["files"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        files = params.get("files") or []
        desc = params.get("description", "")

        if not files:
            return ToolResult(
                success=False, output="",
                error="files list is required",
            )

        risk_score = 0
        factors: list[dict[str, str]] = []
        changed_paths = [Path(f) for f in files]

        # Factor 1: Number of files changed
        num_files = len(files)
        if num_files > 10:
            risk_score += 3
            factors.append({
                "factor": "Large change set",
                "detail": f"{num_files} files being modified",
                "severity": "high",
            })
        elif num_files > 5:
            risk_score += 2
            factors.append({
                "factor": "Medium change set",
                "detail": f"{num_files} files being modified",
                "severity": "medium",
            })
        else:
            risk_score += 1
            factors.append({
                "factor": "Small change set",
                "detail": f"{num_files} files",
                "severity": "low",
            })

        # Factor 2: Sensitive files
        sensitive_patterns = [
            "auth", "security", "crypto", "password", "secret",
            "migration", "schema", "config", "deploy", "docker",
            "ci", "cd", "pipeline",
        ]
        sensitive_hits: list[str] = []
        for fpath in changed_paths:
            name_lower = str(fpath).lower()
            for pattern in sensitive_patterns:
                if pattern in name_lower:
                    sensitive_hits.append(str(fpath))
                    break

        if sensitive_hits:
            risk_score += 3
            factors.append({
                "factor": "Sensitive files touched",
                "detail": ", ".join(sensitive_hits[:5]),
                "severity": "high",
            })

        # Factor 3: Check for imports (blast radius)
        dependents = self._find_dependents(changed_paths)
        if dependents > 10:
            risk_score += 3
            factors.append({
                "factor": "High blast radius",
                "detail": f"{dependents} files import from changed modules",
                "severity": "high",
            })
        elif dependents > 3:
            risk_score += 2
            factors.append({
                "factor": "Moderate blast radius",
                "detail": f"{dependents} files import from changed modules",
                "severity": "medium",
            })

        # Factor 4: Test coverage
        has_tests = any(
            "test" in str(f).lower() for f in changed_paths
        )
        if not has_tests and num_files > 1:
            risk_score += 2
            factors.append({
                "factor": "No test changes",
                "detail": "Changes don't include test files",
                "severity": "medium",
            })

        # Factor 5: Init/config files
        init_files = [f for f in changed_paths if f.name in (
            "__init__.py", "setup.py", "pyproject.toml", "package.json",
            "Makefile", "Dockerfile",
        )]
        if init_files:
            risk_score += 2
            factors.append({
                "factor": "Infrastructure files changed",
                "detail": ", ".join(str(f) for f in init_files),
                "severity": "medium",
            })

        # Calculate risk level
        if risk_score >= 8:
            risk_level = "HIGH"
        elif risk_score >= 4:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        # Format output
        lines = [
            f"Risk Level: {risk_level} (score: {risk_score})",
            "",
            "Risk Factors:",
        ]
        for factor in factors:
            lines.append(
                f"  [{factor['severity'].upper()}] {factor['factor']}: "
                f"{factor['detail']}"
            )

        # Recommendations
        lines.append("\nRecommendations:")
        if risk_level == "HIGH":
            lines.append("  • Require thorough code review by 2+ reviewers")
            lines.append("  • Run full test suite before merging")
            lines.append("  • Consider staged rollout")
        elif risk_level == "MEDIUM":
            lines.append("  • Standard code review recommended")
            lines.append("  • Run affected test suites")
        else:
            lines.append("  • Standard review process is sufficient")

        if not has_tests and num_files > 1:
            lines.append("  • Add tests for changed code paths")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "risk_level": risk_level,
                "risk_score": risk_score,
                "factors": factors,
                "dependents": dependents,
            },
        )

    def _find_dependents(self, changed_paths: list[Path]) -> int:
        """Count files that import from the changed modules."""
        # Extract module names from changed file paths
        module_names: set[str] = set()
        for fpath in changed_paths:
            if fpath.suffix == ".py":
                # Convert path to module name (e.g., cortex/cli/tools/dev.py → cortex.cli.tools.dev)
                parts = list(fpath.with_suffix("").parts)
                module_names.add(".".join(parts))
                # Also add the last component alone (common in from X import Y)
                module_names.add(fpath.stem)

        if not module_names:
            return 0

        # Search for imports in nearby Python files
        search_roots: set[Path] = set()
        for fpath in changed_paths:
            parent = fpath.parent
            if parent.exists():
                search_roots.add(parent)
            grandparent = parent.parent
            if grandparent.exists():
                search_roots.add(grandparent)

        dependents: set[str] = set()
        for root in search_roots:
            for py_file in root.rglob("*.py"):
                if py_file in changed_paths:
                    continue
                try:
                    source = py_file.read_text(encoding="utf-8", errors="ignore")
                    for mod_name in module_names:
                        if mod_name in source:
                            dependents.add(str(py_file))
                            break
                except OSError:
                    continue

        return len(dependents)
