"""Session Memory — conversation persistence and crash recovery for Atlas Cortex.

Stores every conversation turn to SQLite so sessions can be resumed after
restarts, crashes, or device hops.  Provides FTS5 search across all past
conversations and a circuit-breaker that flips to safe_mode after repeated
crashes.

Usage::

    store = SessionStore()        # uses get_db() by default
    sid = store.start_session("user-alice", room="kitchen")
    await store.save_turn(sid, "user", "What time is it?")
    await store.save_turn(sid, "assistant", "It is 3 PM.", model="Qwen3-4B", tokens=12)
    turns = store.get_session(sid)
    store.archive_session(sid)
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from cortex.db import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    """A single conversation turn."""
    id: int
    session_id: str
    turn_index: int
    role: str
    content: str
    model_used: str | None = None
    tokens_used: int = 0
    timestamp: float = 0.0


@dataclass
class Session:
    """Conversation session metadata."""
    id: str
    user_id: str
    room: str | None = None
    status: str = "active"
    crash_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    turns: list[Turn] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    room        TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    crash_count INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cs_user_status
    ON conversation_sessions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_cs_updated
    ON conversation_sessions(updated_at);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    turn_index  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    model_used  TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    timestamp   REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_ct_session
    ON conversation_turns(session_id, turn_index);

CREATE VIRTUAL TABLE IF NOT EXISTS session_turn_fts USING fts5(
    session_id, role, content,
    tokenize='porter unicode61'
);
"""


def _ensure_tables(conn: Any) -> None:
    """Create session tables if they don't exist (idempotent)."""
    conn.executescript(_SESSION_SCHEMA)
    conn.commit()


def _fts_sanitize(query: str) -> str:
    """Sanitize free text for safe use in FTS5 MATCH."""
    clean = ""
    for ch in query:
        if ch.isalnum() or ch in " _-":
            clean += ch
        else:
            clean += " "
    tokens = [t.strip() for t in clean.split() if len(t.strip()) >= 2]
    if not tokens:
        return ""
    return " OR ".join(tokens)


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

CRASH_THRESHOLD = 5


