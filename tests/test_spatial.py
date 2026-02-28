"""Tests for Satellite Room Registry & Spatial Engine (I3.2)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from cortex.voice.spatial import SpatialEngine


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def db():
    """In-memory DB with the required tables."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE satellite_rooms (
            satellite_id TEXT PRIMARY KEY,
            area_id      TEXT NOT NULL,
            area_name    TEXT NOT NULL,
            floor        TEXT,
            mic_x        REAL,
            mic_y        REAL,
            mic_z        REAL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE presence_sensors (
            entity_id               TEXT PRIMARY KEY,
            area_id                 TEXT NOT NULL,
            sensor_type             TEXT NOT NULL,
            priority                INTEGER DEFAULT 1,
            indicates_presence_when TEXT DEFAULT 'on',
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE room_context_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            interaction_id    INTEGER,
            resolved_area     TEXT,
            confidence        REAL,
            satellite_id      TEXT,
            satellite_area    TEXT,
            presence_signals  TEXT,
            speaker_id        TEXT,
            resolution_method TEXT,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    yield conn
    conn.close()


@pytest.fixture
def engine(db):
    return SpatialEngine(db)


# ------------------------------------------------------------------ #
# Satellite registration tests
# ------------------------------------------------------------------ #

class TestSatelliteRegistration:
    def test_register_and_list(self, engine):
        engine.register_satellite("sat-kitchen", "kitchen", "Kitchen", floor="ground")
        sats = engine.list_satellites()
        assert len(sats) == 1
        assert sats[0]["satellite_id"] == "sat-kitchen"
        assert sats[0]["area_name"] == "Kitchen"
        assert sats[0]["floor"] == "ground"

    def test_register_upsert(self, engine):
        engine.register_satellite("sat-1", "a1", "Room A")
        engine.register_satellite("sat-1", "a2", "Room B")
        sats = engine.list_satellites()
        assert len(sats) == 1
        assert sats[0]["area_id"] == "a2"

    def test_unregister(self, engine):
        engine.register_satellite("sat-1", "a1", "Room A")
        engine.unregister_satellite("sat-1")
        assert engine.list_satellites() == []

    def test_unregister_nonexistent(self, engine):
        engine.unregister_satellite("no-such-sat")  # Should not raise

    def test_multiple_satellites(self, engine):
        engine.register_satellite("sat-1", "kitchen", "Kitchen", floor="ground")
        engine.register_satellite("sat-2", "bedroom", "Bedroom", floor="upper")
        assert len(engine.list_satellites()) == 2


# ------------------------------------------------------------------ #
# Presence sensor tests
# ------------------------------------------------------------------ #

class TestPresenceSensors:
    def test_register_sensor(self, engine, db):
        engine.register_presence_sensor("binary_sensor.kitchen_motion", "kitchen", "motion")
        row = db.execute(
            "SELECT * FROM presence_sensors WHERE entity_id = 'binary_sensor.kitchen_motion'"
        ).fetchone()
        assert row is not None

    def test_sensor_upsert(self, engine, db):
        engine.register_presence_sensor("s1", "a1", "motion", priority=1)
        engine.register_presence_sensor("s1", "a1", "occupancy", priority=5)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM presence_sensors WHERE entity_id = 's1'").fetchone()
        assert row["sensor_type"] == "occupancy"
        assert row["priority"] == 5


# ------------------------------------------------------------------ #
# Room resolution tests
# ------------------------------------------------------------------ #

class TestResolveRoom:
    @pytest.mark.asyncio
    async def test_satellite_priority(self, engine):
        """Satellite lookup is highest priority."""
        engine.register_satellite("sat-1", "kitchen", "Kitchen")
        result = await engine.resolve_room(satellite_id="sat-1")
        assert result["area_id"] == "kitchen"
        assert result["method"] == "satellite"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_unknown_satellite_falls_through(self, engine):
        """Unknown satellite falls through to unknown."""
        result = await engine.resolve_room(satellite_id="no-such")
        assert result["method"] == "unknown"
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_presence_sensor_resolution(self, engine):
        """Presence sensors used when satellite is unknown."""
        engine.register_satellite("sat-1", "kitchen", "Kitchen")
        engine.register_presence_sensor(
            "binary_sensor.kitchen_motion", "kitchen", "motion", priority=2
        )

        ha = AsyncMock()
        ha.get_states = AsyncMock(return_value=[
            {"entity_id": "binary_sensor.kitchen_motion", "state": "on"},
        ])

        result = await engine.resolve_room(ha_client=ha)
        assert result["area_id"] == "kitchen"
        assert result["method"] == "presence"
        assert result["confidence"] == 0.7

    @pytest.mark.asyncio
    async def test_presence_sensor_inactive(self, engine):
        """Inactive sensors don't resolve."""
        engine.register_presence_sensor("s1", "kitchen", "motion")

        ha = AsyncMock()
        ha.get_states = AsyncMock(return_value=[
            {"entity_id": "s1", "state": "off"},
        ])

        result = await engine.resolve_room(ha_client=ha)
        assert result["method"] == "unknown"

    @pytest.mark.asyncio
    async def test_speaker_history_fallback(self, engine, db):
        """Speaker history used when no satellite or presence data."""
        db.execute(
            """INSERT INTO room_context_log
               (interaction_id, resolved_area, satellite_area, speaker_id,
                resolution_method, confidence)
               VALUES (1, 'living_room', 'Living Room', 'spk-1', 'satellite', 0.95)"""
        )
        db.commit()

        result = await engine.resolve_room(speaker_id="spk-1")
        assert result["area_id"] == "living_room"
        assert result["method"] == "speaker_history"
        assert result["confidence"] == 0.4

    @pytest.mark.asyncio
    async def test_full_unknown(self, engine):
        """No signals → unknown."""
        result = await engine.resolve_room()
        assert result == {
            "area_id": None,
            "area_name": None,
            "confidence": 0.0,
            "method": "unknown",
        }

    @pytest.mark.asyncio
    async def test_presence_priority_order(self, engine):
        """Higher-priority sensor wins."""
        engine.register_satellite("s", "bedroom", "Bedroom")
        engine.register_satellite("s2", "kitchen", "Kitchen")
        engine.register_presence_sensor("sens_bed", "bedroom", "motion", priority=1)
        engine.register_presence_sensor("sens_kit", "kitchen", "occupancy", priority=10)

        ha = AsyncMock()
        ha.get_states = AsyncMock(return_value=[
            {"entity_id": "sens_bed", "state": "on"},
            {"entity_id": "sens_kit", "state": "on"},
        ])

        result = await engine.resolve_room(ha_client=ha)
        assert result["area_id"] == "kitchen"  # higher priority

    @pytest.mark.asyncio
    async def test_ha_failure_falls_through(self, engine):
        """HA query failure falls through gracefully."""
        engine.register_presence_sensor("s1", "kitchen", "motion")

        ha = AsyncMock()
        ha.get_states = AsyncMock(side_effect=Exception("HA down"))

        result = await engine.resolve_room(ha_client=ha)
        assert result["method"] == "unknown"


# ------------------------------------------------------------------ #
# Logging tests
# ------------------------------------------------------------------ #

class TestLogResolution:
    def test_log_written(self, engine, db):
        result = {"area_id": "kitchen", "area_name": "Kitchen",
                  "confidence": 0.95, "method": "satellite"}
        engine.log_resolution(42, result, satellite_id="sat-1", speaker_id="spk-1")

        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM room_context_log WHERE interaction_id = 42").fetchone()
        assert row is not None
        assert row["resolved_area"] == "kitchen"
        assert row["resolution_method"] == "satellite"
        assert row["speaker_id"] == "spk-1"

    def test_log_with_presence_signals(self, engine, db):
        signals = [{"entity_id": "s1", "state": "on"}]
        result = {"area_id": "k", "confidence": 0.7, "method": "presence"}
        engine.log_resolution(None, result, presence_signals=signals)

        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM room_context_log ORDER BY id DESC LIMIT 1").fetchone()
        assert json.loads(row["presence_signals"]) == signals


# ------------------------------------------------------------------ #
# Floor / area expansion tests
# ------------------------------------------------------------------ #

class TestFloorExpansion:
    def test_expand_floor(self, engine):
        engine.register_satellite("s1", "kitchen", "Kitchen", floor="ground")
        engine.register_satellite("s2", "living", "Living Room", floor="ground")
        engine.register_satellite("s3", "bedroom", "Bedroom", floor="upper")

        ground = engine.expand_floor_areas("ground")
        assert set(ground) == {"kitchen", "living"}

    def test_expand_floor_empty(self, engine):
        assert engine.expand_floor_areas("basement") == []

    def test_expand_all(self, engine):
        engine.register_satellite("s1", "kitchen", "Kitchen", floor="ground")
        engine.register_satellite("s2", "bedroom", "Bedroom", floor="upper")
        assert set(engine.expand_all_areas()) == {"kitchen", "bedroom"}

    def test_expand_all_empty(self, engine):
        assert engine.expand_all_areas() == []
