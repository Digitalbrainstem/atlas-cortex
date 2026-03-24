"""Tests for satellite display content system.

# Module ownership: Satellite display test coverage
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.satellite.display_protocol import (
    DISPLAY_MODES,
    DisplayCommand,
    send_display_command,
    validate_display_command,
)


# ── DisplayCommand dataclass ─────────────────────────────────────


class TestDisplayCommand:
    def test_default_values(self):
        cmd = DisplayCommand(mode="avatar")
        assert cmd.mode == "avatar"
        assert cmd.content == {}
        assert cmd.target_room == ""
        assert cmd.duration_seconds == 0

    def test_with_content(self):
        cmd = DisplayCommand(
            mode="video",
            content={"provider": "youtube", "video_id": "abc123"},
            target_room="kitchen",
            duration_seconds=30,
        )
        assert cmd.mode == "video"
        assert cmd.content["video_id"] == "abc123"
        assert cmd.target_room == "kitchen"
        assert cmd.duration_seconds == 30


# ── DISPLAY_MODES registry ───────────────────────────────────────


class TestDisplayModes:
    def test_all_expected_modes_present(self):
        expected = {
            "avatar", "recipe", "video", "dashboard", "weather",
            "photos", "timer", "list", "calendar", "learning", "media_player",
        }
        assert expected == set(DISPLAY_MODES.keys())

    def test_every_mode_has_description(self):
        for mode, info in DISPLAY_MODES.items():
            assert "description" in info, f"{mode} missing description"

    def test_every_mode_has_content_schema(self):
        for mode, info in DISPLAY_MODES.items():
            assert "content_schema" in info, f"{mode} missing content_schema"

    def test_video_providers(self):
        providers = DISPLAY_MODES["video"]["providers"]
        assert "youtube" in providers
        assert "local" in providers
        assert "plex" in providers
        assert "netflix" in providers

    def test_plex_not_ready(self):
        assert DISPLAY_MODES["video"]["providers"]["plex"]["ready"] is False

    def test_netflix_not_ready(self):
        assert DISPLAY_MODES["video"]["providers"]["netflix"]["ready"] is False

    def test_youtube_auth_required(self):
        assert DISPLAY_MODES["video"]["providers"]["youtube"]["auth_required"] is True

    def test_local_no_auth(self):
        assert DISPLAY_MODES["video"]["providers"]["local"]["auth_required"] is False


# ── validate_display_command ─────────────────────────────────────


class TestValidateDisplayCommand:
    def test_valid_avatar(self):
        ok, err = validate_display_command(DisplayCommand(mode="avatar"))
        assert ok
        assert err == ""

    def test_valid_recipe(self):
        ok, err = validate_display_command(
            DisplayCommand(mode="recipe", content={"title": "Pasta"})
        )
        assert ok

    def test_unknown_mode_rejected(self):
        ok, err = validate_display_command(DisplayCommand(mode="hologram"))
        assert not ok
        assert "Unknown display mode" in err

    def test_dashboard_url_must_match_ha_url(self):
        with patch.dict(os.environ, {"HA_URL": "http://homeassistant.local:8123"}):
            ok, err = validate_display_command(
                DisplayCommand(mode="dashboard", content={"url": "http://evil.com"})
            )
            assert not ok
            assert "HA_URL" in err

    def test_dashboard_url_matching_ha_url_ok(self):
        with patch.dict(os.environ, {"HA_URL": "http://ha.local:8123"}):
            ok, err = validate_display_command(
                DisplayCommand(
                    mode="dashboard",
                    content={"url": "http://ha.local:8123/lovelace/home"},
                )
            )
            assert ok

    def test_dashboard_no_ha_url_allows_anything(self):
        with patch.dict(os.environ, {}, clear=True):
            ok, err = validate_display_command(
                DisplayCommand(mode="dashboard", content={"url": "http://any.url"})
            )
            assert ok

    def test_unknown_video_provider_rejected(self):
        ok, err = validate_display_command(
            DisplayCommand(mode="video", content={"provider": "dailymotion"})
        )
        assert not ok
        assert "Unknown video provider" in err

    def test_valid_youtube_provider(self):
        ok, err = validate_display_command(
            DisplayCommand(
                mode="video",
                content={"provider": "youtube", "video_id": "abc"},
            )
        )
        assert ok

    def test_plex_provider_not_ready(self):
        ok, err = validate_display_command(
            DisplayCommand(mode="video", content={"provider": "plex"})
        )
        assert not ok
        assert "not yet implemented" in err

    def test_netflix_provider_not_ready(self):
        ok, err = validate_display_command(
            DisplayCommand(mode="video", content={"provider": "netflix"})
        )
        assert not ok
        assert "not yet implemented" in err

    def test_local_video_ok(self):
        ok, err = validate_display_command(
            DisplayCommand(
                mode="video",
                content={"provider": "local", "video_id": "/videos/clip.mp4"},
            )
        )
        assert ok


# ── send_display_command ─────────────────────────────────────────


class TestSendDisplayCommand:
    async def test_sends_valid_command(self):
        mock_broadcast = AsyncMock()
        with patch(
            "cortex.satellite.display_protocol.broadcast_to_room",
            mock_broadcast,
            create=True,
        ), patch("cortex.avatar.broadcast.broadcast_to_room", mock_broadcast):
            result = await send_display_command(
                "kitchen", "timer", {"label": "Eggs", "total_seconds": 300}
            )
        assert result is True
        mock_broadcast.assert_called_once()
        call_args = mock_broadcast.call_args
        assert call_args[0][0] == "kitchen"
        msg = call_args[0][1]
        assert msg["type"] == "display"
        assert msg["mode"] == "timer"
        assert msg["content"]["label"] == "Eggs"

    async def test_rejects_invalid_mode(self):
        result = await send_display_command("room", "hologram")
        assert result is False

    async def test_default_content_empty_dict(self):
        mock_broadcast = AsyncMock()
        with patch(
            "cortex.satellite.display_protocol.broadcast_to_room",
            mock_broadcast,
            create=True,
        ), patch("cortex.avatar.broadcast.broadcast_to_room", mock_broadcast):
            result = await send_display_command("room", "avatar")
        assert result is True

    async def test_handles_broadcast_exception(self):
        with patch(
            "cortex.avatar.broadcast.broadcast_to_room",
            AsyncMock(side_effect=RuntimeError("ws down")),
        ):
            result = await send_display_command("room", "avatar")
        assert result is False


# ── MediaAuthManager ─────────────────────────────────────────────

from cortex.satellite.display_auth import (  # noqa: E402
    AUTH_FILE,
    MediaAuth,
    MediaAuthManager,
    get_media_auth,
)


class TestMediaAuth:
    @pytest.fixture(autouse=True)
    def _use_tmp_auth_file(self, tmp_path, monkeypatch):
        auth_path = tmp_path / "media_auth.json"
        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE", auth_path
        )
        self.auth_path = auth_path

    def test_set_and_get_auth(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="tok123", account_name="Me", is_premium=True)
        auth = mgr.get_auth("youtube")
        assert auth is not None
        assert auth.token == "tok123"
        assert auth.account_name == "Me"
        assert auth.is_premium is True

    def test_is_authenticated(self):
        mgr = MediaAuthManager()
        assert not mgr.is_authenticated("youtube")
        mgr.set_auth("youtube", token="tok")
        assert mgr.is_authenticated("youtube")

    def test_is_premium(self):
        mgr = MediaAuthManager()
        assert not mgr.is_premium("youtube")
        mgr.set_auth("youtube", token="tok", is_premium=True)
        assert mgr.is_premium("youtube")

    def test_remove_auth(self):
        mgr = MediaAuthManager()
        mgr.set_auth("plex", token="ptok")
        assert mgr.remove_auth("plex")
        assert mgr.get_auth("plex") is None
        assert not mgr.remove_auth("plex")  # already gone

    def test_list_providers_no_secrets(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="secret!", account_name="Acc", is_premium=True)
        listing = mgr.list_providers()
        assert len(listing) == 1
        entry = listing[0]
        assert entry["provider"] == "youtube"
        assert entry["account_name"] == "Acc"
        assert entry["is_premium"] is True
        assert entry["authenticated"] is True
        # Token must NOT appear in listing
        assert "secret!" not in str(listing)

    def test_persistence(self):
        mgr1 = MediaAuthManager()
        mgr1.set_auth("youtube", token="t1", account_name="A")
        # New manager loads from disk
        mgr2 = MediaAuthManager()
        auth = mgr2.get_auth("youtube")
        assert auth is not None
        assert auth.token == "t1"

    def test_file_permissions(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="x")
        mode = self.auth_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_youtube_embed_url_basic(self):
        mgr = MediaAuthManager()
        url = mgr.get_youtube_embed_url("dQw4w9WgXcQ")
        assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in url
        assert "autoplay=1" in url
        assert "rel=0" in url

    def test_youtube_embed_url_premium(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="tok", is_premium=True)
        url = mgr.get_youtube_embed_url("xyz")
        assert "fs=1" in url
        assert "youtube-nocookie.com" in url

    def test_youtube_embed_url_no_premium(self):
        mgr = MediaAuthManager()
        url = mgr.get_youtube_embed_url("xyz")
        assert "fs=1" not in url

    def test_load_corrupt_file(self):
        self.auth_path.write_text("not json")
        mgr = MediaAuthManager()
        assert mgr.list_providers() == []


# ── get_media_auth singleton ─────────────────────────────────────


class TestGetMediaAuth:
    def test_returns_manager(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )
        monkeypatch.setattr("cortex.satellite.display_auth._manager", None)
        mgr = get_media_auth()
        assert isinstance(mgr, MediaAuthManager)
        assert get_media_auth() is mgr  # same instance


# ── DisplayTool (CLI agent) ──────────────────────────────────────

from cortex.cli.tools.atlas import DisplayTool  # noqa: E402


class TestDisplayTool:
    def test_tool_metadata(self):
        tool = DisplayTool()
        assert tool.tool_id == "display"
        assert "satellite" in tool.description.lower() or "tablet" in tool.description.lower()
        schema = tool.parameters_schema
        assert "mode" in schema["properties"]
        assert "required" in schema
        assert "mode" in schema["required"]

    async def test_execute_avatar(self):
        tool = DisplayTool()
        with patch(
            "cortex.satellite.display_protocol.send_display_command",
            new_callable=AsyncMock,
            return_value=True,
        ), patch("cortex.avatar.broadcast.broadcast_to_room", AsyncMock()):
            result = await tool.execute({"mode": "avatar"})
        assert result.success

    async def test_execute_invalid_mode(self):
        tool = DisplayTool()
        result = await tool.execute({"mode": "hologram"})
        assert not result.success

    async def test_execute_with_room(self):
        tool = DisplayTool()
        with patch(
            "cortex.satellite.display_protocol.send_display_command",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send, patch("cortex.avatar.broadcast.broadcast_to_room", AsyncMock()):
            result = await tool.execute(
                {"mode": "weather", "room": "living_room", "content": {"temperature": 72}}
            )
        assert result.success
        assert "living_room" in result.output


# ── Auto-update script syntax ────────────────────────────────────


class TestAutoUpdate:
    def test_autoupdate_script_syntax(self):
        """Verify the auto-update bash script has valid syntax."""
        import subprocess

        script = Path(__file__).resolve().parent.parent / "satellite" / "tablet" / "autoupdate.sh"
        assert script.exists(), f"autoupdate.sh not found at {script}"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_autoupdate_script_executable(self):
        script = Path(__file__).resolve().parent.parent / "satellite" / "tablet" / "autoupdate.sh"
        assert os.access(script, os.X_OK), "autoupdate.sh must be executable"

    def test_service_file_exists(self):
        svc = Path(__file__).resolve().parent.parent / "satellite" / "tablet" / "atlas-autoupdate.service"
        assert svc.exists()
        text = svc.read_text()
        assert "[Unit]" in text
        assert "[Service]" in text
        assert "autoupdate.sh" in text


# ── Content rendering security ───────────────────────────────────


class TestContentSecurity:
    """Verify content sanitisation and URL validation."""

    def test_display_html_has_escape_function(self):
        """The display.html must define an HTML escape function."""
        html_path = (
            Path(__file__).resolve().parent.parent
            / "cortex"
            / "avatar"
            / "display.html"
        )
        html = html_path.read_text()
        assert "_esc" in html or "escapeHtml" in html

    def test_display_html_has_content_overlay(self):
        html_path = (
            Path(__file__).resolve().parent.parent
            / "cortex"
            / "avatar"
            / "display.html"
        )
        html = html_path.read_text()
        assert "content-overlay" in html
        assert "content-video" in html
        assert "content-recipe" in html
        assert "content-timer" in html
        assert "content-list" in html

    def test_display_html_uses_nocookie(self):
        html_path = (
            Path(__file__).resolve().parent.parent
            / "cortex"
            / "avatar"
            / "display.html"
        )
        html = html_path.read_text()
        assert "youtube-nocookie.com" in html

    def test_dashboard_sandbox_attribute(self):
        html_path = (
            Path(__file__).resolve().parent.parent
            / "cortex"
            / "avatar"
            / "display.html"
        )
        html = html_path.read_text()
        assert "sandbox=" in html

    def test_display_html_handles_display_message(self):
        html_path = (
            Path(__file__).resolve().parent.parent
            / "cortex"
            / "avatar"
            / "display.html"
        )
        html = html_path.read_text()
        assert "handleDisplayCommand" in html
        assert "'display'" in html


# ── Admin media auth endpoints ───────────────────────────────────


class TestAdminMediaAuthEndpoints:
    """Verify the admin router exposes the media auth endpoints."""

    def test_router_has_media_auth_get(self):
        from cortex.admin.media import router

        paths = [r.path for r in router.routes]
        assert "/media/auth" in paths

    def test_router_has_media_auth_post(self):
        from cortex.admin.media import router

        # Check that POST /media/auth/{provider} exists
        found = any(
            "/media/auth/{provider}" in getattr(r, "path", "")
            for r in router.routes
        )
        assert found, "POST /media/auth/{provider} route not found"

    def test_router_has_media_auth_delete(self):
        from cortex.admin.media import router

        found = any(
            "/media/auth/{provider}" in getattr(r, "path", "")
            and "DELETE" in getattr(r, "methods", set())
            for r in router.routes
        )
        assert found, "DELETE /media/auth/{provider} route not found"
