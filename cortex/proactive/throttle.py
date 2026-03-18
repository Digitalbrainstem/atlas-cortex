"""Notification throttle — quiet hours, rate limiting, and cooldowns."""

# Module ownership: Notification delivery gating
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db

logger = logging.getLogger(__name__)

PRIORITY_LEVELS = {"low": 0, "normal": 1, "high": 2, "critical": 3}


class NotificationThrottle:
    """Gate notification delivery based on quiet hours, rate limits, and cooldowns."""

    def __init__(self) -> None:
        self._recent_deliveries: dict[str, list[datetime]] = defaultdict(list)

    # ── Public API ───────────────────────────────────────────────

    def should_deliver(self, user_id: str, priority: str, rule_id: int) -> bool:
        """Return True if a notification should be delivered right now."""
        prefs = self.get_preferences(user_id)
        now = datetime.now(timezone.utc)

        # Critical always gets through
        if priority == "critical":
            return True

        # Check quiet hours
        if self._in_quiet_hours(now, prefs):
            min_pri = prefs.get("min_priority", "normal")
            if PRIORITY_LEVELS.get(priority, 1) < PRIORITY_LEVELS.get(min_pri, 1):
                logger.debug("Throttled rule %d: quiet hours (priority %s < %s)",
                             rule_id, priority, min_pri)
                return False

        # Check rate limit
        max_per_hour = prefs.get("max_per_hour", 10)
        if self._count_recent(user_id, now) >= max_per_hour:
            logger.debug("Throttled rule %d: rate limit (%d/hr)", rule_id, max_per_hour)
            return False

        # Check per-rule cooldown (stored in proactive_rules.last_fired)
        if not self._cooldown_elapsed(rule_id):
            logger.debug("Throttled rule %d: cooldown not elapsed", rule_id)
            return False

        return True

    def record_delivery(self, user_id: str, rule_id: int) -> None:
        """Record that a notification was delivered for rate-limiting."""
        now = datetime.now(timezone.utc)
        self._recent_deliveries[user_id].append(now)
        # Update last_fired on the rule
        try:
            conn = get_db()
            conn.execute(
                "UPDATE proactive_rules SET last_fired = ?, fire_count = fire_count + 1 "
                "WHERE id = ?",
                (now.isoformat(), rule_id),
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to record delivery for rule %d", rule_id)

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        """Load notification preferences for a user (or defaults)."""
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT quiet_hours_start, quiet_hours_end, min_priority, "
                "max_per_hour, channels FROM notification_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                return {
                    "quiet_hours_start": row[0],
                    "quiet_hours_end": row[1],
                    "min_priority": row[2],
                    "max_per_hour": row[3],
                    "channels": json.loads(row[4]) if row[4] else ["log"],
                }
        except Exception:
            logger.exception("Failed to load notification prefs for %s", user_id)
        return {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "min_priority": "normal",
            "max_per_hour": 10,
            "channels": ["log"],
        }

    def set_preferences(self, user_id: str, prefs: dict[str, Any]) -> None:
        """Create or update notification preferences for a user."""
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO notification_preferences "
                "(user_id, quiet_hours_start, quiet_hours_end, min_priority, "
                "max_per_hour, channels) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "quiet_hours_start = excluded.quiet_hours_start, "
                "quiet_hours_end = excluded.quiet_hours_end, "
                "min_priority = excluded.min_priority, "
                "max_per_hour = excluded.max_per_hour, "
                "channels = excluded.channels",
                (
                    user_id,
                    prefs.get("quiet_hours_start", "22:00"),
                    prefs.get("quiet_hours_end", "07:00"),
                    prefs.get("min_priority", "normal"),
                    prefs.get("max_per_hour", 10),
                    json.dumps(prefs.get("channels", ["log"])),
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to set notification prefs for %s", user_id)

    # ── Internal helpers ─────────────────────────────────────────

    def _in_quiet_hours(self, now: datetime, prefs: dict[str, Any]) -> bool:
        """Check whether *now* falls inside the user's quiet-hours window."""
        start_str = prefs.get("quiet_hours_start", "22:00")
        end_str = prefs.get("quiet_hours_end", "07:00")
        try:
            start_h, start_m = (int(p) for p in start_str.split(":"))
            end_h, end_m = (int(p) for p in end_str.split(":"))
        except (ValueError, AttributeError):
            return False

        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes <= end_minutes:
            # Same-day range (e.g. 09:00–17:00)
            return start_minutes <= current_minutes < end_minutes
        # Overnight range (e.g. 22:00–07:00)
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def _count_recent(self, user_id: str, now: datetime) -> int:
        """Count deliveries to *user_id* in the last hour, pruning old entries."""
        cutoff = now.timestamp() - 3600
        entries = self._recent_deliveries[user_id]
        self._recent_deliveries[user_id] = [
            t for t in entries if t.timestamp() > cutoff
        ]
        return len(self._recent_deliveries[user_id])

    def _cooldown_elapsed(self, rule_id: int) -> bool:
        """Return True if the rule's cooldown period has elapsed."""
        try:
            conn = get_db()
            row = conn.execute(
                "SELECT cooldown_minutes, last_fired FROM proactive_rules WHERE id = ?",
                (rule_id,),
            ).fetchone()
            if not row or not row[1]:
                return True
            cooldown_minutes, last_fired_str = row
            last_fired = datetime.fromisoformat(last_fired_str)
            if last_fired.tzinfo is None:
                last_fired = last_fired.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_fired).total_seconds() / 60
            return elapsed >= cooldown_minutes
        except Exception:
            logger.exception("Failed to check cooldown for rule %d", rule_id)
            return True
