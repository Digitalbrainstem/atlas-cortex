"""Characterization of staged dispatch and legacy pipeline consumers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cortex.pipeline import prepare_turn, run_pipeline, run_prepared_pipeline_events
from cortex.pipeline.events import LayerResult, TextToken, TurnEvent
from cortex.pipeline.turns import ActionOutcome, RouteStatus, TurnIdentity
from cortex.plugins import PluginRegistry
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin


class NoLLM:
    async def chat(self, **kwargs):
        raise AssertionError("A handled request must not call the LLM")


class FakePlugin(CortexPlugin):
    plugin_id = "fixture"
    display_name = "Fixture"

    def __init__(self, result=None, healthy=True):
        self.result = result or CommandResult(True, "Done", ["light.bedroom"])
        self.healthy = healthy
        self.calls = 0

    async def setup(self, config):
        return True

    async def health(self):
        return self.healthy

    async def match(self, message, context):
        return CommandMatch(True, "turn_on")

    async def handle(self, message, match, context):
        self.calls += 1
        return self.result


@pytest.fixture
def registry(monkeypatch):
    registry = PluginRegistry()
    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: registry)
    monkeypatch.setattr("cortex.pipeline.layer2_plugins._try_learned_patterns", lambda _: (None, 0))
    return registry


async def consume(prepared):
    return [event async for event in run_prepared_pipeline_events(prepared, NoLLM())]


async def test_instant_identity_and_legacy_text_wrapper(registry):
    metadata = {"turn_id": "turn-1", "conversation_id": "chat-1", "revision": 2, "room": "office"}
    prepared = await prepare_turn("2 + 2", user_id="alice", metadata=metadata)
    assert prepared.context["room"] == "office"
    assert prepared.dispatch.status == RouteStatus.HANDLED
    events = await consume(prepared)
    assert isinstance(events[0], TurnEvent)
    assert events[0].identity.user_id == "alice"
    result = next(event for event in events if isinstance(event, LayerResult))
    assert result.turn_id == "turn-1" and result.revision == 2
    assert "".join(event.text for event in events if isinstance(event, TextToken)) == "4"
    assert "".join([token async for token in run_pipeline("2 + 2", NoLLM())]) == "4"


async def test_prepared_action_is_not_executed_twice(registry):
    plugin = FakePlugin()
    registry.register(plugin)
    prepared = await prepare_turn("turn on the bedroom lights")
    assert plugin.calls == 1
    assert prepared.dispatch.entities == ["light.bedroom"]
    assert prepared.dispatch.action_outcome == ActionOutcome.SUCCEEDED
    await consume(prepared)
    assert plugin.calls == 1
    with pytest.raises(ValueError, match="already been consumed"):
        await consume(prepared)


@pytest.mark.parametrize(
    ("result", "healthy", "status"),
    [
        (CommandResult(False, "Backend failed"), True, RouteStatus.FAILED),
        (CommandResult(True, "Which light?", clarification_required=True), True, RouteStatus.CLARIFICATION),
        (CommandResult(True, "Done"), False, RouteStatus.FAILED),
        (CommandResult(False, ""), True, RouteStatus.FAILED),
    ],
)
async def test_recognized_failure_or_clarification_never_generates(registry, result, healthy, status):
    registry.register(FakePlugin(result, healthy))
    prepared = await prepare_turn("turn on the lights")
    assert prepared.dispatch.status == status
    events = await consume(prepared)
    assert next(e for e in events if isinstance(e, LayerResult)).status == status


async def test_only_explicit_decline_falls_through(registry, monkeypatch):
    registry.register(FakePlugin(CommandResult(False, "", handled=False)))
    prepared = await prepare_turn("explain the night sky")
    assert prepared.dispatch.status == RouteStatus.NOT_MATCHED

    async def generate(**kwargs):
        assert kwargs["context"]["turn_identity"] == prepared.identity
        yield "The sky is dark."

    monkeypatch.setattr("cortex.pipeline.stream_llm_response", generate)
    events = [e async for e in run_prepared_pipeline_events(prepared, NoLLM())]
    assert next(e for e in events if isinstance(e, LayerResult)).layer == "llm"


def test_invalid_revision_is_not_silently_defaulted():
    with pytest.raises(ValueError, match="positive integer"):
        TurnIdentity.from_metadata("alice", {"revision": True})


def test_pipe_consumes_real_async_generator(monkeypatch):
    from cortex.pipe import Pipe

    monkeypatch.setattr("cortex.pipe._ensure_ready", lambda: None)
    monkeypatch.setattr("cortex.pipe._provider", NoLLM())
    monkeypatch.setattr("cortex.pipe._db_conn", None)
    pipe = Pipe()
    assert list(pipe.pipe({"messages": [{"role": "user", "content": "2 + 2"}]})) == ["4"]


async def test_pipe_consumes_generator_inside_running_loop(monkeypatch):
    from cortex.pipe import Pipe

    monkeypatch.setattr("cortex.pipe._ensure_ready", lambda: None)
    monkeypatch.setattr("cortex.pipe._provider", NoLLM())
    monkeypatch.setattr("cortex.pipe._db_conn", None)
    assert list(Pipe().pipe({"messages": [{"role": "user", "content": "2 + 2"}]})) == ["4"]


async def test_satellite_voice_consumes_async_generator(monkeypatch):
    from cortex.orchestrator import voice

    conn = SimpleNamespace(satellite_id="pi-test", session_id="voice-test", send=AsyncMock())
    monkeypatch.setattr(voice, "transcribe", AsyncMock(return_value="2 + 2"))
    monkeypatch.setattr(voice, "is_hallucinated", lambda _: False)
    monkeypatch.setattr("cortex.wake_word.filter_transcript", lambda text: text)
    monkeypatch.setattr("cortex.providers.get_provider", lambda: NoLLM())
    monkeypatch.setattr("cortex.memory.get_memory_system", lambda: None)
    monkeypatch.setattr(voice, "_stream_orpheus_to_satellite", AsyncMock(return_value=(100, 0.01)))
    await voice.process_voice_pipeline(conn, b"\x00\x01" * 24000)
    assert voice._stream_orpheus_to_satellite.await_count == 1
    assert not any(call.args[0].get("type") == "PIPELINE_ERROR" for call in conn.send.call_args_list)


async def test_sse_failure_is_not_a_successful_completion():
    from cortex.pipeline.turns import PipelineGenerationError
    from cortex.server import _sse_stream

    async def failing():
        yield "partial"
        raise PipelineGenerationError("Provider unavailable")

    response = "".join([frame async for frame in _sse_stream(failing(), "atlas")])
    assert '"error"' in response
    assert '"finish_reason": "stop"' not in response
    assert response.endswith("data: [DONE]\n\n")


async def test_http_generation_failure_is_502(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from cortex.server import app
    from cortex.pipeline.turns import PipelineGenerationError

    async def failing(**kwargs):
        yield "partial"
        raise PipelineGenerationError("Provider unavailable")

    monkeypatch.setattr("cortex.server.run_pipeline", failing)
    monkeypatch.setattr("cortex.server._get_provider", lambda: NoLLM())
    monkeypatch.setattr("cortex.server._get_db", lambda: None)
    monkeypatch.setattr("cortex.server._safety_middleware", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "Explain clouds"}],
        })
    assert response.status_code == 502
    assert response.json()["detail"] == "Provider unavailable"
