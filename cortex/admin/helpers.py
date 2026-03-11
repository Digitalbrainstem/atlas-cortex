"""Shared helpers for admin sub-routers."""

from __future__ import annotations

import sqlite3

from cortex.auth import require_admin  # noqa: F401 — re-exported for sub-routers
from cortex.db import get_db, init_db
from cortex.auth import seed_admin


def _db() -> sqlite3.Connection:
    init_db()
    conn = get_db()
    seed_admin(conn)
    return conn


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row(cur: sqlite3.Cursor) -> dict | None:
    r = cur.fetchone()
    if r is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, r))
