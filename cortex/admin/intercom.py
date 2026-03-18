"""Admin intercom endpoints — zones, calls, log, broadcast."""

# Module ownership: Admin intercom management

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import _db, _rows, _row, require_admin

router = APIRouter()


# ── Lazy engine singleton ────────────────────────────────────────

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from cortex.intercom.engine import IntercomEngine
        _engine = IntercomEngine()
    return _engine


# ── Request models ───────────────────────────────────────────────

class ZoneCreateRequest(BaseModel):
    name: str
    satellite_ids: list[str] = []
    description: str = ""


class ZoneUpdateRequest(BaseModel):
    name: str | None = None
    satellite_ids: list[str] | None = None
    description: str | None = None


class BroadcastRequest(BaseModel):
    message: str
    zone: str | None = None
    priority: str = "normal"


# ── Zone endpoints ───────────────────────────────────────────────

@router.get("/intercom/zones")
async def list_zones(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT id, name, description, satellite_ids, created_at "
        "FROM satellite_zones ORDER BY name"
    )
    zones = []
    for r in cur.fetchall():
        cols = [d[0] for d in cur.description]
        row = dict(zip(cols, r)) if not isinstance(r, dict) else dict(r)
        row["satellite_ids"] = json.loads(row.get("satellite_ids", "[]"))
        zones.append(row)
    return {"zones": zones}


@router.post("/intercom/zones", status_code=201)
async def create_zone(req: ZoneCreateRequest, _: dict = Depends(require_admin)):
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO satellite_zones (name, satellite_ids, description) "
            "VALUES (?, ?, ?)",
            (req.name, json.dumps(req.satellite_ids), req.description),
        )
        conn.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="Zone name already exists")
        raise
    return {"id": cur.lastrowid, "name": req.name}


@router.delete("/intercom/zones/{zone_id}")
async def delete_zone(zone_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("DELETE FROM satellite_zones WHERE id = ?", (zone_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"ok": True}


@router.patch("/intercom/zones/{zone_id}")
async def update_zone(
    zone_id: int, req: ZoneUpdateRequest, _: dict = Depends(require_admin)
):
    conn = _db()
    parts: list[str] = []
    params: list[object] = []
    if req.name is not None:
        parts.append("name = ?")
        params.append(req.name)
    if req.satellite_ids is not None:
        parts.append("satellite_ids = ?")
        params.append(json.dumps(req.satellite_ids))
    if req.description is not None:
        parts.append("description = ?")
        params.append(req.description)
    if not parts:
        raise HTTPException(status_code=400, detail="No fields to update")
    params.append(zone_id)
    cur = conn.execute(
        f"UPDATE satellite_zones SET {', '.join(parts)} WHERE id = ?", params
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"ok": True}


# ── Call endpoints ───────────────────────────────────────────────

@router.get("/intercom/calls")
async def list_active_calls(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT id, caller_satellite, callee_satellite, status, started_at "
        "FROM active_calls WHERE status != 'ended' ORDER BY id"
    )
    return {"calls": _rows(cur)}


@router.post("/intercom/calls/{call_id}/end")
async def end_call(call_id: int, _: dict = Depends(require_admin)):
    engine = _get_engine()
    ok = await engine.end_call(call_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Call not found or already ended")
    return {"ok": True}


# ── Log endpoint ─────────────────────────────────────────────────

@router.get("/intercom/log")
async def get_log(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT * FROM intercom_log ORDER BY id DESC LIMIT 100"
    )
    return {"log": _rows(cur)}


# ── Broadcast endpoint ──────────────────────────────────────────

@router.post("/intercom/broadcast")
async def trigger_broadcast(req: BroadcastRequest, _: dict = Depends(require_admin)):
    engine = _get_engine()
    if req.zone:
        count = await engine.zone_broadcast(
            req.message, req.zone, priority=req.priority
        )
    else:
        count = await engine.broadcast(
            req.message, priority=req.priority
        )
    return {"ok": True, "satellites_reached": count}
