"""Admin API — LoRA adapter management."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import require_admin

log = logging.getLogger(__name__)

router = APIRouter()


# ── Request models ────────────────────────────────────────────────


class ComposeRequest(BaseModel):
    domain: str
    base_model: str
    adapter_path: str


class ComposeAllRequest(BaseModel):
    base_model: str | None = None
    lora_dir: str | None = None


# ── Helpers ───────────────────────────────────────────────────────


def _get_mgr():
    from cortex.evolution.lora_manager import get_lora_manager

    mgr = get_lora_manager()
    if mgr is None:
        raise HTTPException(
            status_code=503,
            detail="LoRA manager not initialized (LORA_AUTO_COMPOSE may be disabled)",
        )
    return mgr


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/loras")
async def list_loras(_: dict = Depends(require_admin)):
    """List discovered LoRAs with their composed status."""
    mgr = _get_mgr()
    lora_dir = os.getenv("LORA_DIR", os.path.expanduser("~/.cortex/loras"))

    discovered = mgr.discover(lora_dir)
    active = {cm.domain: cm for cm in mgr.list_active()}

    items = []
    # Include discovered adapters
    for domain, path in discovered.items():
        cm = active.get(domain)
        items.append({
            "domain": domain,
            "adapter_path": str(path),
            "composed": cm is not None,
            "model_name": cm.model_name if cm else None,
            "base_model": cm.base_model if cm else None,
        })

    # Include composed models not found on disk (pre-existing in Ollama)
    for domain, cm in active.items():
        if domain not in discovered:
            items.append({
                "domain": domain,
                "adapter_path": cm.adapter_path,
                "composed": True,
                "model_name": cm.model_name,
                "base_model": cm.base_model,
            })

    return {"loras": items}


@router.post("/loras/compose")
async def compose_lora(req: ComposeRequest, _: dict = Depends(require_admin)):
    """Compose a specific LoRA adapter into an Ollama model."""
    mgr = _get_mgr()
    result = await mgr.compose(req.domain, req.base_model, req.adapter_path)
    if result is None:
        raise HTTPException(status_code=500, detail="Composition failed — check server logs")
    return {
        "ok": True,
        "model_name": result.model_name,
        "domain": result.domain,
    }


@router.post("/loras/compose-all")
async def compose_all_loras(
    req: ComposeAllRequest | None = None,
    _: dict = Depends(require_admin),
):
    """Compose all discovered LoRA adapters."""
    mgr = _get_mgr()
    lora_dir = (req and req.lora_dir) or os.getenv(
        "LORA_DIR", os.path.expanduser("~/.cortex/loras"),
    )
    base_model = (req and req.base_model) or os.getenv(
        "LORA_BASE_MODEL",
    ) or os.getenv("MODEL_FAST", "qwen3.5:9b")

    composed = await mgr.compose_all(base_model, lora_dir)
    return {
        "ok": True,
        "composed": len(composed),
        "models": [c.model_name for c in composed],
    }


@router.delete("/loras/{domain}")
async def remove_lora(domain: str, _: dict = Depends(require_admin)):
    """Remove a composed LoRA model from Ollama."""
    mgr = _get_mgr()
    ok = await mgr.remove(domain)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No composed model for domain '{domain}'")
    return {"ok": True, "domain": domain}


@router.get("/loras/domains")
async def list_domains(_: dict = Depends(require_admin)):
    """List available domains and which are active."""
    mgr = _get_mgr()

    # Known domains from hardware config
    try:
        from cortex.install.hardware import ATLAS_LORAS
        known = {
            k.removesuffix(".lora"): v.get("description", "")
            for k, v in ATLAS_LORAS.items()
        }
    except ImportError:
        known = {}

    active = {cm.domain: cm.model_name for cm in mgr.list_active()}

    domains = []
    all_names = set(known.keys()) | set(active.keys())
    for name in sorted(all_names):
        domains.append({
            "domain": name,
            "description": known.get(name, ""),
            "active": name in active,
            "model_name": active.get(name),
        })

    return {"domains": domains}
