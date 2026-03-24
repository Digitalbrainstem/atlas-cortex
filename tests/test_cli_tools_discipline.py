"""Tests for discipline-specific CLI tools: database, security, DevOps, docs, PM."""

from __future__ import annotations

import json
import os
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cortex.cli.tools import get_default_registry

# ---------------------------------------------------------------------------
# Database tools
# ---------------------------------------------------------------------------

from cortex.cli.tools.database import (
    MigrationGenerateTool,
    QueryExplainTool,
    SchemaInspectTool,
)


class TestSchemaInspectTool:
    @pytest.fixture()
    def tool(self):
        return SchemaInspectTool()

    @pytest.fixture()
    def sample_db(self, tmp_path: Path) -> str:
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE users ("
            "  id INTEGER PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  email TEXT UNIQUE"
            ")"
        )
        conn.execute(
            "CREATE TABLE orders ("
            "  id INTEGER PRIMARY KEY,"
            "  user_id INTEGER REFERENCES users(id),"
            "  total REAL DEFAULT 0.0"
            ")"
        )
        conn.execute("CREATE INDEX idx_orders_user ON orders(user_id)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice', 'alice@test.com')")
        conn.execute("INSERT INTO users VALUES (2, 'Bob', 'bob@test.com')")
        conn.commit()
        conn.close()
        return db_path

    async def test_inspect_all_tables(self, tool: SchemaInspectTool, sample_db: str):
        result = await tool.execute({"db_path": sample_db})
        assert result.success
        assert "users" in result.output
        assert "orders" in result.output
        assert result.metadata["table_count"] == 2

    async def test_inspect_single_table(self, tool: SchemaInspectTool, sample_db: str):
        result = await tool.execute({"db_path": sample_db, "table": "users"})
        assert result.success
        assert "name" in result.output
        assert "email" in result.output
        assert result.metadata["column_count"] == 3

    async def test_inspect_with_indexes(self, tool: SchemaInspectTool, sample_db: str):
        result = await tool.execute({"db_path": sample_db, "table": "orders"})
        assert result.success
        assert "idx_orders_user" in result.output

    async def test_inspect_nonexistent_table(self, tool: SchemaInspectTool, sample_db: str):
        result = await tool.execute({"db_path": sample_db, "table": "nonexistent"})
        assert not result.success
        assert "not found" in result.error.lower()

    async def test_inspect_nonexistent_db(self, tool: SchemaInspectTool):
        result = await tool.execute({"db_path": "/no/such/database.db"})
        assert not result.success

    async def test_missing_db_path(self, tool: SchemaInspectTool):
        result = await tool.execute({"db_path": ""})
        assert not result.success


