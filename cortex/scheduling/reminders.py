"""Reminder engine — time-based, recurring, and event-based reminders."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from cortex.db import get_db
from cortex.scheduling.alarms import cron_matches, next_cron_time

logger = logging.getLogger(__name__)

# ── Reminder Engine ───────────────────────────────────────────────

class ReminderEngine:
    """Manage reminders with time, cron, and event-based triggers."""

    _CHECK_INTERVAL = 30  # seconds

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────

    async def create_reminder(
        self,
        message: str,
        trigger_at: datetime | None = None,
        cron_expression: str | None = None,
        event_condition: str | None = None,
        user_id: str = "",
        room: str = "",
    ) -> int:
        if cron_expression:
            trigger_type = "recurring"
        elif event_condition:
            trigger_type = "event"
        else:
            trigger_type = "time"
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO reminders (message, trigger_type, trigger_at, "
            "cron_expression, event_condition, user_id, room) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message,
                trigger_type,
                trigger_at.isoformat() if trigger_at else None,
                cron_expression,
                event_condition,
                user_id,
                room,
            ),
        )
        conn.commit()
        reminder_id = cur.lastrowid
        assert reminder_id is not None
        return reminder_id

    async def delete_reminder(self, reminder_id: int) -> bool:
        conn = get_db()
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        return cur.rowcount > 0

    async def list_reminders(
        self,
        user_id: str = "",
        include_fired: bool = False,
    ) -> list[dict[str, Any]]:
        conn = get_db()
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if not include_fired:
            clauses.append("fired = 0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM reminders{where} ORDER BY id", params  # noqa: S608
        ).fetchall()
        return [dict(r) for r in rows]

    async def check_event(
        self,
        event_name: str,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fire event-based reminders whose condition matches *event_name*."""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM reminders WHERE trigger_type = 'event' "
            "AND fired = 0 AND event_condition = ?",
            (event_name,),
        ).fetchall()
        fired: list[dict[str, Any]] = []
        for row in rows:
            reminder = dict(row)
            conn.execute(
                "UPDATE reminders SET fired = 1 WHERE id = ?", (reminder["id"],)
            )
            conn.commit()
            fired.append(reminder)
            await self._notify(reminder)
        return fired

    def on_trigger(self, callback: Callable[..., Any]) -> None:
        """Register callback: ``callback(reminder_id, message, user_id, room)``."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._checker_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ── Internal ──────────────────────────────────────────────────

    async def _checker_loop(self) -> None:
        while self._running:
            try:
                await self._check_due()
            except Exception:
                logger.exception("Reminder checker error")
            try:
                await asyncio.sleep(self._CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

    async def _check_due(self) -> None:
        now = datetime.now()
        conn = get_db()

        # Time-based reminders
        rows = conn.execute(
            "SELECT * FROM reminders WHERE trigger_type = 'time' "
            "AND fired = 0 AND trigger_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                trigger = datetime.fromisoformat(row["trigger_at"])
            except (ValueError, TypeError):
                continue
            if trigger <= now:
                reminder = dict(row)
                conn.execute(
                    "UPDATE reminders SET fired = 1 WHERE id = ?", (reminder["id"],)
                )
                conn.commit()
                await self._notify(reminder)

        # Recurring reminders
        rows = conn.execute(
            "SELECT * FROM reminders WHERE trigger_type = 'recurring' "
            "AND fired = 0 AND cron_expression IS NOT NULL"
        ).fetchall()
        for row in rows:
            if cron_matches(row["cron_expression"], now):
                reminder = dict(row)
                # Don't mark recurring as fired — they repeat
                await self._notify(reminder)

    async def _notify(self, reminder: dict[str, Any]) -> None:
        for cb in self._callbacks:
            try:
                result = cb(
                    reminder["id"], reminder["message"],
                    reminder["user_id"], reminder["room"],
                )
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "Reminder trigger callback error for reminder %s",
                    reminder["id"],
                )
