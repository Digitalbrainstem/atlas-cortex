"""Tests for the Atlas Cortex admin API."""

from __future__ import annotations

import sqlite3

import pytest

from cortex.auth import (
    authenticate,
    create_token,
    decode_token,
    hash_password,
    seed_admin,
    verify_password,
)
from cortex.db import init_db, set_db_path


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


# ── Auth unit tests ───────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_and_verify(self):
        h = hash_password("secret123")
        assert verify_password("secret123", h)

    def test_wrong_password(self):
        h = hash_password("secret123")
        assert not verify_password("wrong", h)


class TestJWT:
    def test_create_and_decode(self):
        token = create_token(1, "admin")
        payload = decode_token(token)
        assert payload["sub"] == "1"
        assert payload["username"] == "admin"

    def test_invalid_token(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            decode_token("garbage.token.here")


class TestSeedAdmin:
    def test_seeds_default_admin(self, db):
        seed_admin(db)
        row = db.execute("SELECT * FROM admin_users WHERE username = 'admin'").fetchone()
        assert row is not None
        assert verify_password("atlas-admin", row["password_hash"])

    def test_does_not_duplicate(self, db):
        seed_admin(db)
        seed_admin(db)
        count = db.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        assert count == 1


class TestAuthenticate:
    def test_valid_login(self, db):
        seed_admin(db)
        user = authenticate(db, "admin", "atlas-admin")
        assert user is not None
        assert user["username"] == "admin"

    def test_wrong_password(self, db):
        seed_admin(db)
        assert authenticate(db, "admin", "wrong") is None

    def test_unknown_user(self, db):
        seed_admin(db)
        assert authenticate(db, "nobody", "anything") is None


# ── Admin API integration tests ───────────────────────────────────

@pytest.fixture()
def client(db_path):
    """TestClient with patched DB — creates its own cross-thread-safe connection."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from cortex.admin_api import router
    from cortex.auth import seed_admin as _seed
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    def get_test_db():
        # Create a new connection each time (check_same_thread=False for test client)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _seed(conn)
        return conn

    with patch("cortex.admin_api._db", get_test_db):
        yield TestClient(test_app)


@pytest.fixture()
def auth_header(db):
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


class TestLoginEndpoint:
    def test_login_success(self, client):
        resp = client.post("/admin/auth/login", json={"username": "admin", "password": "atlas-admin"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_failure(self, client):
        resp = client.post("/admin/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401


class TestMeEndpoint:
    def test_me_authenticated(self, client, auth_header):
        resp = client.get("/admin/auth/me", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    def test_me_unauthenticated(self, client):
        resp = client.get("/admin/auth/me")
        assert resp.status_code == 401


class TestDashboardEndpoint:
    def test_dashboard(self, client, auth_header):
        resp = client.get("/admin/dashboard", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "total_interactions" in data


class TestUsersEndpoint:
    def _insert_user(self, db_path, user_id="u1", name="Derek"):
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute(
            "INSERT OR IGNORE INTO user_profiles (user_id, display_name) VALUES (?, ?)",
            (user_id, name),
        )
        conn.commit()
        conn.close()

    def test_list_empty(self, client, auth_header):
        resp = client.get("/admin/users", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_create_and_list(self, client, auth_header, db_path):
        self._insert_user(db_path)
        resp = client.get("/admin/users", headers=auth_header)
        assert resp.json()["total"] == 1
        assert resp.json()["users"][0]["display_name"] == "Derek"

    def test_get_user(self, client, auth_header, db_path):
        self._insert_user(db_path)
        resp = client.get("/admin/users/u1", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Derek"

    def test_get_user_404(self, client, auth_header):
        resp = client.get("/admin/users/nope", headers=auth_header)
        assert resp.status_code == 404

    def test_update_user(self, client, auth_header, db_path):
        self._insert_user(db_path)
        resp = client.patch(
            "/admin/users/u1",
            json={"vocabulary_level": "advanced"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["vocabulary_level"] == "advanced"

    def test_delete_user(self, client, auth_header, db_path):
        self._insert_user(db_path)
        resp = client.delete("/admin/users/u1", headers=auth_header)
        assert resp.status_code == 200


class TestSafetyEndpoints:
    def test_list_events_empty(self, client, auth_header):
        resp = client.get("/admin/safety/events", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_patterns(self, client, auth_header):
        resp = client.get("/admin/safety/patterns", headers=auth_header)
        assert resp.status_code == 200
        assert "patterns" in resp.json()

    def test_add_and_delete_pattern(self, client, auth_header):
        resp = client.post(
            "/admin/safety/patterns",
            json={"pattern": "test.*pattern"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        # Delete (id=1 since it's the first insert)
        resp = client.delete("/admin/safety/patterns/1", headers=auth_header)
        assert resp.status_code == 200


class TestVoiceEndpoints:
    def test_list_speakers_empty(self, client, auth_header):
        resp = client.get("/admin/voice/speakers", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["speakers"] == []


class TestDeviceEndpoints:
    def test_list_devices_empty(self, client, auth_header):
        resp = client.get("/admin/devices", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_patterns_empty(self, client, auth_header):
        resp = client.get("/admin/devices/patterns", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestEvolutionEndpoints:
    def test_list_profiles_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/profiles", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_logs_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/logs", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["logs"] == []


class TestSystemEndpoints:
    def test_hardware(self, client, auth_header):
        resp = client.get("/admin/system/hardware", headers=auth_header)
        assert resp.status_code == 200

    def test_models(self, client, auth_header):
        resp = client.get("/admin/system/models", headers=auth_header)
        assert resp.status_code == 200

    def test_services(self, client, auth_header):
        resp = client.get("/admin/system/services", headers=auth_header)
        assert resp.status_code == 200

    def test_backups(self, client, auth_header):
        resp = client.get("/admin/system/backups", headers=auth_header)
        assert resp.status_code == 200


class TestChangePassword:
    def test_change_password(self, client, auth_header):
        resp = client.post(
            "/admin/auth/change-password",
            json={"current_password": "atlas-admin", "new_password": "new-secret"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        # Verify new password works for login
        resp2 = client.post("/admin/auth/login", json={"username": "admin", "password": "new-secret"})
        assert resp2.status_code == 200

    def test_change_password_wrong_current(self, client, auth_header):
        resp = client.post(
            "/admin/auth/change-password",
            json={"current_password": "wrong", "new_password": "new-secret"},
            headers=auth_header,
        )
        assert resp.status_code == 400