class TestMigrationGenerateTool:
    @pytest.fixture()
    def tool(self):
        return MigrationGenerateTool()

    @pytest.fixture()
    def current_db(self, tmp_path: Path) -> str:
        db_path = str(tmp_path / "current.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()
        return db_path

    async def test_add_new_table(self, tool: MigrationGenerateTool, current_db: str):
        target = (
            "CREATE TABLE users (id INTEGER, name TEXT);\n"
            "CREATE TABLE posts (id INTEGER, title TEXT, body TEXT);"
        )
        result = await tool.execute({
            "db_path": current_db,
            "target_schema": target,
        })
        assert result.success
        assert "CREATE TABLE" in result.output
        assert "posts" in result.output

    async def test_add_column(self, tool: MigrationGenerateTool, current_db: str):
        target = "CREATE TABLE users (id INTEGER, name TEXT, email TEXT);"
        result = await tool.execute({
            "db_path": current_db,
            "target_schema": target,
        })
        assert result.success
        assert "ALTER TABLE" in result.output
        assert "email" in result.output

    async def test_no_changes_needed(self, tool: MigrationGenerateTool, current_db: str):
        target = "CREATE TABLE users (id INTEGER, name TEXT);"
        result = await tool.execute({
            "db_path": current_db,
            "target_schema": target,
        })
        assert result.success
        assert "up to date" in result.output.lower()

    async def test_requires_confirmation(self, tool: MigrationGenerateTool):
        assert tool.requires_confirmation is True

    async def test_write_to_file(
        self, tool: MigrationGenerateTool, current_db: str, tmp_path: Path,
    ):
        target = "CREATE TABLE users (id INTEGER, name TEXT, email TEXT);"
        out_path = str(tmp_path / "migration.sql")
        result = await tool.execute({
            "db_path": current_db,
            "target_schema": target,
            "output_path": out_path,
        })
        assert result.success
        assert Path(out_path).is_file()
        content = Path(out_path).read_text()
        assert "ALTER TABLE" in content


class TestQueryExplainTool:
    @pytest.fixture()
    def tool(self):
        return QueryExplainTool()

    @pytest.fixture()
    def sample_db(self, tmp_path: Path) -> str:
        db_path = str(tmp_path / "explain.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, category TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'Widget', 'A')")
        conn.commit()
        conn.close()
        return db_path

    async def test_explain_simple_query(self, tool: QueryExplainTool, sample_db: str):
        result = await tool.execute({
            "db_path": sample_db,
            "query": "SELECT * FROM items WHERE id = 1",
        })
        assert result.success
        assert "Query Plan" in result.output

    async def test_detect_full_scan(self, tool: QueryExplainTool, sample_db: str):
        result = await tool.execute({
            "db_path": sample_db,
            "query": "SELECT * FROM items WHERE name = 'Widget'",
        })
        assert result.success
        # Should detect a full table scan (no index on name)
        assert result.metadata.get("has_full_scan") is True

    async def test_suggest_select_star(self, tool: QueryExplainTool, sample_db: str):
        result = await tool.execute({
            "db_path": sample_db,
            "query": "SELECT * FROM items",
        })
        assert result.success
        assert "SELECT *" in result.output or "specify only needed" in result.output

    async def test_invalid_query(self, tool: QueryExplainTool, sample_db: str):
        result = await tool.execute({
            "db_path": sample_db,
            "query": "SELECT * FROM nonexistent_table",
        })
        assert not result.success

    async def test_missing_params(self, tool: QueryExplainTool):
        result = await tool.execute({"db_path": "", "query": ""})
        assert not result.success


# ---------------------------------------------------------------------------
# Security tools
# ---------------------------------------------------------------------------

from cortex.cli.tools.security import (
    PermissionAuditTool,
    SecretScanTool,
    VulnScanTool,
)


class TestSecretScanTool:
    @pytest.fixture()
    def tool(self):
        return SecretScanTool()

    async def test_detect_aws_key(self, tool: SecretScanTool, tmp_path: Path):
        secret_file = tmp_path / "config.py"
        secret_file.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert result.metadata["findings"] > 0
        assert "AWS" in result.output

    async def test_detect_github_token(self, tool: SecretScanTool, tmp_path: Path):
        secret_file = tmp_path / "env.sh"
        secret_file.write_text('export TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234\n')
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert result.metadata["findings"] > 0

    async def test_detect_private_key(self, tool: SecretScanTool, tmp_path: Path):
        key_file = tmp_path / "key.txt"
        key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n")
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert result.metadata["findings"] > 0

    async def test_detect_password(self, tool: SecretScanTool, tmp_path: Path):
        code_file = tmp_path / "db.py"
        code_file.write_text('password = "SuperSecret123"\n')
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert result.metadata["findings"] > 0

    async def test_clean_directory(self, tool: SecretScanTool, tmp_path: Path):
        clean_file = tmp_path / "hello.py"
        clean_file.write_text("print('Hello, world!')\n")
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert result.metadata["findings"] == 0

    async def test_custom_pattern(self, tool: SecretScanTool, tmp_path: Path):
        file = tmp_path / "data.txt"
        file.write_text("CUSTOM_SECRET_ABC123\n")
        result = await tool.execute({
            "path": str(tmp_path),
            "patterns": [r"CUSTOM_SECRET_\w+"],
        })
        assert result.success
        assert result.metadata["findings"] > 0

    async def test_nonexistent_path(self, tool: SecretScanTool):
        result = await tool.execute({"path": "/no/such/dir"})
        assert not result.success

    async def test_invalid_custom_pattern(self, tool: SecretScanTool, tmp_path: Path):
        result = await tool.execute({
            "path": str(tmp_path),
            "patterns": ["[invalid"],
        })
        assert not result.success


class TestVulnScanTool:
    @pytest.fixture()
    def tool(self):
        return VulnScanTool()

    async def test_detect_python_project(self, tool: VulnScanTool, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("httpx>=0.24\n")
        # Mock pip audit (may not be installed)
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"No vulnerabilities found\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"path": str(tmp_path)})

        assert result.success
        assert "python" in result.metadata.get("detected", [])

    async def test_detect_node_project(self, tool: VulnScanTool, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"test"}\n')
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"found 0 vulnerabilities\n", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({"path": str(tmp_path)})

        assert result.success
        assert "node" in result.metadata.get("detected", [])

    async def test_no_project_files(self, tool: VulnScanTool, tmp_path: Path):
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert "No supported project" in result.output


class TestPermissionAuditTool:
    @pytest.fixture()
    def tool(self):
        return PermissionAuditTool()

    async def test_audit_files(self, tool: PermissionAuditTool, tmp_path: Path):
        normal = tmp_path / "normal.txt"
        normal.write_text("hello")
        result = await tool.execute({
            "path": str(tmp_path),
            "check_type": "files",
        })
        assert result.success

    async def test_detect_world_writable(self, tool: PermissionAuditTool, tmp_path: Path):
        writable = tmp_path / "writable.txt"
        writable.write_text("data")
        writable.chmod(0o666)
        result = await tool.execute({
            "path": str(tmp_path),
            "check_type": "files",
        })
        assert result.success
        assert result.metadata["issue_count"] > 0

    async def test_ports_check(self, tool: PermissionAuditTool, tmp_path: Path):
        result = await tool.execute({
            "path": str(tmp_path),
            "check_type": "ports",
        })
        assert result.success

    async def test_all_checks(self, tool: PermissionAuditTool, tmp_path: Path):
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success


# ---------------------------------------------------------------------------
# DevOps tools
# ---------------------------------------------------------------------------

from cortex.cli.tools.devops import (
    IncidentTimelineTool,
    LogAnalyzeTool,
    MetricsQueryTool,
)


class TestLogAnalyzeTool:
    @pytest.fixture()
    def tool(self):
        return LogAnalyzeTool()

    async def test_analyze_python_log(self, tool: LogAnalyzeTool, tmp_path: Path):
        log_file = tmp_path / "app.log"
        log_file.write_text(textwrap.dedent("""\
            2024-01-15 10:00:00,123 INFO Starting server
            2024-01-15 10:00:01,456 INFO Listening on port 8080
            2024-01-15 10:01:00,789 ERROR Database connection failed
            2024-01-15 10:01:01,000 ERROR Timeout connecting to postgres
            2024-01-15 10:02:00,000 WARN High memory usage detected
            2024-01-15 10:03:00,000 INFO Request processed
        """))
        result = await tool.execute({"path": str(log_file)})
        assert result.success
        assert result.metadata["level_counts"].get("ERROR", 0) == 2
        assert result.metadata["level_counts"].get("INFO", 0) >= 2

    async def test_analyze_json_log(self, tool: LogAnalyzeTool, tmp_path: Path):
        log_file = tmp_path / "app.json.log"
        log_file.write_text(
            '{"timestamp":"2024-01-15T10:00:00","level":"ERROR","message":"fail"}\n'
            '{"timestamp":"2024-01-15T10:01:00","level":"INFO","message":"ok"}\n'
        )
        result = await tool.execute({"path": str(log_file)})
        assert result.success
        assert result.metadata["format"] == "json"

    async def test_analyze_with_filter(self, tool: LogAnalyzeTool, tmp_path: Path):
        log_file = tmp_path / "app.log"
        log_file.write_text(
            "2024-01-15 10:00:00 INFO start\n"
            "2024-01-15 10:01:00 ERROR database fail\n"
            "2024-01-15 10:02:00 INFO done\n"
        )
        result = await tool.execute({
            "path": str(log_file),
            "pattern": "database",
        })
        assert result.success

    async def test_nonexistent_log(self, tool: LogAnalyzeTool):
        result = await tool.execute({"path": "/no/such/file.log"})
        assert not result.success

    async def test_empty_log(self, tool: LogAnalyzeTool, tmp_path: Path):
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        result = await tool.execute({"path": str(log_file)})
        assert result.success
        assert "empty" in result.output.lower()

    async def test_detect_stack_traces(self, tool: LogAnalyzeTool, tmp_path: Path):
        log_file = tmp_path / "trace.log"
        log_file.write_text(textwrap.dedent("""\
            2024-01-15 10:00:00 ERROR Something went wrong
            Traceback (most recent call last):
              File "app.py", line 42, in main
                raise ValueError("oops")
            ValueError: oops
            2024-01-15 10:01:00 INFO recovered
        """))
        result = await tool.execute({"path": str(log_file)})
        assert result.success
        assert result.metadata["stack_traces"] >= 1


class TestMetricsQueryTool:
    @pytest.fixture()
    def tool(self):
        return MetricsQueryTool()

    async def test_cpu_metric(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "cpu"})
        assert result.success
        # Should work on Linux (reads /proc/stat or getloadavg)
        assert "CPU" in result.output or "Load" in result.output

    async def test_memory_metric(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "memory"})
        assert result.success

    async def test_disk_metric(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "disk"})
        assert result.success

    async def test_all_metrics(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "all"})
        assert result.success

    async def test_unknown_metric(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "quantum"})
        assert not result.success

    async def test_process_metric_no_pid(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "process"})
        assert result.success
        assert "pid" in result.output.lower()

    async def test_process_metric_own_pid(self, tool: MetricsQueryTool):
        result = await tool.execute({"metric": "process", "pid": os.getpid()})
        assert result.success
        assert "python" in result.output.lower() or "pytest" in result.output.lower()


