"""Remember bugs encountered and fixes applied."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ErrorFix:
    """A recorded error → fix mapping."""

    error: str
    fix: str
    file_pattern: str = ""
    library: str = ""
    count: int = 1
    last_seen: float = 0.0


# ---------------------------------------------------------------------------
# Persistent error memory
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_error_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    error       TEXT NOT NULL,
    fix         TEXT NOT NULL,
    file_pattern TEXT DEFAULT '',
    library     TEXT DEFAULT '',
    count       INTEGER DEFAULT 1,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cem_error ON coding_error_memory(error);
CREATE INDEX IF NOT EXISTS idx_cem_library ON coding_error_memory(library);
"""


class ErrorMemory:
    """Remember bugs encountered and fixes applied.

    Persists to SQLite.  When no database path is given it falls back to
    an in-memory dict (useful in tests and ephemeral runs).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._conn: sqlite3.Connection | None = None
        self._mem: dict[str, ErrorFix] = {}
        if db_path:
            self._init_db(db_path)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_db(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, error: str, fix: str, file_pattern: str = "", library: str = "") -> None:
        """Store an error → fix mapping."""
        now = time.time()

        if self._conn:
            row = self._conn.execute(
                "SELECT id, count FROM coding_error_memory WHERE error = ? LIMIT 1",
                (error,),
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE coding_error_memory SET count = count + 1, last_seen = ?, fix = ? WHERE id = ?",
                    (now, fix, row["id"]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO coding_error_memory (error, fix, file_pattern, library, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (error, fix, file_pattern, library, now, now),
                )
            self._conn.commit()
        else:
            existing = self._mem.get(error)
            if existing:
                existing.count += 1
                existing.last_seen = now
                existing.fix = fix
            else:
                self._mem[error] = ErrorFix(
                    error=error,
                    fix=fix,
                    file_pattern=file_pattern,
                    library=library,
                    count=1,
                    last_seen=now,
                )

        log.debug("Error recorded: %s → %s", error[:60], fix[:60])

    def get_known_fix(self, error: str) -> str | None:
        """Return the last known fix for an error, or ``None``."""
        if self._conn:
            row = self._conn.execute(
                "SELECT fix FROM coding_error_memory WHERE error = ? ORDER BY last_seen DESC LIMIT 1",
                (error,),
            ).fetchone()
            return row["fix"] if row else None

        entry = self._mem.get(error)
        return entry.fix if entry else None

    def get_common_errors(self, library: str) -> list[ErrorFix]:
        """Return frequent errors for a library, most common first."""
        if self._conn:
            rows = self._conn.execute(
                "SELECT error, fix, file_pattern, library, count, last_seen "
                "FROM coding_error_memory WHERE library = ? ORDER BY count DESC LIMIT 20",
                (library,),
            ).fetchall()
            return [
                ErrorFix(
                    error=r["error"],
                    fix=r["fix"],
                    file_pattern=r["file_pattern"],
                    library=r["library"],
                    count=r["count"],
                    last_seen=r["last_seen"],
                )
                for r in rows
            ]

        return sorted(
            [ef for ef in self._mem.values() if ef.library == library],
            key=lambda e: e.count,
            reverse=True,
        )

    def get_all(self) -> list[ErrorFix]:
        """Return all recorded errors."""
        if self._conn:
            rows = self._conn.execute(
                "SELECT error, fix, file_pattern, library, count, last_seen "
                "FROM coding_error_memory ORDER BY count DESC",
            ).fetchall()
            return [
                ErrorFix(
                    error=r["error"],
                    fix=r["fix"],
                    file_pattern=r["file_pattern"],
                    library=r["library"],
                    count=r["count"],
                    last_seen=r["last_seen"],
                )
                for r in rows
            ]
        return list(self._mem.values())
