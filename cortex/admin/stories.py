"""Admin API — story library, chapters, characters, and progress."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

log = logging.getLogger(__name__)

router = APIRouter()


# ── Request models ────────────────────────────────────────────────


class CreateStoryRequest(BaseModel):
    title: str
    genre: str = "adventure"
    age_group: str = "child"
    total_chapters: int = 5
    interactive: bool = False


class AssignVoiceRequest(BaseModel):
    name: str
    voice_id: str
    voice_style: str = ""
    description: str = ""


# ── Story CRUD ────────────────────────────────────────────────────


@router.get("/stories")
async def list_stories(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    genre: str | None = None,
    age_group: str | None = None,
):
    conn = _h._db()
    where, params = [], []
    if genre:
        where.append("s.genre = ?")
        params.append(genre)
    if age_group:
        where.append("s.target_age_group = ?")
        params.append(age_group)

    where_sql = " AND ".join(where) if where else "1=1"
    total = conn.execute(
        f"SELECT COUNT(*) FROM stories s WHERE {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT s.*, "
        "(SELECT COUNT(*) FROM story_chapters sc WHERE sc.story_id = s.id) AS chapter_count "
        f"FROM stories s WHERE {where_sql} "
        "ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"stories": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


@router.post("/stories")
async def create_story(req: CreateStoryRequest, _: dict = Depends(require_admin)):
    conn = _h._db()
    conn.execute(
        "INSERT INTO stories (title, genre, target_age_group, total_chapters, "
        "is_interactive, parent_approved, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, datetime('now'))",
        (req.title, req.genre, req.age_group, req.total_chapters, int(req.interactive)),
    )
    conn.commit()
    story_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Created story %s: %s", story_id, req.title)
    return {"ok": True, "id": story_id}


# ── Progress (before parameterized routes) ────────────────────────


@router.get("/stories/progress")
async def all_progress(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute(
        "SELECT sp.*, s.title AS story_title "
        "FROM story_progress sp "
        "LEFT JOIN stories s ON sp.story_id = s.id "
        "ORDER BY sp.last_listened DESC"
    )
    return {"progress": _h._rows(cur)}


# ── Character voices (before parameterized routes) ────────────────


@router.get("/stories/characters/{story_id}")
async def list_characters(story_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT id FROM stories WHERE id = ?", (story_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")
    cur = conn.execute(
        "SELECT * FROM story_characters WHERE story_id = ? ORDER BY name",
        (story_id,),
    )
    return {"characters": _h._rows(cur)}


@router.post("/stories/characters/{story_id}")
async def assign_character_voice(
    story_id: int, req: AssignVoiceRequest, _: dict = Depends(require_admin),
):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT id FROM stories WHERE id = ?", (story_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")

    conn.execute(
        "INSERT INTO story_characters (story_id, name, description, voice_id, "
        "voice_style, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (story_id, req.name, req.description, req.voice_id, req.voice_style),
    )
    conn.commit()
    char_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Assigned voice %s to character %s in story %s", req.voice_id, req.name, story_id)
    return {"ok": True, "id": char_id}


# ── Parameterized story routes ────────────────────────────────────


@router.get("/stories/{story_id}")
async def get_story(story_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")

    chapters_cur = conn.execute(
        "SELECT id, chapter_number, title, choices, narrator_voice, "
        "audio_cached, duration_seconds "
        "FROM story_chapters WHERE story_id = ? ORDER BY chapter_number",
        (story_id,),
    )
    row["chapters"] = _h._rows(chapters_cur)

    chars_cur = conn.execute(
        "SELECT id, name, description, voice_id, voice_style "
        "FROM story_characters WHERE story_id = ?",
        (story_id,),
    )
    row["characters"] = _h._rows(chars_cur)

    return row


@router.delete("/stories/{story_id}")
async def delete_story(story_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT id FROM stories WHERE id = ?", (story_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")

    conn.execute("DELETE FROM story_characters WHERE story_id = ?", (story_id,))
    conn.execute("DELETE FROM story_chapters WHERE story_id = ?", (story_id,))
    conn.execute("DELETE FROM story_progress WHERE story_id = ?", (story_id,))
    conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
    conn.commit()
    log.info("Deleted story %s", story_id)
    return {"ok": True}


# ── Approval ──────────────────────────────────────────────────────


@router.post("/stories/{story_id}/approve")
async def approve_story(story_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT id FROM stories WHERE id = ?", (story_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found")
    conn.execute(
        "UPDATE stories SET parent_approved = 1 WHERE id = ?",
        (story_id,),
    )
    conn.commit()
    return {"ok": True}
