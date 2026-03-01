"""Admin API router for Atlas Cortex.

Provides RESTful endpoints for managing users, parental controls, safety,
voice enrollments, devices, evolution, and system configuration.

All endpoints require a valid admin JWT (see :mod:`cortex.auth`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cortex.auth import (
    authenticate,
    create_token,
    hash_password,
    require_admin,
    seed_admin,
    verify_password,
)
from cortex.db import get_db, init_db

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Helper ────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    init_db()
    conn = get_db()
    seed_admin(conn)
    return conn


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row(cur: sqlite3.Cursor) -> dict | None:
    r = cur.fetchone()
    if r is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, r))


# ══════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/login")
async def login(req: LoginRequest):
    conn = _db()
    user = authenticate(conn, req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(user["id"], user["username"]), "user": user}


@router.get("/auth/me")
async def me(admin: dict = Depends(require_admin)):
    return {"id": admin["sub"], "username": admin["username"]}


@router.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, admin: dict = Depends(require_admin)):
    conn = _db()
    row = conn.execute(
        "SELECT password_hash FROM admin_users WHERE id = ?", (admin["sub"],)
    ).fetchone()
    if not row or not verify_password(req.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (hash_password(req.new_password), admin["sub"]),
    )
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════

@router.get("/dashboard")
async def dashboard(_: dict = Depends(require_admin)):
    conn = _db()
    stats: dict[str, Any] = {}

    stats["total_users"] = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
    stats["total_interactions"] = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    stats["safety_events"] = conn.execute("SELECT COUNT(*) FROM guardrail_events").fetchone()[0]
    stats["command_patterns"] = conn.execute("SELECT COUNT(*) FROM command_patterns").fetchone()[0]
    stats["devices"] = conn.execute("SELECT COUNT(*) FROM ha_devices").fetchone()[0]
    stats["voice_enrollments"] = conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()[0]
    stats["jailbreak_patterns"] = conn.execute("SELECT COUNT(*) FROM jailbreak_patterns").fetchone()[0]

    # Recent safety events
    cur = conn.execute(
        "SELECT * FROM guardrail_events ORDER BY created_at DESC LIMIT 10"
    )
    stats["recent_safety_events"] = _rows(cur)

    # Recent interactions
    cur = conn.execute(
        "SELECT id, user_id, message, matched_layer, sentiment, response_time_ms, created_at "
        "FROM interactions ORDER BY created_at DESC LIMIT 10"
    )
    stats["recent_interactions"] = _rows(cur)

    # Layer distribution
    cur = conn.execute(
        "SELECT matched_layer, COUNT(*) as count FROM interactions GROUP BY matched_layer"
    )
    stats["layer_distribution"] = _rows(cur)

    return stats


# ══════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
    offset = (page - 1) * per_page
    total = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
    cur = conn.execute(
        "SELECT * FROM user_profiles ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    return {"users": _rows(cur), "total": total, "page": page, "per_page": per_page}


@router.get("/users/{user_id}")
async def get_user(user_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    user = _row(cur)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Attach emotional profile
    cur = conn.execute("SELECT * FROM emotional_profiles WHERE user_id = ?", (user_id,))
    user["emotional_profile"] = _row(cur)

    # Attach parental controls
    cur = conn.execute("SELECT * FROM parental_controls WHERE child_user_id = ?", (user_id,))
    user["parental_controls"] = _row(cur)

    # Attach topics
    cur = conn.execute(
        "SELECT topic, mention_count, last_mentioned FROM user_topics WHERE user_id = ? ORDER BY mention_count DESC LIMIT 20",
        (user_id,),
    )
    user["topics"] = _rows(cur)

    # Attach activity hours
    cur = conn.execute(
        "SELECT hour, interaction_count FROM user_activity_hours WHERE user_id = ? ORDER BY hour",
        (user_id,),
    )
    user["activity_hours"] = _rows(cur)

    return user


class UserUpdate(BaseModel):
    display_name: str | None = None
    age: int | None = None
    age_group: str | None = None
    age_confidence: float | None = None
    vocabulary_level: str | None = None
    preferred_tone: str | None = None
    communication_style: str | None = None
    humor_style: str | None = None
    is_parent: bool | None = None
    onboarding_complete: bool | None = None


@router.patch("/users/{user_id}")
async def update_user(user_id: str, update: UserUpdate, _: dict = Depends(require_admin)):
    conn = _db()
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn.execute(f"UPDATE user_profiles SET {set_clause} WHERE user_id = ?", values)
    conn.commit()

    cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    return _row(cur)


class SetAgeRequest(BaseModel):
    birth_year: int
    birth_month: int = 1


@router.post("/users/{user_id}/age")
async def set_user_age(user_id: str, req: SetAgeRequest, _: dict = Depends(require_admin)):
    from cortex.profiles import set_user_age as _set_age
    conn = _db()
    result = _set_age(conn, user_id, birth_year=req.birth_year, birth_month=req.birth_month)
    return result


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM emotional_profiles WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM parental_controls WHERE child_user_id = ?", (user_id,))
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# PARENTAL CONTROLS
# ══════════════════════════════════════════════════════════════════

class ParentalControlsRequest(BaseModel):
    parent_user_id: str
    content_filter_level: str = "strict"
    allowed_hours_start: str = "07:00"
    allowed_hours_end: str = "21:00"
    restricted_topics: list[str] = Field(default_factory=list)
    restricted_actions: list[str] = Field(default_factory=list)


@router.get("/users/{user_id}/parental")
async def get_parental_controls(user_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM parental_controls WHERE child_user_id = ?", (user_id,))
    controls = _row(cur)
    if not controls:
        return {"controls": None}

    cur = conn.execute(
        "SELECT action FROM parental_restricted_actions WHERE child_user_id = ?", (user_id,)
    )
    controls["restricted_actions"] = [r["action"] for r in _rows(cur)]

    cur = conn.execute(
        "SELECT entity_id FROM parental_allowed_devices WHERE child_user_id = ?", (user_id,)
    )
    controls["allowed_devices"] = [r["entity_id"] for r in _rows(cur)]

    return {"controls": controls}


@router.post("/users/{user_id}/parental")
async def set_parental_controls(
    user_id: str, req: ParentalControlsRequest, _: dict = Depends(require_admin)
):
    conn = _db()
    conn.execute(
        """INSERT INTO parental_controls (child_user_id, parent_user_id, content_filter_level,
           allowed_hours_start, allowed_hours_end) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(child_user_id) DO UPDATE SET
           parent_user_id=excluded.parent_user_id,
           content_filter_level=excluded.content_filter_level,
           allowed_hours_start=excluded.allowed_hours_start,
           allowed_hours_end=excluded.allowed_hours_end""",
        (user_id, req.parent_user_id, req.content_filter_level,
         req.allowed_hours_start, req.allowed_hours_end),
    )
    # Restricted actions
    conn.execute("DELETE FROM parental_restricted_actions WHERE child_user_id = ?", (user_id,))
    for action in req.restricted_actions:
        conn.execute(
            "INSERT INTO parental_restricted_actions (child_user_id, action) VALUES (?, ?)",
            (user_id, action),
        )
    conn.commit()
    return {"ok": True}


@router.delete("/users/{user_id}/parental")
async def remove_parental_controls(user_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    conn.execute("DELETE FROM parental_controls WHERE child_user_id = ?", (user_id,))
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# SAFETY
# ══════════════════════════════════════════════════════════════════

@router.get("/safety/events")
async def list_safety_events(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    category: str | None = None,
    severity: str | None = None,
    user_id: str | None = None,
):
    conn = _db()
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
    return {"events": _rows(cur), "total": total, "page": page, "per_page": per_page}


@router.get("/safety/patterns")
async def list_jailbreak_patterns(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM jailbreak_patterns ORDER BY hit_count DESC")
    return {"patterns": _rows(cur)}


class JailbreakPatternRequest(BaseModel):
    pattern: str
    source: str = "manual"


@router.post("/safety/patterns")
async def add_jailbreak_pattern(req: JailbreakPatternRequest, _: dict = Depends(require_admin)):
    conn = _db()
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
    conn = _db()
    conn.execute("DELETE FROM jailbreak_patterns WHERE id = ?", (pattern_id,))
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# VOICE / SPEAKERS
# ══════════════════════════════════════════════════════════════════

@router.get("/voice/speakers")
async def list_speakers(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute(
        "SELECT id, user_id, display_name, enrolled_at, sample_count, last_verified, "
        "confidence_threshold FROM speaker_profiles ORDER BY enrolled_at DESC"
    )
    return {"speakers": _rows(cur)}


@router.delete("/voice/speakers/{speaker_id}")
async def delete_speaker(speaker_id: str, _: dict = Depends(require_admin)):
    conn = _db()
    conn.execute("DELETE FROM speaker_profiles WHERE id = ?", (speaker_id,))
    conn.commit()
    return {"ok": True}


@router.patch("/voice/speakers/{speaker_id}")
async def update_speaker(
    speaker_id: str,
    update: dict,
    _: dict = Depends(require_admin),
):
    conn = _db()
    # Map allowed input field names to fixed SQL assignment fragments
    allowed_fields = {
        "display_name": "display_name = ?",
        "user_id": "user_id = ?",
        "confidence_threshold": "confidence_threshold = ?",
    }
    set_parts: list[str] = []
    params: list[Any] = []
    for key, value in update.items():
        if key in allowed_fields and value is not None:
            set_parts.append(allowed_fields[key])
            params.append(value)
    if not set_parts:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    set_clause = ", ".join(set_parts)
    conn.execute(
        f"UPDATE speaker_profiles SET {set_clause} WHERE id = ?",
        params + [speaker_id],
    )
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# DEVICES & PATTERNS
# ══════════════════════════════════════════════════════════════════

@router.get("/devices")
async def list_devices(
    _: dict = Depends(require_admin),
    domain: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
    where, params = [], []
    if domain:
        where.append("domain = ?")
        params.append(domain)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(f"SELECT COUNT(*) FROM ha_devices WHERE {where_sql}", params).fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM ha_devices WHERE {where_sql} ORDER BY domain, friendly_name LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    devices = _rows(cur)

    # Attach aliases to each device
    for dev in devices:
        cur = conn.execute(
            "SELECT alias, source FROM device_aliases WHERE entity_id = ?",
            (dev["entity_id"],),
        )
        dev["aliases"] = _rows(cur)

    return {"devices": devices, "total": total, "page": page, "per_page": per_page}


@router.get("/devices/patterns")
async def list_command_patterns(
    _: dict = Depends(require_admin),
    source: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
    where, params = [], []
    if source:
        where.append("source = ?")
        params.append(source)
    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) FROM command_patterns WHERE {where_sql}", params
    ).fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM command_patterns WHERE {where_sql} ORDER BY hit_count DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"patterns": _rows(cur), "total": total, "page": page, "per_page": per_page}


class PatternUpdate(BaseModel):
    pattern: str | None = None
    intent: str | None = None
    confidence: float | None = None
    response_template: str | None = None


@router.patch("/devices/patterns/{pattern_id}")
async def update_command_pattern(
    pattern_id: int, update: PatternUpdate, _: dict = Depends(require_admin)
):
    conn = _db()
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE command_patterns SET {set_clause} WHERE id = ?",
        list(fields.values()) + [pattern_id],
    )
    conn.commit()
    return {"ok": True}


@router.delete("/devices/patterns/{pattern_id}")
async def delete_command_pattern(pattern_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    conn.execute("DELETE FROM command_patterns WHERE id = ?", (pattern_id,))
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# EVOLUTION
# ══════════════════════════════════════════════════════════════════

@router.get("/evolution/profiles")
async def list_emotional_profiles(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM emotional_profiles").fetchone()[0]
    offset = (page - 1) * per_page
    cur = conn.execute(
        "SELECT * FROM emotional_profiles ORDER BY last_interaction DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    profiles = _rows(cur)

    for p in profiles:
        uid = p["user_id"]
        cur = conn.execute(
            "SELECT topic, mention_count FROM user_topics WHERE user_id = ? ORDER BY mention_count DESC LIMIT 5",
            (uid,),
        )
        p["top_topics"] = _rows(cur)

    return {"profiles": profiles, "total": total, "page": page, "per_page": per_page}


@router.get("/evolution/logs")
async def list_evolution_logs(
    _: dict = Depends(require_admin),
    limit: int = Query(20, ge=1, le=100),
):
    conn = _db()
    cur = conn.execute("SELECT * FROM evolution_log ORDER BY run_at DESC LIMIT ?", (limit,))
    return {"logs": _rows(cur)}


@router.get("/evolution/mistakes")
async def list_mistakes(
    _: dict = Depends(require_admin),
    resolved: bool | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
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
    return {"mistakes": _rows(cur), "total": total, "page": page, "per_page": per_page}


@router.patch("/evolution/mistakes/{mistake_id}")
async def resolve_mistake(mistake_id: int, _: dict = Depends(require_admin)):
    conn = _db()
    conn.execute("UPDATE mistake_log SET resolved = TRUE WHERE id = ?", (mistake_id,))
    conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# SYSTEM
# ══════════════════════════════════════════════════════════════════

@router.get("/system/hardware")
async def get_hardware(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM hardware_profile WHERE is_current = TRUE")
    profile = _row(cur)
    cur = conn.execute("SELECT * FROM hardware_gpu ORDER BY gpu_index")
    gpus = _rows(cur)
    return {"profile": profile, "gpus": gpus}


@router.get("/system/models")
async def get_model_config(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM model_config ORDER BY role")
    return {"models": _rows(cur)}


@router.get("/system/services")
async def get_services(_: dict = Depends(require_admin)):
    conn = _db()
    cur = conn.execute("SELECT * FROM discovered_services ORDER BY service_type")
    return {"services": _rows(cur)}


@router.get("/system/backups")
async def get_backups(
    _: dict = Depends(require_admin),
    limit: int = Query(20, ge=1, le=100),
):
    conn = _db()
    cur = conn.execute("SELECT * FROM backup_log ORDER BY created_at DESC LIMIT ?", (limit,))
    return {"backups": _rows(cur)}


@router.get("/system/interactions")
async def get_interactions(
    _: dict = Depends(require_admin),
    user_id: str | None = None,
    layer: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _db()
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
    return {"interactions": _rows(cur), "total": total, "page": page, "per_page": per_page}


# ══════════════════════════════════════════════════════════════════
# SATELLITES
# ══════════════════════════════════════════════════════════════════

# Lazy-init singleton to avoid import-time side effects
_satellite_manager = None


def _get_satellite_manager():
    global _satellite_manager
    if _satellite_manager is None:
        from cortex.satellite.manager import SatelliteManager
        _satellite_manager = SatelliteManager()
    return _satellite_manager


class SatelliteAddRequest(BaseModel):
    ip_address: str
    mode: str = "dedicated"
    ssh_username: str = "atlas"
    ssh_password: str = "atlas"
    service_port: int = 5110


class SatelliteProvisionRequest(BaseModel):
    room: str
    display_name: str = ""
    features: dict = Field(default_factory=dict)
    ssh_password: str = "atlas"


class SatelliteUpdateRequest(BaseModel):
    display_name: str | None = None
    room: str | None = None
    wake_word: str | None = None
    volume: float | None = None
    mic_gain: float | None = None
    vad_sensitivity: float | None = None
    features: dict | None = None
    filler_enabled: bool | None = None
    filler_threshold_ms: int | None = None


@router.get("/satellites")
async def list_satellites(
    status: str | None = Query(None),
    mode: str | None = Query(None),
    admin: dict = Depends(require_admin),
):
    """List all satellites with optional filters."""
    mgr = _get_satellite_manager()
    satellites = mgr.list_satellites(status=status, mode=mode)
    # Include announced (undiscovered) count
    announced = await mgr.get_discovered()
    return {
        "satellites": satellites,
        "total": len(satellites),
        "announced_count": len(announced),
    }


@router.get("/satellites/announced")
async def list_announced(admin: dict = Depends(require_admin)):
    """List satellites that have self-announced but aren't yet registered."""
    mgr = _get_satellite_manager()
    announced = await mgr.get_discovered()
    return {
        "announced": [
            {
                "ip_address": s.ip_address,
                "hostname": s.hostname,
                "mac_address": s.mac_address,
                "port": s.port,
                "properties": s.properties,
                "discovered_at": s.discovered_at,
            }
            for s in announced
        ]
    }


