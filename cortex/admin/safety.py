"""Safety events and jailbreak-pattern endpoints."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


@router.get("/safety/events")
async def list_safety_events(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    category: str | None = None,
    severity: str | None = None,
    user_id: str | None = None,
):
    conn = _h._db()
    where, params = [], []
    if category:
        where.append("category = ?")
        params.append(category)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where) if where else "1=1"
    total = conn.execute(
        f"SELECT COUNT(*) FROM guardrail_events WHERE {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM guardrail_events WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"events": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


@router.get("/safety/patterns")
async def list_jailbreak_patterns(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM jailbreak_patterns ORDER BY hit_count DESC")
    return {"patterns": _h._rows(cur)}


class JailbreakPatternRequest(BaseModel):
    pattern: str
    source: str = "manual"


@router.post("/safety/patterns")
async def add_jailbreak_pattern(req: JailbreakPatternRequest, _: dict = Depends(require_admin)):
    conn = _h._db()
    try:
        conn.execute(
            "INSERT INTO jailbreak_patterns (pattern, source) VALUES (?, ?)",
            (req.pattern, req.source),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Pattern already exists")
    return {"ok": True}


@router.delete("/safety/patterns/{pattern_id}")
async def delete_jailbreak_pattern(pattern_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    conn.execute("DELETE FROM jailbreak_patterns WHERE id = ?", (pattern_id,))
    conn.commit()
    return {"ok": True}
