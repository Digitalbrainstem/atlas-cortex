"""Evolution, system info, and settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from cortex.db import get_db
from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Evolution ─────────────────────────────────────────────────────


@router.get("/evolution/profiles")
async def list_emotional_profiles(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
    total = conn.execute("SELECT COUNT(*) FROM emotional_profiles").fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        "SELECT * FROM emotional_profiles ORDER BY last_interaction DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    profiles = _h._rows(cur)

    for p in profiles:
        uid = p["user_id"]
        cur = conn.execute(
            "SELECT topic, mention_count FROM user_topics WHERE user_id = ? ORDER BY mention_count DESC LIMIT 5",
            (uid,),
        )
        p["top_topics"] = _h._rows(cur)

    return {"profiles": profiles, "total": total, "page": page, "per_page": per_page}


@router.get("/evolution/logs")
async def list_evolution_logs(
    _: dict = Depends(require_admin),
    limit: int = Query(20, ge=1, le=100),
):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM evolution_log ORDER BY run_at DESC LIMIT ?", (limit,))
    return {"logs": _h._rows(cur)}


@router.get("/evolution/mistakes")
async def list_mistakes(
    _: dict = Depends(require_admin),
    resolved: bool | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
    where, params = [], []
    if resolved is not None:
        where.append("resolved = ?")
        params.append(resolved)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) FROM mistake_log WHERE {where_sql}", params
    ).fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM mistake_log WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"mistakes": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


@router.patch("/evolution/mistakes/{mistake_id}")
async def resolve_mistake(mistake_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    conn.execute("UPDATE mistake_log SET resolved = TRUE WHERE id = ?", (mistake_id,))
    conn.commit()
    return {"ok": True}


# ── System ────────────────────────────────────────────────────────


@router.get("/system/hardware")
async def get_hardware(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM hardware_profile WHERE is_current = TRUE")
    profile = _h._row(cur)
    cur = conn.execute("SELECT * FROM hardware_gpu ORDER BY gpu_index")
    gpus = _h._rows(cur)
    return {"profile": profile, "gpus": gpus}


@router.get("/system/models")
async def get_model_config(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM model_config ORDER BY role")
    return {"models": _h._rows(cur)}


@router.get("/system/services")
async def get_services(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM discovered_services ORDER BY service_type")
    return {"services": _h._rows(cur)}


@router.get("/system/backups")
async def get_backups(
    _: dict = Depends(require_admin),
    limit: int = Query(20, ge=1, le=100),
):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM backup_log ORDER BY created_at DESC LIMIT ?", (limit,))
    return {"backups": _h._rows(cur)}


@router.get("/system/interactions")
async def get_interactions(
    _: dict = Depends(require_admin),
    user_id: str | None = None,
    layer: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
    where, params = [], []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if layer:
        where.append("matched_layer = ?")
        params.append(layer)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) FROM interactions WHERE {where_sql}", params
    ).fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM interactions WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"interactions": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


# ── Settings ──────────────────────────────────────────────────────


@router.get("/settings")
async def get_system_settings(admin: dict = Depends(require_admin)):
    """Get all system settings."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM system_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


@router.put("/settings/{key}")
async def set_system_setting(key: str, body: dict, admin: dict = Depends(require_admin)):
    """Set a system setting. Body: {\"value\": \"...\"}"""
    value = body.get("value", "")
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    db.commit()
    return {"key": key, "value": value}