class SessionStore:
    """Conversation session persistence backed by SQLite."""

    def __init__(self, conn: Any | None = None) -> None:
        self._conn = conn or get_db()
        _ensure_tables(self._conn)

    # -- lifecycle -----------------------------------------------------------

    def start_session(self, user_id: str, room: str | None = None) -> str:
        """Create a new conversation session. Returns the session id."""
        session_id = uuid.uuid4().hex[:16]
        now = time.time()
        try:
            self._conn.execute(
                """INSERT INTO conversation_sessions
                   (id, user_id, room, status, crash_count, created_at, updated_at)
                   VALUES (?, ?, ?, 'active', 0, ?, ?)""",
                (session_id, user_id, room, now, now),
            )
            self._conn.commit()
        except Exception:
            logger.exception("Failed to start session")
            raise
        return session_id

    async def save_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str | None = None,
        tokens: int = 0,
    ) -> None:
        """Persist a single conversation turn (best-effort, never raises)."""
        try:
            now = time.time()
            # Determine next turn_index
            row = self._conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) FROM conversation_turns "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            turn_index = (row[0] if row else -1) + 1

            self._conn.execute(
                """INSERT INTO conversation_turns
                   (session_id, turn_index, role, content, model_used, tokens_used, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, turn_index, role, content, model, tokens, now),
            )
            # FTS5 mirror
            self._conn.execute(
                "INSERT INTO session_turn_fts (session_id, role, content) "
                "VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            # Touch session updated_at
            self._conn.execute(
                "UPDATE conversation_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()
        except Exception:
            logger.exception("save_turn failed for session %s", session_id)

    # -- retrieval -----------------------------------------------------------

    def get_session(self, session_id: str) -> list[Turn]:
        """Return all turns for a session ordered by turn_index."""
        rows = self._conn.execute(
            """SELECT id, session_id, turn_index, role, content,
                      model_used, tokens_used, timestamp
               FROM conversation_turns
               WHERE session_id = ?
               ORDER BY turn_index""",
            (session_id,),
        ).fetchall()
        return [_row_to_turn(r) for r in rows]

    def get_active_session(self, user_id: str) -> str | None:
        """Return the most-recently-updated active session for a user."""
        row = self._conn.execute(
            """SELECT id FROM conversation_sessions
               WHERE user_id = ? AND status = 'active'
               ORDER BY updated_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return row["id"] if row else None

    def resume_session(
        self, session_id: str, max_turns: int = 50
    ) -> list[Turn]:
        """Load the most recent *max_turns* turns for context injection."""
        rows = self._conn.execute(
            """SELECT id, session_id, turn_index, role, content,
                      model_used, tokens_used, timestamp
               FROM conversation_turns
               WHERE session_id = ?
               ORDER BY turn_index DESC
               LIMIT ?""",
            (session_id, max_turns),
        ).fetchall()
        # Return in chronological order
        return [_row_to_turn(r) for r in reversed(rows)]

    def archive_session(self, session_id: str) -> None:
        """Mark a session as archived (cold storage)."""
        try:
            now = time.time()
            self._conn.execute(
                "UPDATE conversation_sessions SET status = 'archived', updated_at = ? "
                "WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()
        except Exception:
            logger.exception("archive_session failed for %s", session_id)

    # -- search --------------------------------------------------------------

    def search_sessions(
        self, query: str, user_id: str | None = None
    ) -> list[Session]:
        """FTS5 search across all session turn content."""
        safe = _fts_sanitize(query)
        if not safe:
            return []

        user_filter = ""
        params: list[Any] = [safe]
        if user_id:
            user_filter = "AND s.user_id = ?"
            params.append(user_id)

        rows = self._conn.execute(
            f"""SELECT DISTINCT s.id, s.user_id, s.room, s.status,
                       s.crash_count, s.created_at, s.updated_at
                FROM session_turn_fts f
                JOIN conversation_sessions s ON s.id = f.session_id
                WHERE session_turn_fts MATCH ?
                {user_filter}
                ORDER BY s.updated_at DESC
                LIMIT 50""",
            tuple(params),
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    # -- crash recovery ------------------------------------------------------

    def detect_incomplete(self) -> list[Session]:
        """Find active sessions whose last turn is a user message with no
        assistant reply — a likely crash or timeout."""
        rows = self._conn.execute(
            """SELECT s.id, s.user_id, s.room, s.status,
                      s.crash_count, s.created_at, s.updated_at
               FROM conversation_sessions s
               WHERE s.status = 'active'
                 AND EXISTS (
                     SELECT 1 FROM conversation_turns t
                     WHERE t.session_id = s.id
                 )
                 AND (
                     SELECT role FROM conversation_turns t2
                     WHERE t2.session_id = s.id
                     ORDER BY t2.turn_index DESC LIMIT 1
                 ) = 'user'
            """,
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def get_crash_count(self, session_id: str) -> int:
        """Return the crash counter for *session_id*."""
        row = self._conn.execute(
            "SELECT crash_count FROM conversation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return row["crash_count"] if row else 0

    def record_crash(self, session_id: str) -> None:
        """Increment the crash counter; flip to safe_mode at threshold."""
        try:
            self._conn.execute(
                "UPDATE conversation_sessions "
                "SET crash_count = crash_count + 1, updated_at = ? "
                "WHERE id = ?",
                (time.time(), session_id),
            )
            self._conn.commit()
            count = self.get_crash_count(session_id)
            if count >= CRASH_THRESHOLD:
                self._conn.execute(
                    "UPDATE conversation_sessions "
                    "SET status = 'safe_mode', updated_at = ? "
                    "WHERE id = ?",
                    (time.time(), session_id),
                )
                self._conn.commit()
                logger.warning(
                    "Session %s entered safe_mode after %d crashes",
                    session_id,
                    count,
                )
        except Exception:
            logger.exception("record_crash failed for %s", session_id)

    def rollback_turns(self, session_id: str, n: int = 1) -> None:
        """Remove the last *n* turns from a session for recovery."""
        try:
            rows = self._conn.execute(
                """SELECT id, content FROM conversation_turns
                   WHERE session_id = ?
                   ORDER BY turn_index DESC LIMIT ?""",
                (session_id, n),
            ).fetchall()
            if not rows:
                return
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            # Delete from FTS mirror
            for r in rows:
                self._conn.execute(
                    "DELETE FROM session_turn_fts "
                    "WHERE session_id = ? AND content = ?",
                    (session_id, r["content"]),
                )
            # Delete actual turns
            self._conn.execute(
                f"DELETE FROM conversation_turns WHERE id IN ({placeholders})",
                ids,
            )
            self._conn.commit()
        except Exception:
            logger.exception("rollback_turns failed for %s", session_id)


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_session_store: SessionStore | None = None


def get_session_store() -> SessionStore | None:
    """Return the global SessionStore instance (if initialised)."""
    return _session_store


def set_session_store(store: SessionStore) -> None:
    """Set the global SessionStore instance."""
    global _session_store
    _session_store = store


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------

def _row_to_turn(row: Any) -> Turn:
    return Turn(
        id=row["id"],
        session_id=row["session_id"],
        turn_index=row["turn_index"],
        role=row["role"],
        content=row["content"],
        model_used=row["model_used"],
        tokens_used=row["tokens_used"],
        timestamp=row["timestamp"],
    )


def _row_to_session(row: Any) -> Session:
    return Session(
        id=row["id"],
        user_id=row["user_id"],
        room=row["room"],
        status=row["status"],
        crash_count=row["crash_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
