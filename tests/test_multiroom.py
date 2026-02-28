"""Tests for multi-room command expansion."""

import sqlite3
import pytest

from cortex.voice.multiroom import (
    extract_spatial_scope,
    expand_targets,
    build_multi_room_response,
)
from cortex.voice.spatial import SpatialEngine
from cortex.db import init_db, set_db_path


@pytest.fixture()
def engine(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    eng = SpatialEngine(conn)
    # Register some rooms
    eng.register_satellite("sat-living", "living_room", "Living Room", floor="ground")
    eng.register_satellite("sat-kitchen", "kitchen", "Kitchen", floor="ground")
    eng.register_satellite("sat-bed", "bedroom", "Bedroom", floor="upper")
    eng.register_satellite("sat-bath", "bathroom", "Bathroom", floor="upper")
    eng.register_satellite("sat-base", "workshop", "Workshop", floor="basement")
    return eng


class TestExtractSpatialScope:
    def test_everywhere(self):
        assert extract_spatial_scope("turn off lights everywhere")["scope"] == "all"

    def test_every_room(self):
        assert extract_spatial_scope("turn off the lights in every room")["scope"] == "all"

    def test_whole_house(self):
        assert extract_spatial_scope("shut down the whole house")["scope"] == "all"

    def test_downstairs(self):
        r = extract_spatial_scope("turn off the downstairs lights")
        assert r["scope"] == "floor"
        assert r["floor"] == "ground"

    def test_upstairs(self):
        r = extract_spatial_scope("dim the upstairs lights")
        assert r["scope"] == "floor"
        assert r["floor"] == "upper"

    def test_basement(self):
        r = extract_spatial_scope("turn on the basement lights")
        assert r["scope"] == "floor"
        assert r["floor"] == "basement"

    def test_second_floor(self):
        r = extract_spatial_scope("lock the second floor doors")
        assert r["scope"] == "floor"
        assert r["floor"] == "upper"

    def test_no_spatial(self):
        assert extract_spatial_scope("turn off the bedroom lights")["scope"] is None

    def test_plain_message(self):
        assert extract_spatial_scope("what time is it")["scope"] is None


class TestExpandTargets:
    def test_expand_all(self, engine):
        areas = expand_targets("turn off everything everywhere", engine)
        assert len(areas) == 5
        assert set(areas) == {"living_room", "kitchen", "bedroom", "bathroom", "workshop"}

    def test_expand_ground_floor(self, engine):
        areas = expand_targets("turn off the downstairs lights", engine)
        assert set(areas) == {"living_room", "kitchen"}

    def test_expand_upper_floor(self, engine):
        areas = expand_targets("dim the upstairs lights", engine)
        assert set(areas) == {"bedroom", "bathroom"}

    def test_expand_basement(self, engine):
        areas = expand_targets("turn on the basement lights", engine)
        assert set(areas) == {"workshop"}

    def test_no_expansion(self, engine):
        areas = expand_targets("turn off the bedroom lights", engine)
        assert areas == []


class TestBuildResponse:
    def test_all_success(self):
        r = build_multi_room_response("turned off the lights", ["living", "kitchen"], 2, 0)
        assert "2 rooms" in r

    def test_single_success(self):
        r = build_multi_room_response("turned off the lights", ["kitchen"], 1, 0)
        assert "kitchen" in r

    def test_partial_failure(self):
        r = build_multi_room_response("turned off the lights", ["a", "b", "c"], 2, 1)
        assert "2 of 3" in r
        assert "1 failed" in r
