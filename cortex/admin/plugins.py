"""Admin API endpoints for plugin management.

Routes (mounted under ``/admin`` via the admin router):

* ``GET  /plugins``                   — list all plugins with status & health
* ``POST /plugins/{plugin_id}/enable``  — enable a plugin
* ``POST /plugins/{plugin_id}/disable`` — disable a plugin
* ``PATCH /plugins/{plugin_id}/config`` — update plugin config
* ``POST /plugins/{plugin_id}/health``  — force a health check
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin
from cortex.plugins import get_registry
from cortex.plugins.loader import BUILTIN_PLUGINS, check_plugin_health

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response models ──────────────────────────────────────────────

class PluginInfo(BaseModel):
    plugin_id: str
    display_name: str
    plugin_type: str
    source: str = "official"
    enabled: bool = True
    health_ok: bool = True
    last_health_check: str | None = None
    version: str = "0.0.0"
    author: str = ""
    config: dict[str, Any] = {}
    config_schema: dict[str, Any] = {}
    supports_learning: bool = False
    hit_count: int = 0
    registered: bool = False


class ConfigUpdate(BaseModel):
    config: dict[str, Any]


# ── Helpers ──────────────────────────────────────────────────────

def _plugin_hit_count(conn: Any, plugin_id: str) -> int:
    """Count interactions attributed to a plugin via matched_layer='plugin'.

    Uses the interactions table; the ``intent`` column stores the plugin_id
    when the match came from Layer 2.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE matched_layer = 'plugin' AND intent = ?",
        (plugin_id,),
    ).fetchone()
    return row[0] if row else 0


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/plugins")
async def list_plugins(_: dict = Depends(require_admin)):
    """List all known plugins (built-in + community) with status."""
    conn = _h._db()
    registry = get_registry()
    registered_ids = {p.plugin_id for p in registry.list_plugins()}
    registered_map = {p.plugin_id: p for p in registry.list_plugins()}

    # All known plugin_ids: DB + built-in defaults
    cur = conn.execute("SELECT * FROM plugin_config ORDER BY plugin_id")
    db_rows = {r["plugin_id"]: r for r in _h._rows(cur)}

    all_ids = set(db_rows.keys()) | set(BUILTIN_PLUGINS.keys())
    plugins: list[dict[str, Any]] = []

    for pid in sorted(all_ids):
        row = db_rows.get(pid)
        reg_plugin = registered_map.get(pid)

        info: dict[str, Any] = {
            "plugin_id": pid,
            "display_name": reg_plugin.display_name if reg_plugin else pid,
            "plugin_type": reg_plugin.plugin_type if reg_plugin else "action",
            "source": row["source"] if row else ("official" if pid in BUILTIN_PLUGINS else "community"),
            "enabled": bool(row["enabled"]) if row else True,
            "health_ok": bool(row["health_ok"]) if row else False,
            "last_health_check": row["last_health_check"] if row else None,
            "version": getattr(reg_plugin, "version", "0.0.0") if reg_plugin else "0.0.0",
            "author": getattr(reg_plugin, "author", "") if reg_plugin else "",
            "config": json.loads(row["config"]) if row and row["config"] else {},
            "config_schema": getattr(reg_plugin, "config_schema", {}) if reg_plugin else {},
            "supports_learning": getattr(reg_plugin, "supports_learning", False) if reg_plugin else False,
            "hit_count": _plugin_hit_count(conn, pid),
            "registered": pid in registered_ids,
        }
        plugins.append(info)

    return {"plugins": plugins}


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, _: dict = Depends(require_admin)):
    """Enable a plugin (takes effect on next server restart)."""
    conn = _h._db()
    conn.execute(
        """
        INSERT INTO plugin_config (plugin_id, enabled)
        VALUES (?, 1)
        ON CONFLICT(plugin_id) DO UPDATE SET enabled = 1
        """,
        (plugin_id,),
    )
    conn.commit()
    return {"ok": True, "plugin_id": plugin_id, "enabled": True}


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, _: dict = Depends(require_admin)):
    """Disable a plugin (takes effect on next server restart)."""
    conn = _h._db()
    registry = get_registry()
    conn.execute(
        """
        INSERT INTO plugin_config (plugin_id, enabled)
        VALUES (?, 0)
        ON CONFLICT(plugin_id) DO UPDATE SET enabled = 0
        """,
        (plugin_id,),
    )
    conn.commit()
    # Unregister from live registry so it stops matching immediately
    registry.unregister(plugin_id)
    return {"ok": True, "plugin_id": plugin_id, "enabled": False}


@router.patch("/plugins/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    body: ConfigUpdate,
    _: dict = Depends(require_admin),
):
    """Update the JSON config blob for a plugin."""
    conn = _h._db()
    config_json = json.dumps(body.config)
    conn.execute(
        """
        INSERT INTO plugin_config (plugin_id, config)
        VALUES (?, ?)
        ON CONFLICT(plugin_id) DO UPDATE SET config = excluded.config
        """,
        (plugin_id, config_json),
    )
    conn.commit()
    return {"ok": True, "plugin_id": plugin_id, "config": body.config}


@router.post("/plugins/{plugin_id}/health")
async def force_health_check(plugin_id: str, _: dict = Depends(require_admin)):
    """Force an immediate health check on a single plugin."""
    registry = get_registry()
    registered_map = {p.plugin_id: p for p in registry.list_plugins()}
    plugin = registered_map.get(plugin_id)

    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not registered")

    try:
        ok = await plugin.health()
    except Exception as exc:
        logger.warning("Health check failed for %s: %s", plugin_id, exc)
        ok = False

    conn = _h._db()
    conn.execute(
        """
        UPDATE plugin_config
        SET health_ok = ?, last_health_check = CURRENT_TIMESTAMP
        WHERE plugin_id = ?
        """,
        (int(ok), plugin_id),
    )
    conn.commit()
    return {"ok": True, "plugin_id": plugin_id, "health_ok": ok}
