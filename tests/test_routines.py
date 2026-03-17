"""Comprehensive tests for the Routines & Automations engine."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.routines.actions import (
    ActionResult,
    ConditionAction,
    DelayAction,
    HAServiceAction,
    SetVariableAction,
    TTSAnnounceAction,
    get_executor,
)
from cortex.routines.engine import RoutineEngine
from cortex.routines.templates import TEMPLATES, instantiate_template


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def db_conn():
    """Create an isolated in-memory database for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = get_db()
    yield conn


@pytest.fixture()
def engine(db_conn):
    """Return a RoutineEngine backed by the test database."""
    return RoutineEngine()


# ── Routine CRUD ─────────────────────────────────────────────────

class TestRoutineCRUD:
    async def test_create_routine(self, engine):
        rid = await engine.create_routine("Morning", description="Wake up routine")
        assert isinstance(rid, int)
        assert rid > 0

    async def test_list_routines_empty(self, engine):
        routines = await engine.list_routines()
        assert routines == []

    async def test_list_routines(self, engine):
        await engine.create_routine("Alpha")
        await engine.create_routine("Beta")
        routines = await engine.list_routines()
        assert len(routines) == 2
        names = [r["name"] for r in routines]
        assert "Alpha" in names
        assert "Beta" in names

    async def test_list_routines_by_user(self, engine):
        await engine.create_routine("User1 routine", user_id="u1")
        await engine.create_routine("User2 routine", user_id="u2")
        routines = await engine.list_routines(user_id="u1")
        assert len(routines) == 1
        assert routines[0]["name"] == "User1 routine"

    async def test_get_routine(self, engine):
        rid = await engine.create_routine("Test", description="desc")
        routine = await engine.get_routine(rid)
        assert routine is not None
        assert routine["name"] == "Test"
        assert routine["description"] == "desc"
        assert routine["enabled"] == 1
        assert "steps" in routine
        assert "triggers" in routine

    async def test_get_routine_not_found(self, engine):
        assert await engine.get_routine(9999) is None

    async def test_delete_routine(self, engine):
        rid = await engine.create_routine("ToDelete")
        assert await engine.delete_routine(rid) is True
        assert await engine.get_routine(rid) is None

    async def test_delete_nonexistent(self, engine):
        assert await engine.delete_routine(9999) is False

    async def test_enable_disable(self, engine):
        rid = await engine.create_routine("Toggle")
        assert await engine.disable_routine(rid) is True
        r = await engine.get_routine(rid)
        assert r["enabled"] == 0

        assert await engine.enable_routine(rid) is True
        r = await engine.get_routine(rid)
        assert r["enabled"] == 1


# ── Step Management ──────────────────────────────────────────────

class TestStepManagement:
    async def test_add_step(self, engine):
        rid = await engine.create_routine("WithSteps")
        sid = await engine.add_step(rid, "tts_announce", {"message": "Hello"})
        assert isinstance(sid, int)

        routine = await engine.get_routine(rid)
        assert len(routine["steps"]) == 1
        step = routine["steps"][0]
        assert step["action_type"] == "tts_announce"
        assert json.loads(step["action_config"]) == {"message": "Hello"}

    async def test_add_multiple_steps_auto_order(self, engine):
        rid = await engine.create_routine("Multi")
        await engine.add_step(rid, "tts_announce", {"message": "First"})
        await engine.add_step(rid, "delay", {"seconds": 1})
        await engine.add_step(rid, "tts_announce", {"message": "Second"})

        routine = await engine.get_routine(rid)
        assert len(routine["steps"]) == 3
        orders = [s["step_order"] for s in routine["steps"]]
        assert orders == [1, 2, 3]

    async def test_add_step_explicit_order(self, engine):
        rid = await engine.create_routine("Explicit")
        await engine.add_step(rid, "tts_announce", {"message": "A"}, step_order=10)
        await engine.add_step(rid, "tts_announce", {"message": "B"}, step_order=5)

        routine = await engine.get_routine(rid)
        assert routine["steps"][0]["step_order"] == 5
        assert routine["steps"][1]["step_order"] == 10

    async def test_remove_step(self, engine):
        rid = await engine.create_routine("Removal")
        sid = await engine.add_step(rid, "delay", {"seconds": 1})
        assert await engine.remove_step(sid) is True
        routine = await engine.get_routine(rid)
        assert len(routine["steps"]) == 0

    async def test_remove_nonexistent_step(self, engine):
        assert await engine.remove_step(9999) is False

    async def test_reorder_steps(self, engine):
        rid = await engine.create_routine("Reorder")
        s1 = await engine.add_step(rid, "tts_announce", {"message": "A"})
        s2 = await engine.add_step(rid, "delay", {"seconds": 1})
        s3 = await engine.add_step(rid, "tts_announce", {"message": "B"})

        await engine.reorder_steps(rid, [s3, s1, s2])

        routine = await engine.get_routine(rid)
        ids_in_order = [s["id"] for s in routine["steps"]]
        assert ids_in_order == [s3, s1, s2]

    async def test_step_with_condition_and_on_error(self, engine):
        rid = await engine.create_routine("Conditional")
        sid = await engine.add_step(
            rid, "tts_announce", {"message": "Hi"},
            condition="time_check", on_error="stop",
        )
        routine = await engine.get_routine(rid)
        step = routine["steps"][0]
        assert step["condition"] == "time_check"
        assert step["on_error"] == "stop"