@router.get("/satellites/{satellite_id}")
async def get_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Get satellite detail with hardware info."""
    mgr = _get_satellite_manager()
    sat = mgr.get_satellite(satellite_id)
    if not sat:
        raise HTTPException(status_code=404, detail="Satellite not found")
    return sat


@router.post("/satellites/discover")
async def discover_satellites(admin: dict = Depends(require_admin)):
    """Trigger a one-time network scan (fallback for mDNS-blocked networks)."""
    mgr = _get_satellite_manager()
    found = await mgr.scan_now()
    return {
        "found": [
            {
                "ip_address": s.ip_address,
                "hostname": s.hostname,
                "mac_address": s.mac_address,
                "discovery_method": s.discovery_method,
            }
            for s in found
        ],
        "count": len(found),
    }


@router.post("/satellites/add")
async def add_satellite(req: SatelliteAddRequest, admin: dict = Depends(require_admin)):
    """Manually add a satellite by IP address."""
    mgr = _get_satellite_manager()
    sat = await mgr.add_manual(
        ip_address=req.ip_address,
        mode=req.mode,
        ssh_username=req.ssh_username,
        ssh_password=req.ssh_password,
        service_port=req.service_port,
    )
    return sat


@router.post("/satellites/{satellite_id}/detect")
async def detect_hardware(
    satellite_id: str,
    ssh_password: str = "atlas",
    admin: dict = Depends(require_admin),
):
    """SSH into a satellite and detect its hardware."""
    mgr = _get_satellite_manager()
    try:
        profile = await mgr.detect_hardware(satellite_id, ssh_password=ssh_password)
        return {
            "satellite_id": satellite_id,
            "platform": profile.platform_short(),
            "hardware": profile.to_dict(),
            "capabilities": profile.capabilities_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/satellites/{satellite_id}/provision")
async def provision_satellite(
    satellite_id: str,
    req: SatelliteProvisionRequest,
    admin: dict = Depends(require_admin),
):
    """Start provisioning a satellite."""
    mgr = _get_satellite_manager()
    try:
        result = await mgr.provision(
            satellite_id=satellite_id,
            room=req.room,
            display_name=req.display_name,
            features=req.features,
            ssh_password=req.ssh_password,
        )
        return {
            "success": result.success,
            "error": result.error,
            "steps": [
                {"name": s.name, "status": s.status, "detail": s.detail}
                for s in result.steps
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/satellites/{satellite_id}")
async def update_satellite(
    satellite_id: str,
    req: SatelliteUpdateRequest,
    admin: dict = Depends(require_admin),
):
    """Update satellite configuration."""
    mgr = _get_satellite_manager()
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        sat = await mgr.reconfigure(satellite_id, **updates)
        return sat
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/satellites/{satellite_id}/restart")
async def restart_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Restart the satellite agent service."""
    mgr = _get_satellite_manager()
    sent = await mgr.restart_agent(satellite_id)
    return {"sent": sent, "detail": "Restart command sent" if sent else "Satellite not connected"}


