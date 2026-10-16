"""Tests for cortex.pipeline.crash_handler — crash recovery logic layer."""
from __future__ import annotations

import sqlite3
import pytest

from cortex.memory.sessions import (
    CRASH_THRESHOLD,
    SessionStore,
    _ensure_tables,
)
from cortex.pipeline.crash_handler import (
    CrashDiagnosis,
    CrashHandler,
    CrashPattern,
    RecoveryAction,
    RecoveryStrategy,
    _GRACEFUL_ERROR,
    _SAFE_MODE_ERROR,
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


@pytest.fixture()
def handler(store):
    """CrashHandler wired to the in-memory store."""
    return CrashHandler(store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_incomplete(store: SessionStore, crash_count: int = 0) -> str:
    """Create an active session with a dangling user turn and a given crash count."""
    sid = store.start_session("alice")
    await store.save_turn(sid, "user", "Hello?")
    # Manually set crash_count
    if crash_count > 0:
        store._conn.execute(
            "UPDATE conversation_sessions SET crash_count = ? WHERE id = ?",
            (crash_count, sid),
        )
        store._conn.commit()
    return sid


# ---------------------------------------------------------------------------
# on_startup — recovery decision
# ---------------------------------------------------------------------------

class TestOnStartupNoIncomplete:
    async def test_returns_empty(self, handler: CrashHandler):
        actions = await handler.on_startup()
        assert actions == []


class TestOnStartupResume:
    async def test_low_crash_count_resumes(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = await _setup_incomplete(store, crash_count=0)
        actions = await handler.on_startup()

        assert len(actions) == 1
        assert actions[0].session_id == sid
        assert actions[0].strategy is RecoveryStrategy.RESUME
        assert actions[0].crash_count == 0

    async def test_crash_count_2_still_resumes(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = await _setup_incomplete(store, crash_count=2)
        actions = await handler.on_startup()

        assert actions[0].strategy is RecoveryStrategy.RESUME
        assert actions[0].crash_count == 2


class TestOnStartupRollback:
    async def test_crash_3_rolls_back_1(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = await _setup_incomplete(store, crash_count=3)
        # Add a couple more turns so rollback has something to remove
        await store.save_turn(sid, "assistant", "partial")
        await store.save_turn(sid, "user", "retry")

        actions = await handler.on_startup()
        action = actions[0]
        assert action.strategy is RecoveryStrategy.ROLLBACK
        assert action.rollback_count == 1

        # Verify the last turn was actually removed
        turns = store.get_session(sid)
        assert turns[-1].content != "retry"

    async def test_crash_4_rolls_back_2(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = await _setup_incomplete(store, crash_count=4)
        await store.save_turn(sid, "assistant", "partial1")
        await store.save_turn(sid, "user", "retry1")

        actions = await handler.on_startup()
        assert actions[0].strategy is RecoveryStrategy.ROLLBACK
        assert actions[0].rollback_count == 2

    async def test_rollback_capped_at_3(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """Even with crash_count > 4, rollback is capped at 3 (not safe_mode yet)."""
        sid = await _setup_incomplete(store, crash_count=4)
        actions = await handler.on_startup()
        assert actions[0].rollback_count <= 3


class TestOnStartupSafeMode:
    async def test_threshold_triggers_safe_mode(
        self, handler: CrashHandler, store: SessionStore, db,
    ):
        sid = await _setup_incomplete(store, crash_count=CRASH_THRESHOLD)
        actions = await handler.on_startup()

        assert len(actions) == 1
        assert actions[0].strategy is RecoveryStrategy.SAFE_MODE

    async def test_above_threshold_safe_mode(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = await _setup_incomplete(store, crash_count=CRASH_THRESHOLD + 3)
        actions = await handler.on_startup()
        assert actions[0].strategy is RecoveryStrategy.SAFE_MODE

    async def test_multiple_incomplete_sessions(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """Each incomplete session gets its own recovery action."""
        sid1 = await _setup_incomplete(store, crash_count=0)
        sid2 = await _setup_incomplete(store, crash_count=CRASH_THRESHOLD)

        actions = await handler.on_startup()
        assert len(actions) == 2

        strategies = {a.session_id: a.strategy for a in actions}
        assert strategies[sid1] is RecoveryStrategy.RESUME
        assert strategies[sid2] is RecoveryStrategy.SAFE_MODE


# ---------------------------------------------------------------------------
# wrap_response — crash protection
# ---------------------------------------------------------------------------

class TestWrapResponseSuccess:
    async def test_returns_response(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            return "The time is 3 PM."

        result = await handler.wrap_response(
            sid, gen, user_message="What time is it?",
        )
        assert result == "The time is 3 PM."

    async def test_saves_both_turns(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            return "response"

        await handler.wrap_response(sid, gen, user_message="question")
        turns = store.get_session(sid)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "question"
        assert turns[1].role == "assistant"
        assert turns[1].content == "response"

    async def test_resets_crash_count(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        store.record_crash(sid)
        store.record_crash(sid)
        assert store.get_crash_count(sid) == 2

        async def gen():
            return "ok"

        await handler.wrap_response(sid, gen, user_message="hi")
        assert store.get_crash_count(sid) == 0

    async def test_stores_model_name(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            return "answer"

        await handler.wrap_response(
            sid, gen, user_message="q", model="Qwen3-4B",
        )
        turns = store.get_session(sid)
        assert turns[1].model_used == "Qwen3-4B"

    async def test_no_user_message_skips_user_turn(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            return "background response"

        await handler.wrap_response(sid, gen)
        turns = store.get_session(sid)
        assert len(turns) == 1
        assert turns[0].role == "assistant"


class TestWrapResponseFailure:
    async def test_returns_graceful_error(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            raise RuntimeError("GPU OOM")

        result = await handler.wrap_response(
            sid, gen, user_message="question",
        )
        assert result == _GRACEFUL_ERROR

    async def test_increments_crash_count(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            raise ValueError("broken")

        await handler.wrap_response(sid, gen, user_message="q")
        assert store.get_crash_count(sid) == 1

    async def test_rolls_back_user_turn(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            raise RuntimeError("fail")

        await handler.wrap_response(sid, gen, user_message="bad question")
        turns = store.get_session(sid)
        # The dangling user turn should have been rolled back
        assert len(turns) == 0

    async def test_does_not_save_broken_response(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")

        async def gen():
            raise RuntimeError("fail")

        await handler.wrap_response(sid, gen, user_message="q")
        turns = store.get_session(sid)
        assert not any(t.role == "assistant" for t in turns)

    async def test_no_rollback_without_user_message(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """When user_message is None, don't roll back on failure."""
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "pre-existing")

        async def gen():
            raise RuntimeError("fail")

        await handler.wrap_response(sid, gen)
        turns = store.get_session(sid)
        # pre-existing turn should still be there
        assert len(turns) == 1
        assert turns[0].content == "pre-existing"


class TestWrapResponseSafeMode:
    async def test_refuses_in_safe_mode(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        for _ in range(CRASH_THRESHOLD):
            store.record_crash(sid)

        async def gen():
            return "should not reach this"

        result = await handler.wrap_response(sid, gen, user_message="hi")
        assert result == _SAFE_MODE_ERROR

    async def test_does_not_save_turns_in_safe_mode(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        for _ in range(CRASH_THRESHOLD):
            store.record_crash(sid)

        async def gen():
            return "nope"

        await handler.wrap_response(sid, gen, user_message="hi")
        turns = store.get_session(sid)
        assert len(turns) == 0


# ---------------------------------------------------------------------------
# diagnose — crash pattern analysis
# ---------------------------------------------------------------------------

class TestDiagnoseLongContext:
    async def test_detects_long_context(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        # Create a session with > 20k chars total
        big_msg = "x" * 12_000
        await store.save_turn(sid, "user", big_msg)
        await store.save_turn(sid, "assistant", big_msg)

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.LONG_CONTEXT
        assert "chars" in diag.detail
        assert diag.suggested_fix


class TestDiagnoseRapidFire:
    async def test_detects_rapid_fire(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        now = 1000.0
        # Manually insert turns with close timestamps
        for i in range(3):
            store._conn.execute(
                """INSERT INTO conversation_turns
                   (session_id, turn_index, role, content, tokens_used, timestamp)
                   VALUES (?, ?, 'user', ?, 0, ?)""",
                (sid, i, f"msg-{i}", now + i * 0.5),
            )
        store._conn.commit()

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.RAPID_FIRE


class TestDiagnoseBadInput:
    async def test_detects_empty_input(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", " ")

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.BAD_INPUT

    async def test_detects_single_char(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "x")

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.BAD_INPUT


class TestDiagnoseUnknown:
    async def test_unknown_when_no_pattern(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        await store.save_turn(sid, "user", "Normal question about weather")

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.UNKNOWN

    async def test_returns_last_user_messages(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        for i in range(8):
            await store.save_turn(sid, "user", f"msg-{i}")
        diag = await handler.diagnose(sid)
        # Should contain the last 5 user messages
        assert len(diag.last_user_messages) == 5
        assert diag.last_user_messages[-1] == "msg-7"

    async def test_empty_session(
        self, handler: CrashHandler, store: SessionStore,
    ):
        sid = store.start_session("alice")
        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.UNKNOWN
        assert diag.last_user_messages == []


# ---------------------------------------------------------------------------
# notify_admin
# ---------------------------------------------------------------------------

class TestNotifyAdmin:
    async def test_logs_critical(
        self, handler: CrashHandler, store: SessionStore, caplog,
    ):
        sid = store.start_session("alice")
        diag = CrashDiagnosis(
            session_id=sid,
            crash_count=5,
            pattern=CrashPattern.LONG_CONTEXT,
            detail="100k chars",
            suggested_fix="trim context",
        )

        with caplog.at_level("CRITICAL"):
            await handler.notify_admin(sid, diag)

        assert any(sid in r.message for r in caplog.records)
        assert any("long_context" in r.message for r in caplog.records)

    async def test_no_intercom_does_not_raise(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """notify_admin should never raise, even without intercom."""
        sid = store.start_session("alice")
        diag = CrashDiagnosis(
            session_id=sid,
            crash_count=3,
            pattern=CrashPattern.UNKNOWN,
        )
        # Should complete without error
        await handler.notify_admin(sid, diag)


# ---------------------------------------------------------------------------
# Integration: full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    async def test_crash_then_recover(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """Simulate: crash → startup recovery → successful retry."""
        sid = store.start_session("alice")

        # 1. First attempt crashes
        async def failing_gen():
            raise RuntimeError("OOM")

        result = await handler.wrap_response(sid, failing_gen, user_message="hi")
        assert result == _GRACEFUL_ERROR
        assert store.get_crash_count(sid) == 1

        # 2. Startup detects incomplete (we add another user turn)
        await store.save_turn(sid, "user", "retry")
        actions = await handler.on_startup()
        assert len(actions) == 1
        assert actions[0].strategy is RecoveryStrategy.RESUME

        # 3. Retry succeeds
        async def ok_gen():
            return "All good!"

        result = await handler.wrap_response(sid, ok_gen, user_message="try again")
        assert result == "All good!"
        assert store.get_crash_count(sid) == 0

    async def test_repeated_crashes_to_safe_mode(
        self, handler: CrashHandler, store: SessionStore,
    ):
        """Crash CRASH_THRESHOLD times → safe_mode → refused."""
        sid = store.start_session("alice")

        async def failing():
            raise RuntimeError("always fails")

        for i in range(CRASH_THRESHOLD):
            await handler.wrap_response(sid, failing, user_message=f"attempt-{i}")

        # Now should be in safe_mode
        async def ok_gen():
            return "should not run"

        result = await handler.wrap_response(sid, ok_gen, user_message="please")
        assert result == _SAFE_MODE_ERROR

    async def test_diagnose_and_notify(
        self, handler: CrashHandler, store: SessionStore, caplog,
    ):
        """Full flow: crash → diagnose → notify admin."""
        sid = store.start_session("alice")
        big = "x" * 25_000
        await store.save_turn(sid, "user", big)
        store.record_crash(sid)

        diag = await handler.diagnose(sid)
        assert diag.pattern is CrashPattern.LONG_CONTEXT

        with caplog.at_level("CRITICAL"):
            await handler.notify_admin(sid, diag)
        assert any("long_context" in r.message for r in caplog.records)
