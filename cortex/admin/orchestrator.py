"""Orchestrator admin endpoints.

Exposes agent routing stats, active agents, and routing decisions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Agent routing stats ───────────────────────────────────────────

@router.get("/orchestrator/routing")
async def get_agent_routing_stats(_: dict = Depends(require_admin)):
    """Return agent orchestrator routing statistics."""
    try:
        from cortex.orchestrator.agent_orchestrator import get_agent_orchestrator
        ao = get_agent_orchestrator()
        if ao is None:
            return {"stats": {}, "enabled": False}
        return {
            "enabled": True,
            "stats": ao.stats() if hasattr(ao, "stats") else {},
            "routing_rules": ao.routing_rules() if hasattr(ao, "routing_rules") else [],
        }
    except Exception as e:
        return {"stats": {}, "enabled": False, "error": str(e)}


# ── Active agents ─────────────────────────────────────────────────

@router.get("/orchestrator/agents")
async def get_active_agents(_: dict = Depends(require_admin)):
    """Return list of currently active agents."""
    try:
        from cortex.orchestrator.agent_orchestrator import get_agent_orchestrator
        ao = get_agent_orchestrator()
        if ao is None:
            return {"agents": []}
        agents = ao.active_agents() if hasattr(ao, "active_agents") else []
        return {
            "agents": agents if isinstance(agents, list) else [],
            "total": len(agents) if isinstance(agents, list) else 0,
        }
    except Exception as e:
        return {"agents": [], "error": str(e)}


# ── Routing decisions ─────────────────────────────────────────────

@router.get("/orchestrator/decisions")
async def get_routing_decisions(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent routing decisions from interactions log."""
    conn = _h._db()
    try:
        cur = conn.execute(
            "SELECT id, user_id, speaker_id, message, matched_layer, intent, "
            "response_time_ms, llm_model, filler_used, created_at "
            "FROM interactions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return {"decisions": _h._rows(cur)}
    except Exception:
        return {"decisions": []}


# ── Voice pipeline stats ─────────────────────────────────────────

@router.get("/orchestrator/voice-stats")
async def get_voice_pipeline_stats(_: dict = Depends(require_admin)):
    """Return voice pipeline statistics."""
    conn = _h._db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "AVG(response_time_ms) as avg_latency_ms, "
            "SUM(CASE WHEN filler_used IS NOT NULL THEN 1 ELSE 0 END) as filler_count "
            "FROM interactions WHERE matched_layer IS NOT NULL"
        ).fetchone()
        cols = ["total", "avg_latency_ms", "filler_count"]
        stats = dict(zip(cols, row)) if row else {}
    except Exception:
        stats = {}

    return {"stats": stats}


# ── Wake word config ──────────────────────────────────────────────

@router.get("/orchestrator/wake-word")
async def get_wake_word_config(_: dict = Depends(require_admin)):
    """Return wake word configuration."""
    from cortex.wake_word import is_enabled, get_keywords
    return {
        "enabled": is_enabled(),
        "keywords": sorted(get_keywords()),
    }


@router.put("/orchestrator/wake-word")
async def update_wake_word_config(body: dict, _: dict = Depends(require_admin)):
    """Update wake word configuration."""
    from cortex.wake_word import set_enabled, set_keywords
    if "enabled" in body:
        set_enabled(bool(body["enabled"]))
    if "keywords" in body:
        set_keywords(set(body["keywords"]))
    from cortex.wake_word import is_enabled, get_keywords
    return {
        "ok": True,
        "enabled": is_enabled(),
        "keywords": sorted(get_keywords()),
    }
