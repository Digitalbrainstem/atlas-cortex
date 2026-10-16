"""Crash detection, recovery, and circuit breaker for Atlas conversations.

Sits on top of :class:`~cortex.memory.sessions.SessionStore` and provides
the *logic* layer: startup recovery, response wrapping with crash protection,
pattern diagnosis, and admin alerting.

Usage::

    handler = CrashHandler(store)
    actions = await handler.on_startup()       # check for leftover crashes
    response = await handler.wrap_response(     # protect LLM generation
        session_id, generate_fn,
    )
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from cortex.memory.sessions import CRASH_THRESHOLD, Session, SessionStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class RecoveryStrategy(Enum):
    """What to do with a crashed session."""
    RESUME = "resume"
    ROLLBACK = "rollback"
    SAFE_MODE = "safe_mode"
    NONE = "none"


class CrashPattern(Enum):
    """Recognised patterns that cause repeated crashes."""
    LONG_CONTEXT = "long_context"
    PLUGIN_CRASH = "plugin_crash"
    BAD_INPUT = "bad_input"
    RAPID_FIRE = "rapid_fire"
    UNKNOWN = "unknown"


@dataclass
class RecoveryAction:
    """Decision taken for a single crashed session."""
    session_id: str
    strategy: RecoveryStrategy
    crash_count: int = 0
    rollback_count: int = 0
    message: str = ""


@dataclass
class CrashDiagnosis:
    """Analysis of why a session keeps crashing."""
    session_id: str
    crash_count: int
    pattern: CrashPattern
    detail: str = ""
    suggested_fix: str = ""
    last_user_messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_ROLLBACK_MIN_CRASHES = 3
_ROLLBACK_MAX_CRASHES = 4
_LONG_CONTEXT_CHAR_THRESHOLD = 20_000
_RAPID_FIRE_INTERVAL_SECS = 2.0
_GRACEFUL_ERROR = (
    "I'm sorry, I ran into a problem generating a response. "
    "Please try again in a moment."
)
_SAFE_MODE_ERROR = (
    "This conversation has been placed in safe mode after repeated errors. "
    "An admin has been notified."
)


# ---------------------------------------------------------------------------
# CrashHandler
# ---------------------------------------------------------------------------

class CrashHandler:
    """Automatic crash detection, recovery, and circuit breaker."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    # -- startup recovery ----------------------------------------------------

    async def on_startup(self) -> list[RecoveryAction]:
        """Detect incomplete sessions and decide recovery actions.

        Called once during Atlas startup.  For each session whose last turn
        is a user message with no assistant reply:

        * crash_count < 3  → resume from last good state
        * crash_count 3–4  → rollback 1–3 turns, then resume
        * crash_count >= 5 → enter safe_mode, notify admin
        """
        incomplete = self._store.detect_incomplete()
        if not incomplete:
            logger.info("Startup recovery: no incomplete sessions found")
            return []

        logger.info(
            "Startup recovery: %d incomplete session(s) detected",
            len(incomplete),
        )

        actions: list[RecoveryAction] = []
        for session in incomplete:
            action = self._decide_recovery(session)
            self._apply_recovery(action)
            actions.append(action)
            logger.info(
                "Session %s: strategy=%s crash_count=%d rollback=%d — %s",
                action.session_id,
                action.strategy.value,
                action.crash_count,
                action.rollback_count,
                action.message,
            )
        return actions

    def _decide_recovery(self, session: Session) -> RecoveryAction:
        """Choose a recovery strategy based on crash_count."""
        count = session.crash_count

        if count >= CRASH_THRESHOLD:
            return RecoveryAction(
                session_id=session.id,
                strategy=RecoveryStrategy.SAFE_MODE,
                crash_count=count,
                message=f"Entered safe_mode after {count} crashes",
            )

        if count >= _ROLLBACK_MIN_CRASHES:
            # Rollback proportional to crash severity (1–3 turns)
            rollback_n = min(count - _ROLLBACK_MIN_CRASHES + 1, 3)
            return RecoveryAction(
                session_id=session.id,
                strategy=RecoveryStrategy.ROLLBACK,
                crash_count=count,
                rollback_count=rollback_n,
                message=f"Rolling back {rollback_n} turn(s) after {count} crashes",
            )

        return RecoveryAction(
            session_id=session.id,
            strategy=RecoveryStrategy.RESUME,
            crash_count=count,
            message="Resuming from last good turn",
        )

    def _apply_recovery(self, action: RecoveryAction) -> None:
        """Execute the chosen recovery strategy against the store."""
        sid = action.session_id

        if action.strategy is RecoveryStrategy.SAFE_MODE:
            self._store.record_crash(sid)
            return

        if action.strategy is RecoveryStrategy.ROLLBACK:
            self._store.rollback_turns(sid, n=action.rollback_count)
            return

        # RESUME — nothing extra to do; pipeline will pick up where it left off.

    # -- response wrapping ---------------------------------------------------

    async def wrap_response(
        self,
        session_id: str,
        generate_fn: Callable[[], Awaitable[str]],
        *,
        user_message: str | None = None,
        model: str | None = None,
    ) -> str:
        """Wrap LLM generation with crash protection.

        1. Save the user turn **before** calling the LLM.
        2. Try generating the response.
        3. On success → save assistant turn, reset crash count.
        4. On failure → record crash, return graceful error message.

        Parameters
        ----------
        session_id:
            Conversation session to record turns against.
        generate_fn:
            Async callable that produces the full assistant response text.
        user_message:
            If provided, persisted as a user turn before generation.
        model:
            Model identifier stored alongside the assistant turn.
        """
        store = self._store

        # Check safe_mode first
        if self._is_safe_mode(session_id):
            logger.warning("Session %s is in safe_mode — refusing generation", session_id)
            return _SAFE_MODE_ERROR

        # 1. Persist user turn before calling LLM
        if user_message is not None:
            await store.save_turn(session_id, "user", user_message)

        # 2. Attempt generation
        try:
            response = await generate_fn()
        except Exception:
            logger.exception(
                "LLM generation failed for session %s", session_id,
            )
            store.record_crash(session_id)
            # Roll back the unanswered user turn so context stays clean
            if user_message is not None:
                store.rollback_turns(session_id, n=1)
            return _GRACEFUL_ERROR

        # 3. Success — save response and reset crash counter
        await store.save_turn(session_id, "assistant", response, model=model)
        self._reset_crash_count(session_id)
        return response

    # -- diagnosis -----------------------------------------------------------

    async def diagnose(self, session_id: str) -> CrashDiagnosis:
        """Analyse the crash pattern for *session_id*.

        Looks at conversation history to identify common failure causes:

        * **long_context** — total turn content exceeds char threshold
        * **rapid_fire** — multiple user turns within a very short window
        * **bad_input** — last user message is empty or suspiciously short
        * **unknown** — no recognisable pattern
        """
        crash_count = self._store.get_crash_count(session_id)
        turns = self._store.get_session(session_id)
        user_turns = [t for t in turns if t.role == "user"]
        last_msgs = [t.content for t in user_turns[-5:]]

        # --- long context ---------------------------------------------------
        total_chars = sum(len(t.content) for t in turns)
        if total_chars > _LONG_CONTEXT_CHAR_THRESHOLD:
            return CrashDiagnosis(
                session_id=session_id,
                crash_count=crash_count,
                pattern=CrashPattern.LONG_CONTEXT,
                detail=f"Total context is {total_chars:,} chars",
                suggested_fix="Trim or archive older turns to reduce context length",
                last_user_messages=last_msgs,
            )

        # --- rapid fire -----------------------------------------------------
        if len(user_turns) >= 2:
            timestamps = [t.timestamp for t in user_turns[-3:]]
            gaps = [
                timestamps[i + 1] - timestamps[i]
                for i in range(len(timestamps) - 1)
            ]
            if gaps and all(g < _RAPID_FIRE_INTERVAL_SECS for g in gaps):
                return CrashDiagnosis(
                    session_id=session_id,
                    crash_count=crash_count,
                    pattern=CrashPattern.RAPID_FIRE,
                    detail=f"Last {len(gaps)+1} messages within {sum(gaps):.1f}s",
                    suggested_fix="Rate-limit user messages or debounce input",
                    last_user_messages=last_msgs,
                )

        # --- bad input ------------------------------------------------------
        if user_turns and len(user_turns[-1].content.strip()) < 2:
            return CrashDiagnosis(
                session_id=session_id,
                crash_count=crash_count,
                pattern=CrashPattern.BAD_INPUT,
                detail="Last user message is empty or too short",
                suggested_fix="Validate user input before sending to LLM",
                last_user_messages=last_msgs,
            )

        # --- unknown --------------------------------------------------------
        return CrashDiagnosis(
            session_id=session_id,
            crash_count=crash_count,
            pattern=CrashPattern.UNKNOWN,
            detail="No recognisable crash pattern detected",
            suggested_fix="Check server logs for the underlying exception",
            last_user_messages=last_msgs,
        )

    # -- admin notification --------------------------------------------------

    async def notify_admin(
        self,
        session_id: str,
        diagnosis: CrashDiagnosis,
    ) -> None:
        """Alert admin about repeated crashes.

        Logs a critical message and, if the intercom engine is available,
        broadcasts a high-priority notification.
        """
        msg = (
            f"⚠️ Session {session_id} crashed {diagnosis.crash_count} time(s). "
            f"Pattern: {diagnosis.pattern.value}. "
            f"Detail: {diagnosis.detail}. "
            f"Fix: {diagnosis.suggested_fix}"
        )
        logger.critical(msg)

        try:
            from cortex.intercom.engine import IntercomEngine  # type: ignore[import-untyped]

            engine = IntercomEngine()
            await engine.broadcast(msg, priority="high")
        except Exception:
            # Intercom may not be configured — fall back to logging only.
            logger.debug("Intercom broadcast unavailable; admin notified via log only")

    # -- helpers -------------------------------------------------------------

    def _is_safe_mode(self, session_id: str) -> bool:
        """Check whether *session_id* is in safe_mode."""
        row = self._store._conn.execute(
            "SELECT status FROM conversation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return row is not None and row["status"] == "safe_mode"

    def _reset_crash_count(self, session_id: str) -> None:
        """Reset crash counter to 0 after a successful response."""
        try:
            self._store._conn.execute(
                "UPDATE conversation_sessions "
                "SET crash_count = 0, updated_at = ? WHERE id = ?",
                (time.time(), session_id),
            )
            self._store._conn.commit()
        except Exception:
            logger.exception("_reset_crash_count failed for %s", session_id)
