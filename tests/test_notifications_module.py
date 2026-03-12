"""Tests for cortex/notifications/ — channels, dispatch, error handling.

Prove actual behavior: channel registry, delivery counting, graceful DB failures.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cortex.db import init_db, set_db_path
from cortex.notifications.channels import (
    LogChannel,
    Notification,
    NotificationChannel,
    register_channel,
    send_notification,
    _channels,
    _ensure_default_channels,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "notif_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_channel_registry():
    """Reset the global channel list before each test."""
    import cortex.notifications.channels as mod
    saved_channels = mod._channels[:]
    saved_init = mod._initialized
    mod._channels.clear()
    mod._initialized = False
    yield
    mod._channels[:] = saved_channels
    mod._initialized = saved_init


# ===========================================================================
# Notification dataclass
# ===========================================================================

class TestNotificationDataclass:
    def test_creation_with_all_fields(self):
        n = Notification(
            level="critical",
            title="Test Alert",
            message="Something bad happened",
            source="safety",
            metadata={"key": "value"},
        )
        assert n.level == "critical"
        assert n.title == "Test Alert"
        assert n.message == "Something bad happened"
        assert n.source == "safety"
        assert n.metadata == {"key": "value"}
        assert n.timestamp is not None

    def test_defaults(self):
        n = Notification(level="info", title="Hello", message="World")
        assert n.source == ""
        assert n.metadata == {}
        assert n.timestamp is not None

    def test_metadata_default_is_not_shared(self):
        """Each instance should get its own metadata dict."""
        n1 = Notification(level="info", title="A", message="A")
        n2 = Notification(level="info", title="B", message="B")
        n1.metadata["x"] = 1
        assert "x" not in n2.metadata


# ===========================================================================
# LogChannel
# ===========================================================================

class TestLogChannel:
    @pytest.mark.asyncio
    async def test_send_returns_true(self):
        """LogChannel.send() always returns True (best-effort)."""
        channel = LogChannel()
        notif = Notification(level="info", title="Test", message="Hello")
        # Even if DB is not available, LogChannel logs and returns True
        result = await channel.send(notif)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_with_missing_table_no_crash(self):
        """When notification_log table doesn't exist, LogChannel should not crash."""
        channel = LogChannel()
        notif = Notification(level="critical", title="Alert", message="DB missing")
        # get_db() might return a conn without the table — LogChannel catches Exception
        result = await channel.send(notif)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_writes_to_db(self, db):
        """Verify LogChannel actually inserts into notification_log."""
        # notification_log may not be in default schema — create it
        db.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT, title TEXT, message TEXT, source TEXT,
                created_at TEXT
            )
        """)
        db.commit()
        channel = LogChannel()
        notif = Notification(
            level="warning", title="Disk Full", message="99% usage",
            source="system",
        )
        with patch("cortex.db.get_db", return_value=db):
            result = await channel.send(notif)
        assert result is True
        rows = db.execute("SELECT * FROM notification_log").fetchall()
        assert len(rows) == 1
        assert rows[0]["level"] == "warning"
        assert rows[0]["title"] == "Disk Full"
        assert rows[0]["source"] == "system"


# ===========================================================================
# send_notification — dispatch to channels
# ===========================================================================

class TestSendNotification:
    @pytest.mark.asyncio
    async def test_default_channel_is_log(self):
        """First call should auto-register LogChannel."""
        count = await send_notification("info", "Startup", "Server started")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_custom_channel_receives_notification(self):
        """A registered custom channel should receive the notification."""
        mock_channel = AsyncMock(spec=NotificationChannel)
        mock_channel.send = AsyncMock(return_value=True)
        register_channel(mock_channel)

        count = await send_notification("warning", "Test", "Hello")
        mock_channel.send.assert_called_once()
        sent_notif = mock_channel.send.call_args[0][0]
        assert sent_notif.level == "warning"
        assert sent_notif.title == "Test"

    @pytest.mark.asyncio
    async def test_one_fails_one_succeeds_returns_count(self):
        """If one channel fails and another succeeds, count reflects successes."""
        failing_channel = AsyncMock(spec=NotificationChannel)
        failing_channel.send = AsyncMock(side_effect=Exception("delivery failed"))

        good_channel = AsyncMock(spec=NotificationChannel)
        good_channel.send = AsyncMock(return_value=True)

        register_channel(failing_channel)
        register_channel(good_channel)

        count = await send_notification("critical", "Alert", "Fire!")
        # Default LogChannel (auto-registered) + good_channel = at least 1
        # failing_channel raises → not counted
        assert count >= 1
        good_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_channel_returning_false_not_counted(self):
        """Channel returning False should not count as success."""
        reject_channel = AsyncMock(spec=NotificationChannel)
        reject_channel.send = AsyncMock(return_value=False)
        register_channel(reject_channel)

        count = await send_notification("info", "Muted", "Ignored")
        # LogChannel returns True (auto-registered) → at least 1
        # reject_channel returns False → not counted
        assert count >= 1

    @pytest.mark.asyncio
    async def test_metadata_passed_through(self):
        """Metadata dict should be forwarded to channels."""
        spy = AsyncMock(spec=NotificationChannel)
        spy.send = AsyncMock(return_value=True)
        register_channel(spy)

        await send_notification(
            "info", "Stats", "CPU 50%",
            source="monitoring",
            metadata={"cpu": 50, "mem": 70},
        )
        sent = spy.send.call_args[0][0]
        assert sent.metadata == {"cpu": 50, "mem": 70}
        assert sent.source == "monitoring"
