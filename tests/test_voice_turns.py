"""Durable early-STT admission, bounded capture and device delivery."""

from __future__ import annotations

import asyncio
import base64
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cortex.db import _create_schema
from cortex.orchestrator.conversation import ConversationCoordinator
from cortex.orchestrator.turn_store import TurnStore, TurnStorePolicy
from cortex.orchestrator.voice_turns import Capture, VoiceService
from cortex.pipeline.turns import DispatchResult, PreparedTurn, RouteStatus, TurnIdentity
from cortex.safety import GuardrailResult, Severity
from cortex.satellite.websocket import (
    SatelliteConnection, _handle_audio_start, _handle_audio_chunk,
    _handle_audio_phrase_end, _handle_audio_end,
)


class Safety:
    def check_input(self, *args):
        return GuardrailResult(severity=Severity.PASS, category="fixture", reason="fixture")

    def check_output(self, *args):
        return GuardrailResult(severity=Severity.PASS, category="fixture", reason="fixture")

    def build_system_prompt(self, *args):
        return ""


class Provider:
    async def chat(self, messages, **kwargs):
        async def chunks():
            yield "Answer for " + messages[-1]["content"] + "."
        return chunks()


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 3)


@pytest.fixture
async def voice_env(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "voice.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    conn.commit()
    store = TurnStore(conn, TurnStorePolicy(min_free_disk_bytes=1))
    coordinator = ConversationCoordinator(store, Provider(), Safety())
    service = VoiceService(store, coordinator)
    await coordinator.start()
    await service.start()
    frames = []
    async def send(message):
        frames.append(message)
    device = SatelliteConnection(SimpleNamespace(send_json=send), "pi-one")
    device.room = "kitchen"
    device.capabilities = ["playback_ack"]
    transcripts = asyncio.Queue()
    transcribe = AsyncMock(side_effect=lambda *args, **kwargs: transcripts.get_nowait())
    monkeypatch.setattr("cortex.orchestrator.voice.transcribe", transcribe)
    monkeypatch.setattr("cortex.orchestrator.voice.is_hallucinated", lambda text: False)
    monkeypatch.setattr("cortex.orchestrator.voice.resolve_voice", lambda **kwargs: "test")
    monkeypatch.setattr("cortex.orchestrator.voice.synthesize_speech",
                        AsyncMock(return_value=(b"\x00\x00" * 1600, 16000, "fake")))
    monkeypatch.setattr("cortex.pipeline.layer3_llm.select_filler", lambda **kwargs: "")

    async def prepare(text, **kwargs):
        identity = TurnIdentity.from_metadata(
            kwargs["user_id"], kwargs["metadata"], satellite_id=kwargs["satellite_id"],
        )
        assert store.get(identity.turn_id, identity.user_id).transcript == text
        return PreparedTurn(
            identity, text, {"turn_identity": identity, "metadata": kwargs["metadata"],
                             "user_id": identity.user_id, "speaker_id": None, "conversation_history": []},
            DispatchResult(RouteStatus.NOT_MATCHED), 0, {"L0": 0, "L1": 0, "L2": 0},
        )
    monkeypatch.setattr("cortex.orchestrator.voice_turns.prepare_turn", prepare)
    yield SimpleNamespace(store=store, coordinator=coordinator, service=service,
                          device=device, frames=frames, transcripts=transcripts, transcribe=transcribe)
    await service.stop()
    await coordinator.stop()
    conn.close()


async def submit(env, text, number):
    env.transcripts.put_nowait(text)
    await env.service.session(env.device).submit(Capture(b"\x01\x00", str(number), True, "vad_silence"))
    await until(lambda: sum(frame["type"] == "TURN_ACCEPTED" for frame in env.frames) >= number)


async def test_three_questions_are_durable_while_first_playback_waits(voice_env):
    env = voice_env
    for number, text in enumerate(("first question", "second question", "third question"), 1):
        await submit(env, text, number)
    await until(lambda: env.store.status()["counts"] == {"completed": 3})
    assert env.service.audio_bytes == 0
    assert [frame["sequence"] for frame in env.frames if frame["type"] == "TURN_ACCEPTED"] == [1, 2, 3]
    await until(lambda: any(frame["type"] == "TTS_END" for frame in env.frames))
    assert sum(frame["type"] == "TTS_START" for frame in env.frames) == 1
    session = env.device.voice_session
    for count in range(1, 4):
        await until(lambda: sum(frame["type"] == "TTS_END" for frame in env.frames) == count)
        end = [frame for frame in env.frames if frame["type"] == "TTS_END"][-1]
        session.acknowledge({**end, "revision": 999})
        assert not session.played.is_set()
        session.acknowledge(end)
    await until(lambda: env.store.voice_delivery_head("pi-one") is None)


async def test_acoustic_pauses_keep_audio_and_final_end_is_not_duplicated(voice_env):
    env = voice_env
    env.device.activated = True
    await _handle_audio_start(env.device, {"capture_id": "slow"})
    await _handle_audio_chunk(env.device, {"audio": base64.b64encode(b"\x01\x00").decode()})
    await _handle_audio_phrase_end(env.device, {})
    await _handle_audio_phrase_end(env.device, {})
    assert env.device.audio_buffer == b"\x01\x00"
    assert env.transcribe.await_count == 0
    await _handle_audio_chunk(env.device, {"audio": base64.b64encode(b"\x02\x00").decode()})
    env.transcripts.put_nowait("How do I set a timer?")
    await _handle_audio_end(env.device, {})
    await _handle_audio_end(env.device, {})
    await until(lambda: any(frame["type"] == "TURN_ACCEPTED" for frame in env.frames))
    assert env.transcribe.await_count == 1
    assert env.transcribe.call_args.args[0] == b"\x01\x00\x02\x00"


@pytest.mark.parametrize("text", ["stop", "yes", "actually the kitchen"])
async def test_short_valid_speech_is_not_filtered_by_duration_or_repeated_wake(voice_env, text):
    await submit(voice_env, text, 1)
    assert voice_env.transcribe.call_args.args[0] == b"\x01\x00"
    assert voice_env.store.status()["counts"]


async def test_incomplete_capture_never_executes_a_command(voice_env, monkeypatch):
    env = voice_env
    prepare = AsyncMock(side_effect=AssertionError("Incomplete speech must not execute"))
    monkeypatch.setattr("cortex.orchestrator.voice_turns.prepare_turn", prepare)
    env.transcripts.put_nowait("turn on the")
    await env.service.session(env.device).submit(Capture(b"\x01\x00", "limited", True, "capture_limit"))
    await until(lambda: any(frame["type"] == "TURN_ACCEPTED" for frame in env.frames))
    assert prepare.await_count == 0
    turn = env.store.get("voice:pi-one:limited:0", "anonymous")
    assert turn.route_status == "clarification"
    assert "recording limit" in turn.result


async def test_audio_format_and_allocation_errors_are_explicit(voice_env):
    env = voice_env
    await _handle_audio_start(env.device, {"format_info": {"rate": 44100, "width": 2, "channels": 2}})
    assert not env.device.capture_active
    await _handle_audio_start(env.device, {})
    await _handle_audio_chunk(env.device, {"audio": "not base64!"})
    assert not env.device.capture_active
    assert env.device.audio_buffer == b""
    assert sum(frame["type"] == "PIPELINE_ERROR" for frame in env.frames) == 2


async def test_preparation_failure_remains_durable_and_uncertain(voice_env, monkeypatch):
    env = voice_env
    monkeypatch.setattr("cortex.orchestrator.voice_turns.prepare_turn",
                        AsyncMock(side_effect=ConnectionError("backend interrupted")))
    env.transcripts.put_nowait("turn the lamp off")
    await env.service.session(env.device).submit(Capture(b"\x01\x00", "failed", True, "vad_silence"))
    await until(lambda: any(frame["type"] == "PIPELINE_ERROR" for frame in env.frames))
    assert env.store.get("voice:pi-one:failed:0", "anonymous").state == "uncertain"
    assert not any(frame["type"] == "TURN_ACCEPTED" for frame in env.frames)
    assert env.service.audio_bytes == 0


async def test_raw_audio_capacity_rejects_new_capture_without_evicting_pending(voice_env, monkeypatch):
    env = voice_env
    gate = asyncio.Event()

    async def transcribe(*args, **kwargs):
        await gate.wait()
        return "question"

    monkeypatch.setattr("cortex.orchestrator.voice.transcribe", transcribe)
    session = env.service.session(env.device)
    await session.submit(Capture(b"\x01\x00", "1", True, "vad_silence"))
    await asyncio.sleep(0)
    for number in range(2, 5):
        await session.submit(Capture(b"\x01\x00", str(number), True, "vad_silence"))
    assert session.captures.qsize() == 2
    assert env.service.audio_bytes == 6
    assert sum(frame.get("code") == "audio_capacity" for frame in env.frames) == 1
    gate.set()
    await until(lambda: sum(frame["type"] == "TURN_ACCEPTED" for frame in env.frames) == 3)
    assert env.service.audio_bytes == 0


async def test_stt_slots_are_bounded_and_independent_of_llm_slots(voice_env, monkeypatch):
    env = voice_env
    gate = asyncio.Event()
    active = 0
    high_water = 0

    async def transcribe(*args, **kwargs):
        nonlocal active, high_water
        active += 1
        high_water = max(high_water, active)
        try:
            await gate.wait()
            return "question"
        finally:
            active -= 1

    monkeypatch.setattr("cortex.orchestrator.voice.transcribe", transcribe)
    for number in range(3):
        device = SatelliteConnection(env.device.websocket, f"pi-{number}")
        device.capabilities = ["playback_ack"]
        await env.service.session(device).submit(Capture(b"\x01\x00", "one", True, "vad_silence"))
    await until(lambda: active == 2)
    assert high_water == 2
    assert env.coordinator.queue.active_count == 0
    gate.set()
    await until(lambda: sum(frame["type"] == "TURN_ACCEPTED" for frame in env.frames) == 3)
    assert high_water == 2


async def next_audio(env, count):
    await until(lambda: sum(frame["type"] == "TTS_END" for frame in env.frames) >= count)
    return [frame for frame in env.frames if frame["type"] == "TTS_END"][count - 1]


async def test_pause_resume_starts_at_last_acknowledged_sentence(voice_env):
    env = voice_env
    await submit(env, "First sentence. Second sentence", 1)
    session = env.device.voice_session
    first = await next_audio(env, 1)
    session.acknowledge(first)
    second = await next_audio(env, 2)
    turn_id = first["turn_id"]
    before = env.store.get(turn_id, "anonymous")
    assert 0 < before.playback_position < len(before.result)
    await session.pause(second)
    assert env.store.get(turn_id, "anonymous").delivery_state == "paused"
    await submit(env, "yes!", 2)
    control = await next_audio(env, 3)
    session.acknowledge(control)
    resumed = await next_audio(env, 4)
    assert resumed["turn_id"] == turn_id and resumed["revision"] == 2
    start = [frame for frame in env.frames if frame["type"] == "TTS_START"][-1]
    assert start["text"] == "Second sentence."
    session.acknowledge(second)
    assert not session.played.is_set()
    session.acknowledge(resumed)
    await until(lambda: env.store.get(turn_id, "anonymous").delivery_state == "delivered")


async def test_stop_preserves_other_accepted_questions(voice_env):
    env = voice_env
    await submit(env, "first", 1)
    end = await next_audio(env, 1)
    await submit(env, "second", 2)
    await env.device.voice_session.pause(end)
    await submit(env, "stop!", 3)
    first = env.store.get(end["turn_id"], "anonymous")
    assert first.delivery_state == "interrupted" and first.identity.revision == 2
    second = env.store.get("voice:pi-one:2:0", "anonymous")
    assert second.state not in ("cancelled", "superseded")
    assert second.transcript == "second"
    control = await next_audio(env, 2)
    assert control["turn_id"] == "voice:pi-one:3:0"


async def test_stop_cancels_active_generation_without_cancelling_next_question(voice_env, monkeypatch):
    env = voice_env
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def chat(messages, **kwargs):
        async def chunks():
            if messages[-1]["content"] == "first":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            yield "Independent answer."
        return chunks()

    monkeypatch.setattr(env.coordinator.provider, "chat", chat)
    await submit(env, "first", 1)
    await asyncio.wait_for(started.wait(), 3)
    await submit(env, "second", 2)
    await submit(env, "stop!", 3)
    await asyncio.wait_for(cancelled.wait(), 3)
    assert env.store.get("voice:pi-one:1:0", "anonymous").state == "cancelled"
    await until(lambda: env.store.get("voice:pi-one:2:0", "anonymous").state == "completed")


async def test_acknowledgement_is_durable_before_pause_can_run(voice_env):
    env = voice_env
    await submit(env, "First sentence. Second sentence", 1)
    receipt = await next_audio(env, 1)
    session = env.device.voice_session
    session.acknowledge(receipt)
    position = env.store.get(receipt["turn_id"], "anonymous").playback_position
    assert position > 0
    await session.pause(receipt)
    assert env.store.get(receipt["turn_id"], "anonymous").playback_position == position


@pytest.mark.parametrize("capture_id", ["", "x" * 129, 42, None, " trailing ", "\n"])
async def test_invalid_capture_identity_is_rejected(voice_env, capture_id):
    env = voice_env
    await _handle_audio_start(env.device, {"capture_id": capture_id})
    assert not env.device.capture_active
    assert env.frames[-1]["type"] == "PIPELINE_ERROR"


async def test_ambiguous_never_mind_asks_without_draining_backlog(voice_env):
    env = voice_env
    await submit(env, "first", 1)
    end = await next_audio(env, 1)
    await submit(env, "second", 2)
    await env.device.voice_session.pause(end)
    await submit(env, "never mind.", 3)
    turn = env.store.get("voice:pi-one:3:0", "anonymous")
    assert turn.route_status == "clarification"
    assert env.store.get(end["turn_id"], "anonymous").delivery_state == "paused"
    assert env.store.get("voice:pi-one:2:0", "anonymous").state != "cancelled"


async def test_pivot_invalidates_old_audio_but_not_other_room(voice_env):
    env = voice_env
    await submit(env, "first", 1)
    old = await next_audio(env, 1)
    other = SatelliteConnection(env.device.websocket, "other-room")
    other.capabilities = ["playback_ack"]
    env.transcripts.put_nowait("another room")
    await env.service.session(other).submit(Capture(b"\x01\x00", "other", True, "vad_silence"))
    await until(lambda: env.store.get(old["turn_id"], "anonymous").delivery_state == "delivering")
    await env.device.voice_session.pause(old)
    await submit(env, "a new question", 3)
    assert env.store.get(old["turn_id"], "anonymous").delivery_state == "interrupted"
    await until(lambda: env.store.get("voice:other-room:other:0", "anonymous").state == "completed")
    assert env.store.get("voice:other-room:other:0", "anonymous").delivery_state != "interrupted"


async def test_stale_pause_requires_explicit_confirmation(voice_env):
    env = voice_env
    await submit(env, "first", 1)
    old = await next_audio(env, 1)
    session = env.device.voice_session
    await session.pause(old)
    session.paused_at -= 60
    await submit(env, "yes", 2)
    assert "confirm" in env.store.get("voice:pi-one:2:0", "anonymous").result
    assert env.store.get(old["turn_id"], "anonymous").delivery_state == "paused"
    await submit(env, "resume the previous response", 3)
    assert env.store.get(old["turn_id"], "anonymous").identity.revision == 2


async def test_local_stop_precedes_slow_server_and_stale_frames_are_ignored():
    from unittest.mock import MagicMock
    from satellite.atlas_satellite.agent import SatelliteAgent, State
    from satellite.atlas_satellite.config import SatelliteConfig

    agent = SatelliteAgent(SatelliteConfig())
    gate = asyncio.Event()
    agent.audio_out = SimpleNamespace(stop=MagicMock())
    agent.vad = SimpleNamespace(reset=MagicMock())
    agent.led = SimpleNamespace(set_pattern=MagicMock())
    async def slow_server(*args):
        await gate.wait()

    agent.ws = SimpleNamespace(connected=True, send_barge_in=AsyncMock(side_effect=slow_server),
                               send_wake=AsyncMock(), send_audio_start=AsyncMock(), send_status=AsyncMock())
    metadata = {"turn_id": "old", "revision": 1, "playback_id": "old-play"}
    agent._tts_metadata = metadata
    agent.state = State.SPEAKING
    agent._playback_task = asyncio.create_task(asyncio.Event().wait())
    barge = asyncio.create_task(agent._handle_barge_in())
    await until(lambda: agent.audio_out.stop.called)
    await until(lambda: agent._playback_task.done())
    assert not barge.done()
    gate.set()
    await asyncio.wait_for(barge, 2)
    await agent._on_tts_start({**metadata, "sample_rate": 16000})
    await agent._on_tts_chunk({**metadata, "audio": base64.b64encode(b"\x01\x00").decode()})
    assert agent._tts_buffer == b""
    assert agent.state == State.LISTENING
