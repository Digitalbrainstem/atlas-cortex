"""Alarm engine — cron-based scheduling with a built-in cron parser."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from cortex.db import get_db

logger = logging.getLogger(__name__)

# ── Minimal cron parser ──────────────────────────────────────────

def _parse_cron_field(field: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of matching integers.

    Supports: ``*``, exact values, ranges (``1-5``), steps (``*/2``),
    comma-separated lists (``1,3,5``), and range-steps (``1-10/2``).
    """
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            values.update(range(min_val, max_val + 1))
        elif "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if base == "*":
                start = min_val
                end = max_val
            elif "-" in base:
                s, e = base.split("-", 1)
                start, end = int(s), int(e)
            else:
                start = int(base)
                end = max_val
            values.update(range(start, end + 1, step))
        elif "-" in part:
            s, e = part.split("-", 1)
            values.update(range(int(s), int(e) + 1))
        else:
            values.add(int(part))
    return values


def cron_matches(expression: str, dt: datetime) -> bool:
    """Return ``True`` if *dt* matches the 5-field cron *expression*.

    Fields: minute hour day-of-month month day-of-week (0=Sun).
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        return False
    try:
        minutes = _parse_cron_field(parts[0], 0, 59)
        hours = _parse_cron_field(parts[1], 0, 23)
        days = _parse_cron_field(parts[2], 1, 31)
        months = _parse_cron_field(parts[3], 1, 12)
        dow = _parse_cron_field(parts[4], 0, 6)
    except (ValueError, IndexError):
        return False

    # isoweekday(): Mon=1 … Sun=7  →  cron: Sun=0 Mon=1 … Sat=6
    cron_dow = dt.isoweekday() % 7

    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in days
        and dt.month in months
        and cron_dow in dow
    )


def next_cron_time(expression: str, after: datetime | None = None) -> datetime | None:
    """Find the next datetime matching *expression* after *after*.

    Scans minute-by-minute up to 366 days ahead; returns ``None`` if no
    match is found.
    """
    if after is None:
        after = datetime.now()
    # Start from the next whole minute
    candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = after + timedelta(days=366)
    while candidate <= limit:
        if cron_matches(expression, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return None


# ── Alarm Engine ──────────────────────────────────────────────────

class AlarmEngine:
    """Cron-based alarm engine with DB persistence and background checker."""

    _CHECK_INTERVAL = 30  # seconds between due-alarm checks

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────

    async def create_alarm(
        self,
        cron_expression: str,
        label: str = "",
        sound: str = "default",
        tts_message: str = "",
        user_id: str = "",
        room: str = "",
    ) -> int:
        nf = next_cron_time(cron_expression)
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO alarms (label, cron_expression, sound, tts_message, "
            "user_id, room, next_fire) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (label, cron_expression, sound, tts_message, user_id, room,
             nf.isoformat() if nf else None),
        )
        conn.commit()
        alarm_id = cur.lastrowid
        assert alarm_id is not None
        return alarm_id

    async def delete_alarm(self, alarm_id: int) -> bool:
        conn = get_db()
        cur = conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
        conn.commit()
        return cur.rowcount > 0

    async def enable_alarm(self, alarm_id: int) -> bool:
        conn = get_db()
        row = conn.execute(
            "SELECT cron_expression FROM alarms WHERE id = ?", (alarm_id,)
        ).fetchone()
        if row is None:
            return False
        nf = next_cron_time(row["cron_expression"])
        conn.execute(
            "UPDATE alarms SET enabled = 1, next_fire = ? WHERE id = ?",
            (nf.isoformat() if nf else None, alarm_id),
        )
        conn.commit()
        return True

    async def disable_alarm(self, alarm_id: int) -> bool:
        conn = get_db()
        cur = conn.execute(
            "UPDATE alarms SET enabled = 0 WHERE id = ?", (alarm_id,)
        )
        conn.commit()
        return cur.rowcount > 0

    async def list_alarms(self, user_id: str = "") -> list[dict[str, Any]]:
        conn = get_db()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM alarms WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM alarms ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    async def snooze_alarm(self, alarm_id: int, minutes: int = 5) -> bool:
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM alarms WHERE id = ?", (alarm_id,)
        ).fetchone()
        if row is None:
            return False
        snooze_time = datetime.now() + timedelta(minutes=minutes)
        conn.execute(
            "UPDATE alarms SET next_fire = ?, enabled = 1 WHERE id = ?",
            (snooze_time.isoformat(), alarm_id),
        )
        conn.commit()
        return True

    def on_trigger(self, callback: Callable[..., Any]) -> None:
        """Register callback: ``callback(alarm_id, label, sound, tts_message, user_id, room)``."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the background alarm checker loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._checker_loop())

    async def stop(self) -> None:
        """Stop the background alarm checker."""
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
                logger.exception("Alarm checker error")
            try:
                await asyncio.sleep(self._CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

    async def _check_due(self) -> None:
        now = datetime.now()
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM alarms WHERE enabled = 1 AND next_fire IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                nf = datetime.fromisoformat(row["next_fire"])
            except (ValueError, TypeError):
                continue
            if nf <= now:
                await self._fire_alarm(dict(row))

    async def _fire_alarm(self, alarm: dict[str, Any]) -> None:
        conn = get_db()
        nf = next_cron_time(alarm["cron_expression"])
        conn.execute(
            "UPDATE alarms SET last_fired = ?, next_fire = ? WHERE id = ?",
            (datetime.now().isoformat(), nf.isoformat() if nf else None, alarm["id"]),
        )
        conn.commit()
        for cb in self._callbacks:
            try:
                result = cb(
                    alarm["id"], alarm["label"], alarm["sound"],
                    alarm["tts_message"], alarm["user_id"], alarm["room"],
                )
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Alarm trigger callback error for alarm %s", alarm["id"])
