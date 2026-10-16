"""Regression coverage for gaps found during MVP acceptance review."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from cortex.auth_user import UserAuthConfig, UserAuthManager
from cortex.db import get_db, init_db, set_db_path
from cortex.safety import GuardrailResult, Severity


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "repairs.db"
    set_db_path(path)
    init_db(path)
    conn = get_db()
    monkeypatch.setattr("cortex.auth_user._manager", None)
    monkeypatch.setattr("cortex.server._get_db", lambda: conn)
    yield conn
    monkeypatch.setattr("cortex.auth_user._manager", None)


def test_persisted_auth_is_loaded_before_http_identity_resolution(db):
    from cortex.server import app
    manager = UserAuthManager()
    manager._users["alice"] = UserAuthConfig("alice", "Alice")
    manager.set_pin("alice", "1234")
    manager.save_to_db(db, manager.get_user("alice"))
    response = TestClient(app).post("/v1/chat/completions", json={
        "user": "alice", "messages": [{"role": "user", "content": "2 + 2"}],
    })
    assert response.status_code == 401


async def test_setting_pin_preserves_child_tier_and_old_adult_default(db, monkeypatch):
    from cortex.admin.users import SetUserAuthRequest, set_user_auth
    from cortex.safety.middleware import PipelineSafetyMiddleware
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: db)
    db.execute("INSERT INTO user_profiles(user_id,display_name,age_group,age_confidence) VALUES('kid','Kid','child',1)")
    db.commit()
    await set_user_auth("kid", SetUserAuthRequest(method="pin", value="1234"), {})
    assert PipelineSafetyMiddleware(db)._resolve_profile("kid")[1] == "child"
    db.execute("UPDATE user_auth SET content_tier='adult' WHERE user_id='kid'")
    db.commit()
    assert PipelineSafetyMiddleware(db)._resolve_profile("kid")[1] == "child"


def test_http_speech_rechecks_policy_after_synthesis(db, monkeypatch):
    from cortex.server import app
    calls = []
    class Safety:
        blocked = False
        def check_output(self, *args, **kwargs):
            return GuardrailResult(
                Severity.SOFT_BLOCK if self.blocked else Severity.PASS, "canary", "Synthetic",
            )
    safety = Safety()
    class TTS:
        sample_rate = 16000
        async def synthesize(self, **kwargs):
            calls.append(kwargs["text"])
            safety.blocked = True
            yield b"\0\0" * 20
    monkeypatch.setattr("cortex.server._safety_middleware", safety)
    monkeypatch.setattr("cortex.server.get_tts_provider", lambda *args: TTS())
    client = TestClient(app)
    assert client.post("/v1/audio/speech", json={"input": "Synthetic canary"}).status_code == 403
    assert calls == ["Synthetic canary"]
    assert client.post("/v1/audio/speech", json={"input": "Blocked before synthesis"}).status_code == 403
    assert calls == ["Synthetic canary"]
    assert client.post("/v1/audio/speech", json={"input": "x" * 16001}).status_code == 422


def test_auth_load_failure_does_not_publish_empty_manager(db, monkeypatch):
    from cortex.auth_user import get_user_auth
    def fail(self, conn):
        raise RuntimeError("Synthetic storage failure")
    monkeypatch.setattr(UserAuthManager, "load_from_db", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        get_user_auth()
    import cortex.auth_user
    assert cortex.auth_user._manager is None


async def test_scheduler_expiry_uses_registered_room_delivery(db, monkeypatch):
    from cortex.notifications import channels
    from cortex.notifications.satellite import register_satellite_channel
    from cortex.plugins.timers import SchedulingPlugin
    from unittest.mock import AsyncMock
    monkeypatch.setattr(channels, "_channels", [])
    monkeypatch.setattr(channels, "_initialized", False)
    db.execute("INSERT INTO satellites(id,display_name,room,status) VALUES('pi','Pi','kitchen','online')")
    db.commit()
    channel = register_satellite_channel()
    deliver = AsyncMock(return_value=True)
    channel.set_tts_callback(deliver)
    plugin = SchedulingPlugin()
    await plugin.setup({})
    try:
        timer = await plugin.timer_engine.start_timer(0, label="review", user_id="alice", room="kitchen")
        await plugin.timer_engine._tasks[timer]
        deliver.assert_awaited_once_with("pi", "Your timer for review has finished!")
        assert db.execute("SELECT COUNT(*) FROM notification_log WHERE source='scheduling.timer'").fetchone()[0] == 1
    finally:
        await plugin.alarm_engine.stop()
        await plugin.reminder_engine.stop()


async def test_proactive_actions_use_real_notification_contracts(db, monkeypatch):
    from cortex.notifications import channels
    from cortex.notifications.satellite import register_satellite_channel
    from cortex.proactive.engine import RuleEngine
    from unittest.mock import AsyncMock
    monkeypatch.setattr(channels, "_channels", [])
    monkeypatch.setattr(channels, "_initialized", False)
    rule = {"id": 1, "name": "Synthetic", "priority": "normal"}
    assert (await RuleEngine._action_notify(rule, {}, {"message": "Reminder"})).startswith("notified:")
    assert await RuleEngine._action_tts(rule, {}, {"message": "Reminder"}) == "tts_failed"
    db.execute("INSERT INTO satellites(id,display_name,room,status) VALUES('pi','Pi','kitchen','online')")
    db.commit()
    deliver = AsyncMock(return_value=True)
    register_satellite_channel().set_tts_callback(deliver)
    assert (await RuleEngine._action_tts(rule, {}, {"message": "Reminder", "satellite_id": "pi"})).startswith("tts_queued:")
    deliver.assert_awaited_once_with("pi", "Reminder")


@pytest.fixture
async def runtime(db, monkeypatch):
    from cortex.orchestrator.turn_store import TurnStore, TurnStorePolicy
    from cortex.orchestrator.conversation import ConversationCoordinator
    from cortex.safety.middleware import PipelineSafetyMiddleware
    async def chat(**kwargs):
        async def tokens():
            yield "Synthetic answer."
        return tokens()
    provider = SimpleNamespace(chat=chat)
    store = TurnStore(db, TurnStorePolicy(min_free_disk_bytes=1))
    coordinator = ConversationCoordinator(store, provider, PipelineSafetyMiddleware(db))
    monkeypatch.setattr("cortex.server._get_provider", lambda: provider)
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")
    await coordinator.start()
    try:
        yield store, coordinator, provider
    finally:
        await coordinator.stop()


async def test_concurrent_fast_action_retry_executes_once(runtime, monkeypatch):
    from cortex.pipeline import run_pipeline
    from cortex.plugins import PluginRegistry
    from cortex.plugins.timers import SchedulingPlugin
    from cortex.scheduling.timers import TimerEngine
    store, coordinator, provider = runtime
    plugin = SchedulingPlugin()
    plugin.timer_engine = TimerEngine()
    registry = PluginRegistry()
    registry.register(plugin)
    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: registry)
    metadata = {"turn_id": "same", "conversation_id": "review", "idempotency_key": "same"}
    async def request():
        return "".join([part async for part in run_pipeline(
            "set a timer for 5 minutes", provider, user_id="alice", metadata=metadata,
            db_conn=store.conn,
        )])
    try:
        results = await asyncio.gather(request(), request())
        assert results[0] == results[1]
        assert "Timer set" in results[0]
        assert store.conn.execute("SELECT COUNT(*) FROM timers").fetchone()[0] == 1
    finally:
        for timer_id in list(plugin.timer_engine._tasks):
            await plugin.timer_engine.cancel_timer(timer_id)


@pytest.mark.parametrize("streaming", [False, True])
async def test_http_consumption_marks_text_retention_eligible(runtime, streaming):
    from cortex.server import app
    from httpx import AsyncClient, ASGITransport
    store, _, _ = runtime
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json={
            "user": "alice", "stream": streaming,
            "metadata": {"turn_id": "text", "conversation_id": "review"},
            "messages": [{"role": "user", "content": "Explain a synthetic household concept."}],
        })
        assert response.status_code == 200
    assert store.get("text", "alice").delivery_state == "delivered"
    store.conn.execute("UPDATE pending_turns SET updated_at=0")
    store.conn.commit()
    assert store.cleanup() == 1


def test_browser_result_recovery_and_revision_receipt(db, monkeypatch):
    from cortex.server import app
    from cortex.orchestrator.turn_store import TurnStore, TurnStorePolicy
    from cortex.orchestrator.conversation import ConversationCoordinator
    from cortex.pipeline.turns import TurnIdentity, DispatchResult, RouteStatus
    from cortex.safety.middleware import PipelineSafetyMiddleware
    store = TurnStore(db, TurnStorePolicy(min_free_disk_bytes=1))
    identity = TurnIdentity("reply", "chat", "alice", source="browser")
    store.accept(identity, "Synthetic question", input_validated=True)
    store.claim("reply", "alice", "owner")
    store.complete("reply", "alice", "owner", 1,
                   DispatchResult(RouteStatus.HANDLED, "Synthetic answer."), output_validated=True)
    store.recover(startup=True)
    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    monkeypatch.setattr(app.state, "conversation_coordinator",
                        ConversationCoordinator(store, None, PipelineSafetyMiddleware(db)), raising=False)
    client = TestClient(app)
    request = {"turn_id": "reply", "conversation_id": "chat", "user_id": "alice"}
    with client.websocket_connect("/ws/chat") as socket:
        socket.send_json({"type": "turn_result", **request, "user_id": "bob"})
        assert socket.receive_json()["type"] == "error"
        socket.send_json({"type": "turn_result", **request})
        assert socket.receive_json()["text"] == "Synthetic answer."
    assert store.get("reply", "alice").delivery_state == "awaiting_confirmation"
    with client.websocket_connect("/ws/chat") as socket:
        socket.send_json({"type": "turn_result", **request})
        result = socket.receive_json()
        assert result["text"] == "Synthetic answer."
        socket.send_json({"type": "turn_ack", **request, "revision": result["revision"] + 1})
        assert socket.receive_json()["type"] == "error"
        socket.send_json({"type": "turn_ack", **request, "revision": result["revision"]})
        assert socket.receive_json()["type"] == "turn_ack"
    assert store.get("reply", "alice").delivery_state == "delivered"


async def test_real_pipeline_recalls_and_writes_scoped_memory_once(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from cortex.memory.controller import MemorySystem
    from cortex.pipeline import run_pipeline
    from cortex.plugins import PluginRegistry
    store, coordinator, provider = runtime
    monkeypatch.setattr("cortex.memory.controller._HAS_CHROMADB", False)
    monkeypatch.setattr("cortex.memory.controller.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: PluginRegistry())
    monkeypatch.setattr("cortex.pipeline.layer2_plugins._try_learned_patterns", lambda _: (None, 0))
    memory = MemorySystem(store.conn)
    monkeypatch.setattr("cortex.memory.controller._memory_system", memory)
    await memory.start()
    try:
        await memory.remember("My favorite telescope is the blue telescope.", "alice")
        await memory.remember("My private telescope is the red telescope.", "bob")
        await asyncio.wait_for(memory._writer._queue.join(), 3)
        remember = AsyncMock(wraps=memory.remember)
        monkeypatch.setattr(memory, "remember", remember)
        prompts = []
        async def chat(**kwargs):
            prompts.append(kwargs["messages"])
            async def tokens():
                yield "Your favorite telescope is blue."
            return tokens()
        provider.chat = chat
        metadata = {"turn_id": "memory", "conversation_id": "review"}
        for _ in range(2):
            answer = "".join([part async for part in run_pipeline(
                "telescope", provider, user_id="alice", metadata=metadata,
                db_conn=store.conn,
            )])
            assert "blue" in answer
        assert len(prompts) == 1
        assert "blue telescope" in str(prompts)
        assert "red telescope" not in str(prompts)
        remember.assert_awaited_once()
        assert remember.await_args.kwargs["user_id"] == "alice"
        await asyncio.wait_for(memory._writer._queue.join(), 3)
        assert store.conn.execute("SELECT COUNT(*) FROM memory_fts WHERE user_id='alice'").fetchone()[0] == 2
    finally:
        await memory.stop()
