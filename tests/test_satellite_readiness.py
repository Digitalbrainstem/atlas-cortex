"""Software readiness contracts; these are not ReSpeaker qualification."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.db import _create_schema
from cortex.orchestrator.turn_store import TurnStore, TurnStorePolicy
from cortex.pipeline.turns import DispatchResult, RouteStatus, TurnIdentity
from cortex.satellite import websocket as protocol
from cortex.satellite.manager import SatelliteManager
from satellite.atlas_satellite.agent import SatelliteAgent, State
from satellite.atlas_satellite.button import ButtonHandler
from satellite.atlas_satellite.config import SatelliteConfig
from satellite.atlas_satellite.led import NullLED, ReSpeakerLED
from satellite.atlas_satellite.wake_word import WakeWordDetector


async def wait_for(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 3)


@pytest.fixture
def agent(tmp_path):
    instance = SatelliteAgent(SatelliteConfig(satellite_id="pi", wake_word_enabled=False))
    instance._base_dir = tmp_path
    instance.led = NullLED()
    instance.button = ButtonHandler()
    instance.audio_in = MagicMock(available=True)
    instance.audio_out = MagicMock()
    instance.vad = MagicMock()
    instance.ws = SimpleNamespace(
        connected=True, send_heartbeat=AsyncMock(), send_cmd_ack=AsyncMock(),
        send_wake=AsyncMock(), send_audio_start=AsyncMock(), send_status=AsyncMock(),
        send_audio_end=AsyncMock(),
    )
    return instance


@pytest.mark.parametrize("mode", ["toggle", "press", "hold"])
async def test_button_configuration_is_validated_applied_and_persisted(agent, mode):
    await agent._on_config({"button_mode": mode, "room": "study", "vad_sensitivity": 3})
    assert agent.button.mode == mode
    assert agent.ws.room == "study"
    agent.vad.set_aggressiveness.assert_called_once_with(3)
    loaded = SatelliteConfig.load(agent._base_dir / "config.json")
    assert loaded.button_mode == mode and loaded.room == "study"
    before = (agent._base_dir / "config.json").read_bytes()
    await agent._on_server_config({"button_mode": "invalid"})
    assert agent.config.button_mode == mode
    assert (agent._base_dir / "config.json").read_bytes() == before
    assert "button_mode" in agent.readiness()["config_error"]


async def test_missing_wake_does_not_silently_activate_on_arbitrary_speech(agent, monkeypatch):
    agent.config.wake_word_enabled = True
    detector = WakeWordDetector(model_path=str(agent._base_dir / "missing.onnx"))
    agent.wake_word = detector
    agent.vad.process.return_value = "speech_start"
    await agent._process_idle(b"\0\0" * 480)
    assert agent.state == State.IDLE
    assert agent.readiness()["wake"] == "model_missing"
    assert "wake_word" not in agent._detect_capabilities()
    await agent._on_button_press()
    assert agent.state == State.LISTENING
    agent.ws.send_audio_start.assert_awaited_once()


async def test_microphone_failure_is_visible_and_cannot_accept_a_button_capture(agent):
    agent.audio_in.available = False
    await agent._on_button_press()
    assert agent.state == State.IDLE
    assert agent.readiness()["microphone"] is False
    assert agent.led._primary_state == "error"
    agent.ws.send_audio_start.assert_not_awaited()


async def test_cold_wake_loading_does_not_block_button_or_readiness(agent, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def initialize(detector):
        started.set()
        assert release.wait(3)
        detector.status = "warming"

    monkeypatch.setattr(WakeWordDetector, "initialize", initialize)
    agent.config.wake_word_enabled = True
    agent.wake_word = WakeWordDetector(initialize=False)
    agent._schedule_wake_initialization()
    try:
        await wait_for(started.is_set)
        assert agent.readiness()["wake"] == "loading"
        await agent._on_button_press()
        assert agent.state == State.LISTENING
    finally:
        release.set()
        await agent._wake_task
    assert agent.readiness()["wake"] == "warming"


async def test_muting_stops_playback_and_cancels_unaccepted_capture(agent):
    agent.state = State.LISTENING
    await agent._on_button_toggle(False)
    agent.audio_out.stop.assert_called_once()
    agent.ws.send_audio_end.assert_awaited_once_with(reason="cancelled")
    assert agent.state == State.MUTED and agent._capture_done.is_set()


def test_wake_warms_only_on_target_prediction_and_reports_backend_failure(monkeypatch):
    detector = MagicMock()
    detector.models = {"hey_atlas": object(), "unrelated": object()}
    detector.predict.return_value = {"hey_atlas": 0.2, "unrelated": 0.99}
    module = ModuleType("openwakeword.model")
    module.Model = MagicMock(return_value=detector)
    monkeypatch.setitem(sys.modules, "openwakeword", ModuleType("openwakeword"))
    monkeypatch.setitem(sys.modules, "openwakeword.model", module)
    monkeypatch.setitem(sys.modules, "onnxruntime", ModuleType("onnxruntime"))
    wake = WakeWordDetector(wake_word="hey atlas")
    assert wake.status == "warming" and wake.available
    assert wake.process(b"\0\0" * 1280) == 0.2
    assert wake.status == "ready"
    detector.predict.side_effect = RuntimeError("backend failed")
    assert wake.process(b"\0\0" * 1280) == 0
    assert wake.status == "error" and not wake.available
    detector.models = {"unrelated": object()}
    missing = WakeWordDetector(wake_word="hey atlas")
    assert missing.status == "model_missing" and not missing.available


async def test_three_leds_and_no_led_keep_listening_separate_from_processing(agent, monkeypatch):
    leds = ReSpeakerLED(3)
    writes = []
    monkeypatch.setattr(leds, "_write_leds_per_pixel", lambda pixels: writes.append(pixels))
    monkeypatch.setattr(leds, "_start_pulse_activity", lambda color: None)
    leds.set_pattern("listening")
    leds.set_activity("processing")
    assert writes[-1][0] == leds.patterns["processing"]
    assert writes[-1][1:] == [leds.patterns["listening"]] * 2
    leds.close()
    agent.led.set_pattern("listening")
    await agent._on_pipeline_status({"processing": True})
    assert agent.led._primary_state == "listening"
    assert agent.led._activity_state == "processing"
    assert agent.readiness()["led_count"] == 0


class Socket:
    def __init__(self, satellite_id, room="kitchen"):
        self.announce = {"type": "ANNOUNCE", "satellite_id": satellite_id, "room": room}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.inbox = asyncio.Queue()
        self.frames = []

    async def accept(self):
        pass

    async def receive_json(self):
        return self.announce

    async def send_json(self, message):
        self.frames.append(message)

    async def iter_json(self):
        while (message := await self.inbox.get()) is not None:
            yield message

    async def close(self, **kwargs):
        await self.inbox.put(None)


@pytest.fixture
async def network(tmp_path, monkeypatch):
    db = sqlite3.connect(tmp_path / "satellites.db")
    db.row_factory = sqlite3.Row
    _create_schema(db)
    monkeypatch.setattr(protocol, "get_db", lambda: db)
    monkeypatch.setattr("cortex.satellite.manager.get_db", lambda: db)
    monkeypatch.setattr(protocol, "_connected_satellites", {})
    tasks = []

    def connect(satellite_id, room="kitchen"):
        socket = Socket(satellite_id, room)
        tasks.append(asyncio.create_task(protocol.satellite_ws_handler(socket)))
        return socket

    yield SimpleNamespace(db=db, connect=connect, tasks=tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    db.close()


@pytest.mark.parametrize("mode", ["toggle", "press", "hold"])
async def test_saved_config_survives_offline_changes_and_duplicate_reconnect(network, mode):
    first = network.connect("pi")
    await wait_for(lambda: any(frame["type"] == "ACCEPTED" for frame in first.frames))
    manager = SatelliteManager()
    await first.close()
    await network.tasks[0]
    await manager.reconfigure("pi", room="study", button_mode=mode, vad_sensitivity=0.5)
    replacement = network.connect("pi", "stale-room")
    await wait_for(lambda: any(frame["type"] == "CONFIG" for frame in replacement.frames))
    configuration = next(frame for frame in replacement.frames if frame["type"] == "CONFIG")
    assert configuration["room"] == "study" and configuration["button_mode"] == mode
    assert configuration["vad_sensitivity"] == 2
    newer = network.connect("pi", "another-stale-room")
    await wait_for(lambda: any(frame["type"] == "CONFIG" for frame in newer.frames))
    await asyncio.wait_for(network.tasks[1], 3)
    assert protocol.get_connection("pi").websocket is newer
    assert manager.get_satellite("pi")["status"] == "online"
    await newer.inbox.put({"type": "HEARTBEAT", "readiness": {"button_mode": mode, "wake": "warming"}})
    await wait_for(lambda: manager.get_satellite("pi")["readiness"].get("wake") == "warming")


async def test_two_devices_have_independent_room_and_offline_state(network):
    first, second = network.connect("one"), network.connect("two", "bedroom")
    await wait_for(lambda: len(protocol.get_connected_satellites()) == 2)
    await protocol.send_config("one", {"button_mode": "hold"})
    assert any(frame.get("button_mode") == "hold" for frame in first.frames)
    assert not any(frame.get("button_mode") == "hold" for frame in second.frames)
    await first.close()
    await network.tasks[0]
    manager = SatelliteManager()
    assert manager.get_satellite("one")["readiness"] == {"connection": "offline"}
    assert manager.get_satellite("two")["is_connected"]
    assert protocol.get_connection("two").room == "bedroom"


def test_playback_checkpoint_survives_database_reopen(tmp_path):
    path = tmp_path / "playback.db"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    _create_schema(db)
    store = TurnStore(db, TurnStorePolicy(min_free_disk_bytes=1))
    identity = TurnIdentity("receipt", "conversation", "household", source="satellite", device_id="pi")
    store.accept(identity, "question", input_validated=True)
    store.claim("receipt", "household", "owner")
    store.complete("receipt", "household", "owner", 1,
                   DispatchResult(RouteStatus.HANDLED, "First sentence. Second sentence."),
                   output_validated=True)
    store.set_delivery("receipt", "household", 1, "delivering")
    store.checkpoint_playback("receipt", "household", 1, len("First sentence."))
    db.close()
    reopened = sqlite3.connect(path)
    reopened.row_factory = sqlite3.Row
    try:
        turn = TurnStore(reopened, TurnStorePolicy(min_free_disk_bytes=1)).get("receipt", "household")
        assert turn.playback_position == len("First sentence.")
        assert turn.result == "First sentence. Second sentence."
    finally:
        reopened.close()
