"""Built-in DB-backed plugins must receive their startup dependency."""

from __future__ import annotations

import sqlite3

from cortex.db import _create_schema
from cortex.plugins import PluginRegistry
from cortex.plugins.loader import BUILTIN_PLUGINS, _load_single


async def test_db_backed_list_and_knowledge_plugins_load(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "plugins.db")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.commit()
    monkeypatch.setattr("cortex.plugins.loader.get_db", lambda: conn)
    registry = PluginRegistry()
    try:
        for plugin_id in ("lists", "knowledge"):
            loaded = await _load_single(
                plugin_id, BUILTIN_PLUGINS[plugin_id], source="official",
                db=conn, registry=registry,
            )
            assert loaded
        assert {plugin.plugin_id for plugin in registry.list_plugins()} == {"lists", "knowledge"}
    finally:
        conn.close()
