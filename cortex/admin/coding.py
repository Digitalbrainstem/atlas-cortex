"""Coding pipeline admin endpoints.

Exposes coding pipeline configuration, error memory, known bugs,
and code review triggers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from cortex.admin.helpers import require_admin

router = APIRouter()


# ── Pipeline config ───────────────────────────────────────────────

@router.get("/coding/config")
async def get_coding_config(_: dict = Depends(require_admin)):
    """Return coding pipeline configuration."""
    try:
        from cortex.coding.pipeline import CodingPipeline
        cp = CodingPipeline()
        return {
            "config": cp.config() if hasattr(cp, "config") else {},
            "enabled": True,
        }
    except Exception as e:
        return {"config": {}, "enabled": False, "error": str(e)}


# ── Error memory ──────────────────────────────────────────────────

@router.get("/coding/errors")
async def get_error_memory(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return coding error memory entries."""
    try:
        from cortex.coding.error_memory import ErrorMemory
        em = ErrorMemory()
        entries = em.recent(limit) if hasattr(em, "recent") else []
        return {
            "errors": entries if isinstance(entries, list) else [],
            "total": len(entries) if isinstance(entries, list) else 0,
            "stats": em.stats() if hasattr(em, "stats") else {},
        }
    except Exception as e:
        return {"errors": [], "total": 0, "error": str(e)}


# ── Known bugs ────────────────────────────────────────────────────

@router.get("/coding/known-bugs")
async def get_known_bugs(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    """Return known bug database."""
    try:
        from cortex.coding.known_bugs import KnownBugs
        kb = KnownBugs()
        bugs = kb.list_bugs(limit) if hasattr(kb, "list_bugs") else []
        return {
            "bugs": bugs if isinstance(bugs, list) else [],
            "total": len(bugs) if isinstance(bugs, list) else 0,
        }
    except Exception as e:
        return {"bugs": [], "total": 0, "error": str(e)}


# ── Code review trigger ──────────────────────────────────────────

@router.post("/coding/review")
async def trigger_code_review(body: dict, _: dict = Depends(require_admin)):
    """Trigger a code review on given input."""
    code = body.get("code", "")
    language = body.get("language", "python")
    if not code:
        raise HTTPException(status_code=400, detail="'code' field required")

    try:
        from cortex.coding.reviewer import CodeReviewer
        reviewer = CodeReviewer()
        result = reviewer.review(code, language=language) if hasattr(reviewer, "review") else {}
        return {"review": result if isinstance(result, dict) else str(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Pipeline stats ────────────────────────────────────────────────

@router.get("/coding/stats")
async def get_coding_stats(_: dict = Depends(require_admin)):
    """Return coding pipeline aggregate stats."""
    try:
        from cortex.coding.pipeline import CodingPipeline
        cp = CodingPipeline()
        return {"stats": cp.stats() if hasattr(cp, "stats") else {}}
    except Exception as e:
        return {"stats": {}, "error": str(e)}
