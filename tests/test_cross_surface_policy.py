"""Benign policy canaries across fast paths, generated output and browser sessions."""

from __future__ import annotations

from types import SimpleNamespace
import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cortex.auth_user import UserAuthConfig, UserAuthManager, chat_request_actor, has_verified_actor
from cortex.db import get_db, init_db, set_db_path
from cortex.pipeline import run_pipeline, run_pipeline_events
from cortex.pipeline.events import TTSEvent, TextToken
from cortex.plugins import PluginRegistry
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.safety import GuardrailResult, Severity


class CanarySafety:
    def check_input(self, message, *args, **kwargs):
        return self.result("blocked input canary" in message)

    def check_output(self, message, *args, **kwargs):
        return self.result("blocked output canary" in message)

    def build_system_prompt(self, *args, **kwargs):
        return "Household policy prompt."

    @staticmethod
    def result(blocked):
        return GuardrailResult(
            severity=Severity.SOFT_BLOCK if blocked else Severity.PASS,
            category="canary", reason="Benign qualification canary",
            suggested_response="Policy blocked." if blocked else None,
        )


class CanaryPlugin(CortexPlugin):
    plugin_id = "canary"
    calls = 0

    async def setup(self, config):
        return True

    async def health(self):
        return True

    async def match(self, message, context):
        return CommandMatch(True, "canary_action")

    async def handle(self, message, match, context):
        self.calls += 1
        return CommandResult(True, "blocked output canary")


@pytest.fixture
def policy_env(tmp_path, monkeypatch):
    path = tmp_path / "policy.db"
    set_db_path(path)
    init_db(path)
    db = get_db()
    manager = UserAuthManager()
    for user in ("alice", "bob"):
        manager._users[user] = UserAuthConfig(user, user.title())
        manager.set_pin(user, "1234")
        manager.save_to_db(db, manager.get_user(user))
    monkeypatch.setattr("cortex.auth_user._manager", manager)
    safety = CanarySafety()
    monkeypatch.setattr("cortex.pipeline._pipeline_safety", lambda *args: (safety, db))
    registry = PluginRegistry()
    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: registry)
    monkeypatch.setattr("cortex.pipeline.layer2_plugins._try_learned_patterns", lambda _: (None, 0))
    return SimpleNamespace(db=db, manager=manager, safety=safety, registry=registry)


async def test_input_policy_runs_before_any_fast_path_effect(policy_env):
    plugin = CanaryPlugin()
    policy_env.registry.register(plugin)
    text = "".join([part async for part in run_pipeline("blocked input canary", None, db_conn=policy_env.db)])
    assert text == "Policy blocked."
    assert plugin.calls == 0


async def test_fast_path_text_and_audio_are_validated(policy_env):
    policy_env.registry.register(CanaryPlugin())
    events = [event async for event in run_pipeline_events("synthetic action", None, db_conn=policy_env.db)]
    assert [event.text for event in events if isinstance(event, TextToken)] == ["Policy blocked."]
    assert [event.text for event in events if isinstance(event, TTSEvent)] == ["Policy blocked."]


async def test_no_coordinator_generation_is_buffered_before_text_or_audio(policy_env, monkeypatch):
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")
    async def chat(*args, **kwargs):
        async def chunks():
            yield "blocked "
            yield "output canary"
        return chunks()
    provider = SimpleNamespace(chat=chat)
    events = [event async for event in run_pipeline_events(
        "Explain a synthetic household concept.", provider, db_conn=policy_env.db,
    )]
    assert [event.text for event in events if isinstance(event, TextToken)] == ["Policy blocked."]
    assert [event.text for event in events if isinstance(event, TTSEvent)] == ["Policy blocked."]


@pytest.mark.parametrize("limit,allowed", [(15, False), (16, True)])
async def test_fallback_output_limit_measures_utf8_bytes_before_publication(policy_env, monkeypatch, limit, allowed):
    from cortex.pipeline.turns import PipelineGenerationError
    monkeypatch.setenv("CORTEX_TURN_MAX_OUTPUT_BYTES", str(limit))
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")
    text = chr(233) * 8

    async def chat(*args, **kwargs):
        async def chunks():
            yield text
        return chunks()

    published = []
    async def consume():
        async for event in run_pipeline_events(
            "Explain a synthetic household concept.", SimpleNamespace(chat=chat), db_conn=policy_env.db,
        ):
            if isinstance(event, (TextToken, TTSEvent)):
                published.append(event.text)
    if allowed:
        await consume()
        assert published == [text, text]
    else:
        with pytest.raises(PipelineGenerationError, match="output byte limit"):
            await consume()
        assert published == []

