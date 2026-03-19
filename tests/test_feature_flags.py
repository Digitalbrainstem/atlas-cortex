"""Tests for the avatar feature flag system."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cortex.db import init_db, set_db_path


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test_flags.db"
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
    """TestClient with patched DB."""
    from unittest.mock import patch
    from cortex.admin import router as admin_router
    from cortex.auth import seed_admin

    test_app = FastAPI()

    # Mount admin under /admin prefix
    test_app.include_router(admin_router)

    # Mount public avatar config endpoint
    @test_app.get("/api/avatar/config")
    async def get_avatar_config_endpoint(user: str = ""):
        from cortex.avatar.flags import get_avatar_config
        return get_avatar_config(user)

    def get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        seed_admin(conn)
        return conn

    with patch("cortex.admin.helpers._db", get_test_db):
        yield TestClient(test_app)


# ── Unit tests: flag engine ──────────────────────────────────────


class TestGetAvatarConfig:
    def test_defaults_all_off(self, db_path):
        from cortex.avatar.flags import get_avatar_config
        config = get_avatar_config()
        assert isinstance(config, dict)
        for flag, value in config.items():
            assert value is False, f"Flag {flag} should default to False"

    def test_returns_all_known_flags(self, db_path):
        from cortex.avatar.flags import get_avatar_config, KNOWN_FLAGS
        config = get_avatar_config()
        for flag in KNOWN_FLAGS:
            assert flag in config


class TestSetFlag:
    def test_global_flag_persists(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag
        set_flag("global", "show_mic", True)
        config = get_avatar_config()
        assert config["show_mic"] is True

    def test_per_user_override(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag
        set_flag("global", "show_mic", False)
        set_flag("user_123", "show_mic", True)
        config = get_avatar_config("user_123")
        assert config["show_mic"] is True

    def test_user_override_beats_global(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag
        set_flag("global", "show_controls", True)
        set_flag("user_abc", "show_controls", False)
        config = get_avatar_config("user_abc")
        assert config["show_controls"] is False

    def test_unknown_flag_ignored(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag
        set_flag("global", "nonexistent_flag", True)
        config = get_avatar_config()
        assert "nonexistent_flag" not in config


class TestDevMode:
    def test_dev_mode_forces_all_on(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag, KNOWN_FLAGS
        set_flag("global", "dev_mode", True)
        config = get_avatar_config()
        for flag in KNOWN_FLAGS:
            assert config[flag] is True, f"Flag {flag} should be True in dev mode"


class TestGetAllFlags:
    def test_returns_grouped_by_scope(self, db_path):
        from cortex.avatar.flags import get_all_flags, set_flag
        set_flag("global", "show_mic", True)
        set_flag("user_x", "show_debug", True)
        result = get_all_flags()
        assert "global" in result
        assert result["global"]["show_mic"] is True
        assert "user_x" in result
        assert result["user_x"]["show_debug"] is True


class TestResetFlags:
    def test_reset_global(self, db_path):
        from cortex.avatar.flags import get_avatar_config, set_flag, reset_flags
        set_flag("global", "show_mic", True)
        set_flag("global", "show_controls", True)
        reset_flags("global")
        config = get_avatar_config()
        assert config["show_mic"] is False
        assert config["show_controls"] is False

    def test_reset_user_scope(self, db_path):
        from cortex.avatar.flags import get_all_flags, set_flag, reset_flags
        set_flag("user_99", "show_mic", True)
        reset_flags("user_99")
        result = get_all_flags()
        assert "user_99" not in result


# ── API integration tests ────────────────────────────────────────


class TestPublicConfigEndpoint:
    def test_returns_correct_dict(self, client):
        resp = client.get("/api/avatar/config")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "show_mic" in data
        assert "dev_mode" in data
        # All default to False
        for v in data.values():
            assert v is False

    def test_with_user_param(self, client):
        from cortex.avatar.flags import set_flag
        set_flag("test_user", "show_mic", True)
        resp = client.get("/api/avatar/config?user=test_user")
        assert resp.status_code == 200
        assert resp.json()["show_mic"] is True


class TestAdminFlagEndpoints:
    def test_get_flags(self, client):
        resp = client.get("/admin/avatar/flags")
        assert resp.status_code == 200
        data = resp.json()
        assert "global" in data

    def test_update_flag(self, client):
        resp = client.patch(
            "/admin/avatar/flags",
            json={"scope": "global", "flag_name": "show_mic", "enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Verify it persisted
        resp = client.get("/api/avatar/config")
        assert resp.json()["show_mic"] is True

    def test_update_unknown_flag_rejected(self, client):
        resp = client.patch(
            "/admin/avatar/flags",
            json={"scope": "global", "flag_name": "bogus_flag", "enabled": True},
        )
        assert resp.status_code == 400

    def test_toggle_dev_mode(self, client):
        resp = client.post(
            "/admin/avatar/flags/dev-mode",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["dev_mode"] is True

        # All flags should now resolve to True
        resp = client.get("/api/avatar/config")
        data = resp.json()
        for v in data.values():
            assert v is True

    def test_reset_flags(self, client):
        # Set something first
        client.patch(
            "/admin/avatar/flags",
            json={"scope": "global", "flag_name": "show_controls", "enabled": True},
        )
        # Reset
        resp = client.post("/admin/avatar/flags/reset?scope=global")
        assert resp.status_code == 200

        # Verify all off
        resp = client.get("/api/avatar/config")
        for v in resp.json().values():
            assert v is False
