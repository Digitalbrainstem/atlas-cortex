"""Dashboard endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


@router.get("/dashboard")
async def dashboard(_: dict = Depends(require_admin)):
    conn = _h._db()
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
    stats["recent_safety_events"] = _h._rows(cur)

    # Recent interactions
    cur = conn.execute(
        "SELECT id, user_id, message, matched_layer, sentiment, response_time_ms, created_at "
        "FROM interactions ORDER BY created_at DESC LIMIT 10"
    )
    stats["recent_interactions"] = _h._rows(cur)

    # Layer distribution
    cur = conn.execute(
        "SELECT matched_layer, COUNT(*) as count FROM interactions GROUP BY matched_layer"
    )
    stats["layer_distribution"] = _h._rows(cur)

    return stats
