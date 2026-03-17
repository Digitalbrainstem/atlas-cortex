"""Scheduling plugin — alarms, timers & reminders for Layer 2."""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.scheduling import (
    AlarmEngine,
    ParsedTime,
    ReminderEngine,
    TimerEngine,
    parse_time,
)

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_TIMER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("set_timer", re.compile(
        r"\b(?:set\s+(?:a\s+)?timer|timer\s+for|start\s+(?:a\s+)?(?:\w+\s+)?(?:minute|min|hour|hr|second|sec)s?\s+timer)\b",
        re.IGNORECASE,
    )),
    ("list_timers", re.compile(
        r"\b(?:list\s+(?:my\s+)?timers|show\s+(?:my\s+)?timers|what\s+timers|how\s+much\s+time)\b",
        re.IGNORECASE,
    )),
    ("cancel_timer", re.compile(
        r"\b(?:cancel|stop|delete|remove)\s+(?:the\s+|my\s+)?timer\b",
        re.IGNORECASE,
    )),
    ("pause_timer", re.compile(
        r"\bpause\s+(?:the\s+|my\s+)?timer\b",
        re.IGNORECASE,
    )),
    ("resume_timer", re.compile(
        r"\bresume\s+(?:the\s+|my\s+)?timer\b",
        re.IGNORECASE,
    )),
]

_ALARM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("set_alarm", re.compile(
        r"\b(?:set\s+(?:an?\s+)?alarm|wake\s+me\s+up(?:\s+at)?|alarm\s+(?:for|at))\b",
        re.IGNORECASE,
    )),
    ("list_alarms", re.compile(
        r"\b(?:list\s+(?:my\s+)?alarms|show\s+(?:my\s+)?alarms|what\s+alarms)\b",
        re.IGNORECASE,
    )),
    ("cancel_alarm", re.compile(
        r"\b(?:cancel|delete|remove)\s+(?:the\s+|my\s+)?alarm\b",
        re.IGNORECASE,
    )),
    ("snooze_alarm", re.compile(
        r"\bsnooze\b",
        re.IGNORECASE,
    )),
]

_REMINDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("set_reminder", re.compile(
        r"\b(?:remind\s+me\s+to|reminder\s+to|set\s+(?:a\s+)?reminder)\b",
        re.IGNORECASE,
    )),
    ("list_reminders", re.compile(
        r"\b(?:list\s+(?:my\s+)?reminders|show\s+(?:my\s+)?reminders|what\s+(?:are\s+)?(?:my\s+)?reminders)\b",
        re.IGNORECASE,
    )),
    ("cancel_reminder", re.compile(
        r"\b(?:cancel|delete|remove)\s+(?:the\s+|my\s+)?reminder\b",
        re.IGNORECASE,
    )),
]

# Label extraction for cancel / specific operations
_LABEL_RE = re.compile(
    r"(?:called|named|labeled|labelled)\s+[\"']?(.+?)[\"']?\s*$",
    re.IGNORECASE,
)


def _detect_intent(message: str) -> str | None:
    """Return the first matching intent string, or None."""
    for intent, pattern in _TIMER_PATTERNS:
        if pattern.search(message):
            return intent
    for intent, pattern in _ALARM_PATTERNS:
        if pattern.search(message):
            return intent
    for intent, pattern in _REMINDER_PATTERNS:
        if pattern.search(message):
            return intent
    return None


def _extract_label(message: str) -> str:
    """Try to extract a label from the message (e.g. 'timer for eggs')."""
    m = _LABEL_RE.search(message)
    if m:
        return m.group(1).strip()
    # Try "timer for <label>"
    m = re.search(r"timer\s+for\s+(?:the\s+)?(.+?)(?:\s+for\s+|\s*$)", message, re.IGNORECASE)
    if m:
        # Don't capture pure durations as labels
        candidate = m.group(1).strip()
        if not re.match(r"^\d+\s+(?:minute|min|hour|hr|second|sec|day)s?$", candidate, re.IGNORECASE):
            return candidate
    return ""


def _extract_reminder_message(message: str) -> str:
    """Extract the reminder content, e.g. 'remind me to take meds' → 'take meds'."""
    m = re.search(r"remind\s+me\s+to\s+(.+?)(?:\s+(?:at|in|on|every|tomorrow|tonight)\s+|$)", message, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:reminder|remind\s+me)\s+(?:to\s+|about\s+)?(.+?)(?:\s+(?:at|in|on|every|tomorrow|tonight)\s+|$)", message, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return message


def _format_duration(seconds: int) -> str:
    """Human-friendly duration string."""
    if seconds >= 3600:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if mins:
            return f"{hours} hour{'s' if hours != 1 else ''} and {mins} minute{'s' if mins != 1 else ''}"
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if seconds >= 60:
        mins = seconds // 60
        secs = seconds % 60
        if secs:
            return f"{mins} minute{'s' if mins != 1 else ''} and {secs} second{'s' if secs != 1 else ''}"
        return f"{mins} minute{'s' if mins != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _format_time(parsed: ParsedTime) -> str:
    """Human-friendly description of the parsed time."""
    if parsed.absolute_time:
        return parsed.absolute_time.strftime("%-I:%M %p on %B %-d")
    if parsed.duration_seconds:
        return _format_duration(parsed.duration_seconds)
    if parsed.cron_expression:
        return f"recurring ({parsed.cron_expression})"
    return "an unknown time"


