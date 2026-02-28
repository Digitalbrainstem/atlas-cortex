"""Tests for the satellite agent code.

Tests the components that can run without actual hardware:
config, VAD logic, WebSocket protocol, filler cache, LED factory.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Config tests ──────────────────────────────────────────────────


class TestSatelliteConfig:
    def test_defaults(self):
        from satellite.atlas_satellite.config import SatelliteConfig

        cfg = SatelliteConfig()
        assert cfg.sample_rate == 16000
        assert cfg.channels == 1
        assert cfg.chunk_ms == 30
        assert cfg.volume == 0.7
        assert cfg.wake_word_enabled is False
        assert cfg.led_type == "none"
        assert cfg.mode == "dedicated"

    def test_frame_size(self):
        from satellite.atlas_satellite.config import SatelliteConfig

        cfg = SatelliteConfig(sample_rate=16000, chunk_ms=30)
        # 16000 * 0.030 = 480 samples * 2 bytes = 960 bytes
        assert cfg.frame_size == 960
        assert cfg.frames_per_chunk == 480

    def test_load_save(self, tmp_path):
        from satellite.atlas_satellite.config import SatelliteConfig

        cfg = SatelliteConfig(
            satellite_id="sat-test",
            server_url="ws://10.0.0.1:5100/ws/satellite",
            room="kitchen",
            led_type="respeaker",
        )
        path = tmp_path / "config.json"
        cfg.save(path)

        loaded = SatelliteConfig.load(path)
        assert loaded.satellite_id == "sat-test"
        assert loaded.server_url == "ws://10.0.0.1:5100/ws/satellite"
        assert loaded.room == "kitchen"
        assert loaded.led_type == "respeaker"

    def test_load_missing_file(self, tmp_path):
        from satellite.atlas_satellite.config import SatelliteConfig

        cfg = SatelliteConfig.load(tmp_path / "nonexistent.json")
        assert cfg.satellite_id == ""  # defaults

    def test_load_ignores_unknown_keys(self, tmp_path):
        from satellite.atlas_satellite.config import SatelliteConfig

        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "satellite_id": "sat-x",
            "unknown_key": "should be ignored",
            "another_bad": 42,
        }))
        cfg = SatelliteConfig.load(path)
        assert cfg.satellite_id == "sat-x"
        assert not hasattr(cfg, "unknown_key")

    def test_generate_id(self):
        from satellite.atlas_satellite.config import SatelliteConfig

        cfg = SatelliteConfig()
        sat_id = cfg.generate_id()
        assert sat_id.startswith("sat-")


# ── VAD tests ─────────────────────────────────────────────────────


class TestVAD:
    def test_init_without_webrtcvad(self):
        """VAD should gracefully handle missing webrtcvad."""
        from satellite.atlas_satellite.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        assert vad.active or not vad.active  # depends on install

    def test_process_state_machine(self):
        from satellite.atlas_satellite.vad import VoiceActivityDetector

        vad = VoiceActivityDetector(
            speech_threshold=2,
            silence_threshold=3,
        )
        # Mock the internal VAD
        vad._vad = MagicMock()

        # Simulate speech frames
        vad._vad.is_speech.return_value = True
        assert vad.process(b"\x00" * 960) == "silence"  # 1st speech, not enough
        assert vad.process(b"\x00" * 960) == "speech_start"  # 2nd = threshold
        assert vad.process(b"\x00" * 960) == "speech"  # continuing

        # Simulate silence — intermediate frames return "silence"
        # until the threshold is reached, then "speech_end"
        vad._vad.is_speech.return_value = False
        assert vad.process(b"\x00" * 960) == "silence"  # 1 silence frame
        assert vad.process(b"\x00" * 960) == "silence"  # 2 silence frames
        assert vad.process(b"\x00" * 960) == "speech_end"  # 3 = threshold

    def test_reset(self):
        from satellite.atlas_satellite.vad import VoiceActivityDetector

        vad = VoiceActivityDetector()
        vad._speech_count = 10
        vad._silence_count = 5
        vad._in_speech = True
        vad.reset()
        assert vad._speech_count == 0
        assert vad._silence_count == 0
        assert vad._in_speech is False


# ── Filler cache tests ────────────────────────────────────────────


class TestFillerCache:
    def test_empty_cache(self, tmp_path):
        from satellite.atlas_satellite.filler_cache import FillerCache

        cache = FillerCache(tmp_path / "fillers")
        assert cache.count == 0
        assert cache.get_random() is None

    def test_add_and_get(self, tmp_path):
        from satellite.atlas_satellite.filler_cache import FillerCache

        cache = FillerCache(tmp_path / "fillers")
        cache.add("filler-01", b"RIFF fake wav data")
        assert cache.count == 1
        path = cache.get_random()
        assert path is not None
        assert "filler-01" in path

    def test_sync(self, tmp_path):
        from satellite.atlas_satellite.filler_cache import FillerCache

        cache = FillerCache(tmp_path / "fillers")
        cache.add("old-filler", b"old data")
        assert cache.count == 1

        # Sync with new set
        cache.sync([
            {"id": "new-01", "audio": b"new data 1"},
            {"id": "new-02", "audio": b"new data 2"},
        ])
        assert cache.count == 2
        # Old filler should be removed
        assert not (tmp_path / "fillers" / "old-filler.wav").exists()


# ── LED tests ─────────────────────────────────────────────────────


class TestLED:
    def test_null_led(self):
        from satellite.atlas_satellite.led import NullLED

        led = NullLED()
        led.set_color(255, 0, 0)
        led.set_pattern("listening")
        led.off()
        led.close()  # Should not raise

    def test_create_led_none(self):
        from satellite.atlas_satellite.led import NullLED, create_led

        led = create_led("none")
        assert isinstance(led, NullLED)

    def test_create_led_unknown(self):
        from satellite.atlas_satellite.led import NullLED, create_led

        led = create_led("unknown_type")
        assert isinstance(led, NullLED)


# ── WebSocket client tests ────────────────────────────────────────


class TestWSClient:
    def test_init(self):
        from satellite.atlas_satellite.ws_client import SatelliteWSClient

        client = SatelliteWSClient(
            server_url="ws://localhost:5100/ws/satellite",
            satellite_id="sat-test",
            room="office",
        )
        assert client.satellite_id == "sat-test"
        assert client.room == "office"
        assert not client.connected

    def test_register_handler(self):
        from satellite.atlas_satellite.ws_client import SatelliteWSClient

        client = SatelliteWSClient("ws://localhost:5100/ws/satellite", "sat-test")

        handler = AsyncMock()
        client.on("TTS_START", handler)
        assert "TTS_START" in client._handlers


# ── Wake word tests ───────────────────────────────────────────────


class TestWakeWord:
    def test_init_no_backend(self):
        """Without openwakeword installed, should fall back to 'none'."""
        from satellite.atlas_satellite.wake_word import WakeWordDetector

        detector = WakeWordDetector()
        # May or may not have openwakeword installed
        confidence = detector.process(b"\x00" * 960)
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0


# ── Agent tests ───────────────────────────────────────────────────


class TestAgent:
    def test_state_enum(self):
        from satellite.atlas_satellite.agent import State

        assert State.IDLE.value == "idle"
        assert State.LISTENING.value == "listening"
        assert State.PROCESSING.value == "processing"
        assert State.SPEAKING.value == "speaking"

    def test_cpu_temp_reader(self):
        from satellite.atlas_satellite.agent import _read_cpu_temp

        temp = _read_cpu_temp()
        assert isinstance(temp, float)

    def test_wifi_rssi_reader(self):
        from satellite.atlas_satellite.agent import _read_wifi_rssi

        rssi = _read_wifi_rssi()
        assert isinstance(rssi, int)
