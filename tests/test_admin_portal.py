"""Comprehensive tests for every admin portal API endpoint.

Walks through ALL admin panel pages by testing their backing API endpoints.
Uses httpx.AsyncClient with ASGITransport for async testing.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cortex.admin import router
from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import init_db, set_db_path


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Create a fresh temporary database for each test."""
    path = tmp_path / "test_portal.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    """Return a direct connection to the test database."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def _app(db_path):
    """FastAPI app with admin router and patched DB."""
    test_app = FastAPI()
    test_app.include_router(router)

    def _get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        seed_admin(conn)
        return conn

    with patch("cortex.admin.helpers._db", _get_test_db):
        yield test_app


@pytest.fixture()
async def client(_app):
    """Async HTTP client wired to the test app."""
    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
def auth_header(db):
    """Authorization header with a valid admin JWT."""
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _insert_user(db_path, user_id="u1", name="Derek"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT OR IGNORE INTO user_profiles (user_id, display_name) VALUES (?, ?)",
        (user_id, name),
    )
    conn.commit()
    conn.close()


def _insert_device(db_path, entity_id="light.kitchen", name="Kitchen Light", domain="light"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT OR IGNORE INTO ha_devices (entity_id, friendly_name, domain) VALUES (?, ?, ?)",
        (entity_id, name, domain),
    )
    conn.commit()
    conn.close()


def _insert_command_pattern(db_path, pattern="turn on *", intent="turn_on"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO command_patterns (pattern, intent, entity_domain, source) VALUES (?, ?, ?, ?)",
        (pattern, intent, "light", "manual"),
    )
    conn.commit()
    conn.close()


def _insert_safety_event(db_path, category="jailbreak", severity="high"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO guardrail_events (direction, category, severity, user_id, trigger_text, action_taken)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("input", category, severity, "u1", "bad prompt", "blocked"),
    )
    conn.commit()
    conn.close()


def _insert_interaction(db_path, user_id="u1", message="hello", layer="layer1"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO interactions (user_id, message, matched_layer, response, response_time_ms)"
        " VALUES (?, ?, ?, ?, ?)",
        (user_id, message, layer, "Hi there!", 42),
    )
    conn.commit()
    conn.close()


def _insert_speaker(db_path, speaker_id="spk-1", user_id="u1", name="Derek"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO speaker_profiles (id, user_id, display_name, embedding, sample_count)"
        " VALUES (?, ?, ?, ?, ?)",
        (speaker_id, user_id, name, b"\x00" * 16, 3),
    )
    conn.commit()
    conn.close()


def _insert_emotional_profile(db_path, user_id="u1"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT OR IGNORE INTO emotional_profiles (user_id, rapport_score) VALUES (?, ?)",
        (user_id, 0.5),
    )
    conn.commit()
    conn.close()


def _insert_mistake(db_path, interaction_id=1):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO mistake_log (interaction_id, claim_text, detection_method)"
        " VALUES (?, ?, ?)",
        (interaction_id, "Turned on the wrong light", "user_correction"),
    )
    conn.commit()
    conn.close()


def _insert_satellite(db_path, sat_id="sat-1", room="kitchen"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT OR IGNORE INTO satellites"
        " (id, ip_address, room, status, display_name, mode)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sat_id, "192.168.1.100", room, "online", "Kitchen Sat", "dedicated"),
    )
    conn.commit()
    conn.close()


# ════════════════════════════════════════════════════════════════════
# 1. AUTH FLOW (LoginView)
# ════════════════════════════════════════════════════════════════════


class TestAuthLogin:
    """POST /admin/auth/login"""

    async def test_login_success(self, client):
        resp = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "atlas-admin"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert body["user"]["username"] == "admin"

    async def test_login_wrong_password(self, client):
        resp = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_missing_fields(self, client):
        resp = await client.post("/admin/auth/login", json={"username": "admin"})
        assert resp.status_code == 422

    async def test_login_empty_body(self, client):
        resp = await client.post("/admin/auth/login", json={})
        assert resp.status_code == 422

    async def test_login_unknown_user(self, client):
        resp = await client.post(
            "/admin/auth/login",
            json={"username": "nobody", "password": "anything"},
        )
        assert resp.status_code == 401


class TestAuthMe:
    """GET /admin/auth/me"""

    async def test_me_with_token(self, client, auth_header):
        resp = await client.get("/admin/auth/me", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert "id" in body

    async def test_me_without_token(self, client):
        resp = await client.get("/admin/auth/me")
        assert resp.status_code == 401

    async def test_me_with_bad_token(self, client):
        resp = await client.get(
            "/admin/auth/me",
            headers={"Authorization": "Bearer garbage.token.here"},
        )
        assert resp.status_code == 401


class TestAuthChangePassword:
    """POST /admin/auth/change-password"""

    async def test_change_password_success(self, client, auth_header):
        resp = await client.post(
            "/admin/auth/change-password",
            json={"current_password": "atlas-admin", "new_password": "new-secret"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify new password works for login
        resp2 = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "new-secret"},
        )
        assert resp2.status_code == 200

    async def test_change_password_wrong_current(self, client, auth_header):
        resp = await client.post(
            "/admin/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-secret"},
            headers=auth_header,
        )
        assert resp.status_code == 400

    async def test_change_password_no_auth(self, client):
        resp = await client.post(
            "/admin/auth/change-password",
            json={"current_password": "atlas-admin", "new_password": "new"},
        )
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 2. DASHBOARD
# ════════════════════════════════════════════════════════════════════


