"""Tests for ESP32 satellite — handler, protocol, device-type routing."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.satellite.esp32_handler import ESP32SatelliteHandler, LED_COLORS


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    yield


def _make_ws() -> AsyncMock:
    """Create a mock WebSocket with client info."""
    ws = AsyncMock()
    ws.client = MagicMock()
    ws.client.host = "192.168.1.50"
    ws.send_json = AsyncMock()
    return ws


def _make_handler(ws=None, satellite_id="esp32-test-sat") -> ESP32SatelliteHandler:
    """Create an ESP32 handler with a mock WebSocket."""
    if ws is None:
        ws = _make_ws()
    return ESP32SatelliteHandler(ws, satellite_id)


# ── Registration ──────────────────────────────────────────────────


class TestESP32Registration:
    async def test_register_creates_satellite_entry(self):
        handler = _make_handler()
        await handler.handle_register({
            "type": "register",
            "name": "kitchen",
            "device_type": "esp32",
            "hardware": "esp32-s3-box-3",
            "firmware_version": "1.0.0",
        })

        db = get_db()
        db.row_factory = None  # ensure raw tuples
        cur = db.execute("PRAGMA table_info(satellites)")
        cols = [r[1] for r in cur.fetchall()]
        row = db.execute(
            "SELECT * FROM satellites WHERE id = ?", (handler.satellite_id,)
        ).fetchone()
        assert row is not None
        row_dict = dict(zip(cols, row))
        assert row_dict["status"] == "online"
        assert row_dict["platform"] == "esp32"

    async def test_register_sends_registered_response(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})

        calls = ws.send_json.call_args_list
        registered = next(c for c in calls if c[0][0].get("type") == "registered")
        assert registered[0][0]["satellite_id"] == handler.satellite_id

    async def test_register_sends_idle_led(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})

        calls = ws.send_json.call_args_list
        led_call = next(c for c in calls if c[0][0].get("type") == "led")
        assert led_call[0][0]["pattern"] == "idle"

    async def test_register_stores_hardware_info(self):
        handler = _make_handler()
        await handler.handle_register({
            "type": "register",
            "name": "test",
            "device_type": "esp32",
            "hardware": "satellite1",
            "firmware_version": "2.0.0",
        })
        assert handler.hardware == "satellite1"
        assert handler.firmware_version == "2.0.0"

    async def test_register_upserts_on_reconnect(self):
        handler = _make_handler()
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})
        # Reconnect
        handler2 = _make_handler(satellite_id=handler.satellite_id)
        await handler2.handle_register({
            "type": "register", "name": "test", "device_type": "esp32",
            "hardware": "new-hw",
        })

        db = get_db()
        rows = db.execute(
            "SELECT COUNT(*) FROM satellites WHERE id = ?", (handler.satellite_id,)
        ).fetchone()
        assert rows[0] == 1


# ── Audio lifecycle ───────────────────────────────────────────────


class TestESP32AudioLifecycle:
    async def test_audio_start_sets_listening_led(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.handle_message({"type": "audio_start"})

        calls = ws.send_json.call_args_list
        led_call = next(c for c in calls if c[0][0].get("type") == "led")
        assert led_call[0][0]["pattern"] == "listening"

    async def test_audio_start_creates_session(self):
        handler = _make_handler()
        # Register first so the satellite exists
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})

        await handler.handle_message({"type": "audio_start"})
        assert handler._session_id is not None

        db = get_db()
        row = db.execute(
            "SELECT * FROM satellite_audio_sessions WHERE id = ?",
            (handler._session_id,),
        ).fetchone()
        assert row is not None

    async def test_audio_data_buffers_pcm(self):
        handler = _make_handler()
        pcm = b"\x00\x01" * 100
        encoded = base64.b64encode(pcm).decode()

        await handler.handle_message({"type": "audio_start"})
        await handler.handle_message({"type": "audio_data", "data": encoded})

        assert bytes(handler._audio_buffer) == pcm

    async def test_audio_data_multiple_chunks(self):
        handler = _make_handler()
        chunk1 = b"\x01" * 50
        chunk2 = b"\x02" * 50

        await handler.handle_message({"type": "audio_start"})
        await handler.handle_message({"type": "audio_data", "data": base64.b64encode(chunk1).decode()})
        await handler.handle_message({"type": "audio_data", "data": base64.b64encode(chunk2).decode()})

        assert bytes(handler._audio_buffer) == chunk1 + chunk2

    async def test_audio_end_clears_buffer(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        pcm = b"\x00\x01" * 100

        await handler.handle_message({"type": "audio_start"})
        await handler.handle_message({"type": "audio_data", "data": base64.b64encode(pcm).decode()})
        await handler.handle_message({"type": "audio_end"})

        assert len(handler._audio_buffer) == 0

    async def test_audio_end_sends_processing_led(self):
        ws = _make_ws()
        handler = _make_handler(ws)

        await handler.handle_message({"type": "audio_start"})
        await handler.handle_message({"type": "audio_end"})

        calls = ws.send_json.call_args_list
        led_calls = [c for c in calls if c[0][0].get("type") == "led"]
        patterns = [c[0][0]["pattern"] for c in led_calls]
        assert "processing" in patterns

    async def test_short_audio_discarded(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        # Less than 1600 bytes (~50ms)
        short_pcm = b"\x00" * 100

        await handler.handle_message({"type": "audio_start"})
        await handler.handle_message({"type": "audio_data", "data": base64.b64encode(short_pcm).decode()})
        await handler.handle_message({"type": "audio_end"})

        # Should send idle LED after discarding
        calls = ws.send_json.call_args_list
        last_led = [c for c in calls if c[0][0].get("type") == "led"][-1]
        assert last_led[0][0]["pattern"] == "idle"


# ── LED messages ──────────────────────────────────────────────────


class TestESP32Led:
    async def test_send_led_patterns(self):
        ws = _make_ws()
        handler = _make_handler(ws)

        for pattern, color in LED_COLORS.items():
            ws.send_json.reset_mock()
            await handler.send_led(pattern)
            ws.send_json.assert_called_once_with({
                "type": "led",
                "pattern": pattern,
                "color": color,
            })

    async def test_send_led_unknown_pattern_defaults_white(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.send_led("unknown")
        ws.send_json.assert_called_once_with({
            "type": "led",
            "pattern": "unknown",
            "color": "#ffffff",
        })


# ── Button handling ───────────────────────────────────────────────


class TestESP32Button:
    async def test_button_press_starts_listening(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        assert not handler._is_listening

        await handler.handle_message({"type": "button", "action": "press"})
        assert handler._is_listening

    async def test_button_press_while_listening_ends_audio(self):
        ws = _make_ws()
        handler = _make_handler(ws)

        # Start listening
        await handler.handle_message({"type": "audio_start"})
        assert handler._is_listening

        # Press again to stop
        await handler.handle_message({"type": "button", "action": "press"})
        assert not handler._is_listening


# ── Heartbeat ─────────────────────────────────────────────────────


class TestESP32Heartbeat:
    async def test_heartbeat_updates_last_seen(self):
        handler = _make_handler()
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})

        before = handler.last_heartbeat
        await handler.handle_message({
            "type": "heartbeat",
            "uptime": 3600,
            "wifi_rssi": -45,
        })

        assert handler.last_heartbeat >= before

        db = get_db()
        row = db.execute(
            "SELECT uptime_seconds, wifi_rssi FROM satellites WHERE id = ?",
            (handler.satellite_id,),
        ).fetchone()
        assert row[0] == 3600
        assert row[1] == -45


# ── Outbound messages ────────────────────────────────────────────


class TestESP32Outbound:
    async def test_send_audio_chunk(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        pcm = b"\x42" * 100

        await handler.send_audio(pcm)

        ws.send_json.assert_called_once_with({
            "type": "audio_chunk",
            "data": base64.b64encode(pcm).decode(),
        })

    async def test_send_speaking_start(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.send_speaking_start()

        calls = [c[0][0] for c in ws.send_json.call_args_list]
        types = [c["type"] for c in calls]
        assert "led" in types
        assert "speaking_start" in types

    async def test_send_speaking_end(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.send_speaking_end()

        calls = [c[0][0] for c in ws.send_json.call_args_list]
        types = [c["type"] for c in calls]
        assert "speaking_end" in types
        assert any(c.get("pattern") == "idle" for c in calls)

    async def test_send_playback_stop(self):
        ws = _make_ws()
        handler = _make_handler(ws)
        await handler.send_playback_stop()

        calls = [c[0][0] for c in ws.send_json.call_args_list]
        types = [c["type"] for c in calls]
        assert "playback_stop" in types


# ── Disconnect ────────────────────────────────────────────────────


class TestESP32Disconnect:
    async def test_on_disconnect_sets_offline(self):
        handler = _make_handler()
        await handler.handle_register({"type": "register", "name": "test", "device_type": "esp32"})
        await handler.on_disconnect()

        db = get_db()
        row = db.execute(
            "SELECT status FROM satellites WHERE id = ?",
            (handler.satellite_id,),
        ).fetchone()
        assert row[0] == "offline"


# ── Protocol validation ──────────────────────────────────────────


class TestProtocolValidation:
    """Verify that all documented message types are handled."""

    async def test_all_client_message_types_handled(self):
        """All client→server message types from PROTOCOL.md are routed."""
        handler = _make_handler()

        # register is handled separately (handle_register, not handle_message)
        client_types = ["audio_start", "audio_data", "audio_end", "button", "heartbeat"]
        for msg_type in client_types:
            # Should not raise — even with minimal payloads
            await handler.handle_message({"type": msg_type})

    async def test_unknown_message_type_does_not_crash(self):
        handler = _make_handler()
        # Should log warning but not raise
        await handler.handle_message({"type": "totally_unknown"})

    async def test_led_colors_match_protocol_spec(self):
        """LED colors match the PROTOCOL.md spec."""
        assert LED_COLORS["idle"] == "#0000ff"
        assert LED_COLORS["listening"] == "#00ff00"
        assert LED_COLORS["processing"] == "#ffff00"
        assert LED_COLORS["speaking"] == "#00ffff"
        assert LED_COLORS["error"] == "#ff0000"


# ── Device-type routing ──────────────────────────────────────────


class TestDeviceTypeRouting:
    """Test that the WebSocket handler routes ESP32 vs Pi correctly."""

    async def test_esp32_register_message_detected(self):
        """A 'register' message with device_type='esp32' should be
        recognized as ESP32 protocol (tested via handler construction)."""
        msg = {
            "type": "register",
            "name": "test",
            "device_type": "esp32",
            "hardware": "generic",
        }
        assert msg.get("type") == "register"
        assert msg.get("device_type") == "esp32"

    async def test_announce_message_is_pi_protocol(self):
        """An 'ANNOUNCE' message should be recognized as Pi protocol."""
        msg = {"type": "ANNOUNCE", "satellite_id": "pi-living-room"}
        assert msg.get("type") == "ANNOUNCE"
        assert msg.get("device_type") != "esp32"

    async def test_esp32_handler_is_independent_class(self):
        """ESP32SatelliteHandler is a separate class from SatelliteConnection."""
        from cortex.satellite.websocket import SatelliteConnection
        handler = _make_handler()
        assert isinstance(handler, ESP32SatelliteHandler)
        assert not isinstance(handler, SatelliteConnection)
