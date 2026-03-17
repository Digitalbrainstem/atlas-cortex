"""Comprehensive tests for the scheduling module (alarms, timers, reminders)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from cortex.db import init_db, set_db_path, get_db
from cortex.scheduling.nlp_time import parse_time, ParsedTime
from cortex.scheduling.timers import TimerEngine
from cortex.scheduling.alarms import AlarmEngine, cron_matches, next_cron_time
from cortex.scheduling.reminders import ReminderEngine


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _test_db(tmp_path):
    """Provide an isolated in-memory-like DB for every test."""
    path = tmp_path / "test_scheduling.db"
    set_db_path(path)
    init_db(path)
    yield


# ── NLP Time Parser ──────────────────────────────────────────────

class TestParseDuration:
    def test_minutes(self):
        r = parse_time("5 minutes")
        assert r.duration_seconds == 300

    def test_seconds(self):
        r = parse_time("30 seconds")
        assert r.duration_seconds == 30

    def test_hours(self):
        r = parse_time("2 hours")
        assert r.duration_seconds == 7200

    def test_hour_and_half(self):
        r = parse_time("an hour and a half")
        assert r.duration_seconds == 5400

    def test_compound(self):
        r = parse_time("2 hours 15 minutes")
        assert r.duration_seconds == 2 * 3600 + 15 * 60

    def test_word_numbers(self):
        r = parse_time("five minutes")
        assert r.duration_seconds == 300

    def test_an_hour(self):
        r = parse_time("an hour")
        assert r.duration_seconds == 3600

    def test_half_hour(self):
        r = parse_time("half an hour")
        assert r.duration_seconds == 1800

    def test_days(self):
        r = parse_time("2 days")
        assert r.duration_seconds == 2 * 86400


class TestParseAbsolute:
    def test_7am(self):
        now = datetime(2025, 6, 15, 6, 0, 0)
        r = parse_time("7am", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 7
        assert r.absolute_time.minute == 0

    def test_330pm(self):
        now = datetime(2025, 6, 15, 12, 0, 0)
        r = parse_time("3:30 PM", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 15
        assert r.absolute_time.minute == 30

    def test_noon(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        r = parse_time("noon", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 12

    def test_midnight(self):
        now = datetime(2025, 6, 15, 20, 0, 0)
        r = parse_time("midnight", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 0
        # Should roll to next day
        assert r.absolute_time.day == 16

    def test_7_in_the_morning(self):
        now = datetime(2025, 6, 15, 3, 0, 0)
        r = parse_time("7 in the morning", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 7

    def test_rolls_to_tomorrow(self):
        now = datetime(2025, 6, 15, 22, 0, 0)
        r = parse_time("7am", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.day == 16


class TestParseRelative:
    def test_in_15_minutes(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        r = parse_time("in 15 minutes", now=now)
        assert r.absolute_time is not None
        expected = now + timedelta(minutes=15)
        assert abs((r.absolute_time - expected).total_seconds()) < 1

    def test_in_an_hour(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        r = parse_time("in an hour", now=now)
        assert r.absolute_time is not None
        expected = now + timedelta(hours=1)
        assert abs((r.absolute_time - expected).total_seconds()) < 1

    def test_in_2_days(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        r = parse_time("in 2 days", now=now)
        assert r.absolute_time is not None
        expected = now + timedelta(days=2)
        assert abs((r.absolute_time - expected).total_seconds()) < 1


class TestParseRecurrence:
    def test_every_weekday_at_7am(self):
        r = parse_time("every weekday at 7am")
        assert r.is_recurring
        assert r.cron_expression == "0 7 * * 1-5"

    def test_every_monday(self):
        r = parse_time("every Monday")
        assert r.is_recurring
        assert r.cron_expression is not None
        # Monday = dow 1
        assert "1" in r.cron_expression

    def test_daily_at_noon(self):
        r = parse_time("daily at noon")
        assert r.is_recurring
        assert r.cron_expression is not None

    def test_every_2_hours(self):
        r = parse_time("every 2 hours")
        assert r.is_recurring
        assert r.cron_expression == "0 */2 * * *"

    def test_every_30_minutes(self):
        r = parse_time("every 30 minutes")
        assert r.is_recurring
        assert r.cron_expression == "*/30 * * * *"


class TestParseNatural:
    def test_tomorrow_morning(self):
        now = datetime(2025, 6, 15, 22, 0, 0)
        r = parse_time("tomorrow morning", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.day == 16
        assert r.absolute_time.hour == 8

    def test_tonight_at_8(self):
        now = datetime(2025, 6, 15, 12, 0, 0)
        r = parse_time("tonight at 8", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 20

    def test_this_evening(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        r = parse_time("this evening", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.hour == 20

    def test_tomorrow_afternoon(self):
        now = datetime(2025, 6, 15, 22, 0, 0)
        r = parse_time("tomorrow afternoon", now=now)
        assert r.absolute_time is not None
        assert r.absolute_time.day == 16
        assert r.absolute_time.hour == 14


class TestEdgeCases:
    def test_empty_string(self):
        r = parse_time("")
        assert r.duration_seconds is None
        assert r.absolute_time is None
        assert r.cron_expression is None

    def test_garbage_input(self):
        r = parse_time("xyzzy qux blargh")
        assert r.duration_seconds is None
        assert r.absolute_time is None

    def test_raw_text_preserved(self):
        r = parse_time("5 minutes")
        assert r.raw_text == "5 minutes"


# ── Cron Parser ──────────────────────────────────────────────────

class TestCronMatches:
    def test_every_minute(self):
        dt = datetime(2025, 6, 15, 10, 30)
        assert cron_matches("* * * * *", dt) is True

    def test_specific_time(self):
        dt = datetime(2025, 6, 15, 7, 0)
        assert cron_matches("0 7 * * *", dt) is True
        assert cron_matches("0 8 * * *", dt) is False

    def test_dow_monday(self):
        # 2025-06-16 is a Monday
        dt = datetime(2025, 6, 16, 9, 0)
        assert cron_matches("0 9 * * 1", dt) is True
        assert cron_matches("0 9 * * 2", dt) is False

    def test_range(self):
        dt = datetime(2025, 6, 16, 9, 0)  # Monday
        assert cron_matches("0 9 * * 1-5", dt) is True

    def test_step(self):
        dt = datetime(2025, 6, 15, 10, 0)
        assert cron_matches("0 */2 * * *", dt) is True
        dt2 = datetime(2025, 6, 15, 11, 0)
        assert cron_matches("0 */2 * * *", dt2) is False

    def test_invalid_expression(self):
        assert cron_matches("bad cron", datetime.now()) is False

    def test_comma_list(self):
        dt = datetime(2025, 6, 15, 9, 0)
        assert cron_matches("0 9,12,15 * * *", dt) is True
        dt2 = datetime(2025, 6, 15, 10, 0)
        assert cron_matches("0 9,12,15 * * *", dt2) is False


class TestNextCronTime:
    def test_next_minute(self):
        now = datetime(2025, 6, 15, 10, 30, 0)
        nxt = next_cron_time("* * * * *", after=now)
        assert nxt is not None
        assert nxt == datetime(2025, 6, 15, 10, 31)

    def test_specific_hour(self):
        now = datetime(2025, 6, 15, 6, 0, 0)
        nxt = next_cron_time("0 7 * * *", after=now)
        assert nxt is not None
        assert nxt.hour == 7
        assert nxt.minute == 0


# ── Timer Engine ──────────────────────────────────────────────────

class TestTimerEngine:
    async def test_create_timer(self):
        engine = TimerEngine()
        tid = await engine.start_timer(60, label="eggs", user_id="u1")
        assert tid > 0
        t = await engine.get_timer(tid)
        assert t is not None
        assert t["label"] == "eggs"
        assert t["state"] == "running"
        await engine.cancel_timer(tid)

    async def test_cancel_timer(self):
        engine = TimerEngine()
        tid = await engine.start_timer(600, label="cancel-me")
        ok = await engine.cancel_timer(tid)
        assert ok is True
        t = await engine.get_timer(tid)
        assert t is not None
        assert t["state"] == "cancelled"

    async def test_pause_resume(self):
        engine = TimerEngine()
        tid = await engine.start_timer(600, label="pausable")
        await asyncio.sleep(0.05)
        ok = await engine.pause_timer(tid)
        assert ok is True
        t = await engine.get_timer(tid)
        assert t["state"] == "paused"

        ok = await engine.resume_timer(tid)
        assert ok is True
        t = await engine.get_timer(tid)
        assert t["state"] == "running"
        await engine.cancel_timer(tid)

    async def test_expiry_callback(self):
        engine = TimerEngine()
        fired: list[int] = []

        def on_done(timer_id, label, user_id, room):
            fired.append(timer_id)

        engine.on_expire(on_done)
        tid = await engine.start_timer(0, label="instant")
        # Wait briefly for the timer to fire
        await asyncio.sleep(0.15)
        assert tid in fired

    async def test_list_timers(self):
        engine = TimerEngine()
        t1 = await engine.start_timer(600, label="a", user_id="u1")
        t2 = await engine.start_timer(600, label="b", user_id="u2")
        all_t = await engine.list_timers()
        assert len(all_t) >= 2
        u1_t = await engine.list_timers(user_id="u1")
        assert len(u1_t) == 1
        assert u1_t[0]["label"] == "a"
        await engine.cancel_timer(t1)
        await engine.cancel_timer(t2)

    async def test_concurrent_timers(self):
        engine = TimerEngine()
        fired: list[int] = []
        engine.on_expire(lambda tid, *a: fired.append(tid))
        ids = []
        for i in range(5):
            tid = await engine.start_timer(0, label=f"t{i}")
            ids.append(tid)
        await asyncio.sleep(0.3)
        assert set(ids) == set(fired)

    async def test_cancel_nonexistent(self):
        engine = TimerEngine()
        ok = await engine.cancel_timer(99999)
        assert ok is False

    async def test_pause_nonexistent(self):
        engine = TimerEngine()
        ok = await engine.pause_timer(99999)
        assert ok is False

    async def test_restore_from_db(self):
        engine = TimerEngine()
        tid = await engine.start_timer(9999, label="persist")
        await engine.pause_timer(tid)

        engine2 = TimerEngine()
        await engine2.restore_from_db()
        t = await engine2.get_timer(tid)
        assert t is not None
        assert t["state"] == "paused"


# ── Alarm Engine ──────────────────────────────────────────────────

class TestAlarmEngine:
    async def test_create_alarm(self):
        engine = AlarmEngine()
        aid = await engine.create_alarm("0 7 * * *", label="wake up")
        assert aid > 0
        alarms = await engine.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["label"] == "wake up"

    async def test_delete_alarm(self):
        engine = AlarmEngine()
        aid = await engine.create_alarm("0 7 * * *")
        ok = await engine.delete_alarm(aid)
        assert ok is True
        alarms = await engine.list_alarms()
        assert len(alarms) == 0

    async def test_delete_nonexistent(self):
        engine = AlarmEngine()
        ok = await engine.delete_alarm(99999)
        assert ok is False

    async def test_enable_disable(self):
        engine = AlarmEngine()
        aid = await engine.create_alarm("0 7 * * *")
        ok = await engine.disable_alarm(aid)
        assert ok is True
        alarms = await engine.list_alarms()
        assert alarms[0]["enabled"] == 0

        ok = await engine.enable_alarm(aid)
        assert ok is True
        alarms = await engine.list_alarms()
        assert alarms[0]["enabled"] == 1

    async def test_snooze(self):
        engine = AlarmEngine()
        aid = await engine.create_alarm("0 7 * * *")
        ok = await engine.snooze_alarm(aid, minutes=10)
        assert ok is True
        alarm = (await engine.list_alarms())[0]
        nf = datetime.fromisoformat(alarm["next_fire"])
        # Should be ~10 minutes from now
        diff = (nf - datetime.now()).total_seconds()
        assert 500 < diff < 700

    async def test_snooze_nonexistent(self):
        engine = AlarmEngine()
        ok = await engine.snooze_alarm(99999)
        assert ok is False

    async def test_list_by_user(self):
        engine = AlarmEngine()
        await engine.create_alarm("0 7 * * *", user_id="u1")
        await engine.create_alarm("0 8 * * *", user_id="u2")
        u1 = await engine.list_alarms(user_id="u1")
        assert len(u1) == 1

    async def test_trigger_callback(self):
        engine = AlarmEngine()
        triggered: list[int] = []
        engine.on_trigger(lambda aid, *a: triggered.append(aid))

        # Create alarm that should be due (past time)
        conn = get_db()
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute(
            "INSERT INTO alarms (label, cron_expression, enabled, next_fire) "
            "VALUES (?, ?, 1, ?)",
            ("test", "* * * * *", past),
        )
        conn.commit()

        await engine._check_due()
        assert len(triggered) == 1

    async def test_start_stop(self):
        engine = AlarmEngine()
        await engine.start()
        assert engine._running is True
        await engine.stop()
        assert engine._running is False


# ── Reminder Engine ───────────────────────────────────────────────

class TestReminderEngine:
    async def test_create_time_reminder(self):
        engine = ReminderEngine()
        trigger = datetime.now() + timedelta(hours=1)
        rid = await engine.create_reminder("dentist", trigger_at=trigger)
        assert rid > 0
        rems = await engine.list_reminders()
        assert len(rems) == 1
        assert rems[0]["message"] == "dentist"
        assert rems[0]["trigger_type"] == "time"

    async def test_create_recurring_reminder(self):
        engine = ReminderEngine()
        rid = await engine.create_reminder(
            "take meds", cron_expression="0 9 * * *"
        )
        rems = await engine.list_reminders()
        assert rems[0]["trigger_type"] == "recurring"

    async def test_create_event_reminder(self):
        engine = ReminderEngine()
        rid = await engine.create_reminder(
            "grab umbrella", event_condition="arrive_home"
        )
        rems = await engine.list_reminders()
        assert rems[0]["trigger_type"] == "event"

    async def test_delete_reminder(self):
        engine = ReminderEngine()
        rid = await engine.create_reminder("test", trigger_at=datetime.now())
        ok = await engine.delete_reminder(rid)
        assert ok is True
        assert len(await engine.list_reminders(include_fired=True)) == 0

    async def test_delete_nonexistent(self):
        engine = ReminderEngine()
        ok = await engine.delete_reminder(99999)
        assert ok is False

    async def test_event_check(self):
        engine = ReminderEngine()
        triggered: list[int] = []
        engine.on_trigger(lambda rid, *a: triggered.append(rid))
        await engine.create_reminder(
            "grab umbrella", event_condition="arrive_home"
        )
        fired = await engine.check_event("arrive_home")
        assert len(fired) == 1
        assert fired[0]["message"] == "grab umbrella"
        assert len(triggered) == 1

    async def test_event_check_no_match(self):
        engine = ReminderEngine()
        await engine.create_reminder("x", event_condition="arrive_home")
        fired = await engine.check_event("leave_home")
        assert len(fired) == 0

    async def test_time_trigger(self):
        engine = ReminderEngine()
        triggered: list[int] = []
        engine.on_trigger(lambda rid, *a: triggered.append(rid))
        past = datetime.now() - timedelta(minutes=1)
        await engine.create_reminder("overdue", trigger_at=past)
        await engine._check_due()
        assert len(triggered) == 1

    async def test_list_excludes_fired(self):
        engine = ReminderEngine()
        past = datetime.now() - timedelta(minutes=1)
        await engine.create_reminder("fired-one", trigger_at=past)
        await engine._check_due()
        active = await engine.list_reminders()
        assert len(active) == 0
        all_r = await engine.list_reminders(include_fired=True)
        assert len(all_r) == 1

    async def test_list_by_user(self):
        engine = ReminderEngine()
        future = datetime.now() + timedelta(hours=1)
        await engine.create_reminder("a", trigger_at=future, user_id="u1")
        await engine.create_reminder("b", trigger_at=future, user_id="u2")
        u1 = await engine.list_reminders(user_id="u1")
        assert len(u1) == 1

    async def test_start_stop(self):
        engine = ReminderEngine()
        await engine.start()
        assert engine._running is True
        await engine.stop()
        assert engine._running is False

    async def test_recurring_not_marked_fired(self):
        engine = ReminderEngine()
        # Create a recurring reminder with a cron that matches right now
        now = datetime.now()
        cron = f"{now.minute} {now.hour} * * *"
        await engine.create_reminder("meds", cron_expression=cron)
        await engine._check_due()
        # Recurring reminder should NOT be marked as fired
        rems = await engine.list_reminders()
        assert len(rems) == 1
        assert rems[0]["fired"] == 0


# ── Integration: NLP → Engine ────────────────────────────────────

class TestIntegration:
    async def test_nlp_to_timer(self):
        """parse_time → TimerEngine: a duration triggers a timer."""
        parsed = parse_time("5 minutes")
        assert parsed.duration_seconds == 300
        engine = TimerEngine()
        tid = await engine.start_timer(parsed.duration_seconds, label="eggs")
        t = await engine.get_timer(tid)
        assert t["duration_seconds"] == 300
        await engine.cancel_timer(tid)

    async def test_nlp_to_alarm(self):
        """parse_time → AlarmEngine: a recurrence creates an alarm."""
        parsed = parse_time("every weekday at 7am")
        assert parsed.is_recurring
        assert parsed.cron_expression is not None
        engine = AlarmEngine()
        aid = await engine.create_alarm(parsed.cron_expression, label="wake")
        alarms = await engine.list_alarms()
        assert len(alarms) == 1

    async def test_nlp_to_reminder(self):
        """parse_time → ReminderEngine: absolute time → reminder."""
        now = datetime(2025, 6, 15, 10, 0, 0)
        parsed = parse_time("3:30 PM", now=now)
        assert parsed.absolute_time is not None
        engine = ReminderEngine()
        rid = await engine.create_reminder(
            "meeting", trigger_at=parsed.absolute_time
        )
        rems = await engine.list_reminders()
        assert len(rems) == 1


# ── Wave 2: Plugin Tests ─────────────────────────────────────────

class TestSchedulingPluginMatch:
    """Test match() detects the correct intents."""

    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.timers import SchedulingPlugin
        p = SchedulingPlugin()
        await p.setup({})
        yield p
        # Cleanup background tasks
        if p.alarm_engine:
            await p.alarm_engine.stop()
        if p.reminder_engine:
            await p.reminder_engine.stop()

    async def test_set_timer(self, plugin):
        m = await plugin.match("set a timer for 5 minutes", {})
        assert m.matched
        assert m.intent == "set_timer"

    async def test_timer_for(self, plugin):
        m = await plugin.match("timer for 10 minutes", {})
        assert m.matched
        assert m.intent == "set_timer"

    async def test_cancel_timer(self, plugin):
        m = await plugin.match("cancel the timer", {})
        assert m.matched
        assert m.intent == "cancel_timer"

    async def test_pause_timer(self, plugin):
        m = await plugin.match("pause the timer", {})
        assert m.matched
        assert m.intent == "pause_timer"

    async def test_resume_timer(self, plugin):
        m = await plugin.match("resume my timer", {})
        assert m.matched
        assert m.intent == "resume_timer"

    async def test_list_timers(self, plugin):
        m = await plugin.match("list my timers", {})
        assert m.matched
        assert m.intent == "list_timers"

    async def test_how_much_time(self, plugin):
        m = await plugin.match("how much time is left", {})
        assert m.matched
        assert m.intent == "list_timers"

    async def test_set_alarm(self, plugin):
        m = await plugin.match("set an alarm for 7am", {})
        assert m.matched
        assert m.intent == "set_alarm"

    async def test_wake_me_up(self, plugin):
        m = await plugin.match("wake me up at 6:30", {})
        assert m.matched
        assert m.intent == "set_alarm"

    async def test_cancel_alarm(self, plugin):
        m = await plugin.match("cancel my alarm", {})
        assert m.matched
        assert m.intent == "cancel_alarm"

    async def test_snooze(self, plugin):
        m = await plugin.match("snooze", {})
        assert m.matched
        assert m.intent == "snooze_alarm"

    async def test_list_alarms(self, plugin):
        m = await plugin.match("list alarms", {})
        assert m.matched
        assert m.intent == "list_alarms"

    async def test_remind_me(self, plugin):
        m = await plugin.match("remind me to take meds at 9am", {})
        assert m.matched
        assert m.intent == "set_reminder"

    async def test_set_reminder(self, plugin):
        m = await plugin.match("set a reminder to call the dentist", {})
        assert m.matched
        assert m.intent == "set_reminder"

    async def test_cancel_reminder(self, plugin):
        m = await plugin.match("cancel reminder", {})
        assert m.matched
        assert m.intent == "cancel_reminder"

    async def test_list_reminders(self, plugin):
        m = await plugin.match("what are my reminders", {})
        assert m.matched
        assert m.intent == "list_reminders"

    async def test_no_match(self, plugin):
        m = await plugin.match("what's the weather like", {})
        assert not m.matched

    async def test_no_match_random(self, plugin):
        m = await plugin.match("tell me a joke", {})
        assert not m.matched


class TestSchedulingPluginHandle:
    """Test handle() for set/cancel/list operations."""

    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.timers import SchedulingPlugin
        p = SchedulingPlugin()
        await p.setup({})
        yield p
        if p.alarm_engine:
            await p.alarm_engine.stop()
        if p.reminder_engine:
            await p.reminder_engine.stop()

    async def test_set_timer(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="set_timer")
        result = await plugin.handle("set a timer for 5 minutes", match, {"user_id": "u1", "room": "kitchen"})
        assert result.success
        assert "5 minutes" in result.response
        assert result.metadata.get("timer_id") is not None

    async def test_set_timer_bad_time(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="set_timer")
        result = await plugin.handle("set a timer for blah", match, {})
        assert not result.success

    async def test_list_timers_empty(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="list_timers")
        result = await plugin.handle("list timers", match, {"user_id": "u1"})
        assert result.success
        assert "no active timers" in result.response.lower()

    async def test_cancel_timer_none(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="cancel_timer")
        result = await plugin.handle("cancel timer", match, {"user_id": "u1"})
        assert result.success
        assert "don't have" in result.response.lower()

    async def test_set_and_cancel_timer(self, plugin):
        from cortex.plugins.base import CommandMatch
        # Set
        m = CommandMatch(matched=True, intent="set_timer")
        r = await plugin.handle("set a timer for 10 minutes", m, {"user_id": "u1"})
        assert r.success
        # Cancel
        m2 = CommandMatch(matched=True, intent="cancel_timer")
        r2 = await plugin.handle("cancel timer", m2, {"user_id": "u1"})
        assert r2.success
        assert "cancelled" in r2.response.lower()

    async def test_set_and_list_timer(self, plugin):
        from cortex.plugins.base import CommandMatch
        m = CommandMatch(matched=True, intent="set_timer")
        await plugin.handle("set a timer for 3 minutes", m, {"user_id": "u1"})
        m2 = CommandMatch(matched=True, intent="list_timers")
        r = await plugin.handle("list timers", m2, {"user_id": "u1"})
        assert r.success
        assert "remaining" in r.response.lower()

    async def test_pause_resume_timer(self, plugin):
        from cortex.plugins.base import CommandMatch
        # Set a timer
        m = CommandMatch(matched=True, intent="set_timer")
        await plugin.handle("set a timer for 10 minutes", m, {"user_id": "u1"})
        await asyncio.sleep(0.05)
        # Pause
        m2 = CommandMatch(matched=True, intent="pause_timer")
        r = await plugin.handle("pause timer", m2, {"user_id": "u1"})
        assert r.success
        assert "paused" in r.response.lower()
        # Resume
        m3 = CommandMatch(matched=True, intent="resume_timer")
        r2 = await plugin.handle("resume timer", m3, {"user_id": "u1"})
        assert r2.success
        assert "resumed" in r2.response.lower()
        # Cleanup
        m4 = CommandMatch(matched=True, intent="cancel_timer")
        await plugin.handle("cancel timer", m4, {"user_id": "u1"})

    async def test_set_alarm(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="set_alarm")
        result = await plugin.handle("set an alarm for 7am", match, {"user_id": "u1"})
        assert result.success
        assert "alarm set" in result.response.lower()

    async def test_list_alarms_empty(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="list_alarms")
        result = await plugin.handle("list alarms", match, {"user_id": "u1"})
        assert result.success
        assert "no alarms" in result.response.lower()

    async def test_set_and_cancel_alarm(self, plugin):
        from cortex.plugins.base import CommandMatch
        m = CommandMatch(matched=True, intent="set_alarm")
        await plugin.handle("set an alarm for 7am", m, {"user_id": "u1"})
        m2 = CommandMatch(matched=True, intent="cancel_alarm")
        r = await plugin.handle("cancel alarm", m2, {"user_id": "u1"})
        assert r.success
        assert "cancelled" in r.response.lower()

    async def test_snooze_alarm(self, plugin):
        from cortex.plugins.base import CommandMatch
        # Create an alarm first
        m = CommandMatch(matched=True, intent="set_alarm")
        await plugin.handle("set an alarm for 7am", m, {"user_id": "u1"})
        m2 = CommandMatch(matched=True, intent="snooze_alarm")
        r = await plugin.handle("snooze for 10 minutes", m2, {"user_id": "u1"})
        assert r.success
        assert "snoozed" in r.response.lower()

    async def test_set_reminder(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="set_reminder")
        result = await plugin.handle("remind me to take meds at 9am", match, {"user_id": "u1"})
        assert result.success
        assert "remind" in result.response.lower()

    async def test_list_reminders_empty(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="list_reminders")
        result = await plugin.handle("what are my reminders", match, {"user_id": "u1"})
        assert result.success
        assert "no active reminders" in result.response.lower()

    async def test_set_and_cancel_reminder(self, plugin):
        from cortex.plugins.base import CommandMatch
        m = CommandMatch(matched=True, intent="set_reminder")
        await plugin.handle("remind me to take meds in 30 minutes", m, {"user_id": "u1"})
        m2 = CommandMatch(matched=True, intent="cancel_reminder")
        r = await plugin.handle("cancel reminder", m2, {"user_id": "u1"})
        assert r.success
        assert "cancelled" in r.response.lower()

    async def test_set_reminder_with_duration(self, plugin):
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="set_reminder")
        result = await plugin.handle("remind me to check the oven in 15 minutes", match, {"user_id": "u1"})
        assert result.success
        # Response should mention "remind" and the action
        assert "remind" in result.response.lower() or "oven" in result.response.lower()

    async def test_health(self, plugin):
        assert await plugin.health() is True


# ── Wave 2: Notification Routing Tests ───────────────────────────

class TestNotificationRouting:
    """Test notification routing through channels."""

    async def test_satellite_channel_no_satellites(self):
        """SatelliteChannel returns False when no satellites are connected."""
        from cortex.notifications.satellite import SatelliteChannel
        from cortex.notifications.channels import Notification
        channel = SatelliteChannel()
        notif = Notification(
            level="info",
            title="Timer done",
            message="Your timer finished!",
            source="scheduling.timer",
            metadata={"room": "kitchen", "user_id": "u1"},
        )
        result = await channel.send(notif)
        assert result is False

    async def test_notify_timer_expired(self):
        """notify_timer_expired sends a notification."""
        from cortex.notifications.satellite import notify_timer_expired
        # Should not raise; will log to LogChannel
        await notify_timer_expired("eggs", room="kitchen", user_id="u1")

    async def test_notify_alarm_triggered(self):
        from cortex.notifications.satellite import notify_alarm_triggered
        await notify_alarm_triggered("morning", tts_message="Good morning!", room="bedroom")

    async def test_notify_reminder_fired(self):
        from cortex.notifications.satellite import notify_reminder_fired
        await notify_reminder_fired("take meds", room="living_room", user_id="u1")

    async def test_wire_scheduling_callbacks(self):
        """Wiring connects callbacks to engines."""
        from cortex.notifications.satellite import wire_scheduling_callbacks
        timer_eng = TimerEngine()
        alarm_eng = AlarmEngine()
        reminder_eng = ReminderEngine()
        wire_scheduling_callbacks(timer_eng, alarm_eng, reminder_eng)
        assert len(timer_eng._callbacks) == 1
        assert len(alarm_eng._callbacks) == 1
        assert len(reminder_eng._callbacks) == 1

    async def test_timer_expire_triggers_notification(self):
        """When a timer expires, the wired callback fires."""
        from cortex.notifications.satellite import wire_scheduling_callbacks
        timer_eng = TimerEngine()
        wire_scheduling_callbacks(timer_eng, AlarmEngine(), ReminderEngine())
        # Fire a 0-second timer
        tid = await timer_eng.start_timer(0, label="test-notify", user_id="u1", room="office")
        await asyncio.sleep(0.2)
        # Timer should be finished (callback ran without error)
        t = await timer_eng.get_timer(tid)
        assert t["state"] == "finished"


# ── Wave 2: Admin API Tests ──────────────────────────────────────

class TestAdminSchedulingAPI:
    """Test admin scheduling endpoints via FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from cortex.admin.scheduling import router
        from cortex.admin.helpers import require_admin

        app = FastAPI()
        app.include_router(router)
        # Bypass admin auth for testing
        app.dependency_overrides[require_admin] = lambda: {"user": "test"}
        return TestClient(app)

    def test_list_alarms(self, client):
        resp = client.get("/scheduling/alarms")
        assert resp.status_code == 200
        assert "alarms" in resp.json()

    def test_create_and_delete_alarm(self, client):
        resp = client.post("/scheduling/alarms", json={
            "label": "test alarm",
            "cron_expression": "0 7 * * *",
        })
        assert resp.status_code == 200
        alarm_id = resp.json()["id"]
        assert alarm_id is not None

        # Verify it appears in list
        resp = client.get("/scheduling/alarms")
        assert any(a["id"] == alarm_id for a in resp.json()["alarms"])

        # Delete
        resp = client.delete(f"/scheduling/alarms/{alarm_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_alarm(self, client):
        resp = client.delete("/scheduling/alarms/99999")
        assert resp.status_code == 404

    def test_enable_disable_alarm(self, client):
        resp = client.post("/scheduling/alarms", json={
            "label": "toggle",
            "cron_expression": "0 8 * * *",
        })
        alarm_id = resp.json()["id"]

        # Disable
        resp = client.post(f"/scheduling/alarms/{alarm_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # Enable
        resp = client.post(f"/scheduling/alarms/{alarm_id}/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_list_timers(self, client):
        resp = client.get("/scheduling/timers")
        assert resp.status_code == 200
        assert "timers" in resp.json()

    def test_cancel_nonexistent_timer(self, client):
        resp = client.delete("/scheduling/timers/99999")
        assert resp.status_code == 404

    def test_cancel_timer(self, client):
        # Manually insert a timer
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO timers (label, duration_seconds, remaining_seconds, state) "
            "VALUES ('test', 600, 600, 'running')"
        )
        conn.commit()
        timer_id = cur.lastrowid

        resp = client.delete(f"/scheduling/timers/{timer_id}")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

    def test_list_reminders(self, client):
        resp = client.get("/scheduling/reminders")
        assert resp.status_code == 200
        assert "reminders" in resp.json()

    def test_delete_nonexistent_reminder(self, client):
        resp = client.delete("/scheduling/reminders/99999")
        assert resp.status_code == 404

    def test_create_and_delete_reminder(self, client):
        # Manually insert a reminder
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO reminders (message, trigger_type) VALUES ('test reminder', 'time')"
        )
        conn.commit()
        reminder_id = cur.lastrowid

        resp = client.delete(f"/scheduling/reminders/{reminder_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