@router.post("/satellites/{satellite_id}/identify")
async def identify_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Blink LEDs on a satellite for physical identification."""
    mgr = _get_satellite_manager()
    sent = await mgr.identify(satellite_id)
    return {"sent": sent}


@router.post("/satellites/{satellite_id}/test")
async def test_satellite_audio(satellite_id: str, admin: dict = Depends(require_admin)):
    """Run an audio test on the satellite."""
    mgr = _get_satellite_manager()
    sent = await mgr.test_audio(satellite_id)
    return {"sent": sent}


@router.post("/satellites/{satellite_id}/command")
async def send_satellite_command(satellite_id: str, body: dict, admin: dict = Depends(require_admin)):
    """Send an arbitrary command to a connected satellite."""
    from cortex.satellite.websocket import send_command
    action = body.get("action", "")
    params = body.get("params")
    if not action:
        raise HTTPException(status_code=400, detail="Missing action")
    sent = await send_command(satellite_id, action, params)
    return {"sent": sent}


@router.patch("/satellites/{satellite_id}/led_config")
async def update_led_config(satellite_id: str, body: dict, admin: dict = Depends(require_admin)):
    """Update LED pattern colors for a satellite and push live."""
    import json as _json
    from cortex.satellite.websocket import send_command
    patterns = body.get("patterns", {})
    if not patterns:
        raise HTTPException(status_code=400, detail="Missing patterns")
    # Store in DB
    db = get_db()
    existing = db.execute("SELECT led_config FROM satellites WHERE id = ?", (satellite_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Satellite not found")
    current = _json.loads(existing["led_config"]) if existing["led_config"] else {}
    current.update(patterns)
    db.execute("UPDATE satellites SET led_config = ? WHERE id = ?", (_json.dumps(current), satellite_id))
    db.commit()
    # Push to satellite
    sent = await send_command(satellite_id, "led_config", {"patterns": patterns})
    return {"saved": True, "pushed": sent}


@router.get("/satellites/{satellite_id}/led_config")
async def get_led_config(satellite_id: str, admin: dict = Depends(require_admin)):
    """Get the LED pattern configuration for a satellite."""
    import json as _json
    db = get_db()
    row = db.execute("SELECT led_config FROM satellites WHERE id = ?", (satellite_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Satellite not found")
    config = _json.loads(row["led_config"]) if row["led_config"] else {}
    # Return defaults merged with custom
    defaults = {
        "idle": {"r": 0, "g": 0, "b": 0, "brightness": 0.0},
        "listening": {"r": 0, "g": 100, "b": 255, "brightness": 0.4},
        "thinking": {"r": 255, "g": 165, "b": 0, "brightness": 0.3},
        "speaking": {"r": 0, "g": 200, "b": 100, "brightness": 0.4},
        "error": {"r": 255, "g": 0, "b": 0, "brightness": 0.5},
        "muted": {"r": 255, "g": 0, "b": 0, "brightness": 0.1},
        "wakeword": {"r": 0, "g": 200, "b": 255, "brightness": 0.6},
    }
    merged = {**defaults, **config}
    return {"patterns": merged}


@router.delete("/satellites/{satellite_id}")
async def remove_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Remove and deregister a satellite."""
    mgr = _get_satellite_manager()
    await mgr.remove(satellite_id)
    return {"removed": True}


