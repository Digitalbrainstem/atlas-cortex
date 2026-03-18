"""Admin API — evolution runs, model registry, drift & quality metrics."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

log = logging.getLogger(__name__)

router = APIRouter()


# ── Request models ────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    days: int = 7
    min_interactions: int = 5


# ── Evolution profiles / logs / mistakes (existing) ───────────────


@router.get("/evolution/profiles")
async def list_profiles(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute(
        "SELECT user_id, rapport_score, preferred_tone, "
        "interaction_count, top_topics FROM emotional_profiles "
        "ORDER BY interaction_count DESC"
    )
    return {"profiles": _h._rows(cur)}


@router.get("/evolution/logs")
async def list_logs(
    _: dict = Depends(require_admin),
    limit: int = Query(50, ge=1, le=200),
):
    conn = _h._db()
    cur = conn.execute(
        "SELECT * FROM evolution_log ORDER BY run_at DESC LIMIT ?",
        (limit,),
    )
    return {"logs": _h._rows(cur)}


@router.get("/evolution/mistakes")
async def list_mistakes(_: dict = Depends(require_admin)):
    conn = _h._db()
    cur = conn.execute(
        "SELECT id, interaction_id, user_id, claim_text, correction_text, "
        "detection_method, mistake_category AS category, confidence_at_time, "
        "root_cause, resolved, created_at "
        "FROM mistake_log ORDER BY created_at DESC"
    )
    return {"mistakes": _h._rows(cur)}


@router.patch("/evolution/mistakes/{mistake_id}")
async def update_mistake(mistake_id: int, _: dict = Depends(require_admin), resolved: bool = True):
    conn = _h._db()
    conn.execute(
        "UPDATE mistake_log SET resolved = ? WHERE id = ?",
        (resolved, mistake_id),
    )
    conn.commit()
    return {"ok": True}


# ── Evolution runs ────────────────────────────────────────────────


@router.get("/evolution/runs")
async def list_runs(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    run_type: str | None = None,
    status: str | None = None,
):
    conn = _h._db()
    where, params = [], []
    if run_type:
        where.append("run_type = ?")
        params.append(run_type)
    if status:
        where.append("status = ?")
        params.append(status)

    where_sql = " AND ".join(where) if where else "1=1"
    total = conn.execute(
        f"SELECT COUNT(*) FROM evolution_runs WHERE {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM evolution_runs WHERE {where_sql} "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {"runs": _h._rows(cur), "total": total, "page": page, "per_page": per_page}


@router.get("/evolution/runs/{run_id}")
async def get_run(run_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute(
        "SELECT * FROM evolution_runs WHERE id = ?", (run_id,)
    ))
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")

    metrics_cur = conn.execute(
        "SELECT metric_name, metric_value, domain, created_at "
        "FROM evolution_metrics WHERE run_id = ? ORDER BY created_at",
        (run_id,),
    )
    row["metrics"] = _h._rows(metrics_cur)
    return row


# ── Trigger analysis ──────────────────────────────────────────────


@router.post("/evolution/analyze")
async def trigger_analysis(req: AnalyzeRequest, _: dict = Depends(require_admin)):
    conn = _h._db()
    config = json.dumps({"days": req.days, "min_interactions": req.min_interactions})
    conn.execute(
        "INSERT INTO evolution_runs (run_type, status, config, created_at) "
        "VALUES ('analysis', 'pending', ?, datetime('now'))",
        (config,),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("Queued evolution analysis run %s", run_id)
    return {"ok": True, "run_id": run_id}


# ── Model registry ───────────────────────────────────────────────


@router.get("/evolution/models")
async def list_models(
    _: dict = Depends(require_admin),
    status: str | None = None,
):
    conn = _h._db()
    if status:
        cur = conn.execute(
            "SELECT * FROM model_registry WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
    else:
        cur = conn.execute("SELECT * FROM model_registry ORDER BY created_at DESC")
    return {"models": _h._rows(cur)}


@router.post("/evolution/models/{model_id}/promote")
async def promote_model(model_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    if row["status"] == "retired":
        raise HTTPException(status_code=400, detail="Cannot promote a retired model")

    conn.execute(
        "UPDATE model_registry SET status = 'active', promoted_at = datetime('now') "
        "WHERE id = ?",
        (model_id,),
    )
    conn.commit()
    log.info("Promoted model %s (%s)", model_id, row["model_name"])
    return {"ok": True}


@router.post("/evolution/models/{model_id}/retire")
async def retire_model(model_id: int, _: dict = Depends(require_admin)):
    conn = _h._db()
    row = _h._row(conn.execute("SELECT * FROM model_registry WHERE id = ?", (model_id,)))
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")

    conn.execute(
        "UPDATE model_registry SET status = 'retired' WHERE id = ?",
        (model_id,),
    )
    conn.commit()
    log.info("Retired model %s (%s)", model_id, row["model_name"])
    return {"ok": True}


# ── Drift report ──────────────────────────────────────────────────


@router.get("/evolution/drift")
async def drift_report(_: dict = Depends(require_admin)):
    conn = _h._db()

    # Compute drift from recent evolution_metrics
    cur = conn.execute(
        "SELECT metric_name, metric_value, domain, created_at "
        "FROM evolution_metrics "
        "WHERE domain = 'personality' "
        "ORDER BY created_at DESC LIMIT 50"
    )
    personality_metrics = _h._rows(cur)

    # Calculate simple drift score from metric variance
    values = [m["metric_value"] for m in personality_metrics]
    if len(values) >= 2:
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        drift_score = min(variance ** 0.5, 1.0)
    else:
        drift_score = 0.0

    if drift_score < 0.15:
        drift_level = "low"
    elif drift_score < 0.4:
        drift_level = "medium"
    else:
        drift_level = "high"

    return {
        "drift_score": round(drift_score, 4),
        "drift_level": drift_level,
        "sample_count": len(values),
        "recent_metrics": personality_metrics[:10],
    }


# ── Quality metrics over time ────────────────────────────────────


@router.get("/evolution/metrics")
async def quality_metrics(
    _: dict = Depends(require_admin),
    domain: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    conn = _h._db()
    if domain:
        cur = conn.execute(
            "SELECT m.*, r.run_type FROM evolution_metrics m "
            "LEFT JOIN evolution_runs r ON m.run_id = r.id "
            "WHERE m.domain = ? ORDER BY m.created_at DESC LIMIT ?",
            (domain, limit),
        )
    else:
        cur = conn.execute(
            "SELECT m.*, r.run_type FROM evolution_metrics m "
            "LEFT JOIN evolution_runs r ON m.run_id = r.id "
            "ORDER BY m.created_at DESC LIMIT ?",
            (limit,),
        )
    return {"metrics": _h._rows(cur)}
