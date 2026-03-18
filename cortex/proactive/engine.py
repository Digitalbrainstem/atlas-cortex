"""Rule engine — evaluate proactive rules and fire actions."""

# Module ownership: Proactive rule evaluation and action dispatch
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db
from cortex.proactive.throttle import NotificationThrottle

logger = logging.getLogger(__name__)

# Provider registry — populated at startup
_PROVIDERS: dict[str, Any] = {}


def register_provider(provider: Any) -> None:
    """Register a proactive data provider by its provider_id."""
    _PROVIDERS[provider.provider_id] = provider


def get_provider(provider_id: str) -> Any | None:
    """Look up a registered provider."""
    return _PROVIDERS.get(provider_id)


class RuleEngine:
    """Evaluate proactive rules against provider data and fire actions."""

    def __init__(self, throttle: NotificationThrottle | None = None) -> None:
        self.throttle = throttle or NotificationThrottle()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    # ── CRUD ─────────────────────────────────────────────────────

    async def create_rule(
        self,
        name: str,
        provider: str,
        condition_type: str,
        condition_config: dict[str, Any],
        action_type: str,
        action_config: dict[str, Any],
        priority: str = "normal",
        cooldown_minutes: int = 60,
        user_id: str = "",
    ) -> int:
        """Insert a new rule and return its ID."""
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO proactive_rules "
            "(name, provider, condition_type, condition_config, "
            "action_type, action_config, priority, cooldown_minutes, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                provider,
                condition_type,
                json.dumps(condition_config),
                action_type,
                json.dumps(action_config),
                priority,
                cooldown_minutes,
                user_id,
            ),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_rule(self, rule_id: int) -> bool:
        conn = get_db()
        cur = conn.execute("DELETE FROM proactive_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0

    async def enable_rule(self, rule_id: int) -> bool:
        conn = get_db()
        cur = conn.execute(
            "UPDATE proactive_rules SET enabled = 1 WHERE id = ?", (rule_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    async def disable_rule(self, rule_id: int) -> bool:
        conn = get_db()
        cur = conn.execute(
            "UPDATE proactive_rules SET enabled = 0 WHERE id = ?", (rule_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    async def list_rules(self, user_id: str = "") -> list[dict[str, Any]]:
        conn = get_db()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM proactive_rules WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proactive_rules ORDER BY id",
            ).fetchall()
        cols = [
            "id", "name", "description", "provider", "condition_type",
            "condition_config", "action_type", "action_config", "priority",
            "enabled", "cooldown_minutes", "last_fired", "fire_count",
            "user_id", "created_at",
        ]
        results = []
        for row in rows:
            d = dict(zip(cols, row))
            d["condition_config"] = json.loads(d.get("condition_config") or "{}")
            d["action_config"] = json.loads(d.get("action_config") or "{}")
            results.append(d)
        return results

    # ── Evaluation ───────────────────────────────────────────────

    async def evaluate_all(self) -> list[dict[str, Any]]:
        """Evaluate all enabled rules. Return list of fired actions."""
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM proactive_rules WHERE enabled = 1 ORDER BY id",
        ).fetchall()
        cols = [
            "id", "name", "description", "provider", "condition_type",
            "condition_config", "action_type", "action_config", "priority",
            "enabled", "cooldown_minutes", "last_fired", "fire_count",
            "user_id", "created_at",
        ]

        fired: list[dict[str, Any]] = []
        for row in rows:
            rule = dict(zip(cols, row))
            rule["condition_config"] = json.loads(rule.get("condition_config") or "{}")
            rule["action_config"] = json.loads(rule.get("action_config") or "{}")

            provider_id = rule["provider"]
            provider = get_provider(provider_id)
            if provider is None:
                continue

            try:
                provider_data = await provider.fetch_data()
            except Exception:
                logger.exception("Provider %s fetch failed", provider_id)
                continue

            try:
                if await self.evaluate_rule(rule["id"], provider_data, rule=rule):
                    # Throttle check
                    user_id = rule.get("user_id", "")
                    if not self.throttle.should_deliver(
                        user_id, rule["priority"], rule["id"],
                    ):
                        continue
                    action_result = await self.fire_action(rule, provider_data)
                    self.throttle.record_delivery(user_id, rule["id"])
                    fired.append({
                        "rule_id": rule["id"],
                        "rule_name": rule["name"],
                        "action_taken": action_result,
                        "provider_data": provider_data,
                    })
            except Exception:
                logger.exception("Rule %d evaluation failed", rule["id"])

        return fired

    async def evaluate_rule(
        self,
        rule_id: int,
        provider_data: dict[str, Any],
        *,
        rule: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a single rule's condition is met."""
        if rule is None:
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM proactive_rules WHERE id = ?", (rule_id,),
            ).fetchone()
            if not row:
                return False
            cols = [
                "id", "name", "description", "provider", "condition_type",
                "condition_config", "action_type", "action_config", "priority",
                "enabled", "cooldown_minutes", "last_fired", "fire_count",
                "user_id", "created_at",
            ]
            rule = dict(zip(cols, row))
            rule["condition_config"] = json.loads(rule.get("condition_config") or "{}")
            rule["action_config"] = json.loads(rule.get("action_config") or "{}")

        cond_type = rule["condition_type"]
        config = rule["condition_config"]

        if cond_type == "threshold":
            return self._eval_threshold(config, provider_data)
        if cond_type == "change":
            return self._eval_change(config, provider_data)
        if cond_type == "schedule":
            return self._eval_schedule(config)
        if cond_type == "pattern":
            return self._eval_pattern(config, provider_data)

        logger.warning("Unknown condition type: %s", cond_type)
        return False

    async def fire_action(
        self, rule: dict[str, Any], event_data: dict[str, Any],
    ) -> str:
        """Execute the rule's action and log the event."""
        action_type = rule["action_type"]
        action_config = rule.get("action_config", {})
        result = ""

        if action_type == "notify":
            result = await self._action_notify(rule, event_data, action_config)
        elif action_type == "tts_announce":
            result = await self._action_tts(rule, event_data, action_config)
        elif action_type == "routine":
            result = await self._action_routine(action_config)
        elif action_type == "log":
            result = f"logged: {rule['name']}"
            logger.info("Proactive rule fired: %s — %s", rule["name"], event_data)
        else:
            result = f"unknown_action:{action_type}"

        # Record the event
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO proactive_events "
                "(rule_id, provider, event_type, event_data, action_taken) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    rule["id"],
                    rule["provider"],
                    rule["condition_type"],
                    json.dumps(event_data),
                    result,
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to log proactive event")

        return result

    # ── Background loop ──────────────────────────────────────────

    async def start(self) -> None:
        """Start background evaluation loop (every 5 minutes)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Proactive rule engine started")

    async def stop(self) -> None:
        """Stop the background evaluation loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Proactive rule engine stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                fired = await self.evaluate_all()
                if fired:
                    logger.info("Proactive engine fired %d rule(s)", len(fired))
            except Exception:
                logger.exception("Proactive evaluation loop error")
            await asyncio.sleep(300)  # 5 minutes

    # ── Condition evaluators ─────────────────────────────────────

    @staticmethod
    def _eval_threshold(
        config: dict[str, Any], data: dict[str, Any],
    ) -> bool:
        """Fire when value > or < threshold.

        Config: {"field": "temperature", "operator": ">", "value": 95}
        """
        field = config.get("field", "")
        operator = config.get("operator", ">")
        threshold = config.get("value")
        if threshold is None or field not in data:
            return False
        actual = data[field]
        if actual is None:
            return False
        try:
            actual = float(actual)
            threshold = float(threshold)
        except (ValueError, TypeError):
            return False
        if operator == ">":
            return actual > threshold
        if operator == "<":
            return actual < threshold
        if operator == ">=":
            return actual >= threshold
        if operator == "<=":
            return actual <= threshold
        if operator == "==":
            return actual == threshold
        return False

    @staticmethod
    def _eval_change(
        config: dict[str, Any], data: dict[str, Any],
    ) -> bool:
        """Fire when value changes by more than X percent.

        Config: {"field": "current_watts", "percent": 20, "baseline": 3000}
        """
        field = config.get("field", "")
        percent = config.get("percent", 0)
        baseline = config.get("baseline")
        if baseline is None or field not in data:
            return False
        actual = data.get(field)
        if actual is None:
            return False
        try:
            actual = float(actual)
            baseline = float(baseline)
            percent = float(percent)
        except (ValueError, TypeError):
            return False
        if baseline == 0:
            return actual != 0
        change_pct = abs(actual - baseline) / abs(baseline) * 100
        return change_pct > percent

    @staticmethod
    def _eval_schedule(config: dict[str, Any]) -> bool:
        """Fire at a specific time.

        Config: {"hour": 8, "minute": 0, "days": [0,1,2,3,4]}
        Days: 0=Monday ... 6=Sunday (ISO weekday - 1)
        """
        now = datetime.now(timezone.utc)
        target_hour = config.get("hour")
        target_minute = config.get("minute", 0)
        days = config.get("days")
        if target_hour is None:
            return False
        if days is not None and now.weekday() not in days:
            return False
        # Match within a 5-minute window (evaluation interval)
        return now.hour == target_hour and abs(now.minute - target_minute) < 5

    @staticmethod
    def _eval_pattern(
        config: dict[str, Any], data: dict[str, Any],
    ) -> bool:
        """Fire when a pattern is detected in provider data.

        Config: {"match_field": "anomalies", "min_count": 1}
        or: {"match_field": "anomaly", "match_value": true}
        """
        field = config.get("match_field", "")
        if field not in data:
            return False
        value = data[field]

        # List-based: check count
        min_count = config.get("min_count")
        if min_count is not None and isinstance(value, list):
            return len(value) >= min_count

        # Value-based: exact match
        match_value = config.get("match_value")
        if match_value is not None:
            return value == match_value

        # Truthy fallback
        return bool(value)

    # ── Action handlers ──────────────────────────────────────────

    @staticmethod
    async def _action_notify(
        rule: dict[str, Any],
        event_data: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        """Send a notification through the notification system."""
        try:
            from cortex.notifications.channels import Notification, send_notification

            msg = config.get("message", f"Rule triggered: {rule['name']}")
            level = {"low": "info", "normal": "info", "high": "warning", "critical": "critical"}.get(
                rule.get("priority", "normal"), "info",
            )
            notif = Notification(
                level=level,
                title=f"Proactive: {rule['name']}",
                message=msg,
                source="proactive",
                metadata={"rule_id": rule["id"], "event_data": event_data},
            )
            await send_notification(notif)
            return f"notified: {msg}"
        except Exception:
            logger.exception("Notify action failed for rule %d", rule["id"])
            return "notify_failed"

    @staticmethod
    async def _action_tts(
        rule: dict[str, Any],
        event_data: dict[str, Any],
        config: dict[str, Any],
    ) -> str:
        """Speak an announcement via TTS to a satellite."""
        try:
            from cortex.notifications.satellite import SatelliteChannel

            msg = config.get("message", f"Alert: {rule['name']}")
            satellite_id = config.get("satellite_id", "")
            channel = SatelliteChannel(satellite_id=satellite_id)
            from cortex.notifications.channels import Notification

            notif = Notification(
                level="warning",
                title=rule["name"],
                message=msg,
                source="proactive",
            )
            await channel.send(notif)
            return f"tts_announced: {msg}"
        except Exception:
            logger.exception("TTS action failed for rule %d", rule["id"])
            return "tts_failed"

    @staticmethod
    async def _action_routine(config: dict[str, Any]) -> str:
        """Trigger a routine by ID."""
        routine_id = config.get("routine_id")
        if routine_id is None:
            return "routine_failed: no routine_id"
        try:
            from cortex.routines.engine import RoutineEngine

            engine = RoutineEngine()
            result = await engine.run_routine(int(routine_id))
            return f"routine_triggered: {routine_id} ({result})"
        except Exception:
            logger.exception("Routine action failed: %s", routine_id)
            return f"routine_failed: {routine_id}"
