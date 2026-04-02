"""Tools admin endpoints.

Exposes code sandbox execution history, function calling registry,
and documentation lookup cache.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Code sandbox ──────────────────────────────────────────────────

@router.get("/tools/sandbox")
async def get_sandbox_executions(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return recent code sandbox executions."""
    try:
        from cortex.tools.code_sandbox import get_sandbox
        sb = get_sandbox()
        if sb is None:
            return {"executions": [], "enabled": False}
        return {
            "enabled": True,
            "executions": sb.recent_executions(limit) if hasattr(sb, "recent_executions") else [],
            "stats": sb.stats() if hasattr(sb, "stats") else {},
        }
    except Exception as e:
        return {"executions": [], "enabled": False, "error": str(e)}


# ── Function calling ──────────────────────────────────────────────

@router.get("/tools/functions")
async def get_function_registry(_: dict = Depends(require_admin)):
    """Return registered function calling tools."""
    try:
        from cortex.tools.function_calling import get_function_registry
        reg = get_function_registry()
        if reg is None:
            return {"functions": []}
        funcs = reg.list_functions() if hasattr(reg, "list_functions") else []
        return {
            "functions": funcs if isinstance(funcs, list) else [],
            "total": len(funcs) if isinstance(funcs, list) else 0,
        }
    except Exception as e:
        return {"functions": [], "error": str(e)}


# ── Doc lookup ────────────────────────────────────────────────────

@router.get("/tools/doc-cache")
async def get_doc_cache(_: dict = Depends(require_admin)):
    """Return documentation lookup cache stats."""
    try:
        from cortex.tools.doc_lookup import get_doc_lookup
        dl = get_doc_lookup()
        if dl is None:
            return {"cache": {}, "enabled": False}
        return {
            "enabled": True,
            "cache": dl.cache_stats() if hasattr(dl, "cache_stats") else {},
            "entries": dl.cache_size() if hasattr(dl, "cache_size") else 0,
        }
    except Exception as e:
        return {"cache": {}, "enabled": False, "error": str(e)}


# ── Plugin registry (from DB) ────────────────────────────────────

@router.get("/tools/plugin-registry")
async def get_plugin_registry_stats(_: dict = Depends(require_admin)):
    """Return plugin registry statistics from the database."""
    conn = _h._db()
    try:
        cur = conn.execute(
            "SELECT id, plugin_type, display_name, is_active, pattern_count, "
            "health_status, last_health_check, activated_at "
            "FROM plugin_registry ORDER BY display_name"
        )
        return {"plugins": _h._rows(cur)}
    except Exception:
        return {"plugins": []}
