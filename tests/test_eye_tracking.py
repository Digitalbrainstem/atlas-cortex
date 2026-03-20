"""Tests for cortex.avatar.eye_tracking."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from cortex.avatar.eye_tracking import (
    EyeTracker,
    _clamp,
    _find_entity,
    _parse_position,
    get_eye_tracker,
)


# ── Helper tests ──────────────────────────────────────────────────


class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5) == 0.5

    def test_negative_within_range(self):
        assert _clamp(-0.3) == -0.3

    def test_above_one(self):
        assert _clamp(2.5) == 1.0

    def test_below_neg_one(self):
        assert _clamp(-3.0) == -1.0

    def test_boundary_values(self):
        assert _clamp(1.0) == 1.0
        assert _clamp(-1.0) == -1.0

    def test_zero(self):
        assert _clamp(0.0) == 0.0


class TestFindEntity:
    def test_found(self):
        states = [
            {"entity_id": "sensor.a", "state": "on"},
            {"entity_id": "sensor.b", "state": "off", "attributes": {"x": 1}},
        ]
        result = _find_entity(states, "sensor.b")
        assert result is not None
        assert result["state"] == "off"

    def test_not_found(self):
        states = [{"entity_id": "sensor.a", "state": "on"}]
        assert _find_entity(states, "sensor.missing") is None

    def test_empty_list(self):
        assert _find_entity([], "sensor.a") is None


class TestParsePosition:
    def test_coordinate_format(self):
        attrs = {"x": 0.5, "y": -0.3}
        assert _parse_position(attrs) == (0.5, -0.3)

    def test_coordinate_as_strings(self):
        attrs = {"x": "0.7", "y": "-0.2"}
        assert _parse_position(attrs) == (0.7, -0.2)

    def test_zone_format_default_max(self):
        # zone 2 of 4 → (2/4)*2-1 = 0.0
        attrs = {"zone_x": 2, "zone_y": 0}
        x, y = _parse_position(attrs)
        assert x == pytest.approx(0.0, abs=1e-6)
        assert y == pytest.approx(-1.0, abs=1e-6)

    def test_zone_format_custom_max(self):
        attrs = {"zone_x": 5, "zone_y": 5, "max_zone": 5}
        x, y = _parse_position(attrs)
        assert x == pytest.approx(1.0, abs=1e-6)
        assert y == pytest.approx(1.0, abs=1e-6)

    def test_angle_format_zero(self):
        attrs = {"angle": 0}
        x, y = _parse_position(attrs)
        assert x == pytest.approx(0.0, abs=1e-6)
        assert y == 0.0

    def test_angle_format_90(self):
        attrs = {"angle": 90}
        x, y = _parse_position(attrs)
        assert x == pytest.approx(1.0, abs=1e-6)
        assert y == 0.0

    def test_angle_format_neg90(self):
        attrs = {"angle": -90}
        x, y = _parse_position(attrs)
        assert x == pytest.approx(-1.0, abs=1e-6)

    def test_no_recognised_format(self):
        assert _parse_position({"brightness": 200}) is None

    def test_empty_attrs(self):
        assert _parse_position({}) is None


# ── EyeTracker tests ─────────────────────────────────────────────


class TestEyeTrackerNoSensor:
    async def test_no_sensor_no_polling(self):
        tracker = EyeTracker()
        tracker.sensor_entity = ""
        await tracker.start()
        assert tracker._task is None
        assert not tracker._running

    async def test_no_sensor_stop_safe(self):
        tracker = EyeTracker()
        tracker.sensor_entity = ""
        await tracker.stop()  # should not raise


class TestEyeTrackerBroadcast:
    async def test_broadcast_tracking_target(self):
        tracker = EyeTracker()
        with patch(
            "cortex.avatar.broadcast.broadcast_to_avatars", new_callable=AsyncMock
        ) as mock_broadcast:
            await tracker._broadcast_target((0.5, -0.3))
            mock_broadcast.assert_called_once_with(
                {"type": "eye_target", "x": 0.5, "y": -0.3, "tracking": True}
            )

    async def test_broadcast_none_target(self):
        tracker = EyeTracker()
        with patch(
            "cortex.avatar.broadcast.broadcast_to_avatars", new_callable=AsyncMock
        ) as mock_broadcast:
            await tracker._broadcast_target(None)
            mock_broadcast.assert_called_once_with(
                {"type": "eye_target", "x": 0, "y": 0, "tracking": False}
            )

    async def test_broadcast_clamps_values(self):
        tracker = EyeTracker()
        with patch(
            "cortex.avatar.broadcast.broadcast_to_avatars", new_callable=AsyncMock
        ) as mock_broadcast:
            await tracker._broadcast_target((5.0, -3.0))
            call_args = mock_broadcast.call_args[0][0]
            assert call_args["x"] == 1.0
            assert call_args["y"] == -1.0
            assert call_args["tracking"] is True


class TestEyeTrackerSensorPosition:
    async def test_coordinate_sensor(self):
        tracker = EyeTracker()
        tracker.sensor_entity = "sensor.mmwave_position"

        mock_client = AsyncMock()
        mock_client.get_states.return_value = [
            {
                "entity_id": "sensor.mmwave_position",
                "state": "detected",
                "attributes": {"x": 0.3, "y": -0.5},
            }
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ", {"HA_URL": "http://ha:8123", "HA_TOKEN": "test"}
        ), patch(
            "cortex.integrations.ha.client.HAClient", return_value=mock_client
        ):
            result = await tracker._get_sensor_position()

        assert result == (0.3, -0.5)

    async def test_zone_sensor(self):
        tracker = EyeTracker()
        tracker.sensor_entity = "sensor.fp2_zone"

        mock_client = AsyncMock()
        mock_client.get_states.return_value = [
            {
                "entity_id": "sensor.fp2_zone",
                "state": "zone_1",
                "attributes": {"zone_x": 4, "zone_y": 2, "max_zone": 4},
            }
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ", {"HA_URL": "http://ha:8123", "HA_TOKEN": "test"}
        ), patch(
            "cortex.integrations.ha.client.HAClient", return_value=mock_client
        ):
            result = await tracker._get_sensor_position()

        assert result is not None
        x, y = result
        assert x == pytest.approx(1.0, abs=1e-6)
        assert y == pytest.approx(0.0, abs=1e-6)

    async def test_angle_sensor(self):
        tracker = EyeTracker()
        tracker.sensor_entity = "sensor.cam_angle"

        mock_client = AsyncMock()
        mock_client.get_states.return_value = [
            {
                "entity_id": "sensor.cam_angle",
                "state": "tracking",
                "attributes": {"angle": 45},
            }
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ", {"HA_URL": "http://ha:8123", "HA_TOKEN": "test"}
        ), patch(
            "cortex.integrations.ha.client.HAClient", return_value=mock_client
        ):
            result = await tracker._get_sensor_position()

        assert result is not None
        import math
        assert result[0] == pytest.approx(math.sin(math.radians(45)), abs=1e-6)
        assert result[1] == 0.0

    async def test_no_ha_env(self):
        tracker = EyeTracker()
        tracker.sensor_entity = "sensor.test"

        with patch.dict("os.environ", {}, clear=True):
            result = await tracker._get_sensor_position()

        assert result is None

    async def test_entity_not_found(self):
        tracker = EyeTracker()
        tracker.sensor_entity = "sensor.missing"

        mock_client = AsyncMock()
        mock_client.get_states.return_value = [
            {"entity_id": "sensor.other", "state": "on", "attributes": {}}
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            "os.environ", {"HA_URL": "http://ha:8123", "HA_TOKEN": "test"}
        ), patch(
            "cortex.integrations.ha.client.HAClient", return_value=mock_client
        ):
            result = await tracker._get_sensor_position()

        assert result is None


class TestEyeTrackerManualTarget:
    async def test_manual_target_broadcasts(self):
        tracker = EyeTracker()
        with patch(
            "cortex.avatar.broadcast.broadcast_to_avatars", new_callable=AsyncMock
        ) as mock_broadcast:
            await tracker.set_target_manual(0.7, -0.4)
            mock_broadcast.assert_called_once()
            call_args = mock_broadcast.call_args[0][0]
            assert call_args["type"] == "eye_target"
            assert call_args["x"] == pytest.approx(0.7)
            assert call_args["y"] == pytest.approx(-0.4)
            assert call_args["tracking"] is True


class TestGetEyeTracker:
    def test_singleton(self):
        import cortex.avatar.eye_tracking as mod

        mod._tracker = None
        t1 = get_eye_tracker()
        t2 = get_eye_tracker()
        assert t1 is t2
        mod._tracker = None  # cleanup


class TestBroadcastToAvatars:
    async def test_broadcasts_to_all_rooms(self):
        from cortex.avatar.broadcast import broadcast_to_avatars, _clients, _clients_lock

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        async with _clients_lock:
            _clients.clear()
            _clients["room1"] = [mock_ws1]
            _clients["room2"] = [mock_ws2]

        try:
            await broadcast_to_avatars({"type": "eye_target", "x": 0.5, "y": 0, "tracking": True})

            mock_ws1.send_json.assert_called_once()
            mock_ws2.send_json.assert_called_once()
            msg1 = mock_ws1.send_json.call_args[0][0]
            msg2 = mock_ws2.send_json.call_args[0][0]
            assert msg1["type"] == "eye_target"
            assert msg2["type"] == "eye_target"
        finally:
            async with _clients_lock:
                _clients.clear()

    async def test_broadcasts_to_empty_rooms(self):
        from cortex.avatar.broadcast import broadcast_to_avatars, _clients, _clients_lock

        async with _clients_lock:
            _clients.clear()

        # Should not raise
        await broadcast_to_avatars({"type": "eye_target", "x": 0, "y": 0, "tracking": False})
