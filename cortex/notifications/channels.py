"""Notification channels — pluggable delivery backends.

Each channel implements the NotificationChannel ABC.
send_notification() dispatches to all registered channels.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """A notification to be delivered through one or more channels."""
    level: str  # "info", "warning", "critical"
    title: str
    message: str
    source: str = ""  # e.g. "safety", "system", "learning"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class NotificationChannel(abc.ABC):
    """Abstract base for notification delivery channels."""

    @abc.abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Deliver notification. Return True on success."""
        ...


class LogChannel(NotificationChannel):
    """Logs notifications to the database and Python logger."""

    async def send(self, notification: Notification) -> bool:
        try:
            from cortex.db import get_db
            db = get_db()
            db.execute(
                """INSERT INTO notification_log (level, title, message, source, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (notification.level, notification.title, notification.message,
                 notification.source, notification.timestamp.isoformat()),
            )
            db.commit()
        except Exception:
            pass  # DB table may not exist yet — best-effort
        log_fn = logger.warning if notification.level == "critical" else logger.info
        log_fn("[%s] %s: %s", notification.source, notification.title, notification.message[:200])
        return True


# ── Channel registry ─────────────────────────────────────────────

_channels: list[NotificationChannel] = []
_initialized = False


def _ensure_default_channels() -> None:
    global _initialized
    if not _initialized:
        _channels.append(LogChannel())
        _initialized = True


def register_channel(channel: NotificationChannel) -> None:
    """Add a notification channel to the registry."""
    _channels.append(channel)


async def send_notification(
    level: str,
    title: str,
    message: str,
    source: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Send a notification to all registered channels. Returns count of successful deliveries."""
    _ensure_default_channels()
    notif = Notification(
        level=level,
        title=title,
        message=message,
        source=source,
        metadata=metadata or {},
    )
    success = 0
    for channel in _channels:
        try:
            if await channel.send(notif):
                success += 1
        except Exception as e:
            logger.debug("Channel %s failed: %s", type(channel).__name__, e)
    return success
