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


# ── SVG expression mouth tests ──────────────────────────────────

import re
from pathlib import Path

_SKINS_DIR = Path(__file__).resolve().parent.parent / "cortex" / "avatar" / "skins"

_NEED_MOUTH = [
    "happy", "sad", "angry", "surprised", "thinking", "confused",
    "excited", "scared", "concerned", "proud", "love", "crying",
    "sleepy", "winking", "laughing", "silly",
]

_ALL_EXPRESSIONS = [
    "neutral", "happy", "sad", "angry", "surprised", "thinking",
    "confused", "excited", "scared", "concerned", "listening",
    "proud", "love", "crying", "sleepy", "winking", "laughing", "silly",
]


def _expr_groups(svg_text: str) -> dict[str, str]:
    """Return a dict mapping expression name to its full <g>...</g> block."""
    groups = {}
    for m in re.finditer(
        r'<g\s+id="expr-(\w+)"[^>]*>.*?</g>', svg_text, re.DOTALL,
    ):
        groups[m.group(1)] = m.group(0)
    return groups


@pytest.mark.parametrize("skin", ["default.svg", "nick.svg"])
class TestSvgExpressionMouths:
    """Shared expression library provides mouth + eyes + eyebrows."""

    def _load(self, skin: str) -> str:
        return (_SKINS_DIR / skin).read_text()

    def test_all_expression_ids_exist(self, skin):
        svg = self._load(skin)
        for expr in _ALL_EXPRESSIONS:
            assert f'id="expr-{expr}"' in svg, f"Missing expr-{expr} in {skin}"

    def test_mouth_idle_exists(self, skin):
        svg = self._load(skin)
        assert 'id="mouth-IDLE"' in svg, f"Missing mouth-IDLE in {skin}"

    def test_anchors_exist(self, skin):
        svg = self._load(skin)
        assert 'id="mouth-anchor"' in svg, f"Missing mouth-anchor in {skin}"
        assert 'id="eyes-anchor"' in svg, f"Missing eyes-anchor in {skin}"
        assert 'id="eyebrows-anchor"' in svg, f"Missing eyebrows-anchor in {skin}"

    def test_expressions_have_replace_mouth(self, skin):
        """Verify that expressions.json defines mouths for all _NEED_MOUTH entries."""
        import json
        lib_path = _SKINS_DIR / "expressions.json"
        lib = json.loads(lib_path.read_text())
        for expr in _NEED_MOUTH:
            entry = lib["expressions"].get(expr)
            assert entry is not None, f"expression {expr} missing from expressions.json"
            assert entry.get("replace_mouth"), (
                f"{expr} should have replace_mouth=true in expressions.json"
            )

    def test_expressions_have_mouth_child(self, skin):
        """Mouth shapes are in expressions.json, not hardcoded in skin SVGs."""
        import json
        lib_path = _SKINS_DIR / "expressions.json"
        lib = json.loads(lib_path.read_text())
        for expr in _NEED_MOUTH:
            entry = lib["expressions"].get(expr, {})
            mouth = entry.get("mouth", {})
            assert mouth.get("type") in ("path", "ellipse"), (
                f"{expr} has no valid mouth in expressions.json"
            )

    def test_expressions_have_eyes(self, skin):
        """Every non-neutral expression should have eyes in expressions.json."""
        import json
        lib_path = _SKINS_DIR / "expressions.json"
        lib = json.loads(lib_path.read_text())
        for expr in _NEED_MOUTH:
            entry = lib["expressions"].get(expr, {})
            assert entry.get("replace_eyes"), (
                f"{expr} should have replace_eyes=true in expressions.json"
            )
            eyes = entry.get("eyes", {})
            assert "left" in eyes and "right" in eyes, (
                f"{expr} missing left/right eyes in expressions.json"
            )

    def test_expressions_have_eyebrows(self, skin):
        """Every non-neutral expression should have eyebrows in expressions.json."""
        import json
        lib_path = _SKINS_DIR / "expressions.json"
        lib = json.loads(lib_path.read_text())
        for expr in _NEED_MOUTH:
            entry = lib["expressions"].get(expr, {})
            brows = entry.get("eyebrows", {})
            assert "left" in brows and "right" in brows, (
                f"{expr} missing left/right eyebrows in expressions.json"
            )

    def test_neutral_and_listening_no_replace_mouth(self, skin):
        svg = self._load(skin)
        groups = _expr_groups(svg)
        for expr in ("neutral", "listening"):
            g = groups.get(expr, "")
            assert 'data-replace-mouth="true"' not in g, (
                f"expr-{expr} should NOT have data-replace-mouth in {skin}"
            )