# ── Trigger Management ───────────────────────────────────────────

class TestTriggerManagement:
    async def test_add_voice_trigger(self, engine):
        rid = await engine.create_routine("VoiceTrig")
        tid = await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        assert isinstance(tid, int)

        routine = await engine.get_routine(rid)
        assert len(routine["triggers"]) == 1
        t = routine["triggers"][0]
        assert t["trigger_type"] == "voice_phrase"
        assert json.loads(t["trigger_config"]) == {"phrase": "good morning"}

    async def test_add_schedule_trigger(self, engine):
        rid = await engine.create_routine("SchedTrig")
        tid = await engine.add_trigger(rid, "schedule", {"cron": "0 7 * * *"})
        routine = await engine.get_routine(rid)
        assert len(routine["triggers"]) == 1
        assert routine["triggers"][0]["trigger_type"] == "schedule"

    async def test_add_ha_event_trigger(self, engine):
        rid = await engine.create_routine("HATrig")
        tid = await engine.add_trigger(
            rid, "ha_event",
            {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        routine = await engine.get_routine(rid)
        assert len(routine["triggers"]) == 1

    async def test_remove_trigger(self, engine):
        rid = await engine.create_routine("RemTrig")
        tid = await engine.add_trigger(rid, "voice_phrase", {"phrase": "test"})
        assert await engine.remove_trigger(tid) is True
        routine = await engine.get_routine(rid)
        assert len(routine["triggers"]) == 0

    async def test_remove_nonexistent_trigger(self, engine):
        assert await engine.remove_trigger(9999) is False

    async def test_cascade_delete_removes_triggers(self, engine):
        rid = await engine.create_routine("Cascade")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "delete me"})
        await engine.delete_routine(rid)
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM routine_triggers WHERE routine_id = ?", (rid,)
        ).fetchall()
        assert len(rows) == 0


# ── Voice Trigger Matching ───────────────────────────────────────

class TestVoiceTriggerMatching:
    async def test_exact_match(self, engine):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        assert await engine.match_voice_trigger("good morning") == rid

    async def test_case_insensitive(self, engine):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "Good Morning"})
        assert await engine.match_voice_trigger("good morning") == rid

    async def test_fuzzy_match(self, engine):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        # Close enough for fuzzy matching
        assert await engine.match_voice_trigger("good mornings") == rid

    async def test_no_match(self, engine):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        assert await engine.match_voice_trigger("completely different") is None

    async def test_disabled_routine_not_matched(self, engine):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        await engine.disable_routine(rid)
        assert await engine.match_voice_trigger("good morning") is None

    async def test_multiple_triggers_best_match(self, engine):
        r1 = await engine.create_routine("Morning")
        await engine.add_trigger(r1, "voice_phrase", {"phrase": "good morning"})
        r2 = await engine.create_routine("Night")
        await engine.add_trigger(r2, "voice_phrase", {"phrase": "good night"})
        assert await engine.match_voice_trigger("good morning") == r1
        assert await engine.match_voice_trigger("good night") == r2


# ── HA Event Trigger Matching ────────────────────────────────────