class TestIncidentTimelineTool:
    @pytest.fixture()
    def tool(self):
        return IncidentTimelineTool()

    async def test_git_events(self, tool: IncidentTimelineTool, tmp_path: Path):
        """Build timeline from git in a temp repo."""
        import asyncio
        import subprocess

        # Set up a temp git repo with a commit
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), capture_output=True,
        )
        (tmp_path / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=str(tmp_path), capture_output=True,
        )

        result = await tool.execute({
            "start_time": "7d ago",
            "sources": ["git"],
            "cwd": str(tmp_path),
        })
        assert result.success
        assert result.metadata["event_count"] >= 1

    async def test_invalid_start_time(self, tool: IncidentTimelineTool):
        result = await tool.execute({"start_time": "not_a_time"})
        assert not result.success

    async def test_log_events(self, tool: IncidentTimelineTool, tmp_path: Path):
        log_file = tmp_path / "app.log"
        log_file.write_text(
            "2024-01-15 10:00:00 ERROR something failed\n"
            "2024-01-15 10:01:00 CRITICAL total meltdown\n"
        )
        result = await tool.execute({
            "start_time": "2024-01-01",
            "end_time": "2025-01-01",
            "sources": ["logs"],
            "log_path": str(log_file),
        })
        assert result.success


# ---------------------------------------------------------------------------
# Documentation tools (in dev.py)
# ---------------------------------------------------------------------------

