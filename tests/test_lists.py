"""Tests for SQLiteListBackend, ListRegistry, ListPlugin."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.lists.backends import SQLiteListBackend
from cortex.integrations.lists.registry import ListPlugin, ListRegistry
from cortex.plugins.base import CommandMatch


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


class TestSQLiteListBackend:
    async def test_add_and_get_items(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Grocery", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        await backend.add_item(list_id, "milk", "user1")
        items = await backend.get_items(list_id)
        assert len(items) == 1
        assert items[0].content == "milk"

    async def test_add_multiple_items(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Shopping", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        for item in ["eggs", "butter", "flour"]:
            await backend.add_item(list_id, item, "user1")
        items = await backend.get_items(list_id)
        assert len(items) == 3
        contents = {i.content for i in items}
        assert contents == {"eggs", "butter", "flour"}

    async def test_remove_item(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Tasks", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        added = await backend.add_item(list_id, "buy groceries", "user1")
        removed = await backend.remove_item(list_id, added.id)
        assert removed is True
        items = await backend.get_items(list_id)
        assert len(items) == 0

    async def test_mark_done(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Chores", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        added = await backend.add_item(list_id, "vacuum living room", "user1")
        result = await backend.mark_done(list_id, added.id, done=True)
        assert result is True
        items = await backend.get_items(list_id)
        assert items[0].done is True

    async def test_clear_done(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("TodoList", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        items_added = []
        for text in ["task1", "task2", "task3"]:
            item = await backend.add_item(list_id, text, "user1")
            items_added.append(item)
        await backend.mark_done(list_id, items_added[0].id)
        await backend.mark_done(list_id, items_added[1].id)
        cleared = await backend.clear_done(list_id)
        assert cleared == 2
        remaining = await backend.get_items(list_id)
        assert len(remaining) == 1
        assert remaining[0].content == "task3"


class TestListRegistry:
    def test_create_list(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("My List", owner_id="user1")
        assert isinstance(list_id, str)
        assert len(list_id) > 0

    def test_create_list_stored_in_db(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Books", owner_id="user1")
        row = db_conn.execute(
            "SELECT * FROM list_registry WHERE id = ?", (list_id,)
        ).fetchone()
        assert row is not None
        assert row["display_name"] == "Books"
        assert row["owner_id"] == "user1"

    def test_owner_has_full_permissions(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Projects", owner_id="user1")
        assert registry.check_permission(list_id, "user1", "view") is True
        assert registry.check_permission(list_id, "user1", "add") is True
        assert registry.check_permission(list_id, "user1", "remove") is True

    def test_resolve_by_name(self, db_conn):
        registry = ListRegistry(db_conn)
        registry.create_list("Grocery", owner_id="user1")
        result = registry.resolve("grocery", "user1")
        assert result is not None
        assert result["display_name"] == "Grocery"

    def test_resolve_by_alias(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Grocery", owner_id="user1")
        registry.add_alias(list_id, "shopping")
        result = registry.resolve("shopping", "user1")
        assert result is not None
        assert result["id"] == list_id

    def test_resolve_unknown_list(self, db_conn):
        registry = ListRegistry(db_conn)
        result = registry.resolve("nonexistent", "user1")
        assert result is None

    def test_grant_permission(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Shared", owner_id="user1")
        registry.grant_permission(list_id, "user2", can_view=True, can_add=False, can_remove=False)
        assert registry.check_permission(list_id, "user2", "view") is True
        assert registry.check_permission(list_id, "user2", "add") is False

    def test_permission_denied(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("Private", owner_id="user1")
        assert registry.check_permission(list_id, "user2", "view") is False

    def test_list_all_owner_sees_own(self, db_conn):
        registry = ListRegistry(db_conn)
        registry.create_list("My Tasks", owner_id="user1")
        results = registry.list_all("user1")
        assert len(results) >= 1
        assert any(r["display_name"] == "My Tasks" for r in results)


class TestListPlugin:
    def test_plugin_id(self):
        assert ListPlugin.plugin_id == "lists"

    async def test_match_add_item(self, db_conn):
        plugin = ListPlugin(db_conn)
        result = await plugin.match("add milk to my grocery list", {})
        assert result.matched is True
        assert result.intent == "add_item"
        assert result.metadata["item"] == "milk"

    async def test_match_get_list(self, db_conn):
        plugin = ListPlugin(db_conn)
        result = await plugin.match("what's on my shopping list", {})
        assert result.matched is True
        assert result.intent == "get_list"

    async def test_match_no_match(self, db_conn):
        plugin = ListPlugin(db_conn)
        result = await plugin.match("what time is it", {})
        assert result.matched is False

    async def test_handle_add_item(self, db_conn):
        registry = ListRegistry(db_conn)
        registry.create_list("grocery", owner_id="user1")
        plugin = ListPlugin(db_conn)
        match = CommandMatch(
            matched=True,
            intent="add_item",
            confidence=0.9,
            metadata={"item": "milk", "list_name": "grocery"},
        )
        result = await plugin.handle("add milk to my grocery list", match, {"user_id": "user1"})
        assert result.success is True
        assert "milk" in result.response

    async def test_handle_get_list(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("grocery", owner_id="user1")
        backend = SQLiteListBackend(db_conn)
        await backend.add_item(list_id, "eggs", "user1")
        plugin = ListPlugin(db_conn)
        match = CommandMatch(
            matched=True,
            intent="get_list",
            confidence=0.9,
            metadata={"list_name": "grocery"},
        )
        result = await plugin.handle("what's on my grocery list", match, {"user_id": "user1"})
        assert result.success is True
        assert "eggs" in result.response


class TestListPluginPermissions:
    """Verify that add_item and remove_item enforce permissions."""

    async def test_add_item_denied_without_permission(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("grocery", owner_id="owner")
        # Give user2 view-only permission (no add)
        registry.grant_permission(list_id, "user2", can_add=False, can_view=True, can_remove=False)

        plugin = ListPlugin(db_conn)
        match = CommandMatch(
            matched=True,
            intent="add_item",
            confidence=0.9,
            metadata={"item": "milk", "list_name": "grocery"},
        )
        result = await plugin.handle("add milk to my grocery list", match, {"user_id": "user2"})
        assert result.success is False
        assert "permission" in result.response.lower()

    async def test_remove_item_denied_without_permission(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("grocery", owner_id="owner")
        backend = SQLiteListBackend(db_conn)
        await backend.add_item(list_id, "eggs", "owner")
        # Give user2 view-only (no remove)
        registry.grant_permission(list_id, "user2", can_add=False, can_view=True, can_remove=False)

        plugin = ListPlugin(db_conn)
        match = CommandMatch(
            matched=True,
            intent="remove_item",
            confidence=0.9,
            metadata={"item": "eggs", "list_name": "grocery"},
        )
        result = await plugin.handle("remove eggs from my grocery list", match, {"user_id": "user2"})
        assert result.success is False
        assert "permission" in result.response.lower()

    async def test_owner_can_add_and_remove(self, db_conn):
        registry = ListRegistry(db_conn)
        list_id = registry.create_list("grocery", owner_id="owner")
        backend = SQLiteListBackend(db_conn)
        await backend.add_item(list_id, "bread", "owner")

        plugin = ListPlugin(db_conn)
        # Owner can add
        add_match = CommandMatch(matched=True, intent="add_item", confidence=0.9,
                                  metadata={"item": "milk", "list_name": "grocery"})
        add_result = await plugin.handle("add milk", add_match, {"user_id": "owner"})
        assert add_result.success is True

        # Owner can remove
        remove_match = CommandMatch(matched=True, intent="remove_item", confidence=0.9,
                                     metadata={"item": "bread", "list_name": "grocery"})
        remove_result = await plugin.handle("remove bread", remove_match, {"user_id": "owner"})
        assert remove_result.success is True
