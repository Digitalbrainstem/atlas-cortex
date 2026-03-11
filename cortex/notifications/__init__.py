"""Notification system — multi-channel alerting.

Provides a channel-based notification system for safety events,
system alerts, and operational notifications.

Sub-modules:
  channels — NotificationChannel ABC + concrete implementations
"""
from __future__ import annotations

from cortex.notifications.channels import (
    NotificationChannel,
    LogChannel,
    send_notification,
)

__all__ = ["NotificationChannel", "LogChannel", "send_notification"]
