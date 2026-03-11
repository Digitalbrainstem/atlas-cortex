"""User management and parental-controls endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── User CRUD ─────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
    offset = (page - 1) * per_page
    total = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
    cur = conn.execute(
        "SELECT * FROM user_profiles ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    return {"users": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


@router.get("/users/{user_id}")
async def get_user(user_id: str, _: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    user = _h._row(cur)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Attach emotional profile
    cur = conn.execute("SELECT * FROM emotional_profiles WHERE user_id = ?", (user_id,))
    user["emotional_profile"] = _h._row(cur)

    # Attach parental controls
    cur = conn.execute("SELECT * FROM parental_controls WHERE child_user_id = ?", (user_id,))
    user["parental_controls"] = _h._row(cur)

    # Attach topics
    cur = conn.execute(
        "SELECT topic, mention_count, last_mentioned FROM user_topics WHERE user_id = ? ORDER BY mention_count DESC LIMIT 20",
        (user_id,),
    )
    user["topics"] = _h._rows(cur)

    # Attach activity hours
    cur = conn.execute(
        "SELECT hour, interaction_count FROM user_activity_hours WHERE user_id = ? ORDER BY hour",
        (user_id,),
    )
    user["activity_hours"] = _h._rows(cur)

    return user


class UserCreate(BaseModel):
    display_name: str
    user_id: str | None = None


@router.post("/users")
async def create_user(body: UserCreate, _: dict = Depends(require_admin)):
    """Create a new user profile."""
    import uuid
    conn = _h._db()
    user_id = body.user_id or f"user-{uuid.uuid4().hex[:8]}"
    try:
        conn.execute(
            "INSERT INTO user_profiles (user_id, display_name) VALUES (?, ?)",
            (user_id, body.display_name),
        )
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"User already exists: {e}")
    cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    return _h._row(cur)


class UserUpdate(BaseModel):
    display_name: str | None = None
    age: int | None = None
    age_group: str | None = None
    age_confidence: float | None = None
    vocabulary_level: str | None = None
    preferred_tone: str | None = None
    communication_style: str | None = None
    humor_style: str | None = None
    preferred_voice: str | None = None
    is_parent: bool | None = None
    onboarding_complete: bool | None = None


@router.patch("/users/{user_id}")
async def update_user(user_id: str, update: UserUpdate, _: dict = Depends(require_admin)):
    conn = _h._db()
    # exclude_unset allows explicit empty strings (e.g. clearing preferred_voice)
    fields = {k: v for k, v in update.model_dump(exclude_unset=True).items()}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn.execute(f"UPDATE user_profiles SET {set_clause} WHERE user_id = ?", values)
    conn.commit()

    cur = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    return _h._row(cur)


class SetAgeRequest(BaseModel):
    birth_year: int
    birth_month: int = 1


@router.post("/users/{user_id}/age")
async def set_user_age(user_id: str, req: SetAgeRequest, _: dict = Depends(require_admin)):
    from cortex.profiles import set_user_age as _set_age
    conn = _h._db()
    result = _set_age(conn, user_id, birth_year=req.birth_year, birth_month=req.birth_month)
    return result


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, _: dict = Depends(require_admin)):
    conn = _h._db()
    conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM emotional_profiles WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM parental_controls WHERE child_user_id = ?", (user_id,))
    conn.commit()
    return {"ok": True}


# ── Parental Controls ─────────────────────────────────────────────


class ParentalControlsRequest(BaseModel):
    parent_user_id: str
    content_filter_level: str = "strict"
    allowed_hours_start: str = "07:00"
    allowed_hours_end: str = "21:00"
    restricted_topics: list[str] = Field(default_factory=list)
    restricted_actions: list[str] = Field(default_factory=list)


@router.get("/users/{user_id}/parental")
async def get_parental_controls(user_id: str, _: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute("SELECT * FROM parental_controls WHERE child_user_id = ?", (user_id,))
    controls = _h._row(cur)
    if not controls:
        return {"controls": None}

    cur = conn.execute(
        "SELECT action FROM parental_restricted_actions WHERE child_user_id = ?", (user_id,)
    )
    controls["restricted_actions"] = [r["action"] for r in _h._rows(cur)]

    cur = conn.execute(
        "SELECT entity_id FROM parental_allowed_devices WHERE child_user_id = ?", (user_id,)
    )
    controls["allowed_devices"] = [r["entity_id"] for r in _h._rows(cur)]

    return {"controls": controls}


@router.post("/users/{user_id}/parental")
async def set_parental_controls(
    user_id: str, req: ParentalControlsRequest, _: dict = Depends(require_admin)
):
    conn = _h._db()
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
    conn = _h._db()
    conn.execute("DELETE FROM parental_controls WHERE child_user_id = ?", (user_id,))
    conn.commit()
    return {"ok": True}
