"""Tests for KnowledgeSyncScheduler — test start/stop/sync cycle."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.knowledge.scheduler import KnowledgeSyncScheduler


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


def _add_service(conn, service_type, url, username="user", password="pass"):
    """Insert a configured service into discovered_services and service_config."""
    conn.execute(
        "INSERT INTO discovered_services "
        "(service_type, name, url, is_configured, is_active, health_status) "
        "VALUES (?, ?, ?, TRUE, TRUE, 'unknown')",
        (service_type, service_type, url),
    )
    service_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO service_config (service_id, config_key, config_value) VALUES (?, 'username', ?)",
        (service_id, username),
    )
    conn.execute(
        "INSERT INTO service_config (service_id, config_key, config_value) VALUES (?, 'password', ?)",
        (service_id, password),
    )
    conn.commit()
    return service_id


class TestSchedulerProperties:
    def test_initial_state(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn, interval_minutes=30)
        assert scheduler.running is False
        assert scheduler.last_sync is None
        assert scheduler.interval == 30

    async def test_start_sets_running(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn, interval_minutes=60)
        await scheduler.start()
        assert scheduler.running is True
        await scheduler.stop()
        assert scheduler.running is False

    async def test_stop_is_idempotent(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn, interval_minutes=60)
        await scheduler.stop()  # No error when not running
        assert scheduler.running is False

    async def test_double_start_warns(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn, interval_minutes=60)
        await scheduler.start()
        await scheduler.start()  # Should not create a second task
        assert scheduler.running is True
        await scheduler.stop()


class TestSyncCycle:
    async def test_run_sync_no_services(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn)
        result = await scheduler.run_sync()
        assert result["sources_synced"] == 0
        assert result["errors"] == 0
        assert scheduler.last_sync is not None

    @patch("cortex.integrations.knowledge.scheduler.KnowledgeSyncScheduler._sync_webdav")
    async def test_run_sync_webdav(self, mock_sync_webdav, db_conn):
        mock_sync_webdav.return_value = {
            "files_checked": 5,
            "files_new": 2,
            "files_updated": 1,
            "files_deleted": 0,
        }
        _add_service(db_conn, "webdav", "https://cloud.example.com/dav")

        scheduler = KnowledgeSyncScheduler(db_conn)
        result = await scheduler.run_sync()
        assert result["sources_synced"] == 1
        assert result["total_files"] == 3  # 2 new + 1 updated

    @patch("cortex.integrations.knowledge.scheduler.KnowledgeSyncScheduler._sync_caldav")
    async def test_run_sync_caldav(self, mock_sync_caldav, db_conn):
        mock_sync_caldav.return_value = {
            "calendars_synced": 2,
            "events_synced": 10,
            "errors": 0,
        }
        _add_service(db_conn, "caldav", "https://caldav.example.com")

        scheduler = KnowledgeSyncScheduler(db_conn)
        result = await scheduler.run_sync()
        assert result["sources_synced"] == 1
        assert result["total_events"] == 10

    @patch("cortex.integrations.knowledge.scheduler.KnowledgeSyncScheduler._sync_webdav")
    async def test_run_sync_error_handling(self, mock_sync_webdav, db_conn):
        mock_sync_webdav.side_effect = Exception("connection refused")
        _add_service(db_conn, "webdav", "https://bad.example.com")

        scheduler = KnowledgeSyncScheduler(db_conn)
        result = await scheduler.run_sync()
        assert result["sources_synced"] == 0
        assert result["errors"] == 1

        # Health should be set to error
        row = db_conn.execute(
            "SELECT health_status FROM discovered_services WHERE url = ?",
            ("https://bad.example.com",),
        ).fetchone()
        assert row["health_status"] == "error"

    @patch("cortex.integrations.knowledge.scheduler.KnowledgeSyncScheduler._sync_webdav")
    async def test_run_sync_updates_health(self, mock_sync_webdav, db_conn):
        mock_sync_webdav.return_value = {"files_checked": 0, "files_new": 0, "files_updated": 0, "files_deleted": 0}
        _add_service(db_conn, "webdav", "https://good.example.com")

        scheduler = KnowledgeSyncScheduler(db_conn)
        await scheduler.run_sync()

        row = db_conn.execute(
            "SELECT health_status FROM discovered_services WHERE url = ?",
            ("https://good.example.com",),
        ).fetchone()
        assert row["health_status"] == "healthy"


class TestSchedulerLoop:
    async def test_start_stop_cycle(self, db_conn):
        """Scheduler starts and stops cleanly."""
        scheduler = KnowledgeSyncScheduler(db_conn, interval_minutes=1)

        # Patch run_sync so the loop doesn't do real work
        scheduler.run_sync = AsyncMock(return_value={
            "sources_synced": 0,
            "total_files": 0,
            "total_events": 0,
            "errors": 0,
        })

        await scheduler.start()
        assert scheduler.running is True
        assert scheduler._task is not None

        # Give it a moment to run at least once
        await asyncio.sleep(0.1)

        await scheduler.stop()
        assert scheduler.running is False
        assert scheduler._task is None

    async def test_last_sync_updated_after_cycle(self, db_conn):
        scheduler = KnowledgeSyncScheduler(db_conn)
        assert scheduler.last_sync is None

        await scheduler.run_sync()
        assert scheduler.last_sync is not None
        assert "T" in scheduler.last_sync  # ISO format

    async def test_ignores_unknown_service_types(self, db_conn):
        """Unknown service types are skipped, not counted as errors."""
        conn = db_conn
        conn.execute(
            "INSERT INTO discovered_services "
            "(service_type, name, url, is_configured, is_active) "
            "VALUES ('ftp', 'FTP Server', 'ftp://example.com', TRUE, TRUE)"
        )
        conn.commit()

        scheduler = KnowledgeSyncScheduler(conn)
        result = await scheduler.run_sync()
        assert result["sources_synced"] == 0
        assert result["errors"] == 0
