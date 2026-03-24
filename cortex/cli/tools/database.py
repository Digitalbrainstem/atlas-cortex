"""Database engineering tools: schema inspection, migration, query analysis.

Provides database introspection and management for the agent tool system.
"""

# Module ownership: CLI database engineering tools
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format rows as an aligned text table."""
    if not rows:
        return "(no rows)"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * w for w in widths)
    body = "\n".join(
        "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    )
    return f"{header_line}\n{sep}\n{body}"


# ---------------------------------------------------------------------------
# Schema Inspect
# ---------------------------------------------------------------------------


class SchemaInspectTool(AgentTool):
    """Inspect database schema: tables, columns, indexes, foreign keys."""

    tool_id = "schema_inspect"
    description = "Inspect database schema: tables, columns, indexes, foreign keys"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "db_path": {
                "type": "string",
                "description": "Path to SQLite database",
            },
            "table": {
                "type": "string",
                "description": "Specific table to inspect (optional — all tables if omitted)",
            },
        },
        "required": ["db_path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        db_path = params.get("db_path", "")
        if not db_path:
            return ToolResult(success=False, output="", error="db_path is required")

        if not Path(db_path).is_file():
            return ToolResult(
                success=False, output="", error=f"Database not found: {db_path}"
            )

        table = params.get("table", "")

        try:
            conn = _connect_sqlite(db_path)
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            if table:
                return self._inspect_table(conn, table)
            return self._inspect_all(conn)
        finally:
            conn.close()

    def _inspect_table(self, conn: sqlite3.Connection, table: str) -> ToolResult:
        """Inspect a single table: columns, indexes, foreign keys."""
        sections: list[str] = [f"Table: {table}"]

        # Columns
        try:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if not cols:
            return ToolResult(
                success=False, output="",
                error=f"Table not found: {table}",
            )

        col_rows = [
            [str(c["cid"]), c["name"], c["type"] or "ANY",
             "YES" if c["notnull"] else "NO",
             str(c["dflt_value"]) if c["dflt_value"] is not None else "",
             "PK" if c["pk"] else ""]
            for c in cols
        ]
        sections.append(
            "Columns:\n"
            + _format_table(["#", "Name", "Type", "NotNull", "Default", "PK"], col_rows)
        )

        # Indexes
        indexes = conn.execute(f"PRAGMA index_list('{table}')").fetchall()
        if indexes:
            idx_rows = []
            for idx in indexes:
                idx_info = conn.execute(
                    f"PRAGMA index_info('{idx['name']}')"
                ).fetchall()
                idx_cols = ", ".join(i["name"] for i in idx_info)
                idx_rows.append([
                    idx["name"],
                    "UNIQUE" if idx["unique"] else "",
                    idx_cols,
                ])
            sections.append(
                "Indexes:\n"
                + _format_table(["Name", "Unique", "Columns"], idx_rows)
            )

        # Foreign keys
        fks = conn.execute(f"PRAGMA foreign_key_list('{table}')").fetchall()
        if fks:
            fk_rows = [
                [fk["table"], fk["from"], fk["to"]]
                for fk in fks
            ]
            sections.append(
                "Foreign Keys:\n"
                + _format_table(["Ref Table", "From", "To"], fk_rows)
            )

        return ToolResult(
            success=True,
            output="\n\n".join(sections),
            metadata={
                "table": table,
                "column_count": len(cols),
                "index_count": len(indexes),
                "fk_count": len(fks),
            },
        )

    def _inspect_all(self, conn: sqlite3.Connection) -> ToolResult:
        """List all tables with column counts."""
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

        if not tables:
            return ToolResult(
                success=True, output="No tables found.",
                metadata={"table_count": 0},
            )

        rows: list[list[str]] = []
        for t in tables:
            name = t["name"]
            cols = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
            indexes = conn.execute(f"PRAGMA index_list('{name}')").fetchall()
            row_count = conn.execute(f"SELECT COUNT(*) as cnt FROM '{name}'").fetchone()
            rows.append([
                name,
                str(len(cols)),
                str(len(indexes)),
                str(row_count["cnt"]) if row_count else "0",
            ])

        output = (
            f"Database tables ({len(tables)}):\n"
            + _format_table(["Table", "Columns", "Indexes", "Rows"], rows)
        )

        return ToolResult(
            success=True,
            output=output,
            metadata={"table_count": len(tables)},
        )


# ---------------------------------------------------------------------------
# Migration Generate
# ---------------------------------------------------------------------------


def _parse_create_tables(sql: str) -> dict[str, dict[str, Any]]:
    """Extract table definitions from CREATE TABLE statements."""
    import re

    tables: dict[str, dict[str, Any]] = {}
    # Match CREATE TABLE blocks
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?['\"]?(\w+)['\"]?\s*\((.*?)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        table_name = match.group(1)
        body = match.group(2)
        columns: dict[str, str] = {}
        for line in body.split(","):
            line = line.strip()
            if not line:
                continue
            # Skip constraints
            upper = line.upper()
            if any(upper.startswith(kw) for kw in (
                "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT",
            )):
                continue
            parts = line.split()
            if len(parts) >= 2:
                col_name = parts[0].strip("'\"")
                col_type = parts[1].strip("'\"")
                columns[col_name] = col_type
            elif len(parts) == 1:
                col_name = parts[0].strip("'\"")
                columns[col_name] = "ANY"
        tables[table_name] = {"columns": columns}
    return tables


class MigrationGenerateTool(AgentTool):
    """Generate a SQL migration by comparing current schema to target."""

    tool_id = "migration_generate"
    description = "Generate a SQL migration by comparing current schema to target"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "db_path": {
                "type": "string",
                "description": "Path to the current SQLite database",
            },
            "target_schema": {
                "type": "string",
                "description": "Target schema as SQL string or path to .sql file",
            },
            "output_path": {
                "type": "string",
                "description": "Path to write the migration file (optional)",
            },
        },
        "required": ["db_path", "target_schema"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        db_path = params.get("db_path", "")
        target_schema = params.get("target_schema", "")
        output_path = params.get("output_path", "")

        if not db_path or not target_schema:
            return ToolResult(
                success=False, output="",
                error="db_path and target_schema are required",
            )

        if not Path(db_path).is_file():
            return ToolResult(
                success=False, output="", error=f"Database not found: {db_path}"
            )

        # If target_schema is a file path, read it
        target_path = Path(target_schema)
        if target_path.is_file():
            target_schema = target_path.read_text(encoding="utf-8")

        # Parse target schema
        target_tables = _parse_create_tables(target_schema)
        if not target_tables:
            return ToolResult(
                success=False, output="",
                error="Could not parse any CREATE TABLE statements from target schema",
            )

        # Get current schema
        try:
            conn = _connect_sqlite(db_path)
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            current_tables = self._get_current_tables(conn)
        finally:
            conn.close()

        # Generate migration
        statements: list[str] = []
        current_names = set(current_tables.keys())
        target_names = set(target_tables.keys())

        # New tables
        for table_name in sorted(target_names - current_names):
            cols = target_tables[table_name]["columns"]
            col_defs = ", ".join(
                f"{name} {dtype}" for name, dtype in cols.items()
            )
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {table_name} ({col_defs});"
            )

        # Modified tables — add new columns
        for table_name in sorted(current_names & target_names):
            current_cols = set(current_tables[table_name]["columns"].keys())
            target_cols = target_tables[table_name]["columns"]
            for col_name in sorted(set(target_cols.keys()) - current_cols):
                col_type = target_cols[col_name]
                statements.append(
                    f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type};"
                )

        # Dropped tables (commented out for safety)
        for table_name in sorted(current_names - target_names):
            statements.append(f"-- DROP TABLE IF EXISTS {table_name};")

        if not statements:
            return ToolResult(
                success=True,
                output="Schema is up to date — no migration needed.",
                metadata={"statements": 0},
            )

        migration = "-- Auto-generated migration\n" + "\n".join(statements) + "\n"

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(migration, encoding="utf-8")

        return ToolResult(
            success=True,
            output=migration,
            metadata={
                "statements": len(statements),
                "new_tables": len(target_names - current_names),
                "modified_tables": len(
                    [t for t in current_names & target_names
                     if set(target_tables[t]["columns"].keys())
                     - set(current_tables[t]["columns"].keys())]
                ),
                "output_path": output_path or "(stdout)",
            },
        )

    def _get_current_tables(
        self, conn: sqlite3.Connection
    ) -> dict[str, dict[str, Any]]:
        """Read current schema from the database."""
        tables: dict[str, dict[str, Any]] = {}
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for row in rows:
            name = row["name"]
            cols = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
            tables[name] = {
                "columns": {c["name"]: c["type"] or "ANY" for c in cols},
            }
        return tables


# ---------------------------------------------------------------------------
# Query Explain
# ---------------------------------------------------------------------------


class QueryExplainTool(AgentTool):
    """Explain a SQL query plan and suggest optimizations."""

    tool_id = "query_explain"
    description = "Explain a SQL query plan and suggest optimizations"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "db_path": {
                "type": "string",
                "description": "Path to SQLite database",
            },
            "query": {
                "type": "string",
                "description": "SQL query to explain",
            },
        },
        "required": ["db_path", "query"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        db_path = params.get("db_path", "")
        query = params.get("query", "").strip()

        if not db_path or not query:
            return ToolResult(
                success=False, output="",
                error="db_path and query are required",
            )

        if not Path(db_path).is_file():
            return ToolResult(
                success=False, output="", error=f"Database not found: {db_path}"
            )

        try:
            conn = _connect_sqlite(db_path)
        except sqlite3.Error as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            # Run EXPLAIN QUERY PLAN
            rows = conn.execute(f"EXPLAIN QUERY PLAN {query}").fetchall()
        except sqlite3.Error as exc:
            conn.close()
            return ToolResult(success=False, output="", error=str(exc))

        plan_lines: list[str] = []
        suggestions: list[str] = []
        has_scan = False
        scanned_tables: list[str] = []

        for row in rows:
            detail = row["detail"] if "detail" in row.keys() else str(dict(row))
            plan_lines.append(f"  {detail}")

            detail_upper = detail.upper()
            if "SCAN TABLE" in detail_upper or "SCAN" in detail_upper:
                has_scan = True
                # Extract table name
                parts = detail.split()
                for i, part in enumerate(parts):
                    if part.upper() == "TABLE" and i + 1 < len(parts):
                        scanned_tables.append(parts[i + 1])

        if has_scan:
            for table in scanned_tables:
                suggestions.append(
                    f"Full table scan on '{table}' — consider adding an index "
                    f"on the columns used in WHERE/JOIN clauses"
                )

        # Check for common patterns in the query
        query_upper = query.upper()
        if "SELECT *" in query_upper:
            suggestions.append(
                "SELECT * used — specify only needed columns to reduce I/O"
            )
        if query_upper.count("JOIN") > 2:
            suggestions.append(
                "Multiple JOINs detected — verify join order and ensure "
                "indexed join columns"
            )
        if "LIKE '%" in query_upper:
            suggestions.append(
                "Leading wildcard in LIKE — this prevents index usage; "
                "consider FTS5 for text search"
            )

        conn.close()

        sections = ["Query Plan:", *plan_lines]
        if suggestions:
            sections.append("\nSuggestions:")
            for i, s in enumerate(suggestions, 1):
                sections.append(f"  {i}. {s}")

        return ToolResult(
            success=True,
            output="\n".join(sections),
            metadata={
                "has_full_scan": has_scan,
                "suggestion_count": len(suggestions),
                "scanned_tables": scanned_tables,
            },
        )
