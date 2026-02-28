"""Tests for service discovery: ServiceScanner, ServiceRegistry, load_seed_patterns."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.discovery.registry import ServiceRegistry
from cortex.discovery.scanner import DiscoveredService, ServiceScanner
from cortex.discovery.wizard import load_seed_patterns


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


class TestServiceRegistry:
    def test_upsert_new_service(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc = DiscoveredService(
            service_type="home_assistant",
            name="Home Assistant",
            url="http://localhost:8123",
            discovery_method="http_probe",
        )
        svc_id = registry.upsert_service(svc)
        assert isinstance(svc_id, int)
        row = db_conn.execute(
            "SELECT * FROM discovered_services WHERE id = ?", (svc_id,)
        ).fetchone()
        assert row is not None
        assert row["service_type"] == "home_assistant"
        assert row["url"] == "http://localhost:8123"

    def test_upsert_duplicate_same_url(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc = DiscoveredService(
            service_type="home_assistant",
            name="Home Assistant",
            url="http://localhost:8123",
            discovery_method="http_probe",
        )
        registry.upsert_service(svc)
        registry.upsert_service(svc)
        count = db_conn.execute(
            "SELECT count(*) FROM discovered_services WHERE service_type='home_assistant'"
        ).fetchone()[0]
        assert count == 1

    def test_set_and_get_config(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc = DiscoveredService(
            service_type="home_assistant",
            name="HA",
            url="http://localhost:8123",
            discovery_method="http_probe",
        )
        svc_id = registry.upsert_service(svc)
        registry.set_config(svc_id, "token", "my_secret_token", sensitive=True)
        cfg = registry.get_config(svc_id)
        assert cfg["token"] == "my_secret_token"

    def test_list_services_empty(self, db_conn):
        registry = ServiceRegistry(db_conn)
        result = registry.list_services()
        assert result == []

    def test_list_services_filter_by_type(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc_ha = DiscoveredService(
            service_type="home_assistant",
            name="HA",
            url="http://localhost:8123",
            discovery_method="http_probe",
        )
        svc_mqtt = DiscoveredService(
            service_type="mqtt",
            name="MQTT",
            url="tcp://localhost:1883",
            discovery_method="tcp_probe",
        )
        registry.upsert_service(svc_ha)
        registry.upsert_service(svc_mqtt)
        results = registry.list_services(service_type="mqtt")
        assert len(results) == 1
        assert results[0]["service_type"] == "mqtt"

    def test_mark_active(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc = DiscoveredService(
            service_type="nextcloud",
            name="Nextcloud",
            url="http://localhost:80",
            discovery_method="http_probe",
        )
        svc_id = registry.upsert_service(svc)
        registry.mark_active(svc_id, active=True)
        row = db_conn.execute(
            "SELECT is_active FROM discovered_services WHERE id = ?", (svc_id,)
        ).fetchone()
        assert bool(row["is_active"]) is True

    def test_get_active_service(self, db_conn):
        registry = ServiceRegistry(db_conn)
        svc = DiscoveredService(
            service_type="mqtt",
            name="MQTT",
            url="tcp://localhost:1883",
            discovery_method="tcp_probe",
            is_active=True,
        )
        registry.upsert_service(svc)
        result = registry.get_active_service("mqtt")
        assert result is not None
        assert result["service_type"] == "mqtt"


class TestLoadSeedPatterns:
    def test_loads_patterns(self, db_conn):
        count = load_seed_patterns(db_conn)
        assert count > 0
        row_count = db_conn.execute(
            "SELECT count(*) FROM command_patterns"
        ).fetchone()[0]
        assert row_count > 0

    def test_idempotent(self, db_conn):
        count1 = load_seed_patterns(db_conn)
        count2 = load_seed_patterns(db_conn)
        assert count1 > 0
        # Both calls should return the same consistent count
        assert count2 > 0
        assert count2 == count1


class TestServiceScanner:
    def test_scanner_init(self):
        scanner = ServiceScanner()
        assert len(scanner.PROBE_TARGETS) > 0
        types = [t["service_type"] for t in scanner.PROBE_TARGETS]
        assert "home_assistant" in types

    async def test_scan_returns_empty_when_nothing_running(self):
        scanner = ServiceScanner()
        results = await scanner.scan(hosts=["192.0.2.1"], timeout=0.1)
        assert results == []
