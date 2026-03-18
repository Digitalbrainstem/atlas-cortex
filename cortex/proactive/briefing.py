"""Daily briefing generator — compiles morning briefing from proactive providers.

Pulls data from weather, calendar, reminders, timers, and alarms to
build a natural-language summary suitable for TTS delivery.
"""

# Module ownership: Proactive daily briefing

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db

logger = logging.getLogger(__name__)


class DailyBriefing:
    """Compile a morning briefing from all available proactive providers."""

    async def generate(self, user_id: str = "") -> str:
        """Compile morning briefing from all proactive providers.

        Sections: weather, calendar, reminders, active timers, news headlines.
        Returns natural language summary ready for TTS.
        """
        sections = await self.get_sections(user_id)
        parts: list[str] = []

        greeting = self._greeting()
        parts.append(greeting)

        if sections.get("weather"):
            parts.append(self._format_weather(sections["weather"]))

        if sections.get("calendar"):
            parts.append(self._format_calendar(sections["calendar"]))

        if sections.get("reminders"):
            parts.append(self._format_reminders(sections["reminders"]))

        if sections.get("timers"):
            parts.append(self._format_timers(sections["timers"]))

        if sections.get("alarms"):
            parts.append(self._format_alarms(sections["alarms"]))

        if len(parts) == 1:
            parts.append("You have a clear schedule today. Enjoy your day!")

        return " ".join(parts)

    async def get_sections(self, user_id: str = "") -> dict[str, Any]:
        """Get individual briefing sections as structured data."""
        sections: dict[str, Any] = {}

        sections["weather"] = await self._get_weather()
        sections["calendar"] = await self._get_calendar()
        sections["reminders"] = self._get_reminders(user_id)
        sections["timers"] = self._get_timers(user_id)
        sections["alarms"] = self._get_alarms(user_id)

        return sections

    # ── Private helpers ──────────────────────────────────────────────

    def _greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 12:
            return "Good morning!"
        elif hour < 17:
            return "Good afternoon!"
        else:
            return "Good evening!"

    async def _get_weather(self) -> dict[str, Any]:
        """Pull weather from the registered provider if available."""
        try:
            from cortex.proactive.engine import get_provider

            provider = get_provider("weather")
            if provider is None:
                return {}
            data = await provider.fetch_data()
            if data and data.get("temperature") is not None:
                return data
        except Exception:
            logger.debug("Weather unavailable for briefing", exc_info=True)
        return {}

    async def _get_calendar(self) -> list[dict[str, Any]]:
        """Pull upcoming events from calendar provider."""
        try:
            from cortex.proactive.engine import get_provider

            provider = get_provider("calendar")
            if provider is None:
                return []
            data = await provider.fetch_data()
            return data.get("upcoming", [])
        except Exception:
            logger.debug("Calendar unavailable for briefing", exc_info=True)
        return []

    def _get_reminders(self, user_id: str) -> list[dict[str, Any]]:
        """Get unfired reminders from the database."""
        try:
            conn = get_db()
            if user_id:
                rows = conn.execute(
                    "SELECT message, trigger_at, cron_expression "
                    "FROM reminders WHERE fired = 0 AND user_id = ? ORDER BY id",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT message, trigger_at, cron_expression "
                    "FROM reminders WHERE fired = 0 ORDER BY id",
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.debug("Reminders unavailable for briefing", exc_info=True)
        return []

    def _get_timers(self, user_id: str) -> list[dict[str, Any]]:
        """Get active timers."""
        try:
            conn = get_db()
            if user_id:
                rows = conn.execute(
                    "SELECT label, state, expires_at "
                    "FROM timers WHERE state IN ('running', 'paused') AND user_id = ? ORDER BY id",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT label, state, expires_at "
                    "FROM timers WHERE state IN ('running', 'paused') ORDER BY id",
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.debug("Timers unavailable for briefing", exc_info=True)
        return []

    def _get_alarms(self, user_id: str) -> list[dict[str, Any]]:
        """Get enabled alarms."""
        try:
            conn = get_db()
            if user_id:
                rows = conn.execute(
                    "SELECT label, cron_expression, next_fire "
                    "FROM alarms WHERE enabled = 1 AND user_id = ? ORDER BY id",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT label, cron_expression, next_fire "
                    "FROM alarms WHERE enabled = 1 ORDER BY id",
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.debug("Alarms unavailable for briefing", exc_info=True)
        return []

    # ── Formatters ───────────────────────────────────────────────────

    def _format_weather(self, data: dict[str, Any]) -> str:
        temp = data.get("temperature")
        desc = data.get("description", "")
        feels = data.get("feels_like")
        parts = [f"Weather: currently {temp}°F"]
        if desc:
            parts[0] += f" with {desc}"
        parts[0] += "."
        if feels is not None and feels != temp:
            parts.append(f"Feels like {feels}°F.")
        alerts = data.get("alerts", [])
        if alerts:
            parts.append(f"There {'is' if len(alerts) == 1 else 'are'} {len(alerts)} weather alert{'s' if len(alerts) != 1 else ''}.")
        return " ".join(parts)

    def _format_calendar(self, events: list[dict[str, Any]]) -> str:
        if not events:
            return ""
        count = len(events)
        summary = f"You have {count} upcoming event{'s' if count != 1 else ''}."
        details: list[str] = []
        for ev in events[:3]:
            title = ev.get("title", "Untitled")
            mins = ev.get("minutes_until", 0)
            if mins < 60:
                details.append(f"{title} in {mins} minutes")
            else:
                hours = mins // 60
                details.append(f"{title} in {hours} hour{'s' if hours != 1 else ''}")
        return summary + " " + "; ".join(details) + "."

    def _format_reminders(self, reminders: list[dict[str, Any]]) -> str:
        if not reminders:
            return ""
        count = len(reminders)
        msgs = [r.get("message", "") for r in reminders[:3] if r.get("message")]
        summary = f"You have {count} pending reminder{'s' if count != 1 else ''}."
        if msgs:
            summary += " Including: " + "; ".join(msgs) + "."
        return summary

    def _format_timers(self, timers: list[dict[str, Any]]) -> str:
        if not timers:
            return ""
        count = len(timers)
        labels = [t.get("label", "unnamed") for t in timers[:3] if t.get("label")]
        summary = f"You have {count} active timer{'s' if count != 1 else ''}."
        if labels:
            summary += " " + ", ".join(labels) + "."
        return summary

    def _format_alarms(self, alarms: list[dict[str, Any]]) -> str:
        if not alarms:
            return ""
        count = len(alarms)
        labels = [a.get("label", "") for a in alarms[:3] if a.get("label")]
        summary = f"You have {count} alarm{'s' if count != 1 else ''} set."
        if labels:
            summary += " " + ", ".join(labels) + "."
        return summary
