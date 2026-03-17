"""Admin scheduling endpoints — alarms, timers, reminders management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import _db, _rows, require_admin

router = APIRouter()


# ── Request models ───────────────────────────────────────────────

class AlarmCreateRequest(BaseModel):
    label: str = ""
    cron_expression: str
    sound: str = "default"
    tts_message: str = ""
    user_id: str = ""
    room: str = ""


# ── Alarm endpoints ──────────────────────────────────────────────

@router.get("/scheduling/alarms")
async def list_alarms(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM alarms ORDER BY id")
    return {"alarms": _rows(cur)}


@router.post("/scheduling/alarms")
async def create_alarm(req: AlarmCreateRequest, _: dict = Depends(require_admin)):
    conn = _db()
    from cortex.scheduling.alarms import next_cron_time
    nf = next_cron_time(req.cron_expression)
    cur = conn.execute(
        "INSERT INTO alarms (label, cron_expression, sound, tts_message, user_id, room, next_fire) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (req.label, req.cron_expression, req.sound, req.tts_message,
         req.user_id, req.room, nf.isoformat() if nf else None),
    )
    conn.commit()
    alarm_id = cur.lastrowid
    return {"id": alarm_id, "label": req.label, "cron_expression": req.cron_expression}


@router.delete("/scheduling/alarms/{alarm_id}")
async def delete_alarm(alarm_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return {"deleted": True}


@router.post("/scheduling/alarms/{alarm_id}/enable")
async def enable_alarm(alarm_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    row = conn.execute("SELECT cron_expression FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    from cortex.scheduling.alarms import next_cron_time
    nf = next_cron_time(row[0])
    conn.execute(
        "UPDATE alarms SET enabled = 1, next_fire = ? WHERE id = ?",
        (nf.isoformat() if nf else None, alarm_id),
    )
    conn.commit()
    return {"enabled": True}


@router.post("/scheduling/alarms/{alarm_id}/disable")
async def disable_alarm(alarm_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("UPDATE alarms SET enabled = 0 WHERE id = ?", (alarm_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return {"enabled": False}


# ── Timer endpoints ──────────────────────────────────────────────

@router.get("/scheduling/timers")
async def list_timers(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT * FROM timers WHERE state IN ('running', 'paused') ORDER BY id"
    )
    timers = _rows(cur)
    # Compute live remaining for running timers
    from datetime import datetime
    for t in timers:
        if t.get("state") == "running" and t.get("expires_at"):
            try:
                exp = datetime.fromisoformat(t["expires_at"])
                t["remaining_seconds"] = max(0.0, (exp - datetime.now()).total_seconds())
            except (ValueError, TypeError):
                pass
    return {"timers": timers}


@router.delete("/scheduling/timers/{timer_id}")
async def cancel_timer(timer_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    row = conn.execute("SELECT id FROM timers WHERE id = ?", (timer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Timer not found")
    conn.execute(
        "UPDATE timers SET state = 'cancelled', remaining_seconds = 0 WHERE id = ?",
        (timer_id,),
    )
    conn.commit()
    return {"cancelled": True}


# ── Reminder endpoints ──────────────────────────────────────────

@router.get("/scheduling/reminders")
async def list_reminders(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM reminders ORDER BY id")
    return {"reminders": _rows(cur)}


@router.delete("/scheduling/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"deleted": True}
