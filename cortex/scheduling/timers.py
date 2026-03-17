"""Timer engine — in-memory countdown with DB persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from cortex.db import get_db

logger = logging.getLogger(__name__)

# ── Timer Engine ──────────────────────────────────────────────────

class TimerEngine:
    """Manage concurrent countdown timers backed by asyncio tasks."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._callbacks: list[Callable[..., Any]] = []
        # Track pause state per timer: {timer_id: remaining_seconds}
        self._paused: dict[int, float] = {}

    # ── Public API ────────────────────────────────────────────────

    async def start_timer(
        self,
        duration_seconds: int,
        label: str = "",
        user_id: str = "",
        room: str = "",
    ) -> int:
        """Create and start a timer.  Returns the timer_id."""
        expires_at = datetime.now() + timedelta(seconds=duration_seconds)
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO timers (label, duration_seconds, remaining_seconds, "
            "state, user_id, room, expires_at) VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (label, duration_seconds, float(duration_seconds), user_id, room,
             expires_at.isoformat()),
        )
        conn.commit()
        timer_id = cur.lastrowid
        assert timer_id is not None
        self._tasks[timer_id] = asyncio.create_task(
            self._countdown(timer_id, duration_seconds),
        )
        return timer_id

    async def pause_timer(self, timer_id: int) -> bool:
        task = self._tasks.get(timer_id)
        if task is None or task.done():
            return False
        # Compute remaining seconds
        conn = get_db()
        row = conn.execute(
            "SELECT expires_at, state FROM timers WHERE id = ?", (timer_id,)
        ).fetchone()
        if row is None or row["state"] != "running":
            return False
        expires = datetime.fromisoformat(row["expires_at"])
        remaining = max(0.0, (expires - datetime.now()).total_seconds())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._paused[timer_id] = remaining
        del self._tasks[timer_id]
        conn.execute(
            "UPDATE timers SET state = 'paused', remaining_seconds = ? WHERE id = ?",
            (remaining, timer_id),
        )
        conn.commit()
        return True

    async def resume_timer(self, timer_id: int) -> bool:
        remaining = self._paused.pop(timer_id, None)
        if remaining is None:
            # Check DB in case of restart
            conn = get_db()
            row = conn.execute(
                "SELECT remaining_seconds, state FROM timers WHERE id = ?",
                (timer_id,),
            ).fetchone()
            if row is None or row["state"] != "paused":
                return False
            remaining = float(row["remaining_seconds"])
        conn = get_db()
        expires_at = datetime.now() + timedelta(seconds=remaining)
        conn.execute(
            "UPDATE timers SET state = 'running', expires_at = ? WHERE id = ?",
            (expires_at.isoformat(), timer_id),
        )
        conn.commit()
        self._tasks[timer_id] = asyncio.create_task(
            self._countdown(timer_id, remaining),
        )
        return True

    async def cancel_timer(self, timer_id: int) -> bool:
        task = self._tasks.pop(timer_id, None)
        self._paused.pop(timer_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        conn = get_db()
        row = conn.execute("SELECT id FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE timers SET state = 'cancelled', remaining_seconds = 0 WHERE id = ?",
            (timer_id,),
        )
        conn.commit()
        return True

    async def list_timers(self, user_id: str = "") -> list[dict[str, Any]]:
        conn = get_db()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM timers WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM timers ORDER BY id").fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # Compute live remaining for running timers
            if d["state"] == "running" and d.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(d["expires_at"])
                    d["remaining_seconds"] = max(
                        0.0, (exp - datetime.now()).total_seconds()
                    )
                except (ValueError, TypeError):
                    pass
            result.append(d)
        return result

    async def get_timer(self, timer_id: int) -> dict[str, Any] | None:
        conn = get_db()
        row = conn.execute("SELECT * FROM timers WHERE id = ?", (timer_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d["state"] == "running" and d.get("expires_at"):
            try:
                exp = datetime.fromisoformat(d["expires_at"])
                d["remaining_seconds"] = max(
                    0.0, (exp - datetime.now()).total_seconds()
                )
            except (ValueError, TypeError):
                pass
        return d

    def on_expire(self, callback: Callable[..., Any]) -> None:
        """Register a callback invoked when a timer expires.

        Signature: ``callback(timer_id, label, user_id, room)``
        """
        self._callbacks.append(callback)

    async def restore_from_db(self) -> None:
        """Restore running/paused timers from the database on startup."""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM timers WHERE state IN ('running', 'paused')"
        ).fetchall()
        for row in rows:
            tid = row["id"]
            if row["state"] == "paused":
                self._paused[tid] = float(row["remaining_seconds"] or 0)
            elif row["state"] == "running" and row["expires_at"]:
                try:
                    exp = datetime.fromisoformat(row["expires_at"])
                    remaining = (exp - datetime.now()).total_seconds()
                except (ValueError, TypeError):
                    remaining = 0
                if remaining > 0:
                    self._tasks[tid] = asyncio.create_task(
                        self._countdown(tid, remaining),
                    )
                else:
                    await self._fire(tid)

    # ── Internal ──────────────────────────────────────────────────

    async def _countdown(self, timer_id: int, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        await self._fire(timer_id)

    async def _fire(self, timer_id: int) -> None:
        conn = get_db()
        conn.execute(
            "UPDATE timers SET state = 'finished', remaining_seconds = 0 WHERE id = ?",
            (timer_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT label, user_id, room FROM timers WHERE id = ?", (timer_id,)
        ).fetchone()
        label = row["label"] if row else ""
        user_id = row["user_id"] if row else ""
        room = row["room"] if row else ""
        self._tasks.pop(timer_id, None)
        for cb in self._callbacks:
            try:
                result = cb(timer_id, label, user_id, room)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Timer expire callback error for timer %s", timer_id)
