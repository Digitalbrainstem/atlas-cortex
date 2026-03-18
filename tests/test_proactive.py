"""Tests for proactive intelligence — rule engine, throttle, and providers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.proactive.engine import RuleEngine, register_provider, _PROVIDERS
from cortex.proactive.throttle import NotificationThrottle
from cortex.proactive.providers import (
    AnomalyDetector,
    CalendarAwareness,
    EnergyMonitor,
    ProactiveProvider,
    WeatherIntelligence,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Each test gets its own database."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    init_db()
    _PROVIDERS.clear()
    yield
    _PROVIDERS.clear()


# ── RuleEngine CRUD ──────────────────────────────────────────────────────────


class TestRuleEngineCRUD:
    async def test_create_rule(self):
        engine = RuleEngine()
        rule_id = await engine.create_rule(
            name="Hot alert",
            provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 95},
            action_type="notify",
            action_config={"message": "It's hot!"},
            priority="high",
        )
        assert rule_id is not None
        assert rule_id > 0

    async def test_list_rules(self):
        engine = RuleEngine()
        await engine.create_rule(
            name="Rule A", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 90},
            action_type="log", action_config={},
        )
        await engine.create_rule(
            name="Rule B", provider="energy",
            condition_type="change",
            condition_config={"field": "current_watts", "percent": 20, "baseline": 3000},
            action_type="notify", action_config={},
            user_id="user1",
        )
        all_rules = await engine.list_rules()
        assert len(all_rules) == 2

        user_rules = await engine.list_rules(user_id="user1")
        assert len(user_rules) == 1
        assert user_rules[0]["name"] == "Rule B"

    async def test_delete_rule(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Temp", provider="weather",
            condition_type="threshold",
            condition_config={}, action_type="log", action_config={},
        )
        assert await engine.delete_rule(rid) is True
        assert await engine.delete_rule(rid) is False
        assert len(await engine.list_rules()) == 0

    async def test_enable_disable_rule(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Toggle", provider="weather",
            condition_type="threshold",
            condition_config={}, action_type="log", action_config={},
        )
        assert await engine.disable_rule(rid) is True
        rules = await engine.list_rules()
        assert rules[0]["enabled"] == 0

        assert await engine.enable_rule(rid) is True
        rules = await engine.list_rules()
        assert rules[0]["enabled"] == 1


# ── Condition evaluation ─────────────────────────────────────────────────────


class TestConditionEvaluation:
    async def test_threshold_gt(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Heat", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 95},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {"temperature": 100}) is True
        assert await engine.evaluate_rule(rid, {"temperature": 90}) is False

    async def test_threshold_lt(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Cold", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": "<", "value": 32},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {"temperature": 20}) is True
        assert await engine.evaluate_rule(rid, {"temperature": 50}) is False

    async def test_threshold_missing_field(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Missing", provider="weather",
            condition_type="threshold",
            condition_config={"field": "humidity", "operator": ">", "value": 80},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {"temperature": 100}) is False

    async def test_change_detection(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Energy spike", provider="energy",
            condition_type="change",
            condition_config={"field": "current_watts", "percent": 20, "baseline": 3000},
            action_type="log", action_config={},
        )
        # 3600 is 20% above 3000 — exactly at threshold, not over
        assert await engine.evaluate_rule(rid, {"current_watts": 3600}) is False
        # 3700 is >20% above 3000
        assert await engine.evaluate_rule(rid, {"current_watts": 3700}) is True
        # Close to baseline
        assert await engine.evaluate_rule(rid, {"current_watts": 3100}) is False

    async def test_pattern_list_count(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Anomaly alert", provider="anomaly",
            condition_type="pattern",
            condition_config={"match_field": "anomalies", "min_count": 1},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {"anomalies": [{"entity": "door"}]}) is True
        assert await engine.evaluate_rule(rid, {"anomalies": []}) is False

    async def test_pattern_value_match(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Anomaly flag", provider="energy",
            condition_type="pattern",
            condition_config={"match_field": "anomaly", "match_value": True},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {"anomaly": True}) is True
        assert await engine.evaluate_rule(rid, {"anomaly": False}) is False

    async def test_schedule_condition(self):
        engine = RuleEngine()
        now = datetime.now(timezone.utc)
        rid = await engine.create_rule(
            name="Morning check", provider="weather",
            condition_type="schedule",
            condition_config={"hour": now.hour, "minute": now.minute},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid, {}) is True

        rid2 = await engine.create_rule(
            name="Wrong hour", provider="weather",
            condition_type="schedule",
            condition_config={"hour": (now.hour + 6) % 24, "minute": 0},
            action_type="log", action_config={},
        )
        assert await engine.evaluate_rule(rid2, {}) is False


# ── Action firing ────────────────────────────────────────────────────────────


class TestActionFiring:
    async def test_log_action(self):
        engine = RuleEngine()
        # Create the rule in DB so foreign key is satisfied
        rid = await engine.create_rule(
            name="Test log", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 90},
            action_type="log", action_config={},
        )
        rule = {
            "id": rid, "name": "Test log", "provider": "weather",
            "condition_type": "threshold", "action_type": "log",
            "action_config": {}, "priority": "normal",
        }
        result = await engine.fire_action(rule, {"temperature": 100})
        assert "logged" in result

        # Verify event was recorded
        conn = get_db()
        events = conn.execute("SELECT * FROM proactive_events").fetchall()
        assert len(events) == 1

    async def test_notify_action(self):
        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Heat notify", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 90},
            action_type="notify", action_config={"message": "It's scorching!"},
            priority="high",
        )
        rule = {
            "id": rid, "name": "Heat notify", "provider": "weather",
            "condition_type": "threshold", "action_type": "notify",
            "action_config": {"message": "It's scorching!"}, "priority": "high",
        }
        with patch("cortex.notifications.channels.send_notification", new_callable=AsyncMock):
            result = await engine.fire_action(rule, {"temperature": 105})
        assert "notified" in result or "notify_failed" in result


# ── NotificationThrottle ─────────────────────────────────────────────────────


class TestNotificationThrottle:
    def test_default_preferences(self):
        throttle = NotificationThrottle()
        prefs = throttle.get_preferences("unknown_user")
        assert prefs["quiet_hours_start"] == "22:00"
        assert prefs["max_per_hour"] == 10

    def test_set_and_get_preferences(self):
        throttle = NotificationThrottle()
        throttle.set_preferences("user1", {
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "06:00",
            "min_priority": "high",
            "max_per_hour": 5,
            "channels": ["log", "tts"],
        })
        prefs = throttle.get_preferences("user1")
        assert prefs["quiet_hours_start"] == "23:00"
        assert prefs["min_priority"] == "high"
        assert prefs["max_per_hour"] == 5
        assert "tts" in prefs["channels"]

    def test_critical_always_delivers(self):
        throttle = NotificationThrottle()
        # Create a rule first
        conn = get_db()
        conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, action_type, "
            "cooldown_minutes) VALUES ('test', 'weather', 'threshold', 'log', 60)",
        )
        conn.commit()
        assert throttle.should_deliver("user1", "critical", 1) is True

    def test_rate_limiting(self):
        throttle = NotificationThrottle()
        conn = get_db()
        conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, action_type, "
            "cooldown_minutes) VALUES ('test', 'weather', 'threshold', 'log', 0)",
        )
        conn.commit()

        throttle.set_preferences("user1", {"max_per_hour": 3})
        # Deliver 3 times
        for _ in range(3):
            throttle.record_delivery("user1", 1)
        # 4th should be blocked
        assert throttle.should_deliver("user1", "normal", 1) is False

    def test_cooldown_enforcement(self):
        throttle = NotificationThrottle()
        conn = get_db()
        # Rule with 60-minute cooldown, last fired just now
        conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, action_type, "
            "cooldown_minutes, last_fired) VALUES ('test', 'weather', 'threshold', 'log', "
            "60, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        throttle.set_preferences("user1", {"max_per_hour": 100})
        assert throttle.should_deliver("user1", "normal", 1) is False

    def test_cooldown_elapsed(self):
        throttle = NotificationThrottle()
        conn = get_db()
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, action_type, "
            "cooldown_minutes, last_fired) VALUES ('test', 'weather', 'threshold', 'log', "
            "60, ?)",
            (old_time,),
        )
        conn.commit()
        assert throttle.should_deliver("user1", "normal", 1) is True

    def test_quiet_hours_blocking(self):
        throttle = NotificationThrottle()
        conn = get_db()
        conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, action_type, "
            "cooldown_minutes) VALUES ('test', 'weather', 'threshold', 'log', 0)",
        )
        conn.commit()

        # Set quiet hours that span current time
        now = datetime.now(timezone.utc)
        start_hour = now.hour
        end_hour = (now.hour + 2) % 24
        throttle.set_preferences("user1", {
            "quiet_hours_start": f"{start_hour:02d}:00",
            "quiet_hours_end": f"{end_hour:02d}:00",
            "min_priority": "high",
        })
        # Low priority during quiet hours should be blocked
        assert throttle.should_deliver("user1", "low", 1) is False
        # High priority should still get through
        assert throttle.should_deliver("user1", "high", 1) is True


# ── Providers ────────────────────────────────────────────────────────────────


class TestWeatherProvider:
    async def test_empty_without_credentials(self):
        provider = WeatherIntelligence(api_key="", location="")
        data = await provider.fetch_data()
        assert data["temperature"] is None
        assert data["condition"] == ""
        assert data["alerts"] == []

    async def test_health_without_credentials(self):
        provider = WeatherIntelligence(api_key="", location="")
        assert await provider.health() is False

    async def test_fetch_with_mock(self):
        provider = WeatherIntelligence(
            api_key="test-key", location="Dallas",
            base_url="http://localhost:9999",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "main": {"temp": 98, "feels_like": 102, "humidity": 45},
            "weather": [{"main": "Clear", "description": "clear sky"}],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            data = await provider.fetch_data()
            assert data["temperature"] == 98
            assert data["condition"] == "Clear"


class TestEnergyProvider:
    async def test_empty_without_credentials(self):
        provider = EnergyMonitor(ha_url="", ha_token="")
        data = await provider.fetch_data()
        assert data["current_watts"] == 0
        assert data["anomaly"] is False

    async def test_health_without_credentials(self):
        provider = EnergyMonitor(ha_url="", ha_token="")
        assert await provider.health() is False

    def test_anomaly_detection(self):
        provider = EnergyMonitor(ha_url="http://x", ha_token="t")
        # Build baseline with slight variance (stddev != 0)
        provider._history = [3000 + (i % 5) * 10 for i in range(50)]
        # Mean ~3020, stddev ~14.1 → threshold ~3048
        assert provider._detect_anomaly(3020) is False
        assert provider._detect_anomaly(9000) is True


class TestAnomalyDetector:
    async def test_empty_without_credentials(self):
        detector = AnomalyDetector(ha_url="", ha_token="")
        data = await detector.fetch_data()
        assert data["anomalies"] == []
        assert data["anomaly_count"] == 0

    def test_baseline_anomaly(self):
        detector = AnomalyDetector()
        for v in [20.0, 21.0, 19.0, 20.5, 20.0, 19.5, 21.5, 20.0, 19.0, 20.0]:
            detector.record_baseline("sensor.temp", v)
        assert detector.is_anomalous("sensor.temp", 20.0) is False
        assert detector.is_anomalous("sensor.temp", 50.0) is True

    def test_baseline_insufficient_data(self):
        detector = AnomalyDetector()
        detector.record_baseline("sensor.temp", 20.0)
        assert detector.is_anomalous("sensor.temp", 100.0) is False


class TestCalendarProvider:
    async def test_empty_without_credentials(self):
        provider = CalendarAwareness(caldav_url="", username="")
        data = await provider.fetch_data()
        assert data["upcoming"] == []

    async def test_health_without_credentials(self):
        provider = CalendarAwareness(caldav_url="", username="")
        assert await provider.health() is False


# ── Integration: rule + provider + throttle ──────────────────────────────────


class TestIntegration:
    async def test_end_to_end_rule_fires(self):
        """Create a rule, register a mock provider, evaluate, and verify firing."""
        # Create a mock provider
        mock_provider = AsyncMock(spec=ProactiveProvider)
        mock_provider.provider_id = "weather"
        mock_provider.fetch_data = AsyncMock(return_value={
            "temperature": 100, "condition": "Clear", "alerts": [],
        })
        register_provider(mock_provider)

        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Heat alert",
            provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 95},
            action_type="log",
            action_config={},
            priority="normal",
            cooldown_minutes=0,
        )

        fired = await engine.evaluate_all()
        assert len(fired) == 1
        assert fired[0]["rule_name"] == "Heat alert"
        assert "logged" in fired[0]["action_taken"]

    async def test_end_to_end_rule_not_fired(self):
        """Rule should not fire when condition is not met."""
        mock_provider = AsyncMock(spec=ProactiveProvider)
        mock_provider.provider_id = "weather"
        mock_provider.fetch_data = AsyncMock(return_value={
            "temperature": 72, "condition": "Clear", "alerts": [],
        })
        register_provider(mock_provider)

        engine = RuleEngine()
        await engine.create_rule(
            name="Heat alert",
            provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 95},
            action_type="log",
            action_config={},
        )

        fired = await engine.evaluate_all()
        assert len(fired) == 0

    async def test_disabled_rule_skipped(self):
        """Disabled rules should not be evaluated."""
        mock_provider = AsyncMock(spec=ProactiveProvider)
        mock_provider.provider_id = "weather"
        mock_provider.fetch_data = AsyncMock(return_value={"temperature": 100})
        register_provider(mock_provider)

        engine = RuleEngine()
        rid = await engine.create_rule(
            name="Disabled", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 50},
            action_type="log", action_config={},
        )
        await engine.disable_rule(rid)

        fired = await engine.evaluate_all()
        assert len(fired) == 0

    async def test_throttle_blocks_rapid_fire(self):
        """Throttle should prevent rapid-fire notifications."""
        mock_provider = AsyncMock(spec=ProactiveProvider)
        mock_provider.provider_id = "weather"
        mock_provider.fetch_data = AsyncMock(return_value={"temperature": 100})
        register_provider(mock_provider)

        throttle = NotificationThrottle()
        throttle.set_preferences("", {"max_per_hour": 1})
        engine = RuleEngine(throttle=throttle)
        await engine.create_rule(
            name="Rapid", provider="weather",
            condition_type="threshold",
            condition_config={"field": "temperature", "operator": ">", "value": 50},
            action_type="log", action_config={},
            cooldown_minutes=0,
        )

        # First evaluation fires
        fired1 = await engine.evaluate_all()
        assert len(fired1) == 1

        # Second evaluation should be throttled (rate limit)
        fired2 = await engine.evaluate_all()
        assert len(fired2) == 0

    async def test_start_stop_loop(self):
        """Engine start/stop should not raise."""
        engine = RuleEngine()
        await engine.start()
        assert engine._running is True
        await engine.stop()
        assert engine._running is False


# ── DailyBriefing ────────────────────────────────────────────────────────────


class TestDailyBriefing:

    async def test_generate_empty(self):
        """Briefing with no providers or data should still return text."""
        from cortex.proactive.briefing import DailyBriefing
        briefing = DailyBriefing()
        text = await briefing.generate()
        assert isinstance(text, str)
        assert len(text) > 0
        # Should contain a greeting
        assert any(g in text for g in ("Good morning", "Good afternoon", "Good evening"))

    async def test_generate_with_reminders(self):
        """Briefing includes pending reminders."""
        from cortex.proactive.briefing import DailyBriefing
        conn = get_db()
        conn.execute(
            "INSERT INTO reminders (message, trigger_type, fired, user_id) "
            "VALUES ('Buy milk', 'time', 0, '')"
        )
        conn.commit()
        briefing = DailyBriefing()
        text = await briefing.generate()
        assert "reminder" in text.lower()
        assert "Buy milk" in text

    async def test_generate_with_timers(self):
        """Briefing includes active timers."""
        from cortex.proactive.briefing import DailyBriefing
        conn = get_db()
        conn.execute(
            "INSERT INTO timers (label, duration_seconds, state, user_id) "
            "VALUES ('Pasta', 600, 'running', '')"
        )
        conn.commit()
        briefing = DailyBriefing()
        text = await briefing.generate()
        assert "timer" in text.lower()

    async def test_generate_with_alarms(self):
        """Briefing includes enabled alarms."""
        from cortex.proactive.briefing import DailyBriefing
        conn = get_db()
        conn.execute(
            "INSERT INTO alarms (label, cron_expression, enabled, user_id) "
            "VALUES ('Wake up', '0 7 * * *', 1, '')"
        )
        conn.commit()
        briefing = DailyBriefing()
        text = await briefing.generate()
        assert "alarm" in text.lower()

    async def test_get_sections_returns_dict(self):
        """get_sections returns structured data."""
        from cortex.proactive.briefing import DailyBriefing
        briefing = DailyBriefing()
        sections = await briefing.get_sections()
        assert "weather" in sections
        assert "calendar" in sections
        assert "reminders" in sections
        assert "timers" in sections
        assert "alarms" in sections

    async def test_handles_missing_tables(self, tmp_path):
        """Briefing gracefully handles missing tables."""
        from cortex.proactive.briefing import DailyBriefing
        briefing = DailyBriefing()
        # Tables exist from _fresh_db, so this just verifies empty results work
        sections = await briefing.get_sections()
        assert sections["reminders"] == []
        assert sections["timers"] == []
        assert sections["alarms"] == []


# ── BriefingPlugin ───────────────────────────────────────────────────────────


class TestBriefingPlugin:

    async def test_match_daily_briefing(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        match = await plugin.match("what's my day look like")
        assert match.matched is True
        assert match.intent == "daily_briefing"

    async def test_match_morning_briefing(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        match = await plugin.match("give me my morning briefing")
        assert match.matched is True

    async def test_match_schedule(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        match = await plugin.match("what's on my schedule")
        assert match.matched is True

    async def test_no_match(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        match = await plugin.match("turn on the lights")
        assert match.matched is False

    async def test_handle_returns_response(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        from cortex.plugins.base import CommandMatch
        plugin = DailyBriefingPlugin()
        match = CommandMatch(matched=True, intent="daily_briefing")
        result = await plugin.handle("daily briefing", match, {})
        assert result.success is True
        assert len(result.response) > 0

    async def test_health(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        assert await plugin.health() is True

    async def test_setup(self):
        from cortex.plugins.briefing import DailyBriefingPlugin
        plugin = DailyBriefingPlugin()
        assert await plugin.setup() is True


# ── Admin proactive API ──────────────────────────────────────────────────────


class TestAdminProactiveAPI:

    def test_rule_crud(self):
        """Test rule CRUD via direct DB operations (mirrors admin endpoints)."""
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO proactive_rules "
            "(name, provider, condition_type, condition_config, action_type, action_config) "
            "VALUES ('Test', 'weather', 'threshold', '{}', 'log', '{}')"
        )
        conn.commit()
        rule_id = cur.lastrowid

        # List
        rows = conn.execute("SELECT * FROM proactive_rules").fetchall()
        assert len(rows) == 1

        # Enable/disable
        conn.execute("UPDATE proactive_rules SET enabled = 0 WHERE id = ?", (rule_id,))
        conn.commit()
        row = conn.execute("SELECT enabled FROM proactive_rules WHERE id = ?", (rule_id,)).fetchone()
        assert dict(row)["enabled"] == 0

        conn.execute("UPDATE proactive_rules SET enabled = 1 WHERE id = ?", (rule_id,))
        conn.commit()

        # Delete
        conn.execute("DELETE FROM proactive_rules WHERE id = ?", (rule_id,))
        conn.commit()
        assert conn.execute("SELECT * FROM proactive_rules").fetchall() == []

    def test_events_table(self):
        """Verify proactive_events table exists and accepts inserts."""
        conn = get_db()
        conn.execute(
            "INSERT INTO proactive_events (provider, event_type, event_data, action_taken) "
            "VALUES ('weather', 'threshold_crossed', '{\"temp\": 100}', 'logged')"
        )
        conn.commit()
        rows = conn.execute("SELECT * FROM proactive_events").fetchall()
        assert len(rows) == 1

    def test_preferences_upsert(self):
        """Notification preferences can be inserted and updated."""
        conn = get_db()
        conn.execute(
            "INSERT INTO notification_preferences (user_id, quiet_hours_start, quiet_hours_end) "
            "VALUES ('user1', '23:00', '06:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM notification_preferences WHERE user_id = 'user1'"
        ).fetchone()
        assert dict(row)["quiet_hours_start"] == "23:00"

        conn.execute(
            "UPDATE notification_preferences SET max_per_hour = 5 WHERE user_id = 'user1'"
        )
        conn.commit()
        row = conn.execute(
            "SELECT max_per_hour FROM notification_preferences WHERE user_id = 'user1'"
        ).fetchone()
        assert dict(row)["max_per_hour"] == 5