async def test_parental_action_permission_blocks_before_plugin_execution(policy_env):
    plugin = CanaryPlugin()
    policy_env.registry.register(plugin)
    policy_env.db.executemany(
        "INSERT INTO user_profiles(user_id,display_name) VALUES(?,?)", [("kid", "Kid"), ("parent", "Parent")],
    )
    policy_env.db.execute(
        "INSERT INTO parental_controls(child_user_id,parent_user_id,allowed_hours_start,allowed_hours_end) "
        "VALUES('kid','parent','00:00','24:00')"
    )
    policy_env.db.execute("INSERT INTO parental_restricted_actions(child_user_id,action) VALUES('kid','canary_action')")
    policy_env.db.commit()
    text = "".join([part async for part in run_pipeline("synthetic action", None, user_id="kid", db_conn=policy_env.db)])
    assert "not allowed" in text
    assert plugin.calls == 0


def test_speaker_or_metadata_is_not_privileged_authentication(policy_env):
    manager = policy_env.manager
    assert not has_verified_actor("alice")
    token = manager.generate_session_token("alice")
    with chat_request_actor("alice", token):
        assert has_verified_actor("alice")
        assert not has_verified_actor("bob")
        manager.forget_user("alice")
        assert not has_verified_actor("alice")
    assert not has_verified_actor("alice")


def test_browser_protected_profile_binding_and_live_revocation(policy_env, monkeypatch):
    from cortex.server import app
    monkeypatch.setattr("cortex.server._get_db", lambda: policy_env.db)
    monkeypatch.setattr("cortex.server._get_provider", lambda: None)
    async def fake_pipeline(**kwargs):
        yield "Safe response."
    monkeypatch.setattr("cortex.server.run_pipeline", fake_pipeline)
    with TestClient(app).websocket_connect("/ws/chat") as socket:
        socket.send_json({"message": "hi", "user_id": "alice"})
        assert socket.receive_json()["code"] == "authentication"
        socket.send_json({"message": "hi", "user_id": "alice",
                          "chat_token": policy_env.manager.generate_session_token("bob")})
        assert socket.receive_json()["code"] == "authentication"
        token = policy_env.manager.generate_session_token("alice")
        socket.send_json({"message": "hi", "user_id": "alice", "chat_token": token})
        assert [socket.receive_json()["type"] for _ in range(3)] == ["start", "token", "end"]
        policy_env.manager.forget_user("alice")
        socket.send_json({"message": "hi", "user_id": "alice", "chat_token": token})
        assert socket.receive_json()["code"] == "authentication"


def test_browser_tts_policy_blocks_before_synthesis(policy_env, monkeypatch):
    from cortex.server import app
    synthesize = AsyncMock()
    monkeypatch.setattr("cortex.server._safety_middleware", policy_env.safety)
    monkeypatch.setattr("cortex.speech.tts.synthesize_speech", synthesize)
    with TestClient(app).websocket_connect("/ws/chat") as socket:
        socket.send_json({"type": "tts_request", "text": "blocked output canary"})
        assert socket.receive_json()["type"] == "tts_error"
    synthesize.assert_not_awaited()


def test_invalid_audio_discards_entire_capture_not_just_bad_chunk(policy_env, monkeypatch):
    from cortex.server import app
    transcribe = AsyncMock(return_value="synthetic transcript")
    monkeypatch.setattr("cortex.speech.stt.transcribe", transcribe)
    with TestClient(app).websocket_connect("/ws/chat") as socket:
        socket.send_json({"type": "audio_start"})
        socket.send_json({"type": "audio_data", "data": "not-base64"})
        assert socket.receive_json()["type"] == "error"
        socket.send_json({"type": "audio_data", "data": "AAAA"})
        socket.send_json({"type": "audio_end"})
        assert socket.receive_json()["type"] == "error"
    transcribe.assert_not_awaited()


def test_policy_and_credential_metadata_is_not_durable():
    from cortex.pipeline.turns import sanitize_metadata
    metadata = sanitize_metadata({
        "turn_id": "synthetic", "content_tier": "adult", "authenticated": True,
        "nested": {"Authorization": "synthetic-secret", "useful": 1},
    })
    assert metadata == {"turn_id": "synthetic", "nested": {"useful": 1}}


def test_http_protected_identity_and_legacy_service_key(policy_env, monkeypatch):
    from cortex.server import app
    monkeypatch.setattr("cortex.server._get_provider", lambda: None)
    monkeypatch.setattr("cortex.server._get_db", lambda: policy_env.db)
    monkeypatch.setattr("cortex.server._safety_middleware", policy_env.safety)
    body = {"messages": [{"role": "user", "content": "2 + 2"}], "user": "alice"}
    client = TestClient(app)
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    token = policy_env.manager.generate_session_token("alice")
    response = client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "4"
    body["user"] = "default"
    assert client.post("/v1/chat/completions", json=body, headers={
        "Authorization": "Bearer synthetic-openai-placeholder",
    }).status_code == 200


