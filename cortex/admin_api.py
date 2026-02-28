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
    allowed = {"display_name", "user_id", "confidence_threshold"}
    fields = {k: v for k, v in update.items() if k in allowed and v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE speaker_profiles SET {set_clause} WHERE id = ?",
        list(fields.values()) + [speaker_id],
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