class TestHAEventMatching:
    async def test_match_entity_and_state(self, engine):
        rid = await engine.create_routine("DoorOpen")
        await engine.add_trigger(
            rid, "ha_event",
            {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        result = await engine.match_ha_event("binary_sensor.door", "on", "off")
        assert result == [rid]

    async def test_no_match_wrong_entity(self, engine):
        rid = await engine.create_routine("DoorOpen")
        await engine.add_trigger(
            rid, "ha_event",
            {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        result = await engine.match_ha_event("binary_sensor.window", "on", "off")
        assert result == []

    async def test_no_match_wrong_state(self, engine):
        rid = await engine.create_routine("DoorOpen")
        await engine.add_trigger(
            rid, "ha_event",
            {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        result = await engine.match_ha_event("binary_sensor.door", "off", "on")
        assert result == []

    async def test_match_with_from_state(self, engine):
        rid = await engine.create_routine("DoorClose")
        await engine.add_trigger(
            rid, "ha_event",
            {"entity_id": "binary_sensor.door", "to_state": "off", "from_state": "on"},
        )
        result = await engine.match_ha_event("binary_sensor.door", "off", "on")
        assert result == [rid]
        # Wrong from_state
        result = await engine.match_ha_event("binary_sensor.door", "off", "unavailable")
        assert result == []

    async def test_multiple_routines_match(self, engine):
        r1 = await engine.create_routine("Log Door")
        await engine.add_trigger(
            r1, "ha_event", {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        r2 = await engine.create_routine("Alert Door")
        await engine.add_trigger(
            r2, "ha_event", {"entity_id": "binary_sensor.door", "to_state": "on"},
        )
        result = await engine.match_ha_event("binary_sensor.door", "on", "off")
        assert set(result) == {r1, r2}


# ── Action Executors ─────────────────────────────────────────────

class TestActionExecutors:
    async def test_tts_announce_success(self):
        action = TTSAnnounceAction()
        with patch("cortex.avatar.broadcast.stream_tts_to_avatar", new_callable=AsyncMock) as mock_tts:
            result = await action.execute({"message": "Hello!", "room": "bedroom"}, {})
        assert result.success is True
        assert "Hello!" in result.message
        mock_tts.assert_awaited_once_with("bedroom", "Hello!")

    async def test_tts_announce_no_message(self):
        action = TTSAnnounceAction()
        result = await action.execute({}, {})
        assert result.success is False

    async def test_tts_announce_import_failure(self):
        action = TTSAnnounceAction()
        with patch.dict("sys.modules", {"cortex.avatar.broadcast": None}):
            # Even on import failure, it should degrade gracefully
            result = await action.execute({"message": "Hello!"}, {})
            assert result.success is True  # Best-effort: reports what it would say

    async def test_delay_action(self):
        action = DelayAction()
        result = await action.execute({"seconds": 0.01}, {})
        assert result.success is True

    async def test_delay_zero(self):
        action = DelayAction()
        result = await action.execute({"seconds": 0}, {})
        assert result.success is True
        assert "No delay" in result.message

    async def test_set_variable(self):
        action = SetVariableAction()
        result = await action.execute({"name": "greeting", "value": "hello"}, {})
        assert result.success is True
        assert result.variables == {"greeting": "hello"}

    async def test_set_variable_no_name(self):
        action = SetVariableAction()
        result = await action.execute({"value": "hello"}, {})
        assert result.success is False

    async def test_condition_time_between_in_range(self):
        action = ConditionAction()
        result = await action.execute(
            {"type": "time_between", "start": "00:00", "end": "23:59"}, {}
        )
        assert result.success is True

    async def test_condition_time_between_out_of_range(self):
        action = ConditionAction()
        # A window that's impossible to match (same time)
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        # Pick a window that definitely doesn't include now
        hour = int(now.split(":")[0])
        start = f"{(hour + 2) % 24:02d}:00"
        end = f"{(hour + 3) % 24:02d}:00"
        result = await action.execute(
            {"type": "time_between", "start": start, "end": end}, {}
        )
        assert result.success is False

    async def test_condition_unknown_type(self):
        action = ConditionAction()
        result = await action.execute({"type": "unknown"}, {})
        assert result.success is False

    async def test_ha_service_missing_domain(self):
        action = HAServiceAction()
        result = await action.execute({"service": "turn_on"}, {})
        assert result.success is False

    async def test_ha_service_no_env(self):
        action = HAServiceAction()
        with patch.dict("os.environ", {}, clear=True):
            result = await action.execute(
                {"domain": "light", "service": "turn_on", "entity_id": "light.x"},
                {},
            )
        assert result.success is False
        assert "not configured" in result.message

    async def test_get_executor_known_types(self):
        for action_type in ("tts_announce", "ha_service", "delay", "condition", "set_variable"):
            executor = get_executor(action_type)
            assert executor is not None

    async def test_get_executor_unknown(self):
        assert get_executor("nonexistent") is None


# ── Routine Execution ────────────────────────────────────────────

class TestRoutineExecution:
    async def test_run_simple_routine(self, engine):
        rid = await engine.create_routine("Simple")
        await engine.add_step(rid, "set_variable", {"name": "x", "value": "1"})
        await engine.add_step(rid, "delay", {"seconds": 0.01})

        run_id = await engine.run_routine(rid)
        assert isinstance(run_id, int)

        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"
        assert run["steps_completed"] == 2

    async def test_run_updates_routine_stats(self, engine):
        rid = await engine.create_routine("Stats")
        await engine.add_step(rid, "delay", {"seconds": 0.01})
        await engine.run_routine(rid)

        routine = await engine.get_routine(rid)
        assert routine["run_count"] == 1
        assert routine["last_run"] is not None

    async def test_run_disabled_routine_raises(self, engine):
        rid = await engine.create_routine("Disabled")
        await engine.disable_routine(rid)
        with pytest.raises(ValueError, match="disabled"):
            await engine.run_routine(rid)

    async def test_run_nonexistent_routine_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            await engine.run_routine(9999)

    async def test_run_with_error_continue(self, engine):
        rid = await engine.create_routine("ErrContinue")
        # Unknown action type — will be skipped with on_error=continue (default)
        await engine.add_step(rid, "nonexistent_action", {})
        await engine.add_step(rid, "delay", {"seconds": 0.01})

        run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"
        assert run["steps_completed"] == 1  # only delay succeeded

    async def test_run_with_error_stop(self, engine):
        rid = await engine.create_routine("ErrStop")
        await engine.add_step(rid, "nonexistent_action", {}, on_error="stop")
        await engine.add_step(rid, "delay", {"seconds": 0.01})

        run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "failed"

    async def test_run_condition_stops_on_false(self, engine):
        """A failing condition with on_error=continue skips remaining steps."""
        rid = await engine.create_routine("CondStop")
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        hour = int(now.split(":")[0])
        start = f"{(hour + 2) % 24:02d}:00"
        end = f"{(hour + 3) % 24:02d}:00"

        await engine.add_step(rid, "condition", {"type": "time_between", "start": start, "end": end})
        await engine.add_step(rid, "delay", {"seconds": 0.01})

        run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"
        assert run["steps_completed"] == 0  # condition failed, delay skipped

    async def test_run_variable_substitution(self, engine):
        rid = await engine.create_routine("VarSub")
        await engine.add_step(rid, "set_variable", {"name": "who", "value": "World"})
        await engine.add_step(rid, "set_variable", {"name": "msg", "value": "Hello {{who}}"})

        run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"
        assert run["steps_completed"] == 2

    async def test_run_empty_routine(self, engine):
        rid = await engine.create_routine("Empty")
        run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"
        assert run["steps_completed"] == 0

    async def test_cancel_run(self, engine):
        rid = await engine.create_routine("Cancel")
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO routine_runs (routine_id, status) VALUES (?, 'running')",
            (rid,),
        )
        conn.commit()
        run_id = cur.lastrowid
        assert await engine.cancel_run(run_id) is True

        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "cancelled"

    async def test_cancel_already_completed(self, engine):
        rid = await engine.create_routine("Done")
        await engine.add_step(rid, "delay", {"seconds": 0.01})
        run_id = await engine.run_routine(rid)
        # Already completed, can't cancel
        assert await engine.cancel_run(run_id) is False


# ── Template Instantiation ───────────────────────────────────────

class TestTemplates:
    async def test_all_templates_exist(self):
        expected = {"good_morning", "good_night", "movie_time", "dinner_time", "leaving_home", "arriving_home"}
        assert set(TEMPLATES.keys()) == expected

    async def test_instantiate_good_morning(self, engine):
        rid = await instantiate_template(engine, "good_morning", user_id="testuser")
        routine = await engine.get_routine(rid)
        assert routine is not None
        assert routine["name"] == "Good Morning"
        assert routine["template_id"] == "good_morning"
        assert routine["user_id"] == "testuser"
        assert len(routine["steps"]) == 4
        assert len(routine["triggers"]) == 1
        assert routine["triggers"][0]["trigger_type"] == "voice_phrase"

    async def test_instantiate_all_templates(self, engine):
        for template_id in TEMPLATES:
            rid = await instantiate_template(engine, template_id)
            routine = await engine.get_routine(rid)
            assert routine is not None
            assert len(routine["steps"]) > 0

    async def test_instantiate_unknown_template(self, engine):
        with pytest.raises(KeyError):
            await instantiate_template(engine, "nonexistent")

    async def test_template_voice_trigger_matches(self, engine):
        await instantiate_template(engine, "good_morning")
        result = await engine.match_voice_trigger("good morning")
        assert result is not None

    async def test_instantiate_and_run(self, engine):
        """Templates can be instantiated and run without errors (mock TTS/HA)."""
        rid = await instantiate_template(engine, "good_morning")
        with patch("cortex.avatar.broadcast.stream_tts_to_avatar", new_callable=AsyncMock):
            with patch.dict("os.environ", {"HA_URL": "", "HA_TOKEN": ""}):
                run_id = await engine.run_routine(rid)
        conn = get_db()
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        # Some steps may fail (HA not configured) but run should complete
        assert run["status"] in ("completed", "failed")


# ── Edge Cases ───────────────────────────────────────────────────

class TestEdgeCases:
    async def test_cascade_delete_cleans_all(self, engine):
        """Deleting a routine removes steps, triggers, and runs."""
        rid = await engine.create_routine("CascadeFull")
        await engine.add_step(rid, "delay", {"seconds": 0.01})
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "test"})
        await engine.run_routine(rid)

        await engine.delete_routine(rid)
        conn = get_db()
        for table in ("routine_steps", "routine_triggers", "routine_runs"):
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE routine_id = ?", (rid,)
            ).fetchall()
            assert len(rows) == 0, f"{table} should be empty after cascade delete"

    async def test_invalid_action_config_json(self, engine):
        """Steps with invalid JSON in action_config don't crash execution."""
        rid = await engine.create_routine("BadJSON")
        conn = get_db()
        conn.execute(
            "INSERT INTO routine_steps (routine_id, step_order, action_type, action_config) "
            "VALUES (?, 1, 'delay', 'not-json')",
            (rid,),
        )
        conn.commit()
        # Should handle gracefully (empty config)
        run_id = await engine.run_routine(rid)
        run = conn.execute("SELECT * FROM routine_runs WHERE id = ?", (run_id,)).fetchone()
        assert run["status"] == "completed"

    async def test_run_multiple_times(self, engine):
        rid = await engine.create_routine("Multi")
        await engine.add_step(rid, "delay", {"seconds": 0.01})
        await engine.run_routine(rid)
        await engine.run_routine(rid)
        await engine.run_routine(rid)

        routine = await engine.get_routine(rid)
        assert routine["run_count"] == 3

    async def test_variable_substitution_in_engine(self):
        result = RoutineEngine._substitute_variables(
            {"message": "Hello {{name}}, it is {{time}}"},
            {"name": "Alice", "time": "morning"},
        )
        assert result["message"] == "Hello Alice, it is morning"

    async def test_variable_substitution_no_vars(self):
        result = RoutineEngine._substitute_variables(
            {"message": "No vars here"}, {}
        )
        assert result["message"] == "No vars here"

    async def test_variable_substitution_missing_var(self):
        result = RoutineEngine._substitute_variables(
            {"message": "Hello {{name}}"}, {}
        )
        assert result["message"] == "Hello {{name}}"


# ══════════════════════════════════════════════════════════════════
# Wave 2 — TriggerManager, RoutinePlugin, Admin API
# ══════════════════════════════════════════════════════════════════

from cortex.routines.triggers import TriggerManager, _normalise
from cortex.plugins.routines import RoutinePlugin, _parse_action


# ── TriggerManager ───────────────────────────────────────────────

class TestTriggerManagerVoice:
    """Voice trigger matching via TriggerManager."""

    async def test_exact_match(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        tm = TriggerManager()

        result = await tm.check_voice("good morning")
        assert result == rid

    async def test_partial_match_contains(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        tm = TriggerManager()

        result = await tm.check_voice("good morning atlas")
        assert result == rid

    async def test_partial_match_reverse(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        tm = TriggerManager()

        result = await tm.check_voice("hey good morning")
        assert result == rid

    async def test_no_match(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        tm = TriggerManager()

        result = await tm.check_voice("play some music")
        assert result is None

    async def test_disabled_routine_not_matched(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "good morning"})
        await engine.disable_routine(rid)
        tm = TriggerManager()

        result = await tm.check_voice("good morning")
        assert result is None

    async def test_punctuation_stripped(self, engine, db_conn):
        rid = await engine.create_routine("Greet")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "hello!"})
        tm = TriggerManager()

        result = await tm.check_voice("Hello")
        assert result == rid

    async def test_case_insensitive(self, engine, db_conn):
        rid = await engine.create_routine("Greet")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "Good Night"})
        tm = TriggerManager()

        result = await tm.check_voice("good night")
        assert result == rid

    async def test_empty_phrase_returns_none(self, engine, db_conn):
        rid = await engine.create_routine("Empty")
        await engine.add_trigger(rid, "voice_phrase", {"phrase": "hello"})
        tm = TriggerManager()

        result = await tm.check_voice("")
        assert result is None


class TestTriggerManagerHAEvent:
    """HA event trigger matching."""

    async def test_matching_event(self, engine, db_conn):
        rid = await engine.create_routine("Door Alert")
        await engine.add_trigger(rid, "ha_event", {
            "entity_id": "binary_sensor.front_door",
            "to_state": "on",
            "from_state": "off",
        })
        tm = TriggerManager()

        result = await tm.check_ha_event("binary_sensor.front_door", "on", "off")
        assert result == [rid]

    async def test_wrong_entity(self, engine, db_conn):
        rid = await engine.create_routine("Door Alert")
        await engine.add_trigger(rid, "ha_event", {
            "entity_id": "binary_sensor.front_door",
            "to_state": "on",
        })
        tm = TriggerManager()

        result = await tm.check_ha_event("binary_sensor.back_door", "on", "off")
        assert result == []

    async def test_wrong_state(self, engine, db_conn):
        rid = await engine.create_routine("Door Alert")
        await engine.add_trigger(rid, "ha_event", {
            "entity_id": "binary_sensor.front_door",
            "to_state": "on",
            "from_state": "off",
        })
        tm = TriggerManager()

        result = await tm.check_ha_event("binary_sensor.front_door", "off", "on")
        assert result == []

    async def test_partial_state_match(self, engine, db_conn):
        """Only to_state specified, from_state is any."""
        rid = await engine.create_routine("Motion")
        await engine.add_trigger(rid, "ha_event", {
            "entity_id": "binary_sensor.motion",
            "to_state": "on",
        })
        tm = TriggerManager()

        result = await tm.check_ha_event("binary_sensor.motion", "on", "unavailable")
        assert result == [rid]

    async def test_multiple_routines_match(self, engine, db_conn):
        rid1 = await engine.create_routine("Alert1")
        rid2 = await engine.create_routine("Alert2")
        await engine.add_trigger(rid1, "ha_event", {
            "entity_id": "binary_sensor.door", "to_state": "on",
        })
        await engine.add_trigger(rid2, "ha_event", {
            "entity_id": "binary_sensor.door", "to_state": "on",
        })
        tm = TriggerManager()

        result = await tm.check_ha_event("binary_sensor.door", "on", "off")
        assert rid1 in result
        assert rid2 in result


class TestTriggerManagerSchedule:
    """Schedule (cron) trigger matching."""

    async def test_matching_cron(self, engine, db_conn):
        rid = await engine.create_routine("Hourly")
        await engine.add_trigger(rid, "schedule", {"cron": "* * * * *"})
        tm = TriggerManager()

        result = await tm.check_schedule()
        assert rid in result

    async def test_non_matching_cron(self, engine, db_conn):
        """Cron that only matches Feb 30 — never fires."""
        rid = await engine.create_routine("Never")
        await engine.add_trigger(rid, "schedule", {"cron": "0 0 30 2 *"})
        tm = TriggerManager()

        result = await tm.check_schedule()
        assert rid not in result

    async def test_double_fire_prevention(self, engine, db_conn):
        rid = await engine.create_routine("Once")
        await engine.add_trigger(rid, "schedule", {"cron": "* * * * *"})
        tm = TriggerManager()

        result1 = await tm.check_schedule()
        assert rid in result1

        result2 = await tm.check_schedule()
        assert rid not in result2

    async def test_start_stop(self, db_conn):
        tm = TriggerManager()
        await tm.start()
        assert tm._running is True
        await tm.stop()
        assert tm._running is False


# ── RoutinePlugin ────────────────────────────────────────────────

class TestRoutinePluginMatch:
    """RoutinePlugin.match() — intent detection for all patterns."""

    async def test_match_run(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("run morning routine", {})
        assert match.matched
        assert match.intent == "run"
        assert match.metadata["routine_name"] == "morning"

    async def test_match_start(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("start bedtime routine", {})
        assert match.matched
        assert match.intent == "run"

    async def test_match_do(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("do movie time routine", {})
        assert match.matched
        assert match.intent == "run"

    async def test_match_create(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("create a routine", {})
        assert match.matched
        assert match.intent == "create"

    async def test_match_make_new(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("make a new routine", {})
        assert match.matched
        assert match.intent == "create"

    async def test_match_when_say(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("when I say good night, turn off the lights", {})
        assert match.matched
        assert match.intent == "create_when"

    async def test_match_list(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("list routines", {})
        assert match.matched
        assert match.intent == "list"

    async def test_match_show(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("show my routines", {})
        assert match.matched
        assert match.intent == "list"

    async def test_match_what_routines(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("what routines do I have", {})
        assert match.matched
        assert match.intent == "list"

    async def test_match_delete(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("delete morning routine", {})
        assert match.matched
        assert match.intent == "delete"

    async def test_match_enable(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("enable bedtime routine", {})
        assert match.matched
        assert match.intent == "enable"

    async def test_match_disable(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("disable morning routine", {})
        assert match.matched
        assert match.intent == "disable"

    async def test_match_template(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("set up good morning routine", {})
        assert match.matched
        assert match.intent == "template"

    async def test_no_match(self):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("what is the weather today", {})
        assert not match.matched


class TestRoutinePluginHandle:
    """RoutinePlugin.handle() — command execution."""

    async def test_handle_list_empty(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("list routines", {})
        result = await plugin.handle("list routines", match, {})
        assert result.success
        assert "don't have any" in result.response.lower() or "0" in result.response

    async def test_handle_list_with_routines(self, engine, db_conn):
        await engine.create_routine("Alpha")
        await engine.create_routine("Beta")
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("list routines", {})
        result = await plugin.handle("list routines", match, {})
        assert result.success
        assert "2 routine(s)" in result.response

    async def test_handle_run_not_found(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="run", metadata={"routine_name": "nonexistent"})
        result = await plugin.handle("run nonexistent routine", match, {})
        assert not result.success
        assert "couldn't find" in result.response.lower()

    async def test_handle_run_existing(self, engine, db_conn):
        rid = await engine.create_routine("Morning")
        await engine.add_step(rid, "delay", {"seconds": 0})
        plugin = RoutinePlugin()
        await plugin.setup({})
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="run", metadata={"routine_name": "Morning"})
        result = await plugin.handle("run morning routine", match, {})
        assert result.success
        assert "running" in result.response.lower()

    async def test_handle_create_when(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("when I say good night, turn off the lights", {})
        result = await plugin.handle("when I say good night, turn off the lights", match, {})
        assert result.success
        assert "good night" in result.response.lower()
        assert result.metadata.get("routine_id")

    async def test_handle_create_when_tts(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("when I say welcome home, say hello there", {})
        result = await plugin.handle("when I say welcome home, say hello there", match, {})
        assert result.success

    async def test_handle_create_when_unparseable_action(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        match = await plugin.match("when I say hello, do something complex", {})
        result = await plugin.handle("when I say hello, do something complex", match, {})
        assert not result.success
        assert "couldn't parse" in result.response.lower()

    async def test_handle_delete(self, engine, db_conn):
        await engine.create_routine("Doomed")
        plugin = RoutinePlugin()
        await plugin.setup({})
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="delete", metadata={"routine_name": "Doomed"})
        result = await plugin.handle("delete doomed routine", match, {})
        assert result.success
        assert "deleted" in result.response.lower()

    async def test_handle_enable_disable(self, engine, db_conn):
        rid = await engine.create_routine("Toggle")
        plugin = RoutinePlugin()
        await plugin.setup({})
        from cortex.plugins.base import CommandMatch

        match = CommandMatch(matched=True, intent="disable", metadata={"routine_name": "Toggle"})
        result = await plugin.handle("disable toggle routine", match, {})
        assert result.success
        assert "disabled" in result.response.lower()

        match = CommandMatch(matched=True, intent="enable", metadata={"routine_name": "Toggle"})
        result = await plugin.handle("enable toggle routine", match, {})
        assert result.success
        assert "enabled" in result.response.lower()

    async def test_handle_template(self, db_conn):
        plugin = RoutinePlugin()
        await plugin.setup({})
        from cortex.plugins.base import CommandMatch
        match = CommandMatch(matched=True, intent="template", metadata={"template_id": "good_morning"})
        result = await plugin.handle("set up good morning routine", match, {})
        assert result.success
        assert "good morning" in result.response.lower()


# ── Conversational builder parser ────────────────────────────────

class TestParseAction:
    """Test _parse_action helper for conversational builder."""

    def test_turn_on(self):
        action = _parse_action("turn on the bedroom lights")
        assert action is not None
        assert action["action_type"] == "ha_service"
        assert action["action_config"]["service"] == "turn_on"
        assert action["action_config"]["entity_id"] == "light.bedroom_lights"

    def test_turn_off(self):
        action = _parse_action("turn off the living room")
        assert action is not None
        assert action["action_config"]["service"] == "turn_off"
        assert action["action_config"]["entity_id"] == "light.living_room"

    def test_say_action(self):
        action = _parse_action("say hello everyone")
        assert action is not None
        assert action["action_type"] == "tts_announce"
        assert action["action_config"]["message"] == "hello everyone"

    def test_announce_action(self):
        action = _parse_action("announce dinner is ready")
        assert action is not None
        assert action["action_type"] == "tts_announce"

    def test_dim_action(self):
        action = _parse_action("dim the living room to 50%")
        assert action is not None
        assert action["action_type"] == "ha_service"
        assert action["action_config"]["service"] == "turn_on"
        assert "brightness" in action["action_config"].get("data", {})

    def test_unparseable(self):
        action = _parse_action("do something complex and magical")
        assert action is None


class TestNormalise:
    """Test _normalise helper."""

    def test_lowercase(self):
        assert _normalise("Hello World") == "hello world"

    def test_strip_punctuation(self):
        assert _normalise("hello!") == "hello"

    def test_strip_whitespace(self):
        assert _normalise("  hello  ") == "hello"


# ── Admin API ────────────────────────────────────────────────────

class TestAdminRoutineAPI:
    """Test admin API endpoints for routines."""

    @pytest.fixture()
    def client(self, db_conn):
        """Create a test client with admin auth."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from cortex.admin.routines import router

        app = FastAPI()
        app.include_router(router, prefix="/admin")

        # Override require_admin dependency
        from cortex.admin.helpers import require_admin
        app.dependency_overrides[require_admin] = lambda: {"user": "test"}

        return TestClient(app)

    def test_list_routines_empty(self, client):
        resp = client.get("/admin/routines")
        assert resp.status_code == 200
        assert resp.json()["routines"] == []

    def test_create_routine(self, client):
        resp = client.post("/admin/routines", json={"name": "Test Routine"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Routine"
        assert "id" in data

    def test_get_routine(self, client):
        create_resp = client.post("/admin/routines", json={"name": "Detail Test"})
        rid = create_resp.json()["id"]
        resp = client.get(f"/admin/routines/{rid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Detail Test"

    def test_get_routine_not_found(self, client):
        resp = client.get("/admin/routines/99999")
        assert resp.status_code == 404

    def test_delete_routine(self, client):
        create_resp = client.post("/admin/routines", json={"name": "Delete Me"})
        rid = create_resp.json()["id"]
        resp = client.delete(f"/admin/routines/{rid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        resp = client.get(f"/admin/routines/{rid}")
        assert resp.status_code == 404

    def test_enable_disable(self, client):
        create_resp = client.post("/admin/routines", json={"name": "Toggle"})
        rid = create_resp.json()["id"]

        resp = client.post(f"/admin/routines/{rid}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = client.post(f"/admin/routines/{rid}/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_run_routine(self, client, engine, db_conn):
        async def _setup():
            rid = await engine.create_routine("Runnable")
            await engine.add_step(rid, "delay", {"seconds": 0})
            return rid

        rid = asyncio.get_event_loop().run_until_complete(_setup())
        resp = client.post(f"/admin/routines/{rid}/run")
        assert resp.status_code == 200
        assert "run_id" in resp.json()

    def test_list_templates(self, client):
        resp = client.get("/admin/routines/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["templates"]) == len(TEMPLATES)
        names = [t["id"] for t in data["templates"]]
        assert "good_morning" in names

    def test_instantiate_template(self, client):
        resp = client.post("/admin/routines/templates/good_morning/instantiate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["template_id"] == "good_morning"
        assert "id" in data

    def test_instantiate_template_not_found(self, client):
        resp = client.post("/admin/routines/templates/nonexistent/instantiate", json={})
        assert resp.status_code == 404

    def test_add_step(self, client):
        create_resp = client.post("/admin/routines", json={"name": "Steps"})
        rid = create_resp.json()["id"]
        resp = client.post(f"/admin/routines/{rid}/steps", json={
            "action_type": "delay", "action_config": {"seconds": 5},
        })
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_add_trigger(self, client):
        create_resp = client.post("/admin/routines", json={"name": "Triggered"})
        rid = create_resp.json()["id"]
        resp = client.post(f"/admin/routines/{rid}/triggers", json={
            "trigger_type": "voice_phrase", "trigger_config": {"phrase": "hello"},
        })
        assert resp.status_code == 200
        assert "id" in resp.json()