# ── Plugin class ─────────────────────────────────────────────────

class SchedulingPlugin(CortexPlugin):
    """Layer 2 plugin for alarms, timers, and reminders."""

    plugin_id = "scheduling"
    display_name = "Alarms, Timers & Reminders"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        self.timer_engine: TimerEngine | None = None
        self.alarm_engine: AlarmEngine | None = None
        self.reminder_engine: ReminderEngine | None = None

    async def setup(self, config: dict[str, Any]) -> bool:
        self.timer_engine = TimerEngine()
        self.alarm_engine = AlarmEngine()
        self.reminder_engine = ReminderEngine()
        # Start background loops
        await self.alarm_engine.start()
        await self.reminder_engine.start()
        # Restore persisted timers
        await self.timer_engine.restore_from_db()
        logger.info("SchedulingPlugin ready")
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        intent = _detect_intent(message)
        if intent is None:
            return CommandMatch(matched=False)
        return CommandMatch(
            matched=True,
            intent=intent,
            confidence=0.9,
            metadata={"raw": message},
        )

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        intent = match.intent
        user_id = context.get("user_id", "")
        room = context.get("room", "")

        try:
            handler = getattr(self, f"_handle_{intent}", None)
            if handler is None:
                return CommandResult(success=False, response="I'm not sure how to do that.")
            return await handler(message, user_id, room)
        except Exception as exc:
            logger.exception("Scheduling handle error for intent=%s", intent)
            return CommandResult(success=False, response=f"Sorry, something went wrong: {exc}")

    # ── Timer handlers ───────────────────────────────────────────

    async def _handle_set_timer(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.timer_engine is not None
        parsed = parse_time(message)
        duration = parsed.duration_seconds
        if duration is None and parsed.absolute_time:
            from datetime import datetime
            diff = (parsed.absolute_time - datetime.now()).total_seconds()
            duration = int(max(0, diff))
        if not duration or duration <= 0:
            return CommandResult(success=False, response="I couldn't figure out how long to set the timer for. Try something like 'set a timer for 5 minutes'.")
        label = _extract_label(message)
        tid = await self.timer_engine.start_timer(duration, label=label, user_id=user_id, room=room)
        dur_text = _format_duration(duration)
        label_text = f" for {label}" if label else ""
        return CommandResult(
            success=True,
            response=f"Timer set{label_text} — {dur_text}.",
            metadata={"timer_id": tid, "duration_seconds": duration},
        )

    async def _handle_cancel_timer(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.timer_engine is not None
        timers = await self.timer_engine.list_timers(user_id=user_id)
        active = [t for t in timers if t["state"] in ("running", "paused")]
        if not active:
            return CommandResult(success=True, response="You don't have any active timers.")
        # Cancel the most recent active timer
        target = active[-1]
        await self.timer_engine.cancel_timer(target["id"])
        label = target.get("label", "")
        label_text = f" ({label})" if label else ""
        return CommandResult(success=True, response=f"Timer{label_text} cancelled.")

    async def _handle_pause_timer(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.timer_engine is not None
        timers = await self.timer_engine.list_timers(user_id=user_id)
        running = [t for t in timers if t["state"] == "running"]
        if not running:
            return CommandResult(success=True, response="You don't have any running timers to pause.")
        target = running[-1]
        ok = await self.timer_engine.pause_timer(target["id"])
        if ok:
            return CommandResult(success=True, response="Timer paused.")
        return CommandResult(success=False, response="Couldn't pause the timer.")

    async def _handle_resume_timer(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.timer_engine is not None
        timers = await self.timer_engine.list_timers(user_id=user_id)
        paused = [t for t in timers if t["state"] == "paused"]
        if not paused:
            return CommandResult(success=True, response="You don't have any paused timers.")
        target = paused[-1]
        ok = await self.timer_engine.resume_timer(target["id"])
        if ok:
            return CommandResult(success=True, response="Timer resumed.")
        return CommandResult(success=False, response="Couldn't resume the timer.")

    async def _handle_list_timers(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.timer_engine is not None
        timers = await self.timer_engine.list_timers(user_id=user_id)
        active = [t for t in timers if t["state"] in ("running", "paused")]
        if not active:
            return CommandResult(success=True, response="You have no active timers.")
        lines: list[str] = []
        for t in active:
            rem = int(t.get("remaining_seconds", 0))
            label = t.get("label", "")
            state = t["state"]
            desc = _format_duration(rem) if rem > 0 else "finishing"
            prefix = f"{label}: " if label else ""
            lines.append(f"• {prefix}{desc} remaining ({state})")
        return CommandResult(
            success=True,
            response="Your timers:\n" + "\n".join(lines),
        )

    # ── Alarm handlers ───────────────────────────────────────────

    async def _handle_set_alarm(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.alarm_engine is not None
        parsed = parse_time(message)
        if parsed.cron_expression:
            cron = parsed.cron_expression
            label = _extract_label(message) or "alarm"
        elif parsed.absolute_time:
            # One-shot alarm: build a cron for the exact minute
            t = parsed.absolute_time
            cron = f"{t.minute} {t.hour} {t.day} {t.month} *"
            label = _extract_label(message) or "alarm"
        else:
            return CommandResult(success=False, response="I couldn't determine when to set the alarm. Try 'set an alarm for 7 AM'.")
        aid = await self.alarm_engine.create_alarm(cron, label=label, user_id=user_id, room=room)
        time_text = _format_time(parsed)
        return CommandResult(
            success=True,
            response=f"Alarm set for {time_text}.",
            metadata={"alarm_id": aid},
        )

    async def _handle_cancel_alarm(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.alarm_engine is not None
        alarms = await self.alarm_engine.list_alarms(user_id=user_id)
        active = [a for a in alarms if a.get("enabled", 1)]
        if not active:
            return CommandResult(success=True, response="You don't have any active alarms.")
        target = active[-1]
        await self.alarm_engine.delete_alarm(target["id"])
        label = target.get("label", "")
        label_text = f" ({label})" if label else ""
        return CommandResult(success=True, response=f"Alarm{label_text} cancelled.")

    async def _handle_snooze_alarm(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.alarm_engine is not None
        # Extract snooze minutes if specified
        m = re.search(r"(\d+)\s*(?:minute|min)", message, re.IGNORECASE)
        minutes = int(m.group(1)) if m else 5
        alarms = await self.alarm_engine.list_alarms(user_id=user_id)
        if not alarms:
            return CommandResult(success=True, response="No alarms to snooze.")
        # Snooze the most recently fired or latest alarm
        target = alarms[-1]
        ok = await self.alarm_engine.snooze_alarm(target["id"], minutes=minutes)
        if ok:
            return CommandResult(success=True, response=f"Alarm snoozed for {minutes} minutes.")
        return CommandResult(success=False, response="Couldn't snooze the alarm.")

    async def _handle_list_alarms(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.alarm_engine is not None
        alarms = await self.alarm_engine.list_alarms(user_id=user_id)
        if not alarms:
            return CommandResult(success=True, response="You have no alarms.")
        lines: list[str] = []
        for a in alarms:
            label = a.get("label", "") or "alarm"
            enabled = "✓" if a.get("enabled") else "✗"
            nf = a.get("next_fire", "")
            lines.append(f"• [{enabled}] {label} — next: {nf or 'N/A'}")
        return CommandResult(
            success=True,
            response="Your alarms:\n" + "\n".join(lines),
        )

    # ── Reminder handlers ────────────────────────────────────────

    async def _handle_set_reminder(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.reminder_engine is not None
        parsed = parse_time(message)
        reminder_text = _extract_reminder_message(message)
        if parsed.cron_expression:
            rid = await self.reminder_engine.create_reminder(
                reminder_text,
                cron_expression=parsed.cron_expression,
                user_id=user_id,
                room=room,
            )
            time_text = _format_time(parsed)
        elif parsed.absolute_time:
            rid = await self.reminder_engine.create_reminder(
                reminder_text,
                trigger_at=parsed.absolute_time,
                user_id=user_id,
                room=room,
            )
            time_text = _format_time(parsed)
        elif parsed.duration_seconds:
            from datetime import datetime, timedelta
            trigger_at = datetime.now() + timedelta(seconds=parsed.duration_seconds)
            rid = await self.reminder_engine.create_reminder(
                reminder_text,
                trigger_at=trigger_at,
                user_id=user_id,
                room=room,
            )
            time_text = f"in {_format_duration(parsed.duration_seconds)}"
        else:
            return CommandResult(
                success=False,
                response="I couldn't figure out when to remind you. Try 'remind me to take meds at 9 AM'.",
            )
        return CommandResult(
            success=True,
            response=f"I'll remind you to {reminder_text} {time_text}.",
            metadata={"reminder_id": rid},
        )

    async def _handle_cancel_reminder(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.reminder_engine is not None
        reminders = await self.reminder_engine.list_reminders(user_id=user_id)
        if not reminders:
            return CommandResult(success=True, response="You don't have any active reminders.")
        target = reminders[-1]
        await self.reminder_engine.delete_reminder(target["id"])
        msg = target.get("message", "")
        return CommandResult(success=True, response=f"Reminder cancelled: {msg}")

    async def _handle_list_reminders(self, message: str, user_id: str, room: str) -> CommandResult:
        assert self.reminder_engine is not None
        reminders = await self.reminder_engine.list_reminders(user_id=user_id)
        if not reminders:
            return CommandResult(success=True, response="You have no active reminders.")
        lines: list[str] = []
        for r in reminders:
            msg = r.get("message", "")
            trigger = r.get("trigger_at") or r.get("cron_expression") or "event"
            lines.append(f"• {msg} — {trigger}")
        return CommandResult(
            success=True,
            response="Your reminders:\n" + "\n".join(lines),
        )
