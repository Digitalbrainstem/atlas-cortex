"""Plugin discovery, loading, and health-check runner.

Discovers built-in plugins (by well-known import path) and community
plugins (via ``cortex.plugins`` entry-point group), instantiates them,
passes configuration from the DB + environment, and registers healthy
instances with the global :class:`PluginRegistry`.

Usage::

    from cortex.plugins.loader import load_plugins, check_plugin_health
    loaded = await load_plugins()      # returns list of plugin_ids
    health = await check_plugin_health()  # {plugin_id: bool}
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from importlib.metadata import entry_points
from typing import Any

from cortex.db import get_db
from cortex.plugins import get_registry
from cortex.plugins.base import CortexPlugin

logger = logging.getLogger(__name__)

# ── Built-in plugin map ──────────────────────────────────────────
# Keys are canonical plugin_ids; values are "module.path:ClassName".

BUILTIN_PLUGINS: dict[str, str] = {
    "ha_commands": "cortex.integrations.ha:HAPlugin",
    "lists": "cortex.integrations.lists:ListPlugin",
    "knowledge": "cortex.integrations.knowledge.index:KnowledgePlugin",
    "scheduling": "cortex.plugins.timers:SchedulingPlugin",
    "stem_games": "cortex.plugins.games:STEMGamesPlugin",
    "routines": "cortex.plugins.routines:RoutinePlugin",
    "news": "cortex.plugins.news:NewsPlugin",
    "translation": "cortex.plugins.translation:TranslationPlugin",
    "stocks": "cortex.plugins.stocks:StocksPlugin",
    "sports": "cortex.plugins.sports:SportsPlugin",
    "sound_library": "cortex.plugins.sound_library:SoundLibraryPlugin",
    "weather": "cortex.plugins.weather:WeatherPlugin",
    "dictionary": "cortex.plugins.dictionary:DictionaryPlugin",
    "wikipedia": "cortex.plugins.wikipedia:WikipediaPlugin",
    "conversions": "cortex.plugins.conversions:ConversionPlugin",
    "movie": "cortex.plugins.movie:MoviePlugin",
    "cooking": "cortex.plugins.cooking:CookingPlugin",
}


# ── Helpers ──────────────────────────────────────────────────────

def _import_plugin_class(import_path: str) -> type[CortexPlugin]:
    """Import ``'module.path:ClassName'`` and return the class object."""
    module_path, class_name = import_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _env_config_for(plugin_id: str) -> dict[str, Any]:
    """Build a config dict from well-known environment variables."""
    if plugin_id == "ha_commands":
        cfg: dict[str, Any] = {}
        url = os.environ.get("HA_URL")
        token = os.environ.get("HA_TOKEN")
        if url:
            cfg["base_url"] = url
        if token:
            cfg["token"] = token
        timeout = os.environ.get("HA_TIMEOUT")
        if timeout:
            cfg["timeout"] = float(timeout)
        return cfg
    return {}


def _upsert_plugin_config(
    plugin_id: str,
    *,
    source: str = "official",
    health_ok: bool = True,
) -> None:
    """Insert or update the plugin_config row for *plugin_id*."""
    db = get_db()
    db.execute(
        """
        INSERT INTO plugin_config (plugin_id, enabled, source, health_ok, last_health_check)
        VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(plugin_id) DO UPDATE SET
            health_ok = excluded.health_ok,
            last_health_check = CURRENT_TIMESTAMP
        """,
        (plugin_id, source, int(health_ok)),
    )
    db.commit()


# ── Public API ───────────────────────────────────────────────────

async def load_plugins() -> list[str]:
    """Discover, instantiate, configure, and register all plugins.

    Returns a list of successfully loaded *plugin_id* strings.
    """
    registry = get_registry()
    db = get_db()
    loaded: list[str] = []

    # ── Built-in plugins ──────────────────────────────────────
    for plugin_id, import_path in BUILTIN_PLUGINS.items():
        try:
            loaded_ok = await _load_single(
                plugin_id, import_path, source="official", db=db, registry=registry,
            )
            if loaded_ok:
                loaded.append(plugin_id)
        except Exception as exc:
            logger.warning("Plugin %s failed to load: %s", plugin_id, exc)

    # ── Community plugins via entry_points ─────────────────────
    try:
        eps = entry_points(group="cortex.plugins")
        for ep in eps:
            plugin_id = ep.name
            try:
                plugin_class = ep.load()
                loaded_ok = await _setup_and_register(
                    plugin_id,
                    plugin_class,
                    source="community",
                    db=db,
                    registry=registry,
                )
                if loaded_ok:
                    loaded.append(plugin_id)
            except Exception as exc:
                logger.warning("Community plugin %s failed: %s", plugin_id, exc)
    except Exception as exc:
        logger.debug("Entry-point discovery unavailable: %s", exc)

    return loaded


async def _load_single(
    plugin_id: str,
    import_path: str,
    *,
    source: str,
    db: Any,
    registry: Any,
) -> bool:
    """Import, instantiate, configure, and register a single plugin."""
    # Check DB for enabled/disabled state
    row = db.execute(
        "SELECT enabled, config FROM plugin_config WHERE plugin_id = ?",
        (plugin_id,),
    ).fetchone()
    if row and not row["enabled"]:
        logger.info("Plugin %s is disabled — skipping", plugin_id)
        return False

    plugin_class = _import_plugin_class(import_path)
    return await _setup_and_register(
        plugin_id, plugin_class, source=source, db=db, registry=registry, row=row,
    )


async def _setup_and_register(
    plugin_id: str,
    plugin_class: type,
    *,
    source: str,
    db: Any,
    registry: Any,
    row: Any = None,
) -> bool:
    """Instantiate, call setup(), and register on success."""
    plugin: CortexPlugin = plugin_class()

    # Merge env config → DB config (DB wins)
    config = _env_config_for(plugin_id)
    if row:
        try:
            db_config = json.loads(row["config"]) if row["config"] else {}
            config.update(db_config)
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        success = await plugin.setup(config)
    except Exception as exc:
        logger.warning("Plugin %s setup() raised: %s", plugin_id, exc)
        _upsert_plugin_config(plugin_id, source=source, health_ok=False)
        return False

    if not success:
        logger.info("Plugin %s setup() returned False — inactive", plugin_id)
        _upsert_plugin_config(plugin_id, source=source, health_ok=False)
        return False

    registry.register(plugin)
    _upsert_plugin_config(plugin_id, source=source, health_ok=True)
    return True


async def check_plugin_health() -> dict[str, bool]:
    """Run ``health()`` on every registered plugin and persist results.

    Returns ``{plugin_id: healthy}`` for each plugin.
    """
    registry = get_registry()
    results: dict[str, bool] = {}
    db = get_db()

    for plugin in registry.list_plugins():
        pid = plugin.plugin_id
        try:
            ok = await plugin.health()
        except Exception as exc:
            logger.warning("Health check failed for %s: %s", pid, exc)
            ok = False

        results[pid] = ok
        db.execute(
            """
            UPDATE plugin_config
            SET health_ok = ?, last_health_check = CURRENT_TIMESTAMP
            WHERE plugin_id = ?
            """,
            (int(ok), pid),
        )
    db.commit()
    return results
