"""Notification system — multi-channel alerting.

Provides a channel-based notification system for safety events,
system alerts, and operational notifications.

Sub-modules:
  channels — NotificationChannel ABC + concrete implementations
  satellite — Satellite TTS delivery channel
"""

# Module ownership: Notification channels and alerting
from __future__ import annotations

from cortex.notifications.channels import (
    NotificationChannel,
    LogChannel,
    send_notification,
)
from cortex.notifications.satellite import (
    SatelliteChannel,
    notify_timer_expired,
    notify_alarm_triggered,
    notify_reminder_fired,
    register_satellite_channel,
    wire_scheduling_callbacks,
)

__all__ = [
    "NotificationChannel",
    "LogChannel",
    "send_notification",
    "SatelliteChannel",
    "notify_timer_expired",
    "notify_alarm_triggered",
    "notify_reminder_fired",
    "register_satellite_channel",
    "wire_scheduling_callbacks",
]
