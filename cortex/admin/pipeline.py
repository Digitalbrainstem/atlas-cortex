"""Pipeline admin endpoints.

Exposes model router decisions, thinking router stats, model manager
state, request queue depth, and crash handler circuit breaker status.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Model manager ─────────────────────────────────────────────────

@router.get("/pipeline/models")
async def get_model_manager_state(_: dict = Depends(require_admin)):
    """Return loaded models and VRAM usage."""
    try:
        from cortex.pipeline.model_manager import get_model_manager
        mm = get_model_manager()
        if mm is None:
            return {"models": [], "vram": {}}
        return {
            "models": mm.list_models() if hasattr(mm, "list_models") else [],
            "vram": mm.vram_usage() if hasattr(mm, "vram_usage") else {},
            "status": mm.status() if hasattr(mm, "status") else "unknown",
        }
    except Exception as e:
        return {"models": [], "vram": {}, "error": str(e)}


# ── Model router decisions ────────────────────────────────────────

@router.get("/pipeline/routing")
async def get_routing_decisions(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent model routing decisions from interactions log."""
    conn = _h._db()
    try:
        cur = conn.execute(
            "SELECT id, user_id, message, matched_layer, intent, llm_model, "
            "response_time_ms, confidence_score, created_at "
            "FROM interactions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = _h._rows(cur)
    except Exception:
        rows = []

    # Aggregate layer distribution
    try:
        dist_cur = conn.execute(
            "SELECT matched_layer, COUNT(*) as count "
            "FROM interactions GROUP BY matched_layer ORDER BY count DESC"
        )
        distribution = _h._rows(dist_cur)
    except Exception:
        distribution = []

    return {"decisions": rows, "layer_distribution": distribution}


# ── Thinking router ───────────────────────────────────────────────

@router.get("/pipeline/thinking")
async def get_thinking_stats(_: dict = Depends(require_admin)):
    """Return thinking model router statistics."""
    try:
        from cortex.pipeline.thinking_router import get_thinking_router
        tr = get_thinking_router()
        if tr is None:
            return {"stats": {}, "enabled": False}
        return {
            "enabled": True,
            "stats": tr.stats() if hasattr(tr, "stats") else {},
        }
    except Exception as e:
        return {"stats": {}, "enabled": False, "error": str(e)}


# ── Request queue ─────────────────────────────────────────────────

@router.get("/pipeline/queue")
async def get_request_queue_state(_: dict = Depends(require_admin)):
    """Return request queue depth and processing stats."""
    try:
        from cortex.pipeline.request_queue import get_request_queue
        rq = get_request_queue()
        if rq is None:
            return {"depth": 0, "active": 0}
        return {
            "depth": rq.depth() if hasattr(rq, "depth") else 0,
            "active": rq.active_count() if hasattr(rq, "active_count") else 0,
            "stats": rq.stats() if hasattr(rq, "stats") else {},
        }
    except Exception as e:
        return {"depth": 0, "active": 0, "error": str(e)}


# ── Crash handler ─────────────────────────────────────────────────

@router.get("/pipeline/crash-handler")
async def get_crash_handler_state(_: dict = Depends(require_admin)):
    """Return crash handler / circuit breaker state."""
    try:
        from cortex.pipeline.crash_handler import get_crash_handler
        ch = get_crash_handler()
        if ch is None:
            return {"state": "unknown", "errors": []}
        return {
            "state": ch.state() if hasattr(ch, "state") else "unknown",
            "error_count": ch.error_count() if hasattr(ch, "error_count") else 0,
            "recent_errors": ch.recent_errors() if hasattr(ch, "recent_errors") else [],
            "circuit_open": ch.is_open() if hasattr(ch, "is_open") else False,
        }
    except Exception as e:
        return {"state": "error", "errors": [], "error": str(e)}


# ── Pipeline stats summary ────────────────────────────────────────

@router.get("/pipeline/stats")
async def get_pipeline_stats(_: dict = Depends(require_admin)):
    """Return overall pipeline statistics from interactions table."""
    conn = _h._db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "AVG(response_time_ms) as avg_response_ms, "
            "MIN(response_time_ms) as min_response_ms, "
            "MAX(response_time_ms) as max_response_ms "
            "FROM interactions"
        ).fetchone()
        cols = ["total", "avg_response_ms", "min_response_ms", "max_response_ms"]
        stats = dict(zip(cols, row)) if row else {}
    except Exception:
        stats = {}

    return {"stats": stats}
