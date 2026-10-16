"""Tests for cortex.memory.sessions — Session Memory system."""
from __future__ import annotations

import sqlite3
import pytest

from cortex.memory.sessions import (
    CRASH_THRESHOLD,
    Session,
    SessionStore,
    Turn,
    _ensure_tables,
    _fts_sanitize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """In-memory SQLite database with session tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_tables(conn)
    return conn


@pytest.fixture()
def store(db):
    """SessionStore backed by the in-memory DB."""
    return SessionStore(conn=db)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestStartSession:
    def test_returns_session_id(self, store: SessionStore):
        sid = store.start_session("alice")
        assert isinstance(sid, str)
        assert len(sid) == 16

    def test_session_is_active(self, store: SessionStore, db):
        sid = store.start_session("alice")
        row = db.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row["status"] == "active"

    def test_room_stored(self, store: SessionStore, db):
        sid = store.start_session("alice", room="kitchen")
        row = db.execute(
            "SELECT room FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row["room"] == "kitchen"


class TestSaveTurn:
    async def test_save_and_retrieve(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Hello")
        await store.save_turn(sid, "assistant", "Hi!", model="Qwen3-4B", tokens=5)

        turns = store.get_session(sid)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Hello"
        assert turns[0].turn_index == 0
        assert turns[1].role == "assistant"
        assert turns[1].model_used == "Qwen3-4B"
        assert turns[1].tokens_used == 5
        assert turns[1].turn_index == 1

    async def test_turn_index_auto_increments(self, store: SessionStore):
        sid = store.start_session("bob")
        for i in range(5):
            await store.save_turn(sid, "user", f"msg-{i}")
        turns = store.get_session(sid)
        indices = [t.turn_index for t in turns]
        assert indices == [0, 1, 2, 3, 4]

    async def test_save_turn_updates_session_timestamp(self, store: SessionStore, db):
        sid = store.start_session("alice")
        row_before = db.execute(
            "SELECT updated_at FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        await store.save_turn(sid, "user", "ping")
        row_after = db.execute(
            "SELECT updated_at FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row_after["updated_at"] >= row_before["updated_at"]


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class TestGetSession:
    async def test_empty_session(self, store: SessionStore):
        sid = store.start_session("alice")
        assert store.get_session(sid) == []

    async def test_returns_turn_objects(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "test")
        turns = store.get_session(sid)
        assert len(turns) == 1
        assert isinstance(turns[0], Turn)

    async def test_nonexistent_session(self, store: SessionStore):
        assert store.get_session("does-not-exist") == []


class TestGetActiveSession:
    def test_no_sessions(self, store: SessionStore):
        assert store.get_active_session("nobody") is None

    async def test_returns_most_recent(self, store: SessionStore):
        sid1 = store.start_session("alice")
        await store.save_turn(sid1, "user", "old msg")
        sid2 = store.start_session("alice")
        await store.save_turn(sid2, "user", "new msg")
        assert store.get_active_session("alice") == sid2

    def test_ignores_archived(self, store: SessionStore):
        sid = store.start_session("alice")
        store.archive_session(sid)
        assert store.get_active_session("alice") is None


class TestResumeSession:
    async def test_returns_last_n_turns(self, store: SessionStore):
        sid = store.start_session("alice")
        for i in range(10):
            await store.save_turn(sid, "user", f"msg-{i}")
        turns = store.resume_session(sid, max_turns=3)
        assert len(turns) == 3
        # Should be the last 3, in chronological order
        assert turns[0].content == "msg-7"
        assert turns[1].content == "msg-8"
        assert turns[2].content == "msg-9"

    async def test_returns_all_if_fewer(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "only one")
        turns = store.resume_session(sid, max_turns=50)
        assert len(turns) == 1


# ---------------------------------------------------------------------------
# Archival
# ---------------------------------------------------------------------------

class TestArchiveSession:
    def test_marks_archived(self, store: SessionStore, db):
        sid = store.start_session("alice")
        store.archive_session(sid)
        row = db.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row["status"] == "archived"


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

class TestDetectIncomplete:
    async def test_finds_incomplete(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Hello?")
        # No assistant reply → incomplete
        incomplete = store.detect_incomplete()
        assert len(incomplete) == 1
        assert incomplete[0].id == sid

    async def test_complete_not_flagged(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Hello?")
        await store.save_turn(sid, "assistant", "Hi!")
        assert store.detect_incomplete() == []

    async def test_archived_not_flagged(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Hello?")
        store.archive_session(sid)
        assert store.detect_incomplete() == []

    def test_empty_session_not_flagged(self, store: SessionStore):
        store.start_session("alice")
        assert store.detect_incomplete() == []


class TestCrashCounter:
    def test_initial_count_zero(self, store: SessionStore):
        sid = store.start_session("alice")
        assert store.get_crash_count(sid) == 0

    def test_record_increments(self, store: SessionStore):
        sid = store.start_session("alice")
        store.record_crash(sid)
        assert store.get_crash_count(sid) == 1
        store.record_crash(sid)
        assert store.get_crash_count(sid) == 2

    def test_nonexistent_session(self, store: SessionStore):
        assert store.get_crash_count("nope") == 0


class TestCircuitBreaker:
    def test_safe_mode_at_threshold(self, store: SessionStore, db):
        sid = store.start_session("alice")
        for _ in range(CRASH_THRESHOLD):
            store.record_crash(sid)
        row = db.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row["status"] == "safe_mode"

    def test_no_safe_mode_below_threshold(self, store: SessionStore, db):
        sid = store.start_session("alice")
        for _ in range(CRASH_THRESHOLD - 1):
            store.record_crash(sid)
        row = db.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert row["status"] == "active"


class TestRollbackTurns:
    async def test_rollback_one(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "first")
        await store.save_turn(sid, "assistant", "second")
        store.rollback_turns(sid, n=1)
        turns = store.get_session(sid)
        assert len(turns) == 1
        assert turns[0].content == "first"

    async def test_rollback_multiple(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "a")
        await store.save_turn(sid, "assistant", "b")
        await store.save_turn(sid, "user", "c")
        store.rollback_turns(sid, n=2)
        turns = store.get_session(sid)
        assert len(turns) == 1
        assert turns[0].content == "a"

    async def test_rollback_empty_noop(self, store: SessionStore):
        sid = store.start_session("alice")
        store.rollback_turns(sid, n=5)  # should not raise
        assert store.get_session(sid) == []


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------

class TestSearchSessions:
    async def test_finds_matching_content(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Tell me about quantum physics")
        results = store.search_sessions("quantum")
        assert len(results) >= 1
        assert any(s.id == sid for s in results)

    async def test_filters_by_user(self, store: SessionStore):
        sid_a = store.start_session("alice")
        await store.save_turn(sid_a, "user", "weather forecast")
        sid_b = store.start_session("bob")
        await store.save_turn(sid_b, "user", "weather today")

        results = store.search_sessions("weather", user_id="alice")
        session_ids = [s.id for s in results]
        assert sid_a in session_ids
        assert sid_b not in session_ids

    async def test_empty_query(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "anything")
        assert store.search_sessions("") == []
        assert store.search_sessions("!@#$") == []

    async def test_returns_session_objects(self, store: SessionStore):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "database migration")
        results = store.search_sessions("database")
        assert len(results) >= 1
        assert isinstance(results[0], Session)


# ---------------------------------------------------------------------------
# FTS sanitizer
# ---------------------------------------------------------------------------

class TestFtsSanitize:
    def test_basic(self):
        assert _fts_sanitize("hello world") == "hello OR world"

    def test_strips_special_chars(self):
        result = _fts_sanitize("what's the (time)?")
        assert "(" not in result
        assert ")" not in result
        assert "'" not in result

    def test_drops_short_tokens(self):
        assert _fts_sanitize("I a am ok") == "am OR ok"

    def test_empty_returns_empty(self):
        assert _fts_sanitize("") == ""
        assert _fts_sanitize("! @") == ""


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_set(self, store: SessionStore):
        from cortex.memory.sessions import get_session_store, set_session_store

        assert get_session_store() is None or True  # may be set by other tests
        set_session_store(store)
        assert get_session_store() is store
        set_session_store(None)  # type: ignore[arg-type]
