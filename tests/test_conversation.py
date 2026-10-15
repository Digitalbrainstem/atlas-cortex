"""P05 durable LLM perspective, fair admission and obsolete-result suppression."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import replace

import pytest

from cortex.db import _create_schema
from cortex.orchestrator.conversation import ConversationCoordinator, GenerationOptions
from cortex.orchestrator.turn_store import TurnStore, TurnStorePolicy
from cortex.pipeline.turns import (
    ConversationConflictError, DispatchResult, PipelineGenerationError, PreparedTurn,
    RouteStatus, TurnIdentity, TurnSupersededError,
)
from cortex.safety import GuardrailResult, Severity


class Safety:
    blocked_input = False
    blocked_output = False

    def check_input(self, *args):
        return GuardrailResult(
            severity=Severity.SOFT_BLOCK if self.blocked_input else Severity.PASS,
            category="fixture", reason="fixture", suggested_response="Input blocked.",
        )

    def check_output(self, *args):
        return GuardrailResult(
            severity=Severity.SOFT_BLOCK if self.blocked_output else Severity.PASS,
            category="fixture", reason="fixture", suggested_response="Safe replacement.",
        )

    def build_system_prompt(self, *args):
        return "Apply household safety policy."


class Provider:
    def __init__(self):
        self.calls = []
        self.block = set()
        self.entered = asyncio.Queue()
        self.release = asyncio.Event()
        self.fail = False

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        question = messages[-1]["content"]

        async def generate():
            await self.entered.put(question)
            yield "Answer: "
            if question in self.block:
                await self.release.wait()
            if self.fail:
                raise ConnectionError("provider unavailable")
            yield question

        return generate()


OPTIONS = GenerationOptions("fast", "thinking")


def prepared(text, *, turn_id=None, conversation="chat", user="alice", metadata=None):
    identity = TurnIdentity(turn_id or text, conversation, user, source="http", room="office")
    context = {
        "turn_identity": identity, "user_id": user, "speaker_id": None, "room": "office",
        "conversation_history": [], "metadata": metadata or {},
    }
    return PreparedTurn(
        identity, text, context, DispatchResult(RouteStatus.NOT_MATCHED),
        int(time.monotonic() * 1000), {"L0": 0.0, "L1": 0.0, "L2": 0.0},
    )


async def answer(coordinator, turn):
    return "".join([chunk async for chunk in coordinator.stream(turn, OPTIONS)])


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 3)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "conversation.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.commit()
    store = TurnStore(conn, TurnStorePolicy(min_free_disk_bytes=1))
    provider, safety = Provider(), Safety()
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")
    coordinator = ConversationCoordinator(store, provider, safety)
    await coordinator.start()
    yield coordinator, store, provider, safety
    provider.release.set()
    await coordinator.stop()
    conn.close()


async def test_queued_correction_only_generates_current_question(env):
    coordinator, store, provider, _ = env
    provider.block.add("blocker")
    blocker = asyncio.create_task(answer(coordinator, prepared("blocker", conversation="other")))
    assert await asyncio.wait_for(provider.entered.get(), 2) == "blocker"
    old = asyncio.create_task(answer(coordinator, prepared("Explain Mars")))
    await until(lambda: store.status()["counts"].get("llm_pending") == 1)
    new = asyncio.create_task(answer(coordinator, prepared("actually Venus")))
    with pytest.raises(TurnSupersededError):
        await asyncio.wait_for(old, 2)
    provider.release.set()
    assert await asyncio.wait_for(new, 3) == "Answer: actually Venus"
    await blocker
    assert [call[-1]["content"] for call in provider.calls] == ["blocker", "actually Venus"]
    assert "Explain Mars" in provider.calls[-1][0]["content"]
    assert store.get("actually Venus", "alice").related_turn_id == "Explain Mars"


async def test_inflight_supersession_cannot_publish_partial_or_final_old_text(env):
    coordinator, store, provider, _ = env
    provider.block.add("old question")
    old_output = []

    async def consume():
        async for chunk in coordinator.stream(prepared("old question"), OPTIONS):
            old_output.append(chunk)

    old = asyncio.create_task(consume())
    await asyncio.wait_for(provider.entered.get(), 2)
    result = await asyncio.wait_for(answer(coordinator, prepared("actually the corrected question")), 3)
    with pytest.raises(TurnSupersededError):
        await old
    assert old_output == []
    assert result == "Answer: actually the corrected question"
    assert store.get("old question", "alice").state == "superseded"
    await until(lambda: coordinator.queue.active_count == 0 and not coordinator.queue._states)
    assert coordinator.queue._states == {}


async def test_independent_questions_order_and_build_current_history_at_dispatch(env):
    coordinator, store, provider, _ = env
    provider.block.add("first")
    coordinator.store.policy = replace(store.policy, max_active=2)
    coordinator.queue._max_concurrent = 2
    first = asyncio.create_task(answer(coordinator, prepared("first")))
    await asyncio.wait_for(provider.entered.get(), 2)
    second = asyncio.create_task(answer(coordinator, prepared("second")))
    other = asyncio.create_task(answer(coordinator, prepared("other room", conversation="room-2")))
    assert await asyncio.wait_for(other, 2) == "Answer: other room"
    assert [call[-1]["content"] for call in provider.calls] == ["first", "other room"]
    provider.release.set()
    await asyncio.gather(first, second)
    assert provider.calls[-1][1:3] == [
        {"role": "user", "content": "first"}, {"role": "assistant", "content": "Answer: first"},
    ]


async def test_ambiguous_correction_keeps_independent_questions(env):
    coordinator, store, provider, _ = env
    provider.block.add("blocker")
    blocker = asyncio.create_task(answer(coordinator, prepared("blocker", conversation="other")))
    await asyncio.wait_for(provider.entered.get(), 2)
    one = asyncio.create_task(answer(coordinator, prepared("one")))
    two = asyncio.create_task(answer(coordinator, prepared("two")))
    await until(lambda: store.status()["counts"].get("llm_pending") == 2)
    assert "Which question" in await answer(coordinator, prepared("actually blue"))
    assert len(provider.calls) == 1
    assert store.get("one", "alice").state == "llm_pending"
    provider.release.set()
    await asyncio.gather(blocker, one, two)
    assert [call[-1]["content"] for call in provider.calls] == ["blocker", "one", "two"]


async def test_cross_user_relation_is_rejected_and_history_is_not_shared(env):
    coordinator, _, provider, _ = env
    await answer(coordinator, prepared("private question", user="alice"))
    with pytest.raises(ConversationConflictError):
        await answer(coordinator, prepared("replacement", user="bob", metadata={
            "relation": "replacement", "related_turn_id": "private question", "related_revision": 1,
        }))
    await answer(coordinator, prepared("bob question", user="bob"))
    assert all("private question" not in message["content"] for message in provider.calls[-1])


async def test_round_robin_heads_do_not_load_the_backlog_or_starve_other_conversations(env):
    coordinator, store, provider, _ = env
    for number in range(100):
        coordinator._admit(prepared(f"a-{number}"), OPTIONS)
    coordinator._admit(prepared("b-0", conversation="other"), OPTIONS)
    assert coordinator.queue.queued_count == 0
    assert coordinator._jobs == {}
    assert len(store.llm_dispatch_ids()) == 2
    await until(lambda: len(provider.calls) >= 3)
    assert [call[-1]["content"] for call in provider.calls[:3]] == ["a-0", "b-0", "a-1"]


async def test_explicit_cancel_cleans_up_inference_without_publishing(env):
    coordinator, store, provider, _ = env
    provider.block.add("cancel me")
    pending = asyncio.create_task(answer(coordinator, prepared("cancel me")))
    await asyncio.wait_for(provider.entered.get(), 2)
    coordinator.cancel("cancel me", "alice", 1)
    with pytest.raises(TurnSupersededError):
        await pending
    await until(lambda: coordinator.queue.active_count == 0 and not coordinator.queue._states)
    assert store.get("cancel me", "alice").state == "cancelled"
    assert coordinator.queue._states == {}


async def test_same_idempotency_key_reuses_result_but_not_different_work(env):
    coordinator, store, provider, _ = env
    first = prepared("same question", turn_id="first")
    first.identity = replace(first.identity, idempotency_key="retry")
    first.context["turn_identity"] = first.identity
    assert await answer(coordinator, first) == "Answer: same question"
    retry = prepared("same question", turn_id="retry")
    retry.identity = replace(retry.identity, idempotency_key="retry")
    retry.context["turn_identity"] = retry.identity
    assert await answer(coordinator, retry) == "Answer: same question"
    assert len(provider.calls) == 1
    retry.message = "different question"
    with pytest.raises(ConversationConflictError):
        await answer(coordinator, retry)
    assert store.status()["counts"] == {"completed": 1}


async def test_provider_failure_is_not_a_successful_empty_or_partial_answer(env):
    coordinator, store, provider, _ = env
    provider.fail = True
    with pytest.raises(PipelineGenerationError):
        await answer(coordinator, prepared("will fail"))
    assert store.get("will fail", "alice").state == "failed"
    assert coordinator.queue._states == {}


async def test_output_is_buffered_and_validated_before_becoming_visible(env):
    coordinator, store, provider, safety = env
    safety.blocked_output = True
    provider.block.add("unsafe answer")
    task = asyncio.create_task(answer(coordinator, prepared("unsafe answer")))
    await asyncio.wait_for(provider.entered.get(), 2)
    assert not task.done()
    provider.release.set()
    assert await task == "Safe replacement."
    assert store.get("unsafe answer", "alice").result == "Safe replacement."


async def test_input_is_rechecked_at_dispatch_and_blocked_input_is_not_admitted(env):
    coordinator, store, provider, safety = env
    safety.blocked_input = True
    assert await answer(coordinator, prepared("blocked")) == "Input blocked."
    assert store.status()["counts"] == {}
    safety.blocked_input = False
    coordinator._admit(prepared("previously allowed"), OPTIONS)
    safety.blocked_input = True
    await until(lambda: store.get("previously allowed", "alice").state == "failed")
    assert provider.calls == []


async def test_recovery_confirmation_dispatches_without_preloading_or_automatic_replay(env):
    coordinator, store, provider, safety = env
    coordinator._admit(prepared("restored"), OPTIONS)
    await coordinator.stop()
    store.recover(startup=True)
    restarted = ConversationCoordinator(store, provider, safety)
    await restarted.start()
    try:
        await asyncio.sleep(0.05)
        assert provider.calls == []
        store.confirm_recovery("restored", "alice", 2)
        await until(lambda: store.get("restored", "alice").state == "completed")
        assert len(provider.calls) == 1
    finally:
        await restarted.stop()


async def test_recovered_generation_rechecks_current_household_policy(env):
    coordinator, store, provider, safety = env
    coordinator._admit(prepared("restored under changed policy"), OPTIONS)
    await coordinator.stop()
    store.recover(startup=True)
    safety.blocked_input = True
    restarted = ConversationCoordinator(store, provider, safety)
    await restarted.start()
    try:
        store.confirm_recovery("restored under changed policy", "alice", 2)
        await until(lambda: store.get("restored under changed policy", "alice").state == "failed")
        assert provider.calls == []
        assert store.get("restored under changed policy", "alice").result == "Input blocked."
    finally:
        await restarted.stop()

async def test_full_question_correction_replaces_pending_question(env):
    coordinator, store, provider, _ = env
    provider.block.add("old")
    old = asyncio.create_task(answer(coordinator, prepared("old")))
    await asyncio.wait_for(provider.entered.get(), 2)
    assert await answer(coordinator, prepared("actually explain Venus")) == "Answer: actually explain Venus"
    with pytest.raises(TurnSupersededError):
        await old
    assert store.get("actually explain Venus", "alice").relation == "replacement"


async def test_explicit_independent_question_does_not_supersede(env):
    coordinator, store, provider, _ = env
    provider.block.add("first")
    first = asyncio.create_task(answer(coordinator, prepared("first")))
    await asyncio.wait_for(provider.entered.get(), 2)
    second = asyncio.create_task(answer(coordinator, prepared(
        "actually explain Venus", metadata={"relation": "independent"},
    )))
    await until(lambda: store.status()["counts"].get("llm_pending") == 1)
    assert store.get("first", "alice").state == "llm_running"
    provider.release.set()
    await asyncio.gather(first, second)
    assert len(provider.calls) == 2


async def test_clarification_records_lineage_without_replaying_original(env):
    coordinator, store, provider, _ = env
    await answer(coordinator, prepared("original"))
    await answer(coordinator, prepared("more detail", metadata={
        "relation": "clarification", "related_turn_id": "original", "related_revision": 1,
    }))
    turn = store.get("more detail", "alice")
    assert (turn.relation, turn.related_turn_id) == ("clarification", "original")
    assert store.get("original", "alice").state == "completed"
    assert len(provider.calls) == 2


async def test_output_byte_budget_failure_releases_provider_and_slot(env):
    coordinator, store, provider, _ = env
    store.policy = replace(store.policy, max_output_bytes=10)
    with pytest.raises(PipelineGenerationError):
        await answer(coordinator, prepared("too long"))
    await until(lambda: not coordinator._jobs and not coordinator.queue._states)
    assert coordinator.queue.active_count == 0
    assert store.get("too long", "alice").result is None
    assert store.get("too long", "alice").state == "failed"


async def test_heartbeat_storage_failure_does_not_leave_stream_waiting(env, monkeypatch):
    from cortex.orchestrator.turn_store import TurnStoreError
    from cortex.pipeline.turns import ConversationStorageError

    coordinator, store, provider, _ = env
    store.policy = replace(store.policy, lease_seconds=1)
    provider.block.add("waiting")

    def failed_heartbeat(*args):
        raise TurnStoreError("disk unavailable")

    monkeypatch.setattr(store, "heartbeat", failed_heartbeat)
    with pytest.raises(ConversationStorageError):
        await asyncio.wait_for(answer(coordinator, prepared("waiting")), 2)
    await until(lambda: not coordinator._jobs and not coordinator.queue._states)
    assert coordinator.queue.active_count == 0


async def test_fast_answer_bypasses_occupied_authoritative_inference(env):
    from cortex.pipeline import run_pipeline

    coordinator, store, provider, _ = env
    provider.block.add("busy")
    busy = asyncio.create_task(answer(coordinator, prepared("busy")))
    await asyncio.wait_for(provider.entered.get(), 2)

    async def quick_answer():
        return "".join([chunk async for chunk in run_pipeline(
            "2 + 2", provider, user_id="alice", db_conn=store.conn,
        )])

    assert await asyncio.wait_for(quick_answer(), 1) == "4"
    assert coordinator.queue.active_count == 1
    assert len(provider.calls) == 1
    provider.release.set()
    await busy


async def test_detached_consumer_does_not_cancel_accepted_work(env):
    coordinator, store, provider, _ = env
    provider.block.add("durable")
    consumer = asyncio.create_task(answer(coordinator, prepared("durable")))
    await asyncio.wait_for(provider.entered.get(), 2)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert store.get("durable", "alice").state == "llm_running"
    provider.release.set()
    await until(lambda: store.get("durable", "alice").state == "completed")
    assert await answer(coordinator, prepared("durable")) == "Answer: durable"
    assert len(provider.calls) == 1


async def test_restart_cannot_replay_completed_unheard_output_without_confirmation(env):
    coordinator, store, provider, safety = env
    assert await answer(coordinator, prepared("unheard")) == "Answer: unheard"
    await coordinator.stop()
    store.recover(startup=True)
    restarted = ConversationCoordinator(store, provider, safety)
    await restarted.start()
    try:
        with pytest.raises(PipelineGenerationError, match="confirmation"):
            await answer(restarted, prepared("unheard"))
        assert len(provider.calls) == 1
        store.set_delivery("unheard", "alice", 1, "delivering", confirmed=True)
        store.set_delivery("unheard", "alice", 1, "delivered")
        assert await answer(restarted, prepared("unheard")) == "Answer: unheard"
        assert len(provider.calls) == 1
    finally:
        await restarted.stop()


async def test_provider_yielding_after_cancel_is_closed_without_obsolete_output(env):
    coordinator, store, _, _ = env
    entered, closed = asyncio.Event(), asyncio.Event()

    class StubbornProvider(Provider):
        async def chat(self, messages, **kwargs):
            async def generate():
                try:
                    yield "partial"
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        yield "obsolete"
                finally:
                    closed.set()
            return generate()

    provider = StubbornProvider()
    coordinator.provider = provider
    coordinator.queue._provider = provider
    consumer = asyncio.create_task(answer(coordinator, prepared("cancel")))
    await asyncio.wait_for(entered.wait(), 2)
    coordinator.cancel("cancel", "alice", 1)
    with pytest.raises(TurnSupersededError):
        await consumer
    await asyncio.wait_for(closed.wait(), 2)
    await until(lambda: not coordinator._jobs and not coordinator.queue._states)
    assert coordinator.queue.active_count == 0
    assert store.get("cancel", "alice").result is None


@pytest.mark.parametrize("scope", ["room", "device_id", "source", "speaker_id"])
async def test_related_question_and_durable_history_are_scope_isolated(env, scope):
    coordinator, _, provider, _ = env
    await answer(coordinator, prepared("private"))
    other = prepared("other", metadata={
        "relation": "clarification", "related_turn_id": "private", "related_revision": 1,
    })
    if scope == "speaker_id":
        other.context[scope] = "other-speaker"
    else:
        other.identity = replace(other.identity, **{scope: "other-scope"})
        other.context["turn_identity"] = other.identity
    with pytest.raises(ConversationConflictError):
        await answer(coordinator, other)
    other.context["metadata"] = {}
    await answer(coordinator, other)
    assert not any("private" in item["content"] for item in provider.calls[-1])


async def test_queue_admin_reports_durable_backlog_and_explicit_storage_failure(env, monkeypatch):
    from fastapi import HTTPException
    from cortex.admin.pipeline import get_request_queue_state
    from cortex.orchestrator.turn_store import TurnStoreError

    coordinator, store, _, _ = env
    coordinator._admit(prepared("pending"), OPTIONS)
    assert await get_request_queue_state({}) == {
        "depth": 1, "active": 0, "running": True, "prefetched": 0,
    }

    def unavailable():
        raise TurnStoreError("store unavailable")

    monkeypatch.setattr(store, "status", unavailable)
    with pytest.raises(HTTPException) as error:
        await get_request_queue_state({})
    assert error.value.status_code == 503


async def test_failed_coordinator_start_releases_loop_registration(env, monkeypatch):
    from cortex.pipeline.turns import get_conversation_coordinator

    coordinator, store, provider, safety = env
    await coordinator.stop()
    restarted = ConversationCoordinator(store, provider, safety)

    async def unavailable():
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(restarted.queue, "start", unavailable)
    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        await restarted.start()
    assert get_conversation_coordinator() is None
    assert not restarted._running


async def test_named_service_registration_replaces_stale_lifespan_instances(monkeypatch):
    import cortex.scheduler as scheduler

    monkeypatch.setattr(scheduler, "_startup_tasks", [])
    monkeypatch.setattr(scheduler, "_background_services", [])
    monkeypatch.setattr(scheduler, "_running_tasks", [])
    calls = []

    async def stale():
        calls.append("stale")

    async def current():
        calls.append("current")

    async def stopped():
        calls.append("stopped")

    scheduler.register_startup_task("warmup", stale, blocking=True)
    scheduler.register_startup_task("warmup", current, blocking=True)
    scheduler.register_service("conversation", stale, stopped)
    scheduler.register_service("conversation", current, stopped)
    await scheduler.start_all()
    await scheduler.stop_all()
    assert calls == ["current", "current", "stopped"]


def test_pipe_worker_is_shared_across_calls_and_stops_cleanly(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import cortex.pipe as pipe

    conn = sqlite3.connect(tmp_path / "pipe.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.commit()
    provider = Provider()
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CORTEX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pipe, "_ensure_ready", lambda: None)
    monkeypatch.setattr(pipe, "_db_conn", conn)
    monkeypatch.setattr(pipe, "_provider", provider)
    monkeypatch.setattr("cortex.safety.middleware.PipelineSafetyMiddleware", lambda **kwargs: Safety())
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")

    async def prepare(message, **kwargs):
        turn = prepared(message, metadata=kwargs["metadata"])
        turn.identity = replace(turn.identity, source="pipe")
        turn.context["turn_identity"] = turn.identity
        return turn

    monkeypatch.setattr("cortex.pipeline.prepare_turn", prepare)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            loops = list(pool.map(lambda _: pipe._get_worker_loop(), range(4)))
        assert all(loop is loops[0] for loop in loops)
        worker = pipe._worker_thread
        for question in ("question one", "question two"):
            result = list(pipe.Pipe().pipe({"messages": [{"role": "user", "content": question}]}))
            assert result == [f"Answer: {question}"]
            assert pipe._worker_thread is worker
        assert len(provider.calls) == 2
    finally:
        pipe._stop_worker()
        conn.close()
    assert not worker.is_alive()
    assert pipe._worker_loop is None


async def test_http_and_sse_use_registered_coordinator(env, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    import cortex.server as server

    coordinator, store, provider, _ = env
    monkeypatch.setattr(server, "_get_provider", lambda: provider)
    monkeypatch.setattr(server, "_get_db", lambda: store.conn)
    monkeypatch.setattr(server, "_safety_middleware", None)

    async def prepare(message, **kwargs):
        return prepared(message, user=kwargs["user_id"], metadata=kwargs["metadata"])

    monkeypatch.setattr("cortex.pipeline.prepare_turn", prepare)
    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as client:
        for streaming in (False, True):
            question = f"explain streaming {streaming}"
            response = await client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": question}],
                "user": "alice", "stream": streaming,
            })
            assert response.status_code == 200
            if streaming:
                assert "data: [DONE]" in response.text
                assert f"Answer: {question}" in response.text
            else:
                assert response.json()["choices"][0]["message"]["content"] == f"Answer: {question}"
    assert len(provider.calls) == 2
    assert store.status()["counts"] == {"completed": 2}


def test_browser_admits_correction_while_previous_reply_waits(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import cortex.server as server

    conn = sqlite3.connect(tmp_path / "browser.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.commit()
    store = TurnStore(conn, TurnStorePolicy(min_free_disk_bytes=1))
    provider = Provider()
    provider.block = {"old 0", "old 1"}
    coordinators = []

    @asynccontextmanager
    async def lifespan(app):
        provider.entered = asyncio.Queue()
        provider.release = asyncio.Event()
        coordinator = ConversationCoordinator(store, provider, Safety())
        coordinators.append(coordinator)
        await coordinator.start()
        try:
            yield
        finally:
            await coordinator.stop()

    app = FastAPI(lifespan=lifespan)
    app.add_api_websocket_route("/ws/chat", server.chat_ws_handler)
    monkeypatch.setattr(server, "_get_provider", lambda: provider)
    monkeypatch.setattr(server, "_get_db", lambda: conn)
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")

    async def prepare(message, **kwargs):
        return prepared(message, user=kwargs["user_id"], metadata=kwargs["metadata"])

    monkeypatch.setattr("cortex.pipeline.prepare_turn", prepare)
    try:
        for cycle in range(2):
            with TestClient(app) as client, client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": f"old {cycle}", "user_id": "alice"})
                ws.send_json({"message": f"actually new {cycle}", "user_id": "alice"})
                frames = [ws.receive_json() for _ in range(4)]
                assert sorted(frame["type"] for frame in frames) == ["end", "error", "start", "token"]
                assert [frame["text"] for frame in frames if frame["type"] == "token"] == [
                    f"Answer: actually new {cycle}",
                ]
                assert all("turn_id" in frame for frame in frames)
            assert not coordinators[-1]._running
            assert not coordinators[-1]._jobs
            assert coordinators[-1].queue.active_count == 0
    finally:
        conn.close()
