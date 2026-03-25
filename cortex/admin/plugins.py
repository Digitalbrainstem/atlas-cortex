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
from cortex.plugins.base import ConfigField
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
    health_message: str = ""
    last_health_check: str | None = None
    version: str = "0.0.0"
    author: str = ""
    config: dict[str, Any] = {}
    config_schema: dict[str, Any] = {}
    config_fields: list[dict[str, Any]] = []
    supports_learning: bool = False
    hit_count: int = 0
    registered: bool = False
    needs_setup: bool = False


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


def _serialize_config_fields(plugin: Any) -> list[dict[str, Any]]:
    """Serialize a plugin's config_fields list for the API response."""
    fields = getattr(plugin, "config_fields", [])
    return [f.to_dict() if isinstance(f, ConfigField) else f for f in fields]


def _check_needs_setup(plugin: Any, config: dict[str, Any]) -> bool:
    """Return True if the plugin has required fields that are empty."""
    fields = getattr(plugin, "config_fields", [])
    for f in fields:
        if not isinstance(f, ConfigField):
            continue
        if f.required and not config.get(f.key):
            return True
    return False


def _validate_config(fields: list[ConfigField], config: dict[str, Any]) -> list[str]:
    """Validate config values against config_fields. Returns list of errors."""
    errors: list[str] = []
    for f in fields:
        value = config.get(f.key)
        if value is None or value == "":
            continue
        if f.field_type == "number":
            try:
                float(value)
            except (TypeError, ValueError):
                errors.append(f"'{f.key}' must be a number")
        if f.field_type == "toggle" and not isinstance(value, bool):
            if value not in (0, 1, True, False):
                errors.append(f"'{f.key}' must be a boolean")
        if f.field_type == "url" and isinstance(value, str):
            if value and not value.startswith(("http://", "https://")):
                errors.append(f"'{f.key}' must be a valid URL starting with http:// or https://")
        if f.field_type == "select" and f.options:
            valid_values = {opt["value"] for opt in f.options}
            if value not in valid_values:
                errors.append(f"'{f.key}' must be one of: {', '.join(valid_values)}")
    return errors


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
        plugin_config = json.loads(row["config"]) if row and row["config"] else {}

        info: dict[str, Any] = {
            "plugin_id": pid,
            "display_name": reg_plugin.display_name if reg_plugin else pid,
            "plugin_type": reg_plugin.plugin_type if reg_plugin else "action",
            "source": row["source"] if row else ("official" if pid in BUILTIN_PLUGINS else "community"),
            "enabled": bool(row["enabled"]) if row else True,
            "health_ok": bool(row["health_ok"]) if row else (reg_plugin is not None),
            "health_message": reg_plugin.health_message if reg_plugin else "",
            "last_health_check": row["last_health_check"] if row else None,
            "version": getattr(reg_plugin, "version", "0.0.0") if reg_plugin else "0.0.0",
            "author": getattr(reg_plugin, "author", "") if reg_plugin else "",
            "config": plugin_config,
            "config_schema": getattr(reg_plugin, "config_schema", {}) if reg_plugin else {},
            "config_fields": _serialize_config_fields(reg_plugin) if reg_plugin else [],
            "supports_learning": getattr(reg_plugin, "supports_learning", False) if reg_plugin else False,
            "hit_count": _plugin_hit_count(conn, pid),
            "registered": pid in registered_ids,
            "needs_setup": _check_needs_setup(reg_plugin, plugin_config) if reg_plugin else False,
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
    # Validate against config_fields if the plugin is registered
    registry = get_registry()
    registered_map = {p.plugin_id: p for p in registry.list_plugins()}
    plugin = registered_map.get(plugin_id)

    if plugin:
        fields = getattr(plugin, "config_fields", [])
        if fields:
            errors = _validate_config(fields, body.config)
            if errors:
                raise HTTPException(status_code=422, detail="; ".join(errors))

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

    health_message = getattr(plugin, "health_message", "")

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
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "health_ok": ok,
        "health_message": health_message,
    }
