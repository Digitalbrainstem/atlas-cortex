"""Memory system admin endpoints.

Exposes knowledge tree, memory index stats, session history,
compaction controls, and proactive loader state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Knowledge tree ────────────────────────────────────────────────

@router.get("/memory/knowledge-tree")
async def get_knowledge_tree(_: dict = Depends(require_admin)):
    """Return the knowledge tree structure and stats."""
    try:
        from cortex.memory import KnowledgeTree, TreeStats
        tree = KnowledgeTree()
        stats: TreeStats = tree.stats()
        return {
            "stats": {
                "total_nodes": stats.total_nodes,
                "total_facts": stats.total_facts,
                "depth": stats.depth,
                "categories": stats.categories,
            },
            "tree": tree.to_dict() if hasattr(tree, "to_dict") else {},
        }
    except Exception as e:
        return {"stats": {}, "tree": {}, "error": str(e)}


# ── Memory index ──────────────────────────────────────────────────

@router.get("/memory/index-stats")
async def get_memory_index_stats(_: dict = Depends(require_admin)):
    """Return memory index statistics."""
    conn = _h._db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
    except Exception:
        total = 0

    try:
        metrics_cur = conn.execute(
            "SELECT operation, COUNT(*) as count, AVG(latency_ms) as avg_latency_ms "
            "FROM memory_metrics GROUP BY operation ORDER BY count DESC"
        )
        metrics = _h._rows(metrics_cur)
    except Exception:
        metrics = []

    try:
        recent_cur = conn.execute(
            "SELECT * FROM memory_metrics ORDER BY ts DESC LIMIT 20"
        )
        recent = _h._rows(recent_cur)
    except Exception:
        recent = []

    return {
        "total_entries": total,
        "operation_stats": metrics,
        "recent_operations": recent,
    }


# ── Session history ──────────────────────────────────────────────

@router.get("/memory/sessions")
async def list_memory_sessions(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user_id: str | None = None,
):
    """List conversation sessions."""
    conn = _h._db()
    where, params = [], []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where) if where else "1=1"
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM conversation_sessions WHERE {where_sql}", params
        ).fetchone()[0]
    except Exception:
        return {"sessions": [], "total": 0, "page": page, "per_page": per_page}

    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM conversation_sessions WHERE {where_sql} "
        "ORDER BY rowid DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"sessions": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


# ── Compaction ────────────────────────────────────────────────────

@router.get("/memory/compaction")
async def get_compaction_stats(_: dict = Depends(require_admin)):
    """Return context compaction statistics."""
    try:
        from cortex.memory import ContextCompactor
        compactor = ContextCompactor()
        stats = compactor.stats() if hasattr(compactor, "stats") else {}
        return {"stats": stats if isinstance(stats, dict) else {}}
    except Exception as e:
        return {"stats": {}, "error": str(e)}


@router.post("/memory/compaction/trigger")
async def trigger_compaction(_: dict = Depends(require_admin)):
    """Trigger a compaction cycle."""
    try:
        from cortex.memory import ContextCompactor
        compactor = ContextCompactor()
        result = compactor.compact() if hasattr(compactor, "compact") else None
        return {"ok": True, "result": str(result) if result else "triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Proactive loader ─────────────────────────────────────────────

@router.get("/memory/proactive-loader")
async def get_proactive_loader_state(_: dict = Depends(require_admin)):
    """Return proactive loader state and recent context bundles."""
    try:
        from cortex.memory import ProactiveLoader
        loader = ProactiveLoader()
        return {
            "enabled": True,
            "state": loader.state() if hasattr(loader, "state") else {},
        }
    except Exception as e:
        return {"enabled": False, "error": str(e)}


# ── CAG usage ─────────────────────────────────────────────────────

@router.get("/memory/cag-usage")
async def get_cag_usage(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent CAG (knowledge bank) usage logs."""
    conn = _h._db()
    try:
        cur = conn.execute(
            "SELECT * FROM cag_usage_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return {"usage": _h._rows(cur)}
    except Exception:
        return {"usage": []}