from cortex.cli.tools.dev import ChangelogGenerateTool, DocGenerateTool


class TestDocGenerateTool:
    @pytest.fixture()
    def tool(self):
        return DocGenerateTool()

    async def test_scan_missing_docstrings(self, tool: DocGenerateTool, tmp_path: Path):
        py_file = tmp_path / "example.py"
        py_file.write_text(textwrap.dedent("""\
            def undocumented_function(x, y):
                return x + y

            class UndocumentedClass:
                def method(self):
                    pass

            def documented_function():
                \"\"\"This one is fine.\"\"\"
                pass
        """))
        result = await tool.execute({
            "action": "docstrings",
            "path": str(tmp_path),
        })
        assert result.success
        assert result.metadata["missing"] >= 2
        assert "undocumented_function" in result.output
        assert "UndocumentedClass" in result.output

    async def test_all_documented(self, tool: DocGenerateTool, tmp_path: Path):
        py_file = tmp_path / "good.py"
        py_file.write_text(textwrap.dedent("""\
            def good():
                \"\"\"Documented.\"\"\"
                pass

            class GoodClass:
                \"\"\"Also documented.\"\"\"
                pass
        """))
        result = await tool.execute({
            "action": "docstrings",
            "path": str(tmp_path),
        })
        assert result.success
        assert result.metadata["missing"] == 0

    async def test_readme_generation(self, tool: DocGenerateTool, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test(): pass\n")
        (tmp_path / "requirements.txt").write_text("httpx\n")
        result = await tool.execute({
            "action": "readme",
            "path": str(tmp_path),
        })
        assert result.success
        assert "Setup" in result.output
        assert "pip install" in result.output

    async def test_nonexistent_path(self, tool: DocGenerateTool):
        result = await tool.execute({
            "action": "docstrings",
            "path": "/no/such/dir",
        })
        assert not result.success


class TestChangelogGenerateTool:
    @pytest.fixture()
    def tool(self):
        return ChangelogGenerateTool()

    async def test_parse_conventional_commits(
        self, tool: ChangelogGenerateTool, tmp_path: Path,
    ):
        """Test changelog generation with mock git output."""
        git_output = (
            "abc12345|Alice|2024-03-01T10:00:00|feat(auth): add JWT support\n"
            "def67890|Bob|2024-03-02T11:00:00|fix: resolve login crash\n"
            "ghi11111|Alice|2024-03-03T12:00:00|docs: update README\n"
            "jkl22222|Charlie|2024-03-04T13:00:00|chore: bump dependencies\n"
            "mno33333|Bob|2024-03-05T14:00:00|some non-conventional commit\n"
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(git_output.encode(), b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "since": "v1.0.0",
                "cwd": str(tmp_path),
            })

        assert result.success
        assert result.metadata["commit_count"] == 5
        assert result.metadata["conventional_count"] == 4
        assert "Features" in result.output
        assert "Bug Fixes" in result.output

    async def test_keep_a_changelog_format(
        self, tool: ChangelogGenerateTool, tmp_path: Path,
    ):
        git_output = (
            "abc12345|Alice|2024-03-01|feat: new feature\n"
            "def67890|Bob|2024-03-02|fix: bug fix\n"
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(git_output.encode(), b"")
        )
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "since": "v1.0.0",
                "format": "keep-a-changelog",
                "cwd": str(tmp_path),
            })

        assert result.success
        assert "Added" in result.output
        assert "Fixed" in result.output

    async def test_no_commits(self, tool: ChangelogGenerateTool, tmp_path: Path):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute({
                "since": "v99.0.0",
                "cwd": str(tmp_path),
            })

        assert result.success
        assert "No commits" in result.output

    async def test_missing_since(self, tool: ChangelogGenerateTool):
        result = await tool.execute({"since": ""})
        assert not result.success


