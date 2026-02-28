"""Tests for HAPlugin, HAClient errors, and HABootstrap pattern generation."""

from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.ha import HAPlugin
from cortex.integrations.ha.bootstrap import HABootstrap
from cortex.integrations.ha.client import HAAuthError, HAClientError, HAClient, HAConnectionError
from cortex.plugins.base import CommandMatch, CommandResult


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


class TestHAClientErrors:
    async def test_connection_error_unreachable(self):
        client = HAClient("http://192.0.2.1:8123", "fake_token", timeout=0.1)
        result = await client.health()
        assert result is False

    def test_ha_client_error_hierarchy(self):
        assert issubclass(HAConnectionError, HAClientError)
        assert issubclass(HAAuthError, HAClientError)


class TestHABootstrap:
    def test_generate_patterns_for_lights(self, db_conn):
        db_conn.execute(
            "INSERT INTO ha_devices (entity_id, friendly_name, domain, state) VALUES (?, ?, ?, ?)",
            ("light.bedroom", "Bedroom Light", "light", "off"),
        )
        db_conn.commit()
        mock_client = MagicMock()
        bootstrap = HABootstrap(mock_client, db_conn)
        count = bootstrap.generate_patterns(["light"])
        assert count > 0
        row_count = db_conn.execute(
            "SELECT count(*) FROM command_patterns WHERE source='discovered'"
        ).fetchone()[0]
        assert row_count > 0

    def test_generate_patterns_dedup(self, db_conn):
        db_conn.execute(
            "INSERT INTO ha_devices (entity_id, friendly_name, domain, state) VALUES (?, ?, ?, ?)",
            ("light.kitchen", "Kitchen Light", "light", "on"),
        )
        db_conn.commit()
        mock_client = MagicMock()
        bootstrap = HABootstrap(mock_client, db_conn)
        count1 = bootstrap.generate_patterns(["light"])
        count2 = bootstrap.generate_patterns(["light"])
        assert count1 > 0
        assert count2 == 0

    async def test_sync_devices_no_client(self, db_conn):
        mock_client = AsyncMock()
        mock_client.get_states.side_effect = HAConnectionError("unreachable")
        bootstrap = HABootstrap(mock_client, db_conn)
        with pytest.raises(HAConnectionError):
            await bootstrap.sync_devices()


class TestHAPlugin:
    def test_plugin_id(self):
        assert HAPlugin.plugin_id == "ha_commands"

    async def test_health_false_when_no_client(self):
        plugin = HAPlugin(client=None, conn=None)
        result = await plugin.health()
        assert result is False

    async def test_match_no_patterns(self, db_conn):
        plugin = HAPlugin(conn=db_conn)
        result = await plugin.match("turn on the lights", {})
        assert result.matched is False

    async def test_match_with_loaded_patterns(self, db_conn):
        db_conn.execute(
            """INSERT INTO command_patterns
               (pattern, intent, source, confidence)
               VALUES (?, ?, ?, ?)""",
            ("turn on the lights", "toggle", "seed", 0.9),
        )
        db_conn.commit()
        plugin = HAPlugin(conn=db_conn)
        result = await plugin.match("turn on the lights", {})
        assert result.matched is True
        assert result.intent == "toggle"

    async def test_handle_graceful_on_connection_error(self, db_conn):
        db_conn.execute(
            "INSERT INTO ha_devices (entity_id, friendly_name, domain, state) VALUES (?, ?, ?, ?)",
            ("light.bedroom", "bedroom light", "light", "off"),
        )
        db_conn.commit()

        mock_client = AsyncMock()
        mock_client.call_service.side_effect = HAConnectionError("unreachable")

        plugin = HAPlugin(client=mock_client, conn=db_conn)

        # Build a regex match for extracting state group
        regex_match = re.compile(r"turn (on|off) (?:the )?bedroom light").search(
            "turn on the bedroom light"
        )
        match = CommandMatch(
            matched=True,
            intent="toggle",
            entities=["bedroom light"],
            confidence=0.9,
            metadata={
                "pattern_id": None,
                "entity_domain": "light",
                "value_match_group": None,
                "response_template": "Done — {name} turned {value}.",
                "regex_match": regex_match,
            },
        )
        result = await plugin.handle("turn on the bedroom light", match, {})
        assert result.success is False
        assert "not reachable" in result.response.lower() or "not configured" in result.response.lower()


class TestHABootstrapAreaName:
    """Verify area_name is stored and used for room scoping."""

    async def test_sync_stores_area_name(self, db_conn):
        mock_client = AsyncMock()
        mock_client.get_states.return_value = [
            {
                "entity_id": "light.living_room",
                "state": "off",
                "attributes": {
                    "friendly_name": "Living Room Light",
                    "area_id": "area_abc",
                },
            }
        ]
        mock_client.get_areas.return_value = [
            {"area_id": "area_abc", "name": "Living Room"}
        ]
        bootstrap = HABootstrap(mock_client, db_conn)
        await bootstrap.sync_devices()
        row = db_conn.execute(
            "SELECT area_id, area_name FROM ha_devices WHERE entity_id = 'light.living_room'"
        ).fetchone()
        assert row is not None
        assert row["area_id"] == "area_abc"
        assert row["area_name"] == "Living Room"

    def test_entity_in_area_uses_area_name(self, db_conn):
        db_conn.execute(
            """INSERT INTO ha_devices (entity_id, friendly_name, domain, area_id, area_name, state)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("light.kitchen", "Kitchen Light", "light", "area_k", "Kitchen", "off"),
        )
        db_conn.commit()
        plugin = HAPlugin(conn=db_conn)
        # Should match by area_name "Kitchen"
        assert plugin._entity_in_area("kitchen light", "Kitchen") is True
        assert plugin._entity_in_area("kitchen light", "kitchen") is True
        # Wrong room should not match
        assert plugin._entity_in_area("kitchen light", "Bedroom") is False


class TestHAClientConnectionPooling:
    """Verify HAClient reuses a single AsyncClient instance."""

    def test_client_instance_reused(self):
        import httpx
        client = HAClient("http://localhost:8123", "token")
        assert isinstance(client._client, httpx.AsyncClient)
        # Same instance across calls
        assert client._client is client._client

    async def test_aclose_works(self):
        client = HAClient("http://localhost:8123", "token")
        # Should not raise
        await client.aclose()
