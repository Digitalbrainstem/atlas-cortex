"""Evolution, system info, settings, and self-recovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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


# ── Self-Recovery ─────────────────────────────────────────────────


@router.get("/system/resources")
async def get_resources(request: Request, _: dict = Depends(require_admin)):
    """Return current resource levels and monitor state."""
    monitor = getattr(request.app.state, "resource_monitor", None)
    if monitor is None:
        raise HTTPException(503, "Resource monitor not running")
    return monitor.get_status()


@router.put("/system/resources/thresholds")
async def update_thresholds(request: Request, body: dict, _: dict = Depends(require_admin)):
    """Update resource warning/critical thresholds."""
    monitor = getattr(request.app.state, "resource_monitor", None)
    if monitor is None:
        raise HTTPException(503, "Resource monitor not running")
    return monitor.update_thresholds(**body)


@router.get("/system/snapshots")
async def list_snapshots(request: Request, _: dict = Depends(require_admin)):
    """List available state snapshots."""
    snapshotter = getattr(request.app.state, "snapshotter", None)
    if snapshotter is None:
        raise HTTPException(503, "Snapshot system not running")
    snapshots = snapshotter.list_snapshots()
    return {
        "snapshots": [
            {"timestamp": s.timestamp, "path": s.path, "size_bytes": s.size_bytes}
            for s in snapshots
        ],
        "latest_age_seconds": snapshotter.get_latest_snapshot_age(),
    }


@router.post("/system/snapshots")
async def take_snapshot(request: Request, _: dict = Depends(require_admin)):
    """Take a manual state snapshot now."""
    snapshotter = getattr(request.app.state, "snapshotter", None)
    if snapshotter is None:
        raise HTTPException(503, "Snapshot system not running")
    info = await snapshotter.take_snapshot()
    return {"timestamp": info.timestamp, "path": info.path, "size_bytes": info.size_bytes}


@router.post("/system/snapshots/restore")
async def restore_snapshot(request: Request, body: dict, _: dict = Depends(require_admin)):
    """Restore from a specific snapshot. Body: {\"path\": \"...\"}"""
    snapshotter = getattr(request.app.state, "snapshotter", None)
    if snapshotter is None:
        raise HTTPException(503, "Snapshot system not running")
    path = body.get("path", "")
    if not path:
        raise HTTPException(400, "path is required")
    result = await snapshotter.restore_from_snapshot(path)
    return result


@router.get("/system/recovery")
async def get_recovery_status(request: Request, _: dict = Depends(require_admin)):
    """Return the last startup recovery result."""
    recovery_result = getattr(request.app.state, "recovery_result", None)
    if recovery_result is None:
        return {"recovered": False, "issue": "", "details": "", "steps": []}
    return {
        "recovered": recovery_result.recovered,
        "issue": recovery_result.issue,
        "details": recovery_result.details,
        "steps": recovery_result.steps,
    }
