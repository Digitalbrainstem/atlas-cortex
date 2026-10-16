"""P04 command effects, scoped corrections and inference-independent dispatch."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from unittest.mock import AsyncMock

import pytest

from cortex.db import _create_schema
from cortex.integrations.ha import HAPlugin
from cortex.integrations.ha.bootstrap import HABootstrap
from cortex.integrations.ha.client import HAConnectionError
from cortex.integrations.lists.registry import ListPlugin
from cortex.pipeline import prepare_turn, run_prepared_pipeline_events
from cortex.pipeline.turns import ActionOutcome, RouteStatus
from cortex.plugins import PluginRegistry
from cortex.plugins.timers import SchedulingPlugin
from cortex.scheduling.timers import TimerEngine


class NoLLM:
    async def chat(self, **kwargs):
        raise AssertionError("Fast commands and corrections must not generate")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "commands.db")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    for entity, name, area in (
        ("light.kitchen", "Kitchen Light", "Kitchen"),
        ("light.bedroom", "Bedroom Light", "Bedroom"),
        ("light.desk", "Desk Light", "Office"),
    ):
        conn.execute(
            "INSERT INTO ha_devices(entity_id, friendly_name, domain, area_name, state) "
            "VALUES (?, ?, 'light', ?, 'off')", (entity, name, area),
        )
    conn.commit()
    client = AsyncMock()
    client.health.return_value = True
    plugin = HAPlugin(client=client, conn=conn)
    HABootstrap(client, conn).generate_patterns()
    registry = PluginRegistry()
    registry.register(plugin)
    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: registry)
    local = threading.local()
    local.conn = conn
    monkeypatch.setattr("cortex.db._LOCAL", local)
    monkeypatch.setattr("cortex.db._DB_PATH", tmp_path / "commands.db")
    monkeypatch.setattr("cortex.scheduling.timers.get_db", lambda: conn)
    yield conn, client, plugin, registry
    conn.close()


async def command(
    text, *, user="alice", conversation="chat", room="hall", speaker=None,
    source="pipeline", device=None,
):
    turn = await prepare_turn(
        text, user_id=user, speaker_id=speaker, satellite_id=device,
        metadata={"conversation_id": conversation, "room": room, "source": source},
    )
    assert turn.dispatch.status != RouteStatus.NOT_MATCHED
    _ = [event async for event in run_prepared_pipeline_events(turn, NoLLM())]
    return turn.dispatch


async def test_discovered_pattern_calls_backend_not_just_template(setup):
    conn, client, _, _ = setup
    result = await command("turn on the Kitchen Light")
    client.call_service.assert_awaited_once_with("light", "turn_on", {"entity_id": "light.kitchen"})
    assert result.action_outcome == ActionOutcome.SUCCEEDED
    assert "on" in result.response
    assert conn.execute("SELECT outcome FROM ha_command_context").fetchone()[0] == "succeeded"


async def test_completed_target_correction_is_truthful_and_inverse_is_explicit(setup):
    conn, client, _, _ = setup
    await command("turn on the kitchen lights")
    corrected = await command("actually the bedroom")
    assert "already completed" in corrected.response
    assert "did not undo" in corrected.response
    inverse = await command("No, turn them off")
    assert inverse.action_outcome == ActionOutcome.SUCCEEDED
    assert "did not undo" not in inverse.response
    assert [call.args for call in client.call_service.await_args_list] == [
        ("light", "turn_on", {"entity_id": "light.kitchen"}),
        ("light", "turn_on", {"entity_id": "light.bedroom"}),
        ("light", "turn_off", {"entity_id": "light.bedroom"}),
    ]
    assert [row[0] for row in conn.execute(
        "SELECT outcome FROM ha_command_context ORDER BY id",
    )] == ["succeeded", "succeeded", "succeeded"]


@pytest.mark.parametrize("scope", [
    {"user": "bob"}, {"conversation": "elsewhere"}, {"room": "office"}, {"speaker": "different"},
    {"source": "browser"}, {"device": "pi-2"},
])
async def test_corrections_do_not_cross_identity_boundaries(setup, scope):
    _, client, _, _ = setup
    await command("turn on the kitchen lights")
    result = await command("turn them off", **scope)
    assert result.status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1


async def test_ambiguous_and_unknown_targets_do_not_execute(setup):
    _, client, _, _ = setup
    for text in ("turn on the light", "turn on the nonexistent light"):
        result = await command(text)
        assert result.status == RouteStatus.CLARIFICATION
        assert result.action_outcome == ActionOutcome.NONE
    client.call_service.assert_not_awaited()


async def test_ambiguous_target_clarification_keeps_the_original_intent(setup):
    _, client, _, _ = setup
    assert (await command("turn on the light")).status == RouteStatus.CLARIFICATION
    result = await command("actually the bedroom")
    assert result.action_outcome == ActionOutcome.SUCCEEDED
    client.call_service.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.bedroom"},
    )


async def test_failed_backend_is_uncertain_and_never_claims_success(setup):
    conn, client, _, _ = setup
    client.call_service.side_effect = HAConnectionError("timed out after send")
    result = await command("turn on the kitchen lights")
    assert result.status == RouteStatus.FAILED
    assert result.action_outcome == ActionOutcome.UNCERTAIN
    assert conn.execute("SELECT outcome FROM ha_command_context").fetchone()[0] == "uncertain"
    corrected = await command("actually the bedroom")
    assert corrected.status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1


async def test_queued_target_is_replaced_before_effect(setup):
    conn, client, plugin, _ = setup
    # Reserve all resource stripes to deterministically stop before the effect.
    for lock in plugin._commands._resources:
        lock.acquire()
    first = asyncio.create_task(command("turn on the kitchen lights"))
    second = None
    try:
        await asyncio.sleep(0)
        second = asyncio.create_task(command("actually the bedroom"))
        await asyncio.sleep(0)
        for lock in plugin._commands._resources:
            lock.release()
        original, corrected = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert "replaced before" in original.response
        assert original.action_outcome == ActionOutcome.NONE
        assert corrected.action_outcome == ActionOutcome.SUCCEEDED
        client.call_service.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.bedroom"},
        )
        assert [row[0] for row in conn.execute(
            "SELECT outcome FROM ha_command_context ORDER BY id",
        )] == ["superseded", "succeeded"]
    finally:
        for lock in plugin._commands._resources:
            if lock.locked():
                lock.release()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_inflight_correction_waits_and_reports_actual_previous_outcome(setup):
    _, client, _, _ = setup
    entered, release = asyncio.Event(), asyncio.Event()

    async def effect(*args):
        if args[2]["entity_id"] == "light.kitchen":
            entered.set()
            await release.wait()

    client.call_service.side_effect = effect
    first = asyncio.create_task(command("turn on the kitchen lights"))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(command("actually the bedroom"))
    await asyncio.sleep(0)
    assert client.call_service.await_count == 1
    release.set()
    _, result = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert "already completed" in result.response
    assert client.call_service.await_count == 2


async def test_conflicting_users_are_serialized_on_same_entity(setup):
    _, client, _, _ = setup
    entered, release = asyncio.Event(), asyncio.Event()

    async def effect(*args):
        if args[1] == "turn_on":
            entered.set()
            await release.wait()

    client.call_service.side_effect = effect
    first = asyncio.create_task(command("turn on the kitchen lights"))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(command("turn off the kitchen lights", user="bob"))
    await asyncio.sleep(0.03)
    assert client.call_service.await_count == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert [call.args[1] for call in client.call_service.await_args_list] == ["turn_on", "turn_off"]


async def test_reopen_does_not_replay_or_infer_success_from_running_record(setup):
    conn, client, _, registry = setup
    await command("turn on the kitchen lights")
    conn.execute("UPDATE ha_command_context SET outcome='running'")
    conn.commit()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    conn.close()
    reopened = sqlite3.connect(path)
    reopened.row_factory = sqlite3.Row
    try:
        registry.register(HAPlugin(client=client, conn=reopened))
        result = await command("turn them off")
        assert result.status == RouteStatus.CLARIFICATION
        assert client.call_service.await_count == 1
    finally:
        reopened.close()


async def test_store_failure_prevents_effect(setup):
    conn, client, _, _ = setup
    conn.execute("PRAGMA query_only=ON")
    result = await command("turn on the kitchen lights")
    assert result.status == RouteStatus.FAILED
    assert result.action_outcome == ActionOutcome.NONE
    client.call_service.assert_not_awaited()


async def test_informational_fallthrough_is_not_eaten_by_correction_parser(setup):
    turn = await prepare_turn("actually why is the sky blue", metadata={"conversation_id": "chat"})
    assert turn.dispatch.status == RouteStatus.NOT_MATCHED


async def test_ha_list_timer_and_instant_dispatch_with_llm_slot_occupied(setup):
    from cortex.pipeline.request_queue import InferenceRequest, RequestQueue

    conn, client, _, registry = setup
    entered, release = asyncio.Event(), asyncio.Event()

    class BusyLLM:
        async def chat(self, **kwargs):
            async def generate():
                entered.set()
                await release.wait()
                yield "Finished"
            return generate()

    queue = RequestQueue(BusyLLM(), max_concurrent=1)
    await queue.start()
    timer_plugin = SchedulingPlugin()
    timer_plugin.timer_engine = TimerEngine()
    registry.register(ListPlugin(conn))
    registry.register(timer_plugin)
    try:
        queue.submit(InferenceRequest("busy", "someone", []))
        await asyncio.wait_for(entered.wait(), 2)
        for text in (
            "2 + 2", "turn on the kitchen lights", "actually the bedroom",
            "add milk to my grocery list", "set a timer for 5 minutes",
        ):
            result = await asyncio.wait_for(command(text), 1)
            assert result.status == RouteStatus.HANDLED
        assert not release.is_set()
        assert conn.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM timers WHERE user_id='alice'").fetchone()[0] == 1
    finally:
        release.set()
        await queue.stop()
        for timer_id in list(timer_plugin.timer_engine._tasks):
            await timer_plugin.timer_engine.cancel_timer(timer_id)


async def test_learned_toggle_cannot_fake_backend_success(setup):
    conn, client, _, _ = setup
    conn.execute(
        "INSERT INTO command_patterns (pattern, intent, response_template, confidence) "
        "VALUES ('turn on.*', 'toggle', 'Done without a service call', 1.0)",
    )
    conn.commit()
    result = await command("turn on the kitchen lights")
    assert result.action_outcome == ActionOutcome.SUCCEEDED
    assert "without a service" not in result.response
    client.call_service.assert_awaited_once()


async def test_expired_context_requires_explicit_target(setup):
    conn, client, _, _ = setup
    await command("turn on the kitchen lights")
    conn.execute("UPDATE ha_command_context SET updated_at=0")
    conn.commit()
    result = await command("turn them off")
    assert result.status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1


async def test_corrupt_context_is_an_explicit_failure_not_llm_fallthrough(setup):
    conn, client, _, _ = setup
    await command("turn on the kitchen lights")
    conn.execute("UPDATE ha_command_context SET command_json='broken'")
    conn.commit()
    result = await command("turn them off")
    assert result.status == RouteStatus.FAILED
    assert result.action_outcome == ActionOutcome.NONE
    assert client.call_service.await_count == 1


async def test_cancelled_inflight_command_is_persisted_uncertain(setup):
    conn, client, _, _ = setup
    entered = asyncio.Event()

    async def effect(*args):
        entered.set()
        await asyncio.Event().wait()

    client.call_service.side_effect = effect
    task = asyncio.create_task(command("turn on the kitchen lights"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert conn.execute("SELECT outcome FROM ha_command_context").fetchone()[0] == "uncertain"
    assert (await command("turn them off")).status == RouteStatus.CLARIFICATION


async def test_wrong_domain_alias_never_selects_a_lock_for_a_light_command(setup):
    conn, client, _, _ = setup
    conn.execute(
        "INSERT INTO ha_devices(entity_id, friendly_name, domain) "
        "VALUES ('lock.front', 'Front Door', 'lock')",
    )
    conn.execute(
        "INSERT INTO device_aliases(entity_id, alias) VALUES ('lock.front', 'porch_light')",
    )
    conn.commit()
    result = await command("turn on the porch light")
    assert result.status == RouteStatus.CLARIFICATION
    client.call_service.assert_not_awaited()


async def test_independent_question_after_action_remains_fallthrough(setup):
    await command("turn on the kitchen lights")
    turn = await prepare_turn(
        "actually explain why clouds form", user_id="alice",
        metadata={"conversation_id": "chat", "room": "hall"},
    )
    assert turn.dispatch.status == RouteStatus.NOT_MATCHED


async def test_discovered_brightness_has_real_value_and_range_validation(setup):
    _, client, _, _ = setup
    result = await command("set the Kitchen Light to 42%")
    assert result.action_outcome == ActionOutcome.SUCCEEDED
    client.call_service.assert_awaited_once_with(
        "light", "turn_on", {"brightness_pct": 42, "entity_id": "light.kitchen"},
    )
    result = await command("set the Kitchen Light to 900%")
    assert result.status == RouteStatus.FAILED
    assert result.action_outcome == ActionOutcome.NONE
    assert client.call_service.await_count == 1


async def test_list_removal_asks_for_ambiguous_item_and_honors_permission(setup):
    conn, _, _, registry = setup
    plugin = ListPlugin(conn)
    registry.register(plugin)
    await command("add oat milk to my grocery list")
    await command("add almond milk to my grocery list")
    result = await command("remove milk from my grocery list")
    assert result.status == RouteStatus.CLARIFICATION
    assert conn.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 2
    list_id = conn.execute("SELECT id FROM list_registry").fetchone()[0]
    plugin._registry.grant_permission(list_id, "bob", can_view=True, can_remove=False)
    result = await command("remove oat milk from my grocery list", user="bob")
    assert result.status == RouteStatus.FAILED
    assert conn.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 2
    result = await command("remove oat milk from my grocery list")
    assert result.action_outcome == ActionOutcome.SUCCEEDED
    assert conn.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 1


async def test_timer_control_does_not_guess_or_cross_rooms(setup):
    conn, _, _, registry = setup
    plugin = SchedulingPlugin()
    plugin.timer_engine = TimerEngine()
    registry.register(plugin)
    try:
        await command("set a timer for 5 minutes called eggs", room="kitchen")
        await command("set a timer for 10 minutes called pasta", room="kitchen")
        result = await command("cancel the timer", room="kitchen")
        assert result.status == RouteStatus.CLARIFICATION
        result = await command("cancel the timer", room="bedroom")
        assert result.status == RouteStatus.CLARIFICATION
        assert conn.execute("SELECT COUNT(*) FROM timers WHERE state='running'").fetchone()[0] == 2
        result = await command("cancel the timer called eggs", room="kitchen")
        assert result.status == RouteStatus.HANDLED
        assert conn.execute("SELECT state FROM timers WHERE label='eggs'").fetchone()[0] == "cancelled"
        assert conn.execute("SELECT state FROM timers WHERE label='pasta'").fetchone()[0] == "running"
    finally:
        for timer_id in list(plugin.timer_engine._tasks):
            await plugin.timer_engine.cancel_timer(timer_id)


async def test_failed_replacement_commit_preserves_original_queued_action(setup):
    conn, client, plugin, _ = setup
    for lock in plugin._commands._resources:
        lock.acquire()
    first = asyncio.create_task(command("turn on the kitchen lights"))
    try:
        await asyncio.sleep(0)
        conn.execute("PRAGMA query_only=ON")
        corrected = await command("actually the bedroom")
        assert corrected.status == RouteStatus.FAILED
        assert corrected.action_outcome == ActionOutcome.NONE
        conn.execute("PRAGMA query_only=OFF")
        for lock in plugin._commands._resources:
            lock.release()
        original = await asyncio.wait_for(first, 2)
        assert original.action_outcome == ActionOutcome.SUCCEEDED
        client.call_service.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.kitchen"},
        )
    finally:
        conn.execute("PRAGMA query_only=OFF")
        for lock in plugin._commands._resources:
            if lock.locked():
                lock.release()
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)


async def test_deleted_user_loses_action_context_but_other_user_keeps_it(setup, monkeypatch):
    from cortex.admin.users import delete_user

    conn, _, _, _ = setup
    await command("turn on the kitchen lights")
    await command("turn on the bedroom lights", user="bob")
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: conn)
    await delete_user("alice", {})
    assert conn.execute(
        "SELECT DISTINCT user_id FROM ha_command_context",
    ).fetchone()[0] == "bob"
    assert (await command("turn them off")).status == RouteStatus.CLARIFICATION


async def test_unidentified_satellite_speaker_cannot_reuse_device_context(setup):
    _, client, _, _ = setup
    await command("turn on the kitchen lights", user="default", device="pi-1")
    result = await command("turn them off", user="default", device="pi-1")
    assert result.status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1


async def test_newer_list_command_prevents_reusing_older_device_target(setup):
    conn, client, _, registry = setup
    registry.register(ListPlugin(conn))
    await command("turn on the kitchen lights")
    await command("add milk to my grocery list")
    assert (await command("actually eggs")).status == RouteStatus.CLARIFICATION
    assert (await command("turn them off")).status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1
    assert conn.execute("SELECT content FROM list_items").fetchone()[0] == "milk"


async def test_late_completion_cannot_overwrite_newer_command_context(setup):
    conn, client, _, registry = setup
    registry.register(ListPlugin(conn))
    entered, release = asyncio.Event(), asyncio.Event()

    async def effect(*args):
        entered.set()
        await release.wait()

    client.call_service.side_effect = effect
    first = asyncio.create_task(command("turn on the kitchen lights"))
    await asyncio.wait_for(entered.wait(), 2)
    await command("add milk to my grocery list")
    release.set()
    await asyncio.wait_for(first, 2)
    assert (await command("turn them off")).status == RouteStatus.CLARIFICATION
    assert client.call_service.await_count == 1


async def test_pronoun_with_multiple_pending_targets_asks_without_dropping_commands(setup):
    _, client, plugin, _ = setup
    for lock in plugin._commands._resources:
        lock.acquire()
    first = asyncio.create_task(command("turn on the kitchen lights"))
    second = None
    try:
        await asyncio.sleep(0)
        second = asyncio.create_task(command("turn on the bedroom lights"))
        await asyncio.sleep(0)
        assert (await command("turn them off")).status == RouteStatus.CLARIFICATION
        client.call_service.assert_not_awaited()
        for lock in plugin._commands._resources:
            lock.release()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert [call.args[2]["entity_id"] for call in client.call_service.await_args_list] == [
            "light.kitchen", "light.bedroom",
        ]
    finally:
        for lock in plugin._commands._resources:
            if lock.locked():
                lock.release()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