# ── TTS Preview & Voice Management ─────────────────────────────


@router.get("/tts/voices")
async def list_tts_voices(admin: dict = Depends(require_admin)):
    """List available TTS voices from the Piper Wyoming service."""
    import os
    from cortex.voice.wyoming import WyomingClient
    host = os.environ.get("TTS_HOST", "localhost")
    port = int(os.environ.get("TTS_PORT", "10200"))
    tts = WyomingClient(host, port)
    try:
        voices = await tts.list_voices()
        return {"voices": voices}
    except Exception as e:
        return {"voices": [], "error": str(e)}


@router.post("/tts/preview")
async def preview_tts(body: dict, admin: dict = Depends(require_admin)):
    """Synthesize text and return WAV audio for browser playback or push to satellite."""
    import io
    import os
    import wave
    from fastapi.responses import Response
    from cortex.voice.wyoming import WyomingClient, WyomingError

    text = body.get("text", "Hello, I am Atlas.")
    voice = body.get("voice")
    target = body.get("target", "browser")  # "browser" or satellite_id

    host = os.environ.get("TTS_HOST", "localhost")
    port = int(os.environ.get("TTS_PORT", "10200"))
    tts = WyomingClient(host, port, timeout=30.0)

    try:
        audio_data, audio_info = await tts.synthesize(text, voice=voice)
    except WyomingError as e:
        raise HTTPException(status_code=502, detail=f"TTS error: {e}")

    if not audio_data:
        raise HTTPException(status_code=500, detail="TTS returned empty audio")

    rate = audio_info.get("rate", 22050)
    width = audio_info.get("width", 2)
    channels = audio_info.get("channels", 1)

    if target != "browser":
        # Push to satellite speaker at native TTS rate (hardware handles conversion)
        import base64
        conn = _connected_satellites_ref().get(target)
        if not conn:
            raise HTTPException(status_code=404, detail="Satellite not connected")
        await conn.send({"type": "TTS_START", "sample_rate": rate, "format": f"pcm_{rate}_{width*8}bit_{channels}ch"})
        for off in range(0, len(audio_data), 4096):
            await conn.send({"type": "TTS_CHUNK", "audio": base64.b64encode(audio_data[off:off+4096]).decode()})
        await conn.send({"type": "TTS_END"})
        return {"sent": True, "bytes": len(audio_data)}

    # Return WAV for browser playback
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(audio_data)
    wav_bytes = buf.getvalue()

    return Response(content=wav_bytes, media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=preview.wav"})


@router.post("/tts/filler_preview")
async def preview_filler(body: dict, admin: dict = Depends(require_admin)):
    """Synthesize a filler phrase and optionally push to satellite."""
    import os
    from cortex.filler import select_filler
    from cortex.voice.wyoming import WyomingClient

    sentiment = body.get("sentiment", "greeting")
    target = body.get("target", "browser")

    # Pick a filler
    filler_text = select_filler(sentiment, confidence=0.8, user_id="admin")
    if not filler_text:
        filler_text = "Hmm, let me think..."

    host = os.environ.get("TTS_HOST", "localhost")
    port = int(os.environ.get("TTS_PORT", "10200"))
    tts = WyomingClient(host, port, timeout=15.0)
    audio_data, audio_info = await tts.synthesize(filler_text)

    if target != "browser":
        import base64
        from cortex.satellite.websocket import _resample_pcm, get_connection
        rate = audio_info.get("rate", 22050)
        channels = audio_info.get("channels", 1)
        sat_audio = audio_data if rate == 16000 else _resample_pcm(audio_data, rate, 16000, channels)
        conn = get_connection(target)
        if not conn:
            raise HTTPException(status_code=404, detail="Satellite not connected")
        await conn.send({"type": "TTS_START", "sample_rate": 16000, "format": "pcm_16k_16bit_mono"})
        for off in range(0, len(sat_audio), 4096):
            await conn.send({"type": "TTS_CHUNK", "audio": base64.b64encode(sat_audio[off:off+4096]).decode()})
        await conn.send({"type": "TTS_END"})
        return {"sent": True, "filler": filler_text}

    import io, wave
    from fastapi.responses import Response
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(audio_info.get("channels", 1))
        wf.setsampwidth(audio_info.get("width", 2))
        wf.setframerate(audio_info.get("rate", 22050))
        wf.writeframes(audio_data)
    return Response(content=buf.getvalue(), media_type="audio/wav")


def _connected_satellites_ref():
    from cortex.satellite.websocket import get_connected_satellites
    return get_connected_satellites()
