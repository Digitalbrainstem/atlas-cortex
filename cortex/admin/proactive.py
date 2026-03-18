"""Admin proactive endpoints — rules, events, briefing, and preferences."""

# Module ownership: Admin proactive intelligence management

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import _db, _rows, _row, require_admin

router = APIRouter()


# ── Request models ───────────────────────────────────────────────

class RuleCreateRequest(BaseModel):
    name: str
    provider: str
    condition_type: str
    condition_config: dict = {}
    action_type: str
    action_config: dict = {}
    priority: str = "normal"
    cooldown_minutes: int = 60
    user_id: str = ""


class PreferencesUpdateRequest(BaseModel):
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    min_priority: str | None = None
    max_per_hour: int | None = None
    channels: list[str] | None = None


# ── Rule endpoints ───────────────────────────────────────────────

@router.get("/proactive/rules")
async def list_rules(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM proactive_rules ORDER BY id")
    return {"rules": _rows(cur)}


@router.post("/proactive/rules")
async def create_rule(req: RuleCreateRequest, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "INSERT INTO proactive_rules "
        "(name, provider, condition_type, condition_config, "
        "action_type, action_config, priority, cooldown_minutes, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            req.name, req.provider, req.condition_type,
            json.dumps(req.condition_config),
            req.action_type, json.dumps(req.action_config),
            req.priority, req.cooldown_minutes, req.user_id,
        ),
    )
    conn.commit()
    return {"id": cur.lastrowid, "name": req.name}


@router.delete("/proactive/rules/{rule_id}")
async def delete_rule(rule_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("DELETE FROM proactive_rules WHERE id = ?", (rule_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"deleted": True}


@router.post("/proactive/rules/{rule_id}/enable")
async def enable_rule(rule_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "UPDATE proactive_rules SET enabled = 1 WHERE id = ?", (rule_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"enabled": True}


@router.post("/proactive/rules/{rule_id}/disable")
async def disable_rule(rule_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "UPDATE proactive_rules SET enabled = 0 WHERE id = ?", (rule_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"enabled": False}


# ── Event log ────────────────────────────────────────────────────

@router.get("/proactive/events")
async def list_events(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT * FROM proactive_events ORDER BY created_at DESC LIMIT 100"
    )
    return {"events": _rows(cur)}


# ── Briefing preview ─────────────────────────────────────────────

@router.get("/proactive/briefing")
async def briefing_preview(_: dict = Depends(require_admin)):
    from cortex.proactive.briefing import DailyBriefing

    briefing = DailyBriefing()
    text = await briefing.generate()
    sections = await briefing.get_sections()
    return {"text": text, "sections": sections}


# ── Notification preferences ────────────────────────────────────

@router.get("/proactive/preferences/{user_id}")
async def get_preferences(user_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT * FROM notification_preferences WHERE user_id = ?", (user_id,),
    )
    row = _row(cur)
    if row is None:
        return {
            "user_id": user_id,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "min_priority": "normal",
            "max_per_hour": 10,
            "channels": ["log"],
        }
    if isinstance(row.get("channels"), str):
        try:
            row["channels"] = json.loads(row["channels"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


@router.patch("/proactive/preferences/{user_id}")
async def update_preferences(
    user_id: str,
    req: PreferencesUpdateRequest,
    _: dict = Depends(require_admin),
):
    conn = _db()
    existing = conn.execute(
        "SELECT id FROM notification_preferences WHERE user_id = ?", (user_id,),
    ).fetchone()

    if existing is None:
        channels = json.dumps(req.channels) if req.channels else '["log"]'
        conn.execute(
            "INSERT INTO notification_preferences "
            "(user_id, quiet_hours_start, quiet_hours_end, min_priority, max_per_hour, channels) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                req.quiet_hours_start or "22:00",
                req.quiet_hours_end or "07:00",
                req.min_priority or "normal",
                req.max_per_hour if req.max_per_hour is not None else 10,
                channels,
            ),
        )
    else:
        updates: list[str] = []
        params: list[str | int] = []
        if req.quiet_hours_start is not None:
            updates.append("quiet_hours_start = ?")
            params.append(req.quiet_hours_start)
        if req.quiet_hours_end is not None:
            updates.append("quiet_hours_end = ?")
            params.append(req.quiet_hours_end)
        if req.min_priority is not None:
            updates.append("min_priority = ?")
            params.append(req.min_priority)
        if req.max_per_hour is not None:
            updates.append("max_per_hour = ?")
            params.append(req.max_per_hour)
        if req.channels is not None:
            updates.append("channels = ?")
            params.append(json.dumps(req.channels))
        if updates:
            params.append(user_id)
            conn.execute(
                f"UPDATE notification_preferences SET {', '.join(updates)} WHERE user_id = ?",
                params,
            )
    conn.commit()
    return {"updated": True}
