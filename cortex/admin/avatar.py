"""Avatar skins, assignments, and audio-routing endpoints."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


class AvatarSkinCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    type: str = Field(default="svg", pattern=r"^(svg|sprite|custom)$")
    path: str = Field(default="")
    is_default: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AvatarAssignment(BaseModel):
    skin_id: str


@router.get("/avatar/skins")
def list_avatar_skins(db: sqlite3.Connection = Depends(_h._db)):
    """List all available avatar skins."""
    rows = db.execute(
        "SELECT id, name, type, path, is_default, metadata, created_at FROM avatar_skins ORDER BY is_default DESC, name"
    ).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "type": r[2], "path": r[3],
            "is_default": bool(r[4]), "metadata": r[5], "created_at": r[6],
        }
        for r in rows
    ]


@router.post("/avatar/skins", status_code=201)
def create_avatar_skin(skin: AvatarSkinCreate, db: sqlite3.Connection = Depends(_h._db)):
    """Create a new avatar skin."""
    import json
    try:
        db.execute(
            "INSERT INTO avatar_skins (id, name, type, path, is_default, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (skin.id, skin.name, skin.type, skin.path, skin.is_default, json.dumps(skin.metadata)),
        )
        if skin.is_default:
            db.execute("UPDATE avatar_skins SET is_default = FALSE WHERE id != ?", (skin.id,))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Skin '{skin.id}' already exists")
    return {"id": skin.id, "name": skin.name}


@router.get("/avatar/skins/{skin_id}")
def get_avatar_skin(skin_id: str, db: sqlite3.Connection = Depends(_h._db)):
    """Get a single avatar skin by ID."""
    row = db.execute(
        "SELECT id, name, type, path, is_default, metadata, created_at FROM avatar_skins WHERE id = ?",
        (skin_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Skin not found")
    return {
        "id": row[0], "name": row[1], "type": row[2], "path": row[3],
        "is_default": bool(row[4]), "metadata": row[5], "created_at": row[6],
    }


@router.delete("/avatar/skins/{skin_id}")
def delete_avatar_skin(skin_id: str, db: sqlite3.Connection = Depends(_h._db)):
    """Delete an avatar skin. Cannot delete the default skin."""
    row = db.execute("SELECT is_default FROM avatar_skins WHERE id = ?", (skin_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Skin not found")
    if row[0]:
        raise HTTPException(status_code=400, detail="Cannot delete the default skin")
    db.execute("DELETE FROM avatar_skins WHERE id = ?", (skin_id,))
    db.commit()
    return {"deleted": skin_id}


@router.get("/avatar/default")
def get_default_avatar_skin(db: sqlite3.Connection = Depends(_h._db)):
    """Get the default avatar skin."""
    row = db.execute(
        "SELECT id, name, type, path, metadata FROM avatar_skins WHERE is_default = TRUE"
    ).fetchone()
    if not row:
        return {"id": None, "name": None}
    return {"id": row[0], "name": row[1], "type": row[2], "path": row[3], "metadata": row[4]}


@router.put("/avatar/default/{skin_id}")
def set_default_avatar_skin(skin_id: str, db: sqlite3.Connection = Depends(_h._db)):
    """Set a skin as the new default."""
    row = db.execute("SELECT id FROM avatar_skins WHERE id = ?", (skin_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Skin not found")
    db.execute("UPDATE avatar_skins SET is_default = FALSE")
    db.execute("UPDATE avatar_skins SET is_default = TRUE WHERE id = ?", (skin_id,))
    db.commit()
    return {"default": skin_id}


@router.get("/avatar/assignments")
def list_avatar_assignments(db: sqlite3.Connection = Depends(_h._db)):
    """List all user-to-skin assignments."""
    rows = db.execute(
        "SELECT a.user_id, a.skin_id, s.name, a.assigned_at "
        "FROM avatar_assignments a JOIN avatar_skins s ON a.skin_id = s.id "
        "ORDER BY a.assigned_at DESC"
    ).fetchall()
    return [
        {"user_id": r[0], "skin_id": r[1], "skin_name": r[2], "assigned_at": r[3]}
        for r in rows
    ]


@router.put("/avatar/assignments/{user_id}")
def assign_avatar_skin(user_id: str, body: AvatarAssignment, db: sqlite3.Connection = Depends(_h._db)):
    """Assign a specific avatar skin to a user."""
    row = db.execute("SELECT id FROM avatar_skins WHERE id = ?", (body.skin_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Skin not found")
    db.execute(
        "INSERT OR REPLACE INTO avatar_assignments (user_id, skin_id) VALUES (?, ?)",
        (user_id, body.skin_id),
    )
    db.commit()
    return {"user_id": user_id, "skin_id": body.skin_id}


@router.delete("/avatar/assignments/{user_id}")
def remove_avatar_assignment(user_id: str, db: sqlite3.Connection = Depends(_h._db)):
    """Remove a user's skin assignment (reverts to default)."""
    db.execute("DELETE FROM avatar_assignments WHERE user_id = ?", (user_id,))
    db.commit()
    return {"user_id": user_id, "reverted_to": "default"}


# ── Audio routing ────────────────────────────────────────────────


class AudioRouteUpdate(BaseModel):
    route: str = Field(..., pattern="^(avatar|satellite|both)$")


@router.get("/avatar/audio-route/{room}")
def get_audio_route(room: str):
    """Get the audio routing mode for a room."""
    from cortex.avatar.websocket import get_audio_route
    return {"room": room, "route": get_audio_route(room)}


@router.put("/avatar/audio-route/{room}")
def set_audio_route(room: str, body: AudioRouteUpdate):
    """Set audio routing for a room: 'avatar', 'satellite', or 'both'."""
    from cortex.avatar.websocket import set_audio_route as _set_route
    _set_route(room, body.route)
    return {"room": room, "route": body.route}
