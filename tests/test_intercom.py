"""Tests for the intercom engine, zones, personalizer, plugin, and admin API."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def client(db_path):
    from unittest.mock import patch as _patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cortex.admin_api import router
    from cortex.auth import seed_admin as _seed

    test_app = FastAPI()
    test_app.include_router(router)

    def get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _seed(conn)
        return conn

    with _patch("cortex.admin.helpers._db", get_test_db):
        yield TestClient(test_app)


@pytest.fixture()
def auth_header(db):
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


# ── ZoneManager ──────────────────────────────────────────────────

class TestZoneManager:
    @pytest.mark.asyncio
    async def test_create_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        zid = await zm.create_zone("upstairs", ["sat-1", "sat-2"], description="Top floor")
        assert isinstance(zid, int)
        assert zid > 0

    @pytest.mark.asyncio
    async def test_list_zones(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        await zm.create_zone("upstairs", ["sat-1"])
        await zm.create_zone("downstairs", ["sat-2"])
        zones = await zm.list_zones()
        assert len(zones) == 2
        names = {z["name"] for z in zones}
        assert names == {"upstairs", "downstairs"}

    @pytest.mark.asyncio
    async def test_get_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        await zm.create_zone("kitchen_zone", ["sat-k1"])
        zone = await zm.get_zone("kitchen_zone")
        assert zone is not None
        assert zone["name"] == "kitchen_zone"
        assert zone["satellite_ids"] == ["sat-k1"]

    @pytest.mark.asyncio
    async def test_get_zone_not_found(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        zone = await zm.get_zone("nonexistent")
        assert zone is None

    @pytest.mark.asyncio
    async def test_delete_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        zid = await zm.create_zone("temp", [])
        ok = await zm.delete_zone(zid)
        assert ok is True
        assert await zm.get_zone("temp") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        ok = await zm.delete_zone(9999)
        assert ok is False

    @pytest.mark.asyncio
    async def test_update_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        zid = await zm.create_zone("old", ["sat-1"])
        ok = await zm.update_zone(zid, name="new", satellite_ids=["sat-1", "sat-2"])
        assert ok is True
        zone = await zm.get_zone("new")
        assert zone is not None
        assert zone["satellite_ids"] == ["sat-1", "sat-2"]

    @pytest.mark.asyncio
    async def test_get_satellites_for_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        await zm.create_zone("bedrooms", ["sat-b1", "sat-b2"])
        sats = await zm.get_satellites_for_zone("bedrooms")
        assert sats == ["sat-b1", "sat-b2"]

    @pytest.mark.asyncio
    async def test_get_satellites_empty_zone(self, db_path):
        from cortex.intercom.zones import ZoneManager
        zm = ZoneManager()
        sats = await zm.get_satellites_for_zone("nonexistent")
        assert sats == []


# ── IntercomEngine ───────────────────────────────────────────────

def _make_mock_satellite(sat_id="sat-1", session_id="sess-1"):
    sat = AsyncMock()
    sat.satellite_id = sat_id
    sat.session_id = session_id
    sat.send = AsyncMock()
    sat.send_command = AsyncMock()
    return sat


class TestIntercomEngine:
    @pytest.mark.asyncio
    async def test_announce_no_satellite(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        ok = await engine.announce("hello", "nonexistent_room")
        assert ok is False

    @pytest.mark.asyncio
    async def test_announce_with_satellite(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        mock_sat = _make_mock_satellite()

        with patch.object(engine, "_get_satellite_for_room", return_value=mock_sat), \
             patch.object(engine, "_send_tts", new_callable=AsyncMock, return_value=True):
            ok = await engine.announce("dinner is ready", "kitchen", user_id="admin")
            assert ok is True

    @pytest.mark.asyncio
    async def test_broadcast_no_satellites(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()

        with patch("cortex.satellite.websocket.get_connected_satellites", return_value={}):
            count = await engine.broadcast("hello everyone")
            assert count == 0

    @pytest.mark.asyncio
    async def test_broadcast_multiple(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        sat1 = _make_mock_satellite("sat-1")
        sat2 = _make_mock_satellite("sat-2")

        with patch("cortex.satellite.websocket.get_connected_satellites",
                    return_value={"sat-1": sat1, "sat-2": sat2}), \
             patch.object(engine, "_send_tts", new_callable=AsyncMock, return_value=True):
            count = await engine.broadcast("attention please")
            assert count == 2

    @pytest.mark.asyncio
    async def test_zone_broadcast(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        await engine.zone_manager.create_zone("upstairs", ["sat-a", "sat-b"])
        sat_a = _make_mock_satellite("sat-a")

        with patch("cortex.satellite.websocket.get_connection",
                    side_effect=lambda sid: sat_a if sid == "sat-a" else None), \
             patch.object(engine, "_send_tts", new_callable=AsyncMock, return_value=True):
            count = await engine.zone_broadcast("goodnight", "upstairs")
            assert count == 1  # only sat-a is connected

    @pytest.mark.asyncio
    async def test_zone_broadcast_empty(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        count = await engine.zone_broadcast("hello", "nonexistent_zone")
        assert count == 0


# ── Two-way calling ──────────────────────────────────────────────

class TestTwoWayCalling:
    @pytest.mark.asyncio
    async def test_start_call(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        caller = _make_mock_satellite("sat-caller")
        callee = _make_mock_satellite("sat-callee")

        with patch.object(engine, "_get_satellite_for_room",
                          side_effect=lambda r: caller if r == "kitchen" else callee):
            call_id = await engine.start_call("kitchen", "garage")
            assert isinstance(call_id, int)
            assert call_id in engine._active_calls
            caller.send_command.assert_called()
            callee.send_command.assert_called()

    @pytest.mark.asyncio
    async def test_end_call(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        caller = _make_mock_satellite("sat-caller")
        callee = _make_mock_satellite("sat-callee")

        with patch.object(engine, "_get_satellite_for_room",
                          side_effect=lambda r: caller if r == "kitchen" else callee):
            call_id = await engine.start_call("kitchen", "garage")

        with patch("cortex.satellite.websocket.get_connection",
                    side_effect=lambda sid: caller if sid == "sat-caller" else callee):
            ok = await engine.end_call(call_id)
            assert ok is True
            assert call_id not in engine._active_calls

    @pytest.mark.asyncio
    async def test_end_nonexistent_call(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        ok = await engine.end_call(99999)
        assert ok is False

    @pytest.mark.asyncio
    async def test_call_to_self(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        sat = _make_mock_satellite("sat-same")

        with patch.object(engine, "_get_satellite_for_room", return_value=sat):
            with pytest.raises(ValueError, match="Cannot call the same satellite"):
                await engine.start_call("room", "room")

    @pytest.mark.asyncio
    async def test_get_active_calls(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        caller = _make_mock_satellite("sat-c")
        callee = _make_mock_satellite("sat-d")

        with patch.object(engine, "_get_satellite_for_room",
                          side_effect=lambda r: caller if r == "a" else callee):
            await engine.start_call("a", "b")

        calls = await engine.get_active_calls()
        assert len(calls) >= 1
        assert calls[0]["status"] in ("ringing", "active")


# ── Drop-in ──────────────────────────────────────────────────────

class TestDropIn:
    @pytest.mark.asyncio
    async def test_start_drop_in(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()
        target = _make_mock_satellite("sat-nursery")
        listener = _make_mock_satellite("sat-office")

        with patch.object(engine, "_get_satellite_for_room",
                          side_effect=lambda r: target if r == "nursery" else listener):
            call_id = await engine.start_drop_in("nursery", "office")
            assert isinstance(call_id, int)
            # LED indicator sent to target for transparency
            target.send_command.assert_any_call("led_indicator", {
                "pattern": "drop_in_active",
                "color": "amber",
            })

    @pytest.mark.asyncio
    async def test_drop_in_missing_satellite(self, db_path):
        from cortex.intercom.engine import IntercomEngine
        engine = IntercomEngine()

        with patch.object(engine, "_get_satellite_for_room", return_value=None):
            with pytest.raises(ValueError, match="missing satellite"):
                await engine.start_drop_in("room1", "room2")


# ── MessagePersonalizer ─────────────────────────────────────────

class TestMessagePersonalizer:
    def test_adult_no_change(self):
        from cortex.intercom.personalizer import MessagePersonalizer
        p = MessagePersonalizer()
        msg = "Dinner is ready."
        assert p.personalize(msg, "adult") == msg

    def test_child_friendly_prefix(self):
        from cortex.intercom.personalizer import MessagePersonalizer
        p = MessagePersonalizer()
        result = p.personalize("Time to eat", "child")
        assert result.startswith(("Hey buddy!", "Hey there!", "Listen up, friends!"))

    def test_child_word_simplification(self):
        from cortex.intercom.personalizer import MessagePersonalizer
        p = MessagePersonalizer()
        result = p.personalize("Proceed to the kitchen immediately", "child")
        assert "right now" in result
        assert "go to" in result.lower()
        assert "immediately" not in result

    def test_teen_casual(self):
        from cortex.intercom.personalizer import MessagePersonalizer
        p = MessagePersonalizer()
        result = p.personalize("Proceed to the kitchen immediately", "teen")
        assert "head to" in result.lower()
        assert "now" in result


# ── IntercomPlugin ───────────────────────────────────────────────

class TestIntercomPlugin:
    @pytest.mark.asyncio
    async def test_match_announce(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("tell the kitchen that dinner is ready", {})
        assert m.matched is True
        assert m.intent == "intercom_announce"
        assert "kitchen" in m.metadata.get("room", "")

    @pytest.mark.asyncio
    async def test_match_announce_say_in(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("say hello in the bedroom", {})
        assert m.matched is True
        assert m.intent == "intercom_announce"

    @pytest.mark.asyncio
    async def test_match_broadcast(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("broadcast dinner is ready", {})
        assert m.matched is True
        assert m.intent == "intercom_broadcast"

    @pytest.mark.asyncio
    async def test_match_broadcast_tell_everyone(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("tell everyone that the movie is starting", {})
        assert m.matched is True
        assert m.intent == "intercom_broadcast"

    @pytest.mark.asyncio
    async def test_match_call(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("call the garage", {})
        assert m.matched is True
        assert m.intent == "intercom_call"
        assert "garage" in m.metadata.get("room", "")

    @pytest.mark.asyncio
    async def test_match_intercom_to(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("intercom to the office", {})
        assert m.matched is True
        assert m.intent == "intercom_call"

    @pytest.mark.asyncio
    async def test_match_drop_in(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("listen to the nursery", {})
        assert m.matched is True
        assert m.intent == "intercom_drop_in"
        assert "nursery" in m.metadata.get("room", "")

    @pytest.mark.asyncio
    async def test_match_check_on(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("check on the baby room", {})
        assert m.matched is True
        assert m.intent == "intercom_drop_in"

    @pytest.mark.asyncio
    async def test_match_end_call(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("hang up", {})
        assert m.matched is True
        assert m.intent == "intercom_end"

    @pytest.mark.asyncio
    async def test_match_end_intercom(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("end call", {})
        assert m.matched is True
        assert m.intent == "intercom_end"

    @pytest.mark.asyncio
    async def test_match_stop_listening(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("stop listening", {})
        assert m.matched is True
        assert m.intent == "intercom_end"

    @pytest.mark.asyncio
    async def test_no_match(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        m = await plugin.match("what time is it", {})
        assert m.matched is False

    @pytest.mark.asyncio
    async def test_handle_announce(self, db_path):
        from cortex.plugins.intercom import IntercomPlugin
        from cortex.plugins.base import CommandMatch
        plugin = IntercomPlugin()

        engine_mock = AsyncMock()
        engine_mock.announce = AsyncMock(return_value=True)
        plugin._engine = engine_mock

        match = CommandMatch(
            matched=True, intent="intercom_announce",
            metadata={"room": "kitchen", "message": "dinner is ready"},
        )
        result = await plugin.handle("tell the kitchen dinner is ready", match, {"room": "living_room"})
        assert result.success is True
        assert "kitchen" in result.response

    @pytest.mark.asyncio
    async def test_handle_broadcast(self, db_path):
        from cortex.plugins.intercom import IntercomPlugin
        from cortex.plugins.base import CommandMatch
        plugin = IntercomPlugin()

        engine_mock = AsyncMock()
        engine_mock.broadcast = AsyncMock(return_value=3)
        plugin._engine = engine_mock

        match = CommandMatch(
            matched=True, intent="intercom_broadcast",
            metadata={"message": "fire drill"},
        )
        result = await plugin.handle("broadcast fire drill", match, {})
        assert result.success is True
        assert "3" in result.response

    @pytest.mark.asyncio
    async def test_handle_end_no_calls(self, db_path):
        from cortex.plugins.intercom import IntercomPlugin
        from cortex.plugins.base import CommandMatch
        plugin = IntercomPlugin()

        engine_mock = AsyncMock()
        engine_mock.get_active_calls = AsyncMock(return_value=[])
        plugin._engine = engine_mock

        match = CommandMatch(matched=True, intent="intercom_end")
        result = await plugin.handle("hang up", match, {})
        assert result.success is True
        assert "No active calls" in result.response

    @pytest.mark.asyncio
    async def test_setup_and_health(self):
        from cortex.plugins.intercom import IntercomPlugin
        plugin = IntercomPlugin()
        assert await plugin.setup({}) is True
        assert await plugin.health() is True


# ── Admin API ────────────────────────────────────────────────────

class TestAdminIntercomAPI:
    def test_list_zones_empty(self, client, auth_header):
        resp = client.get("/admin/intercom/zones", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["zones"] == []

    def test_create_zone(self, client, auth_header):
        resp = client.post("/admin/intercom/zones", headers=auth_header, json={
            "name": "upstairs",
            "satellite_ids": ["sat-1", "sat-2"],
            "description": "Top floor",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "upstairs"
        assert "id" in data

    def test_create_duplicate_zone(self, client, auth_header):
        client.post("/admin/intercom/zones", headers=auth_header, json={
            "name": "dupe",
            "satellite_ids": [],
        })
        resp = client.post("/admin/intercom/zones", headers=auth_header, json={
            "name": "dupe",
            "satellite_ids": [],
        })
        assert resp.status_code == 409

    def test_delete_zone(self, client, auth_header):
        resp = client.post("/admin/intercom/zones", headers=auth_header, json={
            "name": "temp_zone",
            "satellite_ids": [],
        })
        zid = resp.json()["id"]
        resp = client.delete(f"/admin/intercom/zones/{zid}", headers=auth_header)
        assert resp.status_code == 200

    def test_delete_nonexistent_zone(self, client, auth_header):
        resp = client.delete("/admin/intercom/zones/99999", headers=auth_header)
        assert resp.status_code == 404

    def test_update_zone(self, client, auth_header):
        resp = client.post("/admin/intercom/zones", headers=auth_header, json={
            "name": "old_name",
            "satellite_ids": ["sat-1"],
        })
        zid = resp.json()["id"]
        resp = client.patch(f"/admin/intercom/zones/{zid}", headers=auth_header, json={
            "name": "new_name",
            "satellite_ids": ["sat-1", "sat-2"],
        })
        assert resp.status_code == 200

    def test_list_active_calls(self, client, auth_header):
        resp = client.get("/admin/intercom/calls", headers=auth_header)
        assert resp.status_code == 200
        assert "calls" in resp.json()

    def test_get_log(self, client, auth_header):
        resp = client.get("/admin/intercom/log", headers=auth_header)
        assert resp.status_code == 200
        assert "log" in resp.json()

    def test_broadcast_endpoint(self, client, auth_header):
        with patch("cortex.admin.intercom._get_engine") as mock_eng:
            eng = AsyncMock()
            eng.broadcast = AsyncMock(return_value=2)
            mock_eng.return_value = eng
            resp = client.post("/admin/intercom/broadcast", headers=auth_header, json={
                "message": "test broadcast",
            })
            assert resp.status_code == 200
            assert resp.json()["satellites_reached"] == 2

    def test_end_call_not_found(self, client, auth_header):
        with patch("cortex.admin.intercom._get_engine") as mock_eng:
            eng = AsyncMock()
            eng.end_call = AsyncMock(return_value=False)
            mock_eng.return_value = eng
            resp = client.post("/admin/intercom/calls/99999/end", headers=auth_header)
            assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/admin/intercom/zones")
        assert resp.status_code in (401, 403, 422)
