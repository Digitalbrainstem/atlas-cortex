"""Tests for HA to-do list discovery — mock HA API responses."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.lists.ha_discovery import HAListDiscovery


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


def _make_ha_client(states=None, service_result=None):
    """Create a mock HA client."""
    client = AsyncMock()
    client.get_states = AsyncMock(return_value=states or [])
    client.call_service = AsyncMock(return_value=service_result or {})
    return client


def _make_list_registry():
    """Create a mock list registry."""
    return MagicMock()


SAMPLE_HA_STATES = [
    {
        "entity_id": "todo.shopping_list",
        "state": "1",
        "attributes": {
            "friendly_name": "Shopping List",
        },
    },
    {
        "entity_id": "todo.chores",
        "state": "3",
        "attributes": {
            "friendly_name": "Household Chores",
        },
    },
    {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {
            "friendly_name": "Living Room Light",
        },
    },
    {
        "entity_id": "todo.groceries",
        "state": "0",
        "attributes": {
            "friendly_name": "Grocery List",
        },
    },
]


class TestHAListDiscovery:
    async def test_discover_finds_todo_entities(self, db_conn):
        ha_client = _make_ha_client(states=SAMPLE_HA_STATES)
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        result = await discovery.discover(db_conn)
        assert result["discovered"] == 3  # 3 todo.* entities
        assert result["new"] == 3
        assert result["existing"] == 0

    async def test_discover_creates_list_entries(self, db_conn):
        ha_client = _make_ha_client(states=SAMPLE_HA_STATES)
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        await discovery.discover(db_conn)

        rows = db_conn.execute(
            "SELECT * FROM list_registry WHERE backend = 'ha_todo'"
        ).fetchall()
        assert len(rows) == 3

        names = {r["display_name"] for r in rows}
        assert "Shopping List" in names
        assert "Household Chores" in names
        assert "Grocery List" in names

    async def test_discover_creates_aliases(self, db_conn):
        ha_client = _make_ha_client(states=SAMPLE_HA_STATES)
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        await discovery.discover(db_conn)

        aliases = db_conn.execute(
            "SELECT alias FROM list_aliases"
        ).fetchall()
        alias_values = {r["alias"] for r in aliases}
        assert "shopping list" in alias_values
        assert "chores" in alias_values

    async def test_discover_skips_existing(self, db_conn):
        ha_client = _make_ha_client(states=SAMPLE_HA_STATES)
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        # First discovery
        r1 = await discovery.discover(db_conn)
        assert r1["new"] == 3

        # Second discovery — all should be existing
        r2 = await discovery.discover(db_conn)
        assert r2["discovered"] == 3
        assert r2["new"] == 0
        assert r2["existing"] == 3

    async def test_discover_handles_no_entities(self, db_conn):
        ha_client = _make_ha_client(states=[
            {"entity_id": "light.kitchen", "state": "off", "attributes": {}},
        ])
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        result = await discovery.discover(db_conn)
        assert result["discovered"] == 0
        assert result["new"] == 0

    async def test_discover_handles_api_error(self, db_conn):
        ha_client = AsyncMock()
        ha_client.get_states = AsyncMock(side_effect=Exception("Connection refused"))
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        result = await discovery.discover(db_conn)
        assert result["discovered"] == 0

    async def test_discover_sets_backend_config(self, db_conn):
        ha_client = _make_ha_client(states=[SAMPLE_HA_STATES[0]])
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        await discovery.discover(db_conn)

        row = db_conn.execute(
            "SELECT backend_config FROM list_registry WHERE backend = 'ha_todo'"
        ).fetchone()
        config = json.loads(row["backend_config"])
        assert config["entity_id"] == "todo.shopping_list"


class TestHAListSyncItems:
    async def test_sync_items_adds_new(self, db_conn):
        ha_client = _make_ha_client(
            states=SAMPLE_HA_STATES,
            service_result={
                "todo.shopping_list": {
                    "items": [
                        {"summary": "Milk", "status": "needs_action"},
                        {"summary": "Bread", "status": "needs_action"},
                    ]
                }
            },
        )
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        # First discover to create the list
        await discovery.discover(db_conn)

        result = await discovery.sync_items(db_conn, "todo.shopping_list")
        assert result["added"] == 2
        assert result["removed"] == 0

        items = db_conn.execute(
            "SELECT content FROM list_items ORDER BY content"
        ).fetchall()
        contents = [r["content"] for r in items]
        assert "Bread" in contents
        assert "Milk" in contents

    async def test_sync_items_removes_old(self, db_conn):
        ha_client = _make_ha_client(
            states=SAMPLE_HA_STATES,
            service_result={
                "todo.shopping_list": {
                    "items": [
                        {"summary": "Milk", "status": "needs_action"},
                    ]
                }
            },
        )
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        # Discover and add initial items
        await discovery.discover(db_conn)

        # Add an extra item that shouldn't exist after sync
        list_row = db_conn.execute(
            "SELECT id FROM list_registry WHERE backend = 'ha_todo' LIMIT 1"
        ).fetchone()
        db_conn.execute(
            "INSERT INTO list_items (id, list_id, content, done) VALUES ('old1', ?, 'Old Item', FALSE)",
            (list_row["id"],),
        )
        db_conn.commit()

        result = await discovery.sync_items(db_conn, "todo.shopping_list")
        assert result["added"] == 1  # Milk
        assert result["removed"] == 1  # Old Item

    async def test_sync_items_no_list_found(self, db_conn):
        ha_client = _make_ha_client()
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        result = await discovery.sync_items(db_conn, "todo.nonexistent")
        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["unchanged"] == 0

    async def test_sync_items_handles_completed(self, db_conn):
        ha_client = _make_ha_client(
            states=SAMPLE_HA_STATES,
            service_result={
                "todo.shopping_list": {
                    "items": [
                        {"summary": "Done Task", "status": "completed"},
                    ]
                }
            },
        )
        registry = _make_list_registry()
        discovery = HAListDiscovery(ha_client, registry)

        await discovery.discover(db_conn)
        result = await discovery.sync_items(db_conn, "todo.shopping_list")
        assert result["added"] == 1

        item = db_conn.execute(
            "SELECT done FROM list_items WHERE content = 'Done Task'"
        ).fetchone()
        assert item["done"] == 1  # marked as done