class TestDashboard:
    """GET /admin/dashboard"""

    async def test_dashboard_shape(self, client, auth_header):
        resp = await client.get("/admin/dashboard", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        # Stat counters
        for key in (
            "total_users",
            "total_interactions",
            "safety_events",
            "command_patterns",
            "devices",
            "voice_enrollments",
            "jailbreak_patterns",
        ):
            assert key in data, f"Missing dashboard key: {key}"
            assert isinstance(data[key], int) and data[key] >= 0
        # List sections
        assert isinstance(data["recent_safety_events"], list)
        assert isinstance(data["recent_interactions"], list)
        assert isinstance(data["layer_distribution"], list)

    async def test_dashboard_counts_reflect_data(self, client, auth_header, db_path):
        _insert_user(db_path)
        _insert_interaction(db_path)
        _insert_safety_event(db_path)
        resp = await client.get("/admin/dashboard", headers=auth_header)
        data = resp.json()
        assert data["total_users"] >= 1
        assert data["total_interactions"] >= 1
        assert data["safety_events"] >= 1

    async def test_dashboard_no_auth(self, client):
        resp = await client.get("/admin/dashboard")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 3. USERS (UsersView + UserDetailView)
# ════════════════════════════════════════════════════════════════════


class TestUsersList:
    """GET /admin/users"""

    async def test_list_empty(self, client, auth_header):
        resp = await client.get("/admin/users", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["users"] == []
        assert body["page"] == 1

    async def test_list_with_users(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Alice")
        _insert_user(db_path, "u2", "Bob")
        resp = await client.get("/admin/users", headers=auth_header)
        body = resp.json()
        assert body["total"] == 2
        assert len(body["users"]) == 2

    async def test_list_pagination(self, client, auth_header, db_path):
        for i in range(5):
            _insert_user(db_path, f"u{i}", f"User{i}")
        resp = await client.get(
            "/admin/users", params={"page": 1, "per_page": 2}, headers=auth_header
        )
        body = resp.json()
        assert body["total"] == 5
        assert len(body["users"]) == 2
        assert body["page"] == 1
        assert body["per_page"] == 2

    async def test_list_no_auth(self, client):
        resp = await client.get("/admin/users")
        assert resp.status_code == 401


class TestUserCreate:
    """POST /admin/users"""

    async def test_create_user(self, client, auth_header):
        resp = await client.post(
            "/admin/users",
            json={"display_name": "Charlie"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Charlie"
        assert "user_id" in body

    async def test_create_user_with_custom_id(self, client, auth_header):
        resp = await client.post(
            "/admin/users",
            json={"display_name": "Diana", "user_id": "diana-1"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "diana-1"

    async def test_create_duplicate_user(self, client, auth_header):
        await client.post(
            "/admin/users",
            json={"display_name": "Eve", "user_id": "eve-1"},
            headers=auth_header,
        )
        resp = await client.post(
            "/admin/users",
            json={"display_name": "Eve2", "user_id": "eve-1"},
            headers=auth_header,
        )
        assert resp.status_code == 409


class TestUserDetail:
    """GET /admin/users/{user_id}"""

    async def test_get_user(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.get("/admin/users/u1", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Derek"
        assert body["user_id"] == "u1"
        # Detail view includes enrichment
        assert "emotional_profile" in body
        assert "parental_controls" in body
        assert "topics" in body
        assert "activity_hours" in body

    async def test_get_nonexistent_user(self, client, auth_header):
        resp = await client.get("/admin/users/nonexistent", headers=auth_header)
        assert resp.status_code == 404


class TestUserUpdate:
    """PATCH /admin/users/{user_id}"""

    async def test_update_display_name(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.patch(
            "/admin/users/u1",
            json={"display_name": "Derek Jr."},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Derek Jr."

    async def test_update_vocabulary_level(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.patch(
            "/admin/users/u1",
            json={"vocabulary_level": "advanced"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["vocabulary_level"] == "advanced"

    async def test_update_multiple_fields(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.patch(
            "/admin/users/u1",
            json={
                "preferred_tone": "friendly",
                "humor_style": "dad_jokes",
                "communication_style": "casual",
            },
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["preferred_tone"] == "friendly"
        assert body["humor_style"] == "dad_jokes"

    async def test_update_no_fields(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.patch(
            "/admin/users/u1", json={}, headers=auth_header
        )
        assert resp.status_code == 400


class TestUserAge:
    """POST /admin/users/{user_id}/age"""

    async def test_set_age(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.post(
            "/admin/users/u1/age",
            json={"birth_year": 2010, "birth_month": 6},
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "age" in body
        assert "age_group" in body
        assert body["age"] >= 0


class TestUserDelete:
    """DELETE /admin/users/{user_id}"""

    async def test_delete_user(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        resp = await client.delete("/admin/users/u1", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify user is gone
        resp2 = await client.get("/admin/users/u1", headers=auth_header)
        assert resp2.status_code == 404

    async def test_delete_nonexistent_user(self, client, auth_header):
        # Delete is idempotent (no error on missing user in current impl)
        resp = await client.delete("/admin/users/nonexistent", headers=auth_header)
        assert resp.status_code == 200


# ════════════════════════════════════════════════════════════════════
# 4. PARENTAL CONTROLS
# ════════════════════════════════════════════════════════════════════


class TestParentalControls:
    """GET/POST/DELETE /admin/users/{user_id}/parental"""

    async def test_get_no_controls(self, client, auth_header, db_path):
        _insert_user(db_path, "child-1", "Kid")
        resp = await client.get("/admin/users/child-1/parental", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["controls"] is None

    async def test_set_and_get_controls(self, client, auth_header, db_path):
        _insert_user(db_path, "child-1", "Kid")
        _insert_user(db_path, "parent-1", "Parent")

        # Set controls
        resp = await client.post(
            "/admin/users/child-1/parental",
            json={
                "parent_user_id": "parent-1",
                "content_filter_level": "strict",
                "allowed_hours_start": "08:00",
                "allowed_hours_end": "20:00",
                "restricted_topics": ["violence"],
                "restricted_actions": ["device_control"],
            },
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Get controls
        resp2 = await client.get("/admin/users/child-1/parental", headers=auth_header)
        assert resp2.status_code == 200
        controls = resp2.json()["controls"]
        assert controls is not None
        assert controls["parent_user_id"] == "parent-1"
        assert controls["content_filter_level"] == "strict"
        assert "device_control" in controls["restricted_actions"]

    async def test_update_controls(self, client, auth_header, db_path):
        _insert_user(db_path, "child-1", "Kid")
        _insert_user(db_path, "p1", "Parent")
        # Set initial
        await client.post(
            "/admin/users/child-1/parental",
            json={"parent_user_id": "p1", "content_filter_level": "strict"},
            headers=auth_header,
        )
        # Update (upsert)
        resp = await client.post(
            "/admin/users/child-1/parental",
            json={"parent_user_id": "p1", "content_filter_level": "moderate"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        resp2 = await client.get("/admin/users/child-1/parental", headers=auth_header)
        assert resp2.json()["controls"]["content_filter_level"] == "moderate"

    async def test_remove_controls(self, client, auth_header, db_path):
        _insert_user(db_path, "child-1", "Kid")
        _insert_user(db_path, "p1", "Parent")
        await client.post(
            "/admin/users/child-1/parental",
            json={"parent_user_id": "p1"},
            headers=auth_header,
        )
        resp = await client.delete("/admin/users/child-1/parental", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify removed
        resp2 = await client.get("/admin/users/child-1/parental", headers=auth_header)
        assert resp2.json()["controls"] is None

    async def test_parental_no_auth(self, client, db_path):
        _insert_user(db_path, "child-1", "Kid")
        resp = await client.get("/admin/users/child-1/parental")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 5. SAFETY (SafetyView)
# ════════════════════════════════════════════════════════════════════


class TestSafetyEvents:
    """GET /admin/safety/events"""

    async def test_list_empty(self, client, auth_header):
        resp = await client.get("/admin/safety/events", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["events"] == []

    async def test_list_with_events(self, client, auth_header, db_path):
        _insert_safety_event(db_path, "jailbreak", "high")
        _insert_safety_event(db_path, "content", "medium")
        resp = await client.get("/admin/safety/events", headers=auth_header)
        body = resp.json()
        assert body["total"] == 2
        assert len(body["events"]) == 2

    async def test_filter_by_category(self, client, auth_header, db_path):
        _insert_safety_event(db_path, "jailbreak", "high")
        _insert_safety_event(db_path, "content", "medium")
        resp = await client.get(
            "/admin/safety/events",
            params={"category": "jailbreak"},
            headers=auth_header,
        )
        body = resp.json()
        assert body["total"] == 1

    async def test_filter_by_severity(self, client, auth_header, db_path):
        _insert_safety_event(db_path, "jailbreak", "high")
        _insert_safety_event(db_path, "content", "low")
        resp = await client.get(
            "/admin/safety/events",
            params={"severity": "high"},
            headers=auth_header,
        )
        assert resp.json()["total"] == 1

    async def test_events_pagination(self, client, auth_header, db_path):
        for _ in range(5):
            _insert_safety_event(db_path)
        resp = await client.get(
            "/admin/safety/events",
            params={"page": 1, "per_page": 2},
            headers=auth_header,
        )
        body = resp.json()
        assert body["total"] == 5
        assert len(body["events"]) == 2

    async def test_events_no_auth(self, client):
        resp = await client.get("/admin/safety/events")
        assert resp.status_code == 401


class TestSafetyPatterns:
    """GET/POST/DELETE /admin/safety/patterns"""

    async def test_list_patterns_empty(self, client, auth_header):
        resp = await client.get("/admin/safety/patterns", headers=auth_header)
        assert resp.status_code == 200
        assert "patterns" in resp.json()

    async def test_add_pattern(self, client, auth_header):
        resp = await client.post(
            "/admin/safety/patterns",
            json={"pattern": "ignore all instructions"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify in list
        resp2 = await client.get("/admin/safety/patterns", headers=auth_header)
        patterns = resp2.json()["patterns"]
        assert any(p["pattern"] == "ignore all instructions" for p in patterns)

    async def test_add_pattern_with_source(self, client, auth_header):
        resp = await client.post(
            "/admin/safety/patterns",
            json={"pattern": "bypass safety", "source": "automated"},
            headers=auth_header,
        )
        assert resp.status_code == 200

    async def test_add_duplicate_pattern(self, client, auth_header):
        await client.post(
            "/admin/safety/patterns",
            json={"pattern": "unique_test_pattern"},
            headers=auth_header,
        )
        resp = await client.post(
            "/admin/safety/patterns",
            json={"pattern": "unique_test_pattern"},
            headers=auth_header,
        )
        assert resp.status_code == 409

    async def test_delete_pattern(self, client, auth_header):
        await client.post(
            "/admin/safety/patterns",
            json={"pattern": "to_delete"},
            headers=auth_header,
        )
        # Get pattern id
        resp = await client.get("/admin/safety/patterns", headers=auth_header)
        patterns = resp.json()["patterns"]
        pid = next(p["id"] for p in patterns if p["pattern"] == "to_delete")

        resp2 = await client.delete(
            f"/admin/safety/patterns/{pid}", headers=auth_header
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    async def test_patterns_no_auth(self, client):
        resp = await client.get("/admin/safety/patterns")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 6. VOICE / SPEAKERS (VoiceView)
# ════════════════════════════════════════════════════════════════════


class TestVoiceSpeakers:
    """GET/PATCH/DELETE /admin/voice/speakers"""

    async def test_list_speakers_empty(self, client, auth_header):
        resp = await client.get("/admin/voice/speakers", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["speakers"] == []

    async def test_list_speakers_with_data(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        _insert_speaker(db_path, "spk-1", "u1", "Derek")
        resp = await client.get("/admin/voice/speakers", headers=auth_header)
        speakers = resp.json()["speakers"]
        assert len(speakers) == 1
        assert speakers[0]["display_name"] == "Derek"
        assert speakers[0]["sample_count"] == 3

    async def test_update_speaker(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        _insert_speaker(db_path, "spk-1", "u1", "Derek")
        resp = await client.patch(
            "/admin/voice/speakers/spk-1",
            json={"display_name": "Derek Updated"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_update_speaker_no_fields(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        _insert_speaker(db_path, "spk-1", "u1", "Derek")
        resp = await client.patch(
            "/admin/voice/speakers/spk-1",
            json={},
            headers=auth_header,
        )
        assert resp.status_code == 400

    async def test_delete_speaker(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        _insert_speaker(db_path, "spk-1", "u1", "Derek")
        resp = await client.delete("/admin/voice/speakers/spk-1", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify gone
        resp2 = await client.get("/admin/voice/speakers", headers=auth_header)
        assert resp2.json()["speakers"] == []

    async def test_speakers_no_auth(self, client):
        resp = await client.get("/admin/voice/speakers")
        assert resp.status_code == 401


class TestTTSVoices:
    """GET /admin/tts/voices — voice listing from TTS providers."""

    async def test_list_tts_voices(self, client, auth_header):
        """Voices endpoint should return even when providers are offline."""
        resp = await client.get("/admin/tts/voices", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "voices" in body
        assert isinstance(body["voices"], list)
        assert "system_default" in body

    async def test_tts_voices_no_auth(self, client):
        resp = await client.get("/admin/tts/voices")
        assert resp.status_code == 401


class TestTTSDefaultVoice:
    """PUT /admin/tts/default_voice"""

    async def test_set_default_voice(self, client, auth_header):
        resp = await client.put(
            "/admin/tts/default_voice",
            json={"voice": "af_bella"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["default_voice"] == "af_bella"

    async def test_set_default_voice_no_auth(self, client):
        resp = await client.put(
            "/admin/tts/default_voice", json={"voice": "af_bella"}
        )
        assert resp.status_code == 401


class TestTTSRegenerate:
    """POST /admin/tts/regenerate"""

    async def test_regenerate_with_voice(self, client, auth_header):
        resp = await client.post(
            "/admin/tts/regenerate",
            json={"voice": "af_bella"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "regenerating"
        assert body["voice"] == "af_bella"


# ════════════════════════════════════════════════════════════════════
# 7. AVATAR (AvatarView)
# ════════════════════════════════════════════════════════════════════


class TestAvatarSkins:
    """CRUD /admin/avatar/skins"""

    async def test_list_skins(self, client, auth_header):
        resp = await client.get("/admin/avatar/skins", headers=auth_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_create_skin(self, client, auth_header):
        resp = await client.post(
            "/admin/avatar/skins",
            json={
                "id": "test-skin",
                "name": "Test Skin",
                "type": "svg",
                "path": "/skins/test.svg",
            },
            headers=auth_header,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "test-skin"
        assert body["name"] == "Test Skin"

    async def test_create_duplicate_skin(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "dup-skin", "name": "Dup"},
            headers=auth_header,
        )
        resp = await client.post(
            "/admin/avatar/skins",
            json={"id": "dup-skin", "name": "Dup2"},
            headers=auth_header,
        )
        assert resp.status_code == 409

    async def test_get_skin(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "get-skin", "name": "Get Me"},
            headers=auth_header,
        )
        resp = await client.get("/admin/avatar/skins/get-skin", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Me"

    async def test_get_skin_404(self, client, auth_header):
        resp = await client.get("/admin/avatar/skins/nope", headers=auth_header)
        assert resp.status_code == 404

    async def test_delete_skin(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "del-skin", "name": "Delete Me"},
            headers=auth_header,
        )
        resp = await client.delete("/admin/avatar/skins/del-skin", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "del-skin"

    async def test_delete_default_skin_rejected(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "def-skin", "name": "Default", "is_default": True},
            headers=auth_header,
        )
        resp = await client.delete("/admin/avatar/skins/def-skin", headers=auth_header)
        assert resp.status_code == 400

    async def test_delete_nonexistent_skin(self, client, auth_header):
        resp = await client.delete("/admin/avatar/skins/nope", headers=auth_header)
        assert resp.status_code == 404


class TestAvatarDefault:
    """GET/PUT /admin/avatar/default"""

    async def test_get_default_skin_none(self, client, auth_header):
        resp = await client.get("/admin/avatar/default", headers=auth_header)
        assert resp.status_code == 200
        # When no default exists the seeded skins may provide one; at minimum
        # the endpoint must return successfully with an id field.
        body = resp.json()
        assert "id" in body

    async def test_set_default_skin(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "new-default", "name": "New Default"},
            headers=auth_header,
        )
        resp = await client.put(
            "/admin/avatar/default/new-default", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["default"] == "new-default"

    async def test_set_default_nonexistent(self, client, auth_header):
        resp = await client.put("/admin/avatar/default/nope", headers=auth_header)
        assert resp.status_code == 404


class TestAvatarAssignments:
    """GET/PUT/DELETE /admin/avatar/assignments"""

    async def test_list_assignments_empty(self, client, auth_header):
        resp = await client.get("/admin/avatar/assignments", headers=auth_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_assign_skin(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "assign-skin", "name": "Assign Skin"},
            headers=auth_header,
        )
        resp = await client.put(
            "/admin/avatar/assignments/user-1",
            json={"skin_id": "assign-skin"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "user-1"
        assert body["skin_id"] == "assign-skin"

    async def test_assign_nonexistent_skin(self, client, auth_header):
        resp = await client.put(
            "/admin/avatar/assignments/user-1",
            json={"skin_id": "nonexistent"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    async def test_remove_assignment(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "rm-skin", "name": "RM"},
            headers=auth_header,
        )
        await client.put(
            "/admin/avatar/assignments/user-1",
            json={"skin_id": "rm-skin"},
            headers=auth_header,
        )
        resp = await client.delete(
            "/admin/avatar/assignments/user-1", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["reverted_to"] == "default"

    async def test_list_assignments_after_assign(self, client, auth_header):
        await client.post(
            "/admin/avatar/skins",
            json={"id": "list-skin", "name": "LS"},
            headers=auth_header,
        )
        await client.put(
            "/admin/avatar/assignments/user-x",
            json={"skin_id": "list-skin"},
            headers=auth_header,
        )
        resp = await client.get("/admin/avatar/assignments", headers=auth_header)
        assignments = resp.json()
        assert any(a["user_id"] == "user-x" for a in assignments)


class TestAvatarAudioRoute:
    """GET/PUT /admin/avatar/audio-route/{room}"""

    async def test_get_audio_route(self, client, auth_header):
        resp = await client.get("/admin/avatar/audio-route/kitchen", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["room"] == "kitchen"
        assert "route" in body

    async def test_set_audio_route(self, client, auth_header):
        resp = await client.put(
            "/admin/avatar/audio-route/kitchen",
            json={"route": "both"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["route"] == "both"

    async def test_set_invalid_audio_route(self, client, auth_header):
        resp = await client.put(
            "/admin/avatar/audio-route/kitchen",
            json={"route": "invalid"},
            headers=auth_header,
        )
        assert resp.status_code == 422


# ════════════════════════════════════════════════════════════════════
# 8. DEVICES (DevicesView)
# ════════════════════════════════════════════════════════════════════


class TestDevices:
    """GET /admin/devices"""

    async def test_list_empty(self, client, auth_header):
        resp = await client.get("/admin/devices", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["devices"] == []

    async def test_list_with_devices(self, client, auth_header, db_path):
        _insert_device(db_path, "light.kitchen", "Kitchen Light", "light")
        _insert_device(db_path, "switch.fan", "Fan", "switch")
        resp = await client.get("/admin/devices", headers=auth_header)
        body = resp.json()
        assert body["total"] == 2

    async def test_filter_by_domain(self, client, auth_header, db_path):
        _insert_device(db_path, "light.kitchen", "Kitchen Light", "light")
        _insert_device(db_path, "switch.fan", "Fan", "switch")
        resp = await client.get(
            "/admin/devices", params={"domain": "light"}, headers=auth_header
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["devices"][0]["domain"] == "light"

    async def test_devices_no_auth(self, client):
        resp = await client.get("/admin/devices")
        assert resp.status_code == 401


class TestDevicePatterns:
    """GET/PATCH/DELETE /admin/devices/patterns"""

    async def test_list_empty(self, client, auth_header):
        resp = await client.get("/admin/devices/patterns", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0

    async def test_list_with_patterns(self, client, auth_header, db_path):
        _insert_device(db_path)
        _insert_command_pattern(db_path)
        resp = await client.get("/admin/devices/patterns", headers=auth_header)
        assert resp.json()["total"] >= 1

    async def test_update_pattern(self, client, auth_header, db_path):
        _insert_device(db_path)
        _insert_command_pattern(db_path)
        resp = await client.get("/admin/devices/patterns", headers=auth_header)
        pid = resp.json()["patterns"][0]["id"]

        resp2 = await client.patch(
            f"/admin/devices/patterns/{pid}",
            json={"intent": "toggle"},
            headers=auth_header,
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    async def test_delete_pattern(self, client, auth_header, db_path):
        _insert_device(db_path)
        _insert_command_pattern(db_path)
        resp = await client.get("/admin/devices/patterns", headers=auth_header)
        pid = resp.json()["patterns"][0]["id"]

        resp2 = await client.delete(
            f"/admin/devices/patterns/{pid}", headers=auth_header
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    async def test_patterns_no_auth(self, client):
        resp = await client.get("/admin/devices/patterns")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 9. SATELLITES (SatellitesView)
# ════════════════════════════════════════════════════════════════════


class TestSatellites:
    """GET /admin/satellites — satellite listing and management.

    Most satellite endpoints proxy to SatelliteManager which needs live
    hardware.  We test the DB-backed list and detail endpoints directly.
    """

    async def test_list_satellites(self, client, auth_header, db_path):
        """List endpoint uses SatelliteManager which may not be available in tests."""
        from unittest.mock import AsyncMock, MagicMock

        _insert_satellite(db_path)
        mock_mgr = MagicMock()
        mock_mgr.list_satellites.return_value = [
            {"id": "sat-1", "room": "kitchen", "status": "online"}
        ]
        mock_mgr.get_discovered = AsyncMock(return_value=[])
        with patch("cortex.admin.satellites._get_satellite_manager", return_value=mock_mgr):
            resp = await client.get("/admin/satellites", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "satellites" in body
        assert "total" in body
        assert "announced_count" in body

    async def test_get_satellite_not_found(self, client, auth_header):
        from unittest.mock import MagicMock

        mock_mgr = MagicMock()
        mock_mgr.get_satellite.return_value = None
        with patch("cortex.admin.satellites._get_satellite_manager", return_value=mock_mgr):
            resp = await client.get("/admin/satellites/nope", headers=auth_header)
        assert resp.status_code == 404

    async def test_discover_satellites(self, client, auth_header):
        from unittest.mock import AsyncMock, MagicMock

        mock_mgr = MagicMock()
        mock_mgr.scan_now = AsyncMock(return_value=[])
        with patch("cortex.admin.satellites._get_satellite_manager", return_value=mock_mgr):
            resp = await client.post("/admin/satellites/discover", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "found" in body
        assert "count" in body

    async def test_satellites_no_auth(self, client):
        resp = await client.get("/admin/satellites")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 10. EVOLUTION (EvolutionView)
# ════════════════════════════════════════════════════════════════════


class TestEvolutionProfiles:
    """GET /admin/evolution/profiles"""

    async def test_list_profiles_empty(self, client, auth_header):
        resp = await client.get("/admin/evolution/profiles", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["profiles"] == []

    async def test_list_profiles_with_data(self, client, auth_header, db_path):
        _insert_user(db_path, "u1", "Derek")
        _insert_emotional_profile(db_path, "u1")
        resp = await client.get("/admin/evolution/profiles", headers=auth_header)
        body = resp.json()
        assert body["total"] >= 1
        assert "top_topics" in body["profiles"][0]

    async def test_profiles_no_auth(self, client):
        resp = await client.get("/admin/evolution/profiles")
        assert resp.status_code == 401


class TestEvolutionLogs:
    """GET /admin/evolution/logs"""

    async def test_list_logs_empty(self, client, auth_header):
        resp = await client.get("/admin/evolution/logs", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["logs"] == []

    async def test_logs_with_limit(self, client, auth_header):
        resp = await client.get(
            "/admin/evolution/logs", params={"limit": 5}, headers=auth_header
        )
        assert resp.status_code == 200

    async def test_logs_no_auth(self, client):
        resp = await client.get("/admin/evolution/logs")
        assert resp.status_code == 401


class TestEvolutionMistakes:
    """GET/PATCH /admin/evolution/mistakes"""

    async def test_list_mistakes_empty(self, client, auth_header):
        resp = await client.get("/admin/evolution/mistakes", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["mistakes"] == []

    async def test_list_mistakes_with_data(self, client, auth_header, db_path):
        _insert_interaction(db_path)
        _insert_mistake(db_path, interaction_id=1)
        resp = await client.get("/admin/evolution/mistakes", headers=auth_header)
        body = resp.json()
        assert body["total"] >= 1

    async def test_resolve_mistake(self, client, auth_header, db_path):
        _insert_interaction(db_path)
        _insert_mistake(db_path, interaction_id=1)
        resp = await client.get("/admin/evolution/mistakes", headers=auth_header)
        mid = resp.json()["mistakes"][0]["id"]

        resp2 = await client.patch(
            f"/admin/evolution/mistakes/{mid}", headers=auth_header
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    async def test_filter_mistakes_by_resolved(self, client, auth_header, db_path):
        _insert_interaction(db_path)
        _insert_mistake(db_path, interaction_id=1)
        resp = await client.get(
            "/admin/evolution/mistakes",
            params={"resolved": "false"},
            headers=auth_header,
        )
        assert resp.status_code == 200

    async def test_mistakes_no_auth(self, client):
        resp = await client.get("/admin/evolution/mistakes")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 11. SYSTEM (SystemView)
# ════════════════════════════════════════════════════════════════════


class TestSystemHardware:
    """GET /admin/system/hardware"""

    async def test_hardware_info(self, client, auth_header):
        resp = await client.get("/admin/system/hardware", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "profile" in body
        assert "gpus" in body

    async def test_hardware_no_auth(self, client):
        resp = await client.get("/admin/system/hardware")
        assert resp.status_code == 401


class TestSystemModels:
    """GET /admin/system/models"""

    async def test_models_info(self, client, auth_header):
        resp = await client.get("/admin/system/models", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "models" in body
        assert isinstance(body["models"], list)


class TestSystemServices:
    """GET /admin/system/services"""

    async def test_services_info(self, client, auth_header):
        resp = await client.get("/admin/system/services", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "services" in body
        assert isinstance(body["services"], list)


class TestSystemBackups:
    """GET /admin/system/backups"""

    async def test_backups_list(self, client, auth_header):
        resp = await client.get("/admin/system/backups", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "backups" in body
        assert isinstance(body["backups"], list)

    async def test_backups_with_limit(self, client, auth_header):
        resp = await client.get(
            "/admin/system/backups", params={"limit": 5}, headers=auth_header
        )
        assert resp.status_code == 200


class TestSystemInteractions:
    """GET /admin/system/interactions"""

    async def test_interactions_empty(self, client, auth_header):
        resp = await client.get("/admin/system/interactions", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["interactions"] == []

    async def test_interactions_with_data(self, client, auth_header, db_path):
        _insert_interaction(db_path, "u1", "hello", "layer1")
        _insert_interaction(db_path, "u2", "bye", "layer3")
        resp = await client.get("/admin/system/interactions", headers=auth_header)
        body = resp.json()
        assert body["total"] == 2

    async def test_interactions_filter_by_user(self, client, auth_header, db_path):
        _insert_interaction(db_path, "u1", "hello", "layer1")
        _insert_interaction(db_path, "u2", "bye", "layer3")
        resp = await client.get(
            "/admin/system/interactions",
            params={"user_id": "u1"},
            headers=auth_header,
        )
        body = resp.json()
        assert body["total"] == 1

    async def test_interactions_filter_by_layer(self, client, auth_header, db_path):
        _insert_interaction(db_path, "u1", "hello", "layer1")
        _insert_interaction(db_path, "u2", "bye", "layer3")
        resp = await client.get(
            "/admin/system/interactions",
            params={"layer": "layer1"},
            headers=auth_header,
        )
        body = resp.json()
        assert body["total"] == 1

    async def test_interactions_no_auth(self, client):
        resp = await client.get("/admin/system/interactions")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 12. SETTINGS
# ════════════════════════════════════════════════════════════════════


class TestSettings:
    """GET /admin/settings, PUT /admin/settings/{key}"""

    async def test_get_settings_empty(self, client, auth_header):
        resp = await client.get("/admin/settings", headers=auth_header)
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    async def test_set_and_get_setting(self, client, auth_header):
        resp = await client.put(
            "/admin/settings/theme",
            json={"value": "dark"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "theme"
        assert resp.json()["value"] == "dark"

        # Verify in GET
        resp2 = await client.get("/admin/settings", headers=auth_header)
        assert resp2.json().get("theme") == "dark"

    async def test_update_setting(self, client, auth_header):
        await client.put(
            "/admin/settings/theme",
            json={"value": "dark"},
            headers=auth_header,
        )
        resp = await client.put(
            "/admin/settings/theme",
            json={"value": "light"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "light"

    async def test_settings_no_auth(self, client):
        resp = await client.get("/admin/settings")
        assert resp.status_code == 401


# ════════════════════════════════════════════════════════════════════
# 13. CROSS-CUTTING: Auth enforcement across all protected endpoints
# ════════════════════════════════════════════════════════════════════


class TestAuthEnforcement:
    """Verify every protected endpoint rejects unauthenticated requests."""

    PROTECTED_GETS = [
        "/admin/dashboard",
        "/admin/users",
        "/admin/safety/events",
        "/admin/safety/patterns",
        "/admin/voice/speakers",
        "/admin/devices",
        "/admin/devices/patterns",
        "/admin/evolution/profiles",
        "/admin/evolution/logs",
        "/admin/evolution/mistakes",
        "/admin/system/hardware",
        "/admin/system/models",
        "/admin/system/services",
        "/admin/system/backups",
        "/admin/system/interactions",
        "/admin/settings",
        "/admin/tts/voices",
    ]

    PROTECTED_POSTS = [
        "/admin/users",
        "/admin/safety/patterns",
        "/admin/auth/change-password",
    ]

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    async def test_get_requires_auth(self, client, path):
        resp = await client.get(path)
        assert resp.status_code == 401, f"GET {path} should require auth"

    @pytest.mark.parametrize("path", PROTECTED_POSTS)
    async def test_post_requires_auth(self, client, path):
        resp = await client.post(path, json={})
        assert resp.status_code in (401, 422), f"POST {path} should require auth"


# ════════════════════════════════════════════════════════════════════
# 14. END-TO-END FLOW: Full admin portal walkthrough
# ════════════════════════════════════════════════════════════════════


class TestFullPortalWalkthrough:
    """Simulate a complete admin session: login → navigate pages → manage data."""

    async def test_login_and_browse_all_pages(self, client):
        """Login, then visit every page's primary endpoint."""
        # 1. Login
        login_resp = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "atlas-admin"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Dashboard
        resp = await client.get("/admin/dashboard", headers=headers)
        assert resp.status_code == 200

        # 3. Users
        resp = await client.get("/admin/users", headers=headers)
        assert resp.status_code == 200

        # 4. Safety
        resp = await client.get("/admin/safety/events", headers=headers)
        assert resp.status_code == 200

        # 5. Devices
        resp = await client.get("/admin/devices", headers=headers)
        assert resp.status_code == 200

        # 6. Voice
        resp = await client.get("/admin/voice/speakers", headers=headers)
        assert resp.status_code == 200

        # 7. Evolution
        resp = await client.get("/admin/evolution/profiles", headers=headers)
        assert resp.status_code == 200

        # 8. System
        resp = await client.get("/admin/system/hardware", headers=headers)
        assert resp.status_code == 200

        # 9. Settings
        resp = await client.get("/admin/settings", headers=headers)
        assert resp.status_code == 200

        # 10. Avatar
        resp = await client.get("/admin/avatar/skins", headers=headers)
        assert resp.status_code == 200

        # 11. Who am I?
        resp = await client.get("/admin/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    async def test_user_lifecycle(self, client, auth_header):
        """Create → read → update → set age → set parental → delete."""
        # Create
        resp = await client.post(
            "/admin/users",
            json={"display_name": "Lifecycle User", "user_id": "lc-1"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "lc-1"

        # Read
        resp = await client.get("/admin/users/lc-1", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Lifecycle User"

        # Update
        resp = await client.patch(
            "/admin/users/lc-1",
            json={"vocabulary_level": "advanced", "preferred_tone": "warm"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["vocabulary_level"] == "advanced"

        # Set age
        resp = await client.post(
            "/admin/users/lc-1/age",
            json={"birth_year": 2015, "birth_month": 3},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["age_group"] in ("child", "teen", "adult")

        # Create a parent user for parental controls FK
        await client.post(
            "/admin/users",
            json={"display_name": "Parent User", "user_id": "lc-parent"},
            headers=auth_header,
        )

        # Set parental controls
        resp = await client.post(
            "/admin/users/lc-1/parental",
            json={
                "parent_user_id": "lc-parent",
                "restricted_actions": ["device_control"],
            },
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Verify parental controls
        resp = await client.get("/admin/users/lc-1/parental", headers=auth_header)
        assert resp.json()["controls"] is not None

        # Remove parental controls before deleting user (FK constraint)
        resp = await client.delete("/admin/users/lc-1/parental", headers=auth_header)
        assert resp.status_code == 200

        # Delete
        resp = await client.delete("/admin/users/lc-1", headers=auth_header)
        assert resp.status_code == 200

        # Also clean up the parent user
        resp = await client.delete("/admin/users/lc-parent", headers=auth_header)
        assert resp.status_code == 200

        # Verify gone
        resp = await client.get("/admin/users/lc-1", headers=auth_header)
        assert resp.status_code == 404

    async def test_safety_pattern_lifecycle(self, client, auth_header):
        """Add pattern → verify in list → delete → verify removed."""
        resp = await client.post(
            "/admin/safety/patterns",
            json={"pattern": "lifecycle_test_pattern"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        resp = await client.get("/admin/safety/patterns", headers=auth_header)
        patterns = resp.json()["patterns"]
        matched = [p for p in patterns if p["pattern"] == "lifecycle_test_pattern"]
        assert len(matched) == 1
        pid = matched[0]["id"]

        resp = await client.delete(
            f"/admin/safety/patterns/{pid}", headers=auth_header
        )
        assert resp.status_code == 200

        resp = await client.get("/admin/safety/patterns", headers=auth_header)
        patterns = resp.json()["patterns"]
        assert not any(p["pattern"] == "lifecycle_test_pattern" for p in patterns)

    async def test_avatar_skin_lifecycle(self, client, auth_header):
        """Create skin → assign → list assignments → remove → delete skin."""
        # Create
        resp = await client.post(
            "/admin/avatar/skins",
            json={"id": "lc-skin", "name": "Lifecycle Skin", "type": "svg"},
            headers=auth_header,
        )
        assert resp.status_code == 201

        # Assign to user
        resp = await client.put(
            "/admin/avatar/assignments/lc-user",
            json={"skin_id": "lc-skin"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Verify in assignments list
        resp = await client.get("/admin/avatar/assignments", headers=auth_header)
        assert any(a["user_id"] == "lc-user" for a in resp.json())

        # Remove assignment
        resp = await client.delete(
            "/admin/avatar/assignments/lc-user", headers=auth_header
        )
        assert resp.status_code == 200

        # Delete skin
        resp = await client.delete("/admin/avatar/skins/lc-skin", headers=auth_header)
        assert resp.status_code == 200

    async def test_settings_round_trip(self, client, auth_header):
        """Set multiple settings → verify in bulk GET."""
        await client.put(
            "/admin/settings/locale",
            json={"value": "en-US"},
            headers=auth_header,
        )
        await client.put(
            "/admin/settings/timezone",
            json={"value": "America/New_York"},
            headers=auth_header,
        )

        resp = await client.get("/admin/settings", headers=auth_header)
        settings = resp.json()
        assert settings["locale"] == "en-US"
        assert settings["timezone"] == "America/New_York"
