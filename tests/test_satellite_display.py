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

    # -- multi-user ---------------------------------------------------------

    def test_per_user_set_and_get(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="global_tok", account_name="Family")
        mgr.set_auth("youtube", token="jake_tok", account_name="Jake", user_id="jake")
        # Jake gets his own
        auth = mgr.get_auth("youtube", user_id="jake")
        assert auth is not None
        assert auth.token == "jake_tok"
        assert auth.account_name == "Jake"
        # Global still accessible
        global_auth = mgr.get_auth("youtube")
        assert global_auth is not None
        assert global_auth.token == "global_tok"

    def test_user_fallback_to_global(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="global_tok")
        # No per-user auth for emma → falls back to global
        auth = mgr.get_auth("youtube", user_id="emma")
        assert auth is not None
        assert auth.token == "global_tok"

    def test_user_no_fallback_when_no_global(self):
        mgr = MediaAuthManager()
        auth = mgr.get_auth("youtube", user_id="nobody")
        assert auth is None

    def test_remove_user_auth_keeps_global(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g")
        mgr.set_auth("youtube", token="u", user_id="jake")
        assert mgr.remove_auth("youtube", user_id="jake")
        assert mgr.get_auth("youtube") is not None  # global still there
        assert mgr.get_auth("youtube", user_id="jake") is not None  # falls back

    def test_remove_global_keeps_user(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g")
        mgr.set_auth("youtube", token="u", user_id="jake")
        assert mgr.remove_auth("youtube")  # remove global
        assert mgr.get_auth("youtube") is None
        jake = mgr.get_auth("youtube", user_id="jake")
        assert jake is not None
        assert jake.token == "u"

    def test_set_global_default_from_user(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="dad_tok", account_name="Dad", is_premium=True, user_id="dad")
        assert mgr.set_global_default("youtube", "dad")
        global_auth = mgr.get_auth("youtube")
        assert global_auth is not None
        assert global_auth.token == "dad_tok"
        assert global_auth.account_name == "Dad"
        assert global_auth.is_premium is True

    def test_set_global_default_nonexistent_user(self):
        mgr = MediaAuthManager()
        assert not mgr.set_global_default("youtube", "ghost")

    def test_is_authenticated_user_fallback(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g")
        assert mgr.is_authenticated("youtube", user_id="anyone")

    def test_is_premium_user_specific(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g", is_premium=False)
        mgr.set_auth("youtube", token="u", is_premium=True, user_id="jake")
        assert mgr.is_premium("youtube", user_id="jake")
        assert not mgr.is_premium("youtube")

    def test_list_providers_includes_user_info(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g", account_name="Family")
        mgr.set_auth("youtube", token="u", account_name="Jake", user_id="jake")
        listing = mgr.list_providers()
        assert len(listing) == 2
        global_entry = [e for e in listing if e["is_global"]]
        user_entry = [e for e in listing if not e["is_global"]]
        assert len(global_entry) == 1
        assert global_entry[0]["user_id"] == ""
        assert len(user_entry) == 1
        assert user_entry[0]["user_id"] == "jake"

    def test_storage_key_helper(self):
        assert MediaAuthManager._key("youtube") == "youtube"
        assert MediaAuthManager._key("youtube", "jake") == "youtube:jake"
        assert MediaAuthManager._key("plex", "") == "plex"

    def test_persistence_multi_user(self):
        mgr1 = MediaAuthManager()
        mgr1.set_auth("youtube", token="g", account_name="Global")
        mgr1.set_auth("youtube", token="j", account_name="Jake", user_id="jake")
        # Reload
        mgr2 = MediaAuthManager()
        assert mgr2.get_auth("youtube").token == "g"
        assert mgr2.get_auth("youtube", user_id="jake").token == "j"

    def test_embed_url_per_user_premium(self):
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="g", is_premium=False)
        mgr.set_auth("youtube", token="u", is_premium=True, user_id="jake")
        url = mgr.get_youtube_embed_url("vid1", user_id="jake")
        assert "fs=1" in url
        url_global = mgr.get_youtube_embed_url("vid1")
        assert "fs=1" not in url_global


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

    def test_router_has_youtube_start(self):
        from cortex.admin.media import router

        paths = [getattr(r, "path", "") for r in router.routes]
        assert "/media/auth/youtube/start" in paths

    def test_router_has_youtube_complete(self):
        from cortex.admin.media import router

        paths = [getattr(r, "path", "") for r in router.routes]
        assert "/media/auth/youtube/complete" in paths

    def test_router_has_set_global(self):
        from cortex.admin.media import router

        paths = [getattr(r, "path", "") for r in router.routes]
        assert "/media/auth/{provider}/set-global" in paths


# ── YouTubeOAuth ─────────────────────────────────────────────────

from cortex.satellite.display_auth import YouTubeOAuth  # noqa: E402


class TestYouTubeOAuth:
    """Test the OAuth device flow with mocked Google endpoints."""

    def test_class_constants(self):
        oauth = YouTubeOAuth()
        assert "googleapis.com" in oauth.DEVICE_CODE_URL
        assert "googleapis.com" in oauth.TOKEN_URL
        assert oauth.CLIENT_ID
        assert oauth.CLIENT_SECRET
        assert "youtube" in oauth.SCOPES

    async def test_start_device_flow(self):
        oauth = YouTubeOAuth()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "device_code": "dev_abc",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://google.com/device",
            "expires_in": 1800,
            "interval": 5,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.start_device_flow()

        assert result["device_code"] == "dev_abc"
        assert result["user_code"] == "ABCD-EFGH"
        assert result["verification_url"] == "https://google.com/device"
        assert result["expires_in"] == 1800
        assert result["interval"] == 5

    async def test_poll_for_token_success(self):
        """Simulate: first poll → pending, second poll → success."""
        oauth = YouTubeOAuth()

        responses = [
            {"error": "authorization_pending"},
            {
                "access_token": "ya29.token",
                "refresh_token": "1//refresh",
                "expires_in": 3600,
            },
        ]
        call_count = 0

        mock_client = AsyncMock()

        async def mock_post(url, data=None):
            nonlocal call_count
            resp = MagicMock()
            resp.json.return_value = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.poll_for_token("dev_abc", interval=0, timeout=5)

        assert result is not None
        assert result["access_token"] == "ya29.token"
        assert result["refresh_token"] == "1//refresh"

    async def test_poll_for_token_denied(self):
        oauth = YouTubeOAuth()

        mock_client = AsyncMock()

        async def mock_post(url, data=None):
            resp = MagicMock()
            resp.json.return_value = {"error": "access_denied"}
            return resp

        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.poll_for_token("dev_abc", interval=0, timeout=5)

        assert result is None

    async def test_poll_for_token_expired(self):
        oauth = YouTubeOAuth()

        mock_client = AsyncMock()

        async def mock_post(url, data=None):
            resp = MagicMock()
            resp.json.return_value = {"error": "expired_token"}
            return resp

        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.poll_for_token("dev_abc", interval=0, timeout=5)

        assert result is None

    async def test_poll_for_token_slow_down(self):
        """Verify interval increases when Google returns slow_down."""
        oauth = YouTubeOAuth()
        call_count = 0

        mock_client = AsyncMock()

        async def mock_post(url, data=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.json.return_value = {"error": "slow_down"}
            else:
                resp.json.return_value = {
                    "access_token": "tok",
                    "refresh_token": "ref",
                    "expires_in": 3600,
                }
            return resp

        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.poll_for_token("dev_abc", interval=0, timeout=10)

        assert result is not None
        assert result["access_token"] == "tok"

    async def test_poll_for_token_timeout(self):
        """Verify timeout returns None when it expires without success."""
        oauth = YouTubeOAuth()

        mock_client = AsyncMock()

        async def mock_post(url, data=None):
            resp = MagicMock()
            resp.json.return_value = {"error": "authorization_pending"}
            return resp

        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.poll_for_token("dev_abc", interval=0, timeout=0)

        assert result is None

    async def test_refresh_token_success(self):
        oauth = YouTubeOAuth()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "ya29.new_token",
            "expires_in": 3600,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.refresh_token("1//old_refresh")

        assert result is not None
        assert result["access_token"] == "ya29.new_token"

    async def test_refresh_token_failure(self):
        oauth = YouTubeOAuth()

        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await oauth.refresh_token("1//bad_refresh")

        assert result is None


# ── YouTube OAuth admin endpoint wiring ──────────────────────────


class TestYouTubeOAuthEndpoints:
    """Verify the admin endpoints call the right OAuth methods."""

    async def test_start_youtube_auth_endpoint(self):
        from cortex.admin.media import YouTubeStartRequest, start_youtube_auth

        mock_flow = {
            "device_code": "dc_123",
            "user_code": "WXYZ-1234",
            "verification_url": "https://google.com/device",
            "expires_in": 1800,
            "interval": 5,
        }

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.start_device_flow",
            new_callable=AsyncMock,
            return_value=mock_flow,
        ):
            req = YouTubeStartRequest(user_id="")
            result = await start_youtube_auth(req=req, _={})

        assert result["user_code"] == "WXYZ-1234"
        assert result["device_code"] == "dc_123"
        assert "google.com/device" in result["verification_url"]
        assert "WXYZ-1234" in result["message"]

    async def test_start_youtube_auth_with_user_id(self):
        from cortex.admin.media import YouTubeStartRequest, start_youtube_auth

        mock_flow = {
            "device_code": "dc_456",
            "user_code": "USER-CODE",
            "verification_url": "https://google.com/device",
            "expires_in": 1800,
            "interval": 5,
        }

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.start_device_flow",
            new_callable=AsyncMock,
            return_value=mock_flow,
        ):
            req = YouTubeStartRequest(user_id="jake")
            result = await start_youtube_auth(req=req, _={})

        assert result["user_id"] == "jake"

    async def test_complete_youtube_auth_success(self, tmp_path, monkeypatch):
        from cortex.admin.media import YouTubeCompleteRequest, complete_youtube_auth

        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )
        monkeypatch.setattr("cortex.satellite.display_auth._manager", None)

        mock_token = {
            "access_token": "ya29.ok",
            "refresh_token": "1//ref",
            "expires_in": 3600,
        }

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.poll_for_token",
            new_callable=AsyncMock,
            return_value=mock_token,
        ):
            req = YouTubeCompleteRequest(device_code="dc_123", timeout=5)
            result = await complete_youtube_auth(req=req, _={})

        assert result["ok"] is True
        assert "linked" in result["message"].lower()

        # Verify token was stored
        from cortex.satellite.display_auth import get_media_auth

        mgr = get_media_auth()
        assert mgr.is_authenticated("youtube")
        assert mgr.is_premium("youtube")

    async def test_complete_youtube_auth_per_user(self, tmp_path, monkeypatch):
        from cortex.admin.media import YouTubeCompleteRequest, complete_youtube_auth

        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )
        monkeypatch.setattr("cortex.satellite.display_auth._manager", None)

        mock_token = {
            "access_token": "ya29.jake",
            "refresh_token": "1//jake_ref",
            "expires_in": 3600,
        }

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.poll_for_token",
            new_callable=AsyncMock,
            return_value=mock_token,
        ):
            req = YouTubeCompleteRequest(
                device_code="dc_jake", timeout=5, user_id="jake"
            )
            result = await complete_youtube_auth(req=req, _={})

        assert result["ok"] is True

        from cortex.satellite.display_auth import get_media_auth

        mgr = get_media_auth()
        assert mgr.is_authenticated("youtube", user_id="jake")
        # Global should NOT be set
        assert not mgr.is_authenticated("youtube")

    async def test_complete_youtube_auth_set_global(self, tmp_path, monkeypatch):
        from cortex.admin.media import YouTubeCompleteRequest, complete_youtube_auth

        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )
        monkeypatch.setattr("cortex.satellite.display_auth._manager", None)

        mock_token = {
            "access_token": "ya29.dad",
            "refresh_token": "1//dad_ref",
            "expires_in": 3600,
        }

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.poll_for_token",
            new_callable=AsyncMock,
            return_value=mock_token,
        ):
            req = YouTubeCompleteRequest(
                device_code="dc_dad", timeout=5,
                user_id="dad", set_global=True,
            )
            result = await complete_youtube_auth(req=req, _={})

        assert result["ok"] is True

        from cortex.satellite.display_auth import get_media_auth

        mgr = get_media_auth()
        # Both per-user and global should be set
        assert mgr.is_authenticated("youtube", user_id="dad")
        assert mgr.is_authenticated("youtube")
        assert mgr.get_auth("youtube").token == "ya29.dad"

    async def test_complete_youtube_auth_denied(self, tmp_path, monkeypatch):
        from cortex.admin.media import YouTubeCompleteRequest, complete_youtube_auth

        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )
        monkeypatch.setattr("cortex.satellite.display_auth._manager", None)

        with patch(
            "cortex.satellite.display_auth.YouTubeOAuth.poll_for_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            req = YouTubeCompleteRequest(device_code="dc_123", timeout=5)
            result = await complete_youtube_auth(req=req, _={})

        assert result["ok"] is False
        assert "timed out" in result["message"].lower() or "denied" in result["message"].lower()


# ── Auto-refresh token ───────────────────────────────────────────


class TestEnsureFreshToken:
    """Test transparent auto-refresh of expired access tokens."""

    @pytest.fixture(autouse=True)
    def _use_tmp_auth_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "cortex.satellite.display_auth.AUTH_FILE",
            tmp_path / "media_auth.json",
        )

    async def test_returns_token_when_not_expired(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="valid_tok", refresh_token="ref",
            expires_at=_time.time() + 3600,
        )
        tok = await mgr.ensure_fresh_token("youtube")
        assert tok == "valid_tok"

    async def test_refreshes_expired_token(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="old_tok", refresh_token="ref_tok",
            expires_at=_time.time() - 100,  # expired
        )

        mock_refresh = AsyncMock(return_value={
            "access_token": "new_tok",
            "expires_in": 3600,
        })

        with patch.object(YouTubeOAuth, "refresh_token", mock_refresh):
            tok = await mgr.ensure_fresh_token("youtube")

        assert tok == "new_tok"
        # Verify it was persisted
        auth = mgr.get_auth("youtube")
        assert auth.token == "new_tok"

    async def test_refreshes_within_5min_buffer(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="almost_expired", refresh_token="ref",
            expires_at=_time.time() + 200,  # within 5-min buffer
        )

        mock_refresh = AsyncMock(return_value={
            "access_token": "refreshed",
            "expires_in": 3600,
        })

        with patch.object(YouTubeOAuth, "refresh_token", mock_refresh):
            tok = await mgr.ensure_fresh_token("youtube")

        assert tok == "refreshed"

    async def test_returns_stale_when_no_refresh_token(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="stale_tok", refresh_token="",
            expires_at=_time.time() - 100,
        )
        tok = await mgr.ensure_fresh_token("youtube")
        assert tok == "stale_tok"

    async def test_returns_stale_when_refresh_fails(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="stale_tok", refresh_token="ref",
            expires_at=_time.time() - 100,
        )

        with patch.object(YouTubeOAuth, "refresh_token", AsyncMock(return_value=None)):
            tok = await mgr.ensure_fresh_token("youtube")

        assert tok == "stale_tok"

    async def test_returns_none_when_no_auth(self):
        mgr = MediaAuthManager()
        tok = await mgr.ensure_fresh_token("youtube")
        assert tok is None

    async def test_per_user_auto_refresh(self):
        import time as _time

        mgr = MediaAuthManager()
        mgr.set_auth(
            "youtube", token="jake_old", refresh_token="jake_ref",
            expires_at=_time.time() - 100, user_id="jake",
        )

        mock_refresh = AsyncMock(return_value={
            "access_token": "jake_new",
            "expires_in": 3600,
        })

        with patch.object(YouTubeOAuth, "refresh_token", mock_refresh):
            tok = await mgr.ensure_fresh_token("youtube", user_id="jake")

        assert tok == "jake_new"

    async def test_no_refresh_when_expires_at_zero(self):
        """When expires_at is 0 (not set), do not attempt refresh."""
        mgr = MediaAuthManager()
        mgr.set_auth("youtube", token="tok_no_expiry", refresh_token="ref")
        tok = await mgr.ensure_fresh_token("youtube")
        assert tok == "tok_no_expiry"
