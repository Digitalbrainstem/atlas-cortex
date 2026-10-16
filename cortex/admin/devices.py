"""Voice/speakers and HA device/command-pattern endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Voice / Speakers ──────────────────────────────────────────────


class VoiceEnrollmentRequest(BaseModel):
    user_id: str
    audio_base64: str = Field(min_length=1, max_length=640000)
    sample_rate: Literal[16000] = 16000


@router.post("/voice/speakers/enroll")
async def enroll_speaker(body: VoiceEnrollmentRequest, _: dict = Depends(require_admin)):
    import base64
    import logging
    import uuid
    from cortex.speech.recognition import audio_embedding
    conn = _h._db()
    profile = conn.execute("SELECT display_name FROM user_profiles WHERE user_id=?", (body.user_id,)).fetchone()
    if profile is None:
        raise HTTPException(status_code=404, detail="Household profile not found")
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
        if len(audio) < 32000:
            raise ValueError("Enrollment requires at least one second of speech; 10-15 seconds is recommended")
        embedding = await audio_embedding(audio)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ImportError, OSError, RuntimeError) as exc:
        logging.getLogger(__name__).exception("Speaker enrollment backend unavailable")
        raise HTTPException(status_code=503, detail="Speaker recognition is unavailable") from exc
    if conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (body.user_id,)).fetchone() is None:
        raise HTTPException(status_code=409, detail="Household profile was deleted during enrollment")
    speaker_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO speaker_profiles(id,user_id,display_name,embedding) VALUES(?,?,?,?)",
        (speaker_id, body.user_id, profile["display_name"], embedding),
    )
    conn.commit()
    return {"ok": True, "speaker_id": speaker_id}


@router.get("/voice/speakers")
async def list_speakers(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute(
        "SELECT id, user_id, display_name, enrolled_at, sample_count, last_verified, "
        "confidence_threshold FROM speaker_profiles ORDER BY enrolled_at DESC"
    )
    return {"speakers": _h._rows(cur)}


@router.delete("/voice/speakers/{speaker_id}")
async def delete_speaker(speaker_id: str, _: dict = Depends(require_admin)):
    conn = _h._db()
    conn.execute("DELETE FROM speaker_profiles WHERE id = ?", (speaker_id,))
    conn.commit()
    return {"ok": True}


@router.patch("/voice/speakers/{speaker_id}")
async def update_speaker(
    speaker_id: str,
    update: dict,
    _: dict = Depends(require_admin),
):
    conn = _h._db()
    if "confidence_threshold" in update:
        threshold = update["confidence_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.85 <= threshold <= 1:
            raise HTTPException(status_code=400, detail="Recognition threshold must be between 0.85 and 1")
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


# ── Devices & Command Patterns ────────────────────────────────────


@router.get("/devices")
async def list_devices(
    _: dict = Depends(require_admin),
    domain: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
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
    devices = _h._rows(cur)

    # Attach aliases to each device
    for dev in devices:
        cur = conn.execute(
            "SELECT alias, source FROM device_aliases WHERE entity_id = ?",
            (dev["entity_id"],),
        )
        dev["aliases"] = _h._rows(cur)

    return {"devices": devices, "total": total, "page": page, "per_page": per_page}


@router.get("/devices/patterns")
async def list_command_patterns(
    _: dict = Depends(require_admin),
    source: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
):
    conn = _h._db()
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
    return {"patterns": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


class PatternUpdate(BaseModel):
    pattern: str | None = None
    intent: str | None = None
    confidence: float | None = None
    response_template: str | None = None


@router.patch("/devices/patterns/{pattern_id}")
async def update_command_pattern(
    pattern_id: int, update: PatternUpdate, _: dict = Depends(require_admin)
):
    conn = _h._db()
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
    conn = _h._db()
    conn.execute("DELETE FROM command_patterns WHERE id = ?", (pattern_id,))
    conn.commit()
    return {"ok": True}