# ---------------------------------------------------------------------------
# Project management tools
# ---------------------------------------------------------------------------

from cortex.cli.tools.project import (
    RiskAssessTool,
    TaskTrackTool,
    TimeEstimateTool,
)


class TestTaskTrackTool:
    @pytest.fixture()
    def tool(self):
        return TaskTrackTool()

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> str:
        return str(tmp_path / "tasks.db")

    async def test_create_task(self, tool: TaskTrackTool, db_path: str):
        result = await tool.execute({
            "action": "create",
            "title": "Implement feature X",
            "priority": "P1",
            "db_path": db_path,
        })
        assert result.success
        assert result.metadata["id"] is not None
        assert "P1" in result.output

    async def test_list_tasks(self, tool: TaskTrackTool, db_path: str):
        await tool.execute({
            "action": "create", "title": "Task A", "priority": "P1",
            "db_path": db_path,
        })
        await tool.execute({
            "action": "create", "title": "Task B", "priority": "P3",
            "db_path": db_path,
        })
        result = await tool.execute({
            "action": "list", "db_path": db_path,
        })
        assert result.success
        assert result.metadata["count"] == 2
        assert "Task A" in result.output
        assert "Task B" in result.output

    async def test_update_task(self, tool: TaskTrackTool, db_path: str):
        create = await tool.execute({
            "action": "create", "title": "To update",
            "db_path": db_path,
        })
        task_id = create.metadata["id"]
        result = await tool.execute({
            "action": "update",
            "id": task_id,
            "status": "in_progress",
            "db_path": db_path,
        })
        assert result.success

    async def test_complete_task(self, tool: TaskTrackTool, db_path: str):
        create = await tool.execute({
            "action": "create", "title": "To complete",
            "db_path": db_path,
        })
        task_id = create.metadata["id"]
        result = await tool.execute({
            "action": "complete", "id": task_id,
            "db_path": db_path,
        })
        assert result.success
        assert "Completed" in result.output

    async def test_delete_task(self, tool: TaskTrackTool, db_path: str):
        create = await tool.execute({
            "action": "create", "title": "To delete",
            "db_path": db_path,
        })
        task_id = create.metadata["id"]
        result = await tool.execute({
            "action": "delete", "id": task_id,
            "db_path": db_path,
        })
        assert result.success

        # Verify deleted
        list_result = await tool.execute({
            "action": "list", "db_path": db_path,
        })
        assert "To delete" not in list_result.output

    async def test_create_without_title(self, tool: TaskTrackTool, db_path: str):
        result = await tool.execute({
            "action": "create", "title": "",
            "db_path": db_path,
        })
        assert not result.success

    async def test_filter_by_status(self, tool: TaskTrackTool, db_path: str):
        await tool.execute({
            "action": "create", "title": "Done task",
            "db_path": db_path,
        })
        create = await tool.execute({
            "action": "create", "title": "Active task",
            "db_path": db_path,
        })
        await tool.execute({
            "action": "complete", "id": 1,
            "db_path": db_path,
        })

        result = await tool.execute({
            "action": "list", "status": "todo",
            "db_path": db_path,
        })
        assert result.success
        assert result.metadata["count"] == 1
        assert "Active task" in result.output