def test_pipe_has_the_same_pre_action_input_policy(policy_env, monkeypatch):
    from cortex.pipe import Pipe
    plugin = CanaryPlugin()
    policy_env.registry.register(plugin)
    monkeypatch.setattr("cortex.pipe._ensure_ready", lambda: None)
    monkeypatch.setattr("cortex.pipe._provider", None)
    monkeypatch.setattr("cortex.pipe._db_conn", None)
    for key in ("LLM_PROVIDER", "LLM_URL"):
        monkeypatch.setenv(key, os.environ.get(key, "openai_compatible" if key == "LLM_PROVIDER" else "http://127.0.0.1:1"))
    text = "".join(Pipe().pipe({"messages": [{"role": "user", "content": "blocked input canary"}]}))
    assert text == "Policy blocked."
    assert plugin.calls == 0


async def test_door_lock_requires_verified_session_not_speaker_label(policy_env):
    from cortex.integrations.ha import HAPlugin
    from cortex.integrations.ha.bootstrap import HABootstrap
    policy_env.db.execute(
        "INSERT INTO ha_devices(entity_id,friendly_name,domain,state) VALUES('lock.front','Front Door','lock','locked')"
    )
    policy_env.db.commit()
    client = AsyncMock()
    HABootstrap(client, policy_env.db).generate_patterns(["lock"])
    plugin = HAPlugin(client=client, conn=policy_env.db)
    context = {"user_id": "alice", "speaker_id": "alice", "metadata": {"authenticated": True}}
    match = await plugin.match("unlock the Front Door", context)
    assert match.matched
    result = await plugin.handle("unlock the Front Door", match, context)
    assert not result.success
    assert result.metadata["action_outcome"] == "none"
    client.call_service.assert_not_awaited()
    token = policy_env.manager.generate_session_token("alice")
    with chat_request_actor("alice", token):
        result = await plugin.handle("unlock the Front Door", match, context)
    assert result.success
    client.call_service.assert_awaited_once()


async def test_deletion_invalidates_queued_and_inflight_memory_vectors(policy_env, monkeypatch):
    from cortex.memory.cold import MemoryWriter
    from unittest.mock import MagicMock
    started, release = asyncio.Event(), asyncio.Event()

    async def embedding(text, provider):
        if "dark" in text:
            started.set()
            await release.wait()
        return [0.1, 0.2]

    monkeypatch.setattr("cortex.memory.cold.get_embedding", embedding)
    vector = MagicMock()
    writer = MemoryWriter(policy_env.db, vector_store=vector, provider=object())
    await writer.enqueue("I prefer dark mode", "alice")
    await writer.enqueue("I prefer quiet mornings", "alice")
    await writer.enqueue("I prefer green tea", "bob")
    writer.start()
    try:
        await asyncio.wait_for(started.wait(), 3)
        writer.forget_user("alice")
        release.set()
        await asyncio.wait_for(writer._queue.join(), 3)
        assert [row[0] for row in policy_env.db.execute("SELECT user_id FROM memory_fts")] == ["bob"]
        assert [call.kwargs["metadata"]["user_id"] for call in vector.upsert.call_args_list] == ["bob"]
        vector.delete_user.assert_called_once_with("alice")
    finally:
        release.set()
        await writer.stop()


async def test_user_deletion_removes_owned_memory_auth_and_durable_data(policy_env, monkeypatch):
    from cortex.admin.users import delete_user
    from cortex.memory.controller import set_memory_system
    from cortex.orchestrator.turn_store import TurnStore
    from cortex.pipeline.turns import TurnIdentity
    set_memory_system(None)
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: policy_env.db)
    store = TurnStore(policy_env.db)
    for user in ("alice", "bob"):
        store.accept(TurnIdentity(user, "synthetic", user), "Synthetic transcript", input_validated=True)
        policy_env.db.execute(
            "INSERT INTO memory_fts(doc_id,user_id,text,type,tags) VALUES(?,?,?,'preference','')",
            (user, user, "Synthetic preference"),
        )
        policy_env.db.commit()
    policy_env.db.commit()
    token = policy_env.manager.generate_session_token("alice")
    assert await delete_user("alice", {}) == {"ok": True}
    assert policy_env.manager.get_user("alice") is None
    with pytest.raises(ValueError):
        policy_env.manager.resolve_request_user("alice", token)
    assert [row[0] for row in policy_env.db.execute("SELECT user_id FROM memory_fts")] == ["bob"]
    assert store.status()["counts"] == {"accepted": 1}
