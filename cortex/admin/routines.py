"""Admin routines endpoints — routine CRUD, triggers, run history, templates."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import _db, _rows, _row, require_admin
from cortex.routines.engine import RoutineEngine
from cortex.routines.templates import TEMPLATES, instantiate_template

router = APIRouter()

_engine = RoutineEngine()


# ── Request models ───────────────────────────────────────────────

class RoutineCreateRequest(BaseModel):
    name: str
    description: str = ""
    user_id: str = ""


class StepCreateRequest(BaseModel):
    action_type: str
    action_config: dict = {}
    step_order: int | None = None
    condition: str = ""
    on_error: str = "continue"


class TriggerCreateRequest(BaseModel):
    trigger_type: str
    trigger_config: dict = {}


class TemplateInstantiateRequest(BaseModel):
    user_id: str = ""


# ── Routine CRUD ─────────────────────────────────────────────────

@router.get("/routines")
async def list_routines(_: dict = Depends(require_admin)):
    conn = _db()
    routines = _rows(conn.execute("SELECT * FROM routines ORDER BY name"))

    for r in routines:
        rid = r["id"]
        r["steps"] = _rows(conn.execute(
            "SELECT * FROM routine_steps WHERE routine_id = ? ORDER BY step_order", (rid,)
        ))
        r["triggers"] = _rows(conn.execute(
            "SELECT * FROM routine_triggers WHERE routine_id = ?", (rid,)
        ))
        runs = _rows(conn.execute(
            "SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY started_at DESC LIMIT 5",
            (rid,),
        ))
        r["recent_runs"] = runs

    return {"routines": routines}


@router.post("/routines")
async def create_routine(req: RoutineCreateRequest, _: dict = Depends(require_admin)):
    rid = await _engine.create_routine(
        name=req.name, description=req.description, user_id=req.user_id,
    )
    return {"id": rid, "name": req.name}


@router.get("/routines/templates")
async def list_templates(_: dict = Depends(require_admin)):
    templates = []
    for tid, tmpl in TEMPLATES.items():
        templates.append({
            "id": tid,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "step_count": len(tmpl.get("steps", [])),
            "default_trigger": tmpl.get("default_trigger"),
        })
    return {"templates": templates}


@router.post("/routines/templates/{template_id}/instantiate")
async def instantiate(
    template_id: str,
    req: TemplateInstantiateRequest,
    _: dict = Depends(require_admin),
):
    if template_id not in TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    rid = await instantiate_template(_engine, template_id, user_id=req.user_id)
    return {"id": rid, "template_id": template_id, "name": TEMPLATES[template_id]["name"]}


@router.get("/routines/{routine_id}")
async def get_routine(routine_id: int, _: dict = Depends(require_admin)):
    routine = await _engine.get_routine(routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")

    conn = _db()
    runs = _rows(conn.execute(
        "SELECT * FROM routine_runs WHERE routine_id = ? ORDER BY started_at DESC LIMIT 10",
        (routine_id,),
    ))
    routine["recent_runs"] = runs
    return routine


@router.delete("/routines/{routine_id}")
async def delete_routine(routine_id: int, _: dict = Depends(require_admin)):
    ok = await _engine.delete_routine(routine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"deleted": True}


@router.post("/routines/{routine_id}/enable")
async def enable_routine(routine_id: int, _: dict = Depends(require_admin)):
    ok = await _engine.enable_routine(routine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"enabled": True}


@router.post("/routines/{routine_id}/disable")
async def disable_routine(routine_id: int, _: dict = Depends(require_admin)):
    ok = await _engine.disable_routine(routine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"enabled": False}


@router.post("/routines/{routine_id}/run")
async def run_routine(routine_id: int, _: dict = Depends(require_admin)):
    try:
        run_id = await _engine.run_routine(routine_id)
        return {"run_id": run_id, "status": "completed"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Steps ────────────────────────────────────────────────────────

@router.post("/routines/{routine_id}/steps")
async def add_step(
    routine_id: int, req: StepCreateRequest, _: dict = Depends(require_admin),
):
    routine = await _engine.get_routine(routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")

    step_id = await _engine.add_step(
        routine_id, req.action_type, req.action_config,
        step_order=req.step_order, condition=req.condition, on_error=req.on_error,
    )
    return {"id": step_id}


@router.delete("/routines/{routine_id}/steps/{step_id}")
async def remove_step(routine_id: int, step_id: int, _: dict = Depends(require_admin)):
    ok = await _engine.remove_step(step_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"deleted": True}


# ── Triggers ─────────────────────────────────────────────────────

@router.post("/routines/{routine_id}/triggers")
async def add_trigger(
    routine_id: int, req: TriggerCreateRequest, _: dict = Depends(require_admin),
):
    routine = await _engine.get_routine(routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")

    trigger_id = await _engine.add_trigger(
        routine_id, req.trigger_type, req.trigger_config,
    )
    return {"id": trigger_id}


@router.delete("/routines/{routine_id}/triggers/{trigger_id}")
async def remove_trigger(
    routine_id: int, trigger_id: int, _: dict = Depends(require_admin),
):
    ok = await _engine.remove_trigger(trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"deleted": True}
