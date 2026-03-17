"""Satellite notification channel — routes notifications via TTS to satellites."""

from __future__ import annotations

import logging
from typing import Any

from cortex.notifications.channels import (
    Notification,
    NotificationChannel,
    register_channel,
    send_notification,
)

logger = logging.getLogger(__name__)


class SatelliteChannel(NotificationChannel):
    """Routes notifications to satellites via TTS."""

    def __init__(self) -> None:
        self._tts_callback: Any | None = None

    def set_tts_callback(self, callback: Any) -> None:
        """Set the TTS delivery callback: ``async callback(satellite_id, text) -> bool``."""
        self._tts_callback = callback

    async def send(self, notification: Notification) -> bool:
        """Route to the right satellite based on notification metadata.

        Strategy:
        1. If metadata has 'room', try that satellite first
        2. If metadata has 'user_id', try user's last known room
        3. Escalate to all connected satellites
        4. Fall back to log channel
        """
        meta = notification.metadata
        room = meta.get("room", "")
        user_id = meta.get("user_id", "")
        tts_text = notification.message

        targets = await self._resolve_targets(room, user_id)
        if not targets:
            logger.info(
                "No satellite targets for notification '%s', logged only",
                notification.title,
            )
            return False

        delivered = False
        for sat_id in targets:
            try:
                ok = await self._deliver_tts(sat_id, tts_text)
                if ok:
                    delivered = True
            except Exception:
                logger.debug("Failed to deliver to satellite %s", sat_id, exc_info=True)

        return delivered

    async def _resolve_targets(self, room: str, user_id: str) -> list[str]:
        """Determine which satellite(s) should receive the notification."""
        try:
            from cortex.db import get_db
            conn = get_db()
        except Exception:
            return []

        # 1. Room-based lookup
        if room:
            rows = conn.execute(
                "SELECT id FROM satellites WHERE room = ? AND status = 'online'",
                (room,),
            ).fetchall()
            if rows:
                return [r["id"] for r in rows]

        # 2. User's last known room via room_context_log
        if user_id:
            try:
                row = conn.execute(
                    "SELECT satellite_id FROM room_context_log "
                    "ORDER BY created_at DESC LIMIT 1",
                ).fetchone()
                if row and row["satellite_id"]:
                    sat = conn.execute(
                        "SELECT id, room FROM satellites WHERE id = ? AND status = 'online'",
                        (row["satellite_id"],),
                    ).fetchone()
                    if sat:
                        return [sat["id"]]
            except Exception:
                pass

        # 3. Broadcast to all connected satellites
        rows = conn.execute(
            "SELECT id FROM satellites WHERE status = 'online'"
        ).fetchall()
        return [r["id"] for r in rows]

    async def _deliver_tts(self, satellite_id: str, text: str) -> bool:
        """Send a TTS message to a specific satellite via registered callback."""
        if self._tts_callback is not None:
            try:
                return await self._tts_callback(satellite_id, text)
            except Exception:
                logger.debug("TTS delivery failed for %s", satellite_id, exc_info=True)
                return False
        logger.debug("No TTS delivery callback registered for satellite %s", satellite_id)
        return False


# ── Convenience helpers ──────────────────────────────────────────

async def notify_timer_expired(
    timer_label: str,
    room: str = "",
    user_id: str = "",
) -> None:
    """Convenience for timer expiry notifications."""
    label = timer_label or "Timer"
    await send_notification(
        level="info",
        title=f"{label} finished",
        message=f"Your timer{' for ' + timer_label if timer_label else ''} has finished!",
        source="scheduling.timer",
        metadata={"room": room, "user_id": user_id},
    )


async def notify_alarm_triggered(
    alarm_label: str,
    tts_message: str = "",
    room: str = "",
    user_id: str = "",
) -> None:
    """Convenience for alarm trigger notifications."""
    label = alarm_label or "Alarm"
    message = tts_message or f"Your alarm{' — ' + alarm_label if alarm_label else ''} is going off!"
    await send_notification(
        level="info",
        title=f"{label} triggered",
        message=message,
        source="scheduling.alarm",
        metadata={"room": room, "user_id": user_id},
    )


async def notify_reminder_fired(
    reminder_message: str,
    room: str = "",
    user_id: str = "",
) -> None:
    """Convenience for reminder fire notifications."""
    await send_notification(
        level="info",
        title="Reminder",
        message=f"Reminder: {reminder_message}",
        source="scheduling.reminder",
        metadata={"room": room, "user_id": user_id},
    )


def register_satellite_channel() -> SatelliteChannel:
    """Create and register the satellite channel. Returns the instance."""
    channel = SatelliteChannel()
    register_channel(channel)
    return channel


def wire_scheduling_callbacks(
    timer_engine: Any,
    alarm_engine: Any,
    reminder_engine: Any,
) -> None:
    """Wire scheduling engine expiry callbacks to the notification system."""

    async def _on_timer_expire(timer_id: int, label: str, user_id: str, room: str) -> None:
        await notify_timer_expired(label, room=room, user_id=user_id)

    async def _on_alarm_trigger(
        alarm_id: int, label: str, sound: str,
        tts_message: str, user_id: str, room: str,
    ) -> None:
        await notify_alarm_triggered(label, tts_message=tts_message, room=room, user_id=user_id)

    async def _on_reminder_fire(reminder_id: int, message: str, user_id: str, room: str) -> None:
        await notify_reminder_fired(message, room=room, user_id=user_id)

    timer_engine.on_expire(_on_timer_expire)
    alarm_engine.on_trigger(_on_alarm_trigger)
    reminder_engine.on_trigger(_on_reminder_fire)