class TestTimeEstimateTool:
    @pytest.fixture()
    def tool(self):
        return TimeEstimateTool()

    async def test_estimate_with_code_path(
        self, tool: TimeEstimateTool, tmp_path: Path,
    ):
        # Create some Python files
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "utils.py").write_text(
            "def helper():\n    if True:\n        pass\n"
        )
        result = await tool.execute({
            "description": "Add authentication module",
            "path": str(tmp_path),
        })
        assert result.success
        assert result.metadata["hours"] > 0
        assert result.metadata["confidence"] in ("low", "medium", "high")

    async def test_estimate_without_path(self, tool: TimeEstimateTool):
        result = await tool.execute({
            "description": "Fix a small bug in the login page",
        })
        assert result.success
        assert "Estimate" in result.output
        assert result.metadata["confidence"] == "low"

    async def test_security_task_multiplier(self, tool: TimeEstimateTool):
        result = await tool.execute({
            "description": "Implement security audit for authentication",
        })
        assert result.success
        # Security tasks should have higher estimates
        assert result.metadata["hours"] > 2.0

    async def test_empty_description(self, tool: TimeEstimateTool):
        result = await tool.execute({"description": ""})
        assert not result.success


class TestRiskAssessTool:
    @pytest.fixture()
    def tool(self):
        return RiskAssessTool()

    async def test_small_change(self, tool: RiskAssessTool):
        result = await tool.execute({
            "files": ["src/utils.py"],
            "description": "Minor refactoring",
        })
        assert result.success
        assert result.metadata["risk_level"] in ("LOW", "MEDIUM", "HIGH")

    async def test_sensitive_files(self, tool: RiskAssessTool):
        result = await tool.execute({
            "files": [
                "cortex/security/auth.py",
                "cortex/security/crypto.py",
                "config/deploy.yml",
            ],
            "description": "Update authentication",
        })
        assert result.success
        # Should detect sensitive files
        assert any(
            f["factor"] == "Sensitive files touched"
            for f in result.metadata["factors"]
        )

    async def test_large_change_set(self, tool: RiskAssessTool):
        files = [f"src/module_{i}.py" for i in range(15)]
        result = await tool.execute({
            "files": files,
            "description": "Large refactoring",
        })
        assert result.success
        assert result.metadata["risk_score"] >= 3

    async def test_no_test_changes(self, tool: RiskAssessTool):
        result = await tool.execute({
            "files": ["src/main.py", "src/utils.py"],
            "description": "Code change without tests",
        })
        assert result.success
        assert any(
            "test" in f["factor"].lower()
            for f in result.metadata["factors"]
        )

    async def test_empty_files(self, tool: RiskAssessTool):
        result = await tool.execute({"files": []})
        assert not result.success

    async def test_infrastructure_files(self, tool: RiskAssessTool):
        result = await tool.execute({
            "files": ["Dockerfile", "pyproject.toml", "__init__.py"],
        })
        assert result.success
        assert any(
            "infrastructure" in f["factor"].lower()
            for f in result.metadata["factors"]
        )


# ---------------------------------------------------------------------------
# Registry integration — verify all new tools appear
# ---------------------------------------------------------------------------


class TestDisciplineToolsRegistry:
    def test_new_tools_registered(self):
        registry = get_default_registry()
        tool_ids = {t.tool_id for t in registry.list_tools()}

        new_tools = {
            # Database
            "schema_inspect", "migration_generate", "query_explain",
            # Security
            "secret_scan", "vuln_scan", "permission_audit",
            # DevOps
            "log_analyze", "metrics_query", "incident_timeline",
            # Docs
            "doc_generate", "changelog_generate",
            # Project management
            "task_track", "time_estimate", "risk_assess",
        }

        missing = new_tools - tool_ids
        assert not missing, f"Missing tools in registry: {missing}"

    def test_schemas_valid(self):
        registry = get_default_registry()
        schemas = registry.get_function_schemas()
        # Should have at least 31 original + 14 new = 45
        assert len(schemas) >= 45
        for s in schemas:
            assert s["type"] == "function"
            fn = s["function"]
            assert isinstance(fn["name"], str)
            assert isinstance(fn["description"], str)
            assert isinstance(fn["parameters"], dict)
