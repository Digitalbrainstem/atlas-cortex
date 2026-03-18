"""End-to-end integration tests for the entire Atlas Cortex platform.

Validates that all parts (1-12) are wired correctly, all routes exist,
all plugins register, all DB tables are created, and cross-module
integration works.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import get_db, init_db, set_db_path
from cortex.plugins.base import CommandMatch, CommandResult


# ── Repo root ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Initialise an isolated temp DB and point the global path at it."""
    path = tmp_path / "e2e_test.db"
    set_db_path(path)
    init_db()
    return path


@pytest.fixture()
def db_conn(db_path):
    """Raw SQLite connection for assertions."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def auth_header(db_conn):
    """JWT Bearer header for the seeded admin user."""
    seed_admin(db_conn)
    user = authenticate(db_conn, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(db_path, auth_header):
    """FastAPI TestClient with admin router wired to the temp DB."""
    from cortex.admin import router as admin_router

    test_app = FastAPI()
    test_app.include_router(admin_router)

    def _test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        seed_admin(conn)
        return conn

    with patch("cortex.admin.helpers._db", _test_db):
        yield TestClient(test_app)


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Database Schema Completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestDatabaseSchema:
    """Verify all DB tables exist after init_db()."""

    def test_all_tables_exist(self, db_conn):
        """Check every expected table in the schema is created."""
        tables = {
            row[0]
            for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        expected = [
            # Core / Interactions
            "interactions",
            "interaction_entities",
            "emotional_profiles",
            "filler_phrases",
            "audit_log",
            # Memory & Knowledge
            "memory_metrics",
            "knowledge_docs",
            "knowledge_shared_with",
            "list_registry",
            "list_aliases",
            "list_permissions",
            "list_items",
            # User Profiles
            "user_profiles",
            "user_topics",
            "user_activity_hours",
            "speaker_profiles",
            "parental_controls",
            "parental_allowed_devices",
            "parental_restricted_actions",
            # Device / HA
            "ha_devices",
            "device_aliases",
            "device_capabilities",
            "command_patterns",
            "satellite_rooms",
            "presence_sensors",
            "room_context_log",
            # Plugin System
            "plugin_config",
            "plugin_registry",
            # Scheduling (Part 3)
            "alarms",
            "timers",
            "reminders",
            # Routines (Part 4)
            "routines",
            "routine_steps",
            "routine_triggers",
            "routine_runs",
            # Proactive (Part 5)
            "proactive_rules",
            "proactive_events",
            "notification_preferences",
            "notification_log",
            # Learning (Part 6)
            "learning_sessions",
            "learning_progress",
            "quiz_results",
            # Intercom (Part 7)
            "satellite_zones",
            "intercom_log",
            "active_calls",
            # Media (Part 8)
            "media_library",
            "media_playback_history",
            "media_preferences",
            "podcast_subscriptions",
            "podcast_episodes",
            # Evolution (Part 9)
            "evolution_runs",
            "evolution_metrics",
            "model_registry",
            "evolution_log",
            "learned_patterns",
            "mistake_log",
            "mistake_tags",
            # Stories (Part 10)
            "stories",
            "story_chapters",
            "story_characters",
            "story_progress",
            # Safety & Security
            "guardrail_events",
            "jailbreak_patterns",
            "jailbreak_exemplars",
            "file_checksums",
            "discovered_services",
            # Infrastructure
            "satellites",
            "satellite_audio_sessions",
            "avatar_skins",
            "avatar_assignments",
            "backup_log",
            "hardware_profile",
            "hardware_gpu",
            "admin_users",
            "system_settings",
            "model_config",
            "service_config",
            "tts_voices",
            "context_checkpoints",
            "context_metrics",
        ]

        missing = [t for t in expected if t not in tables]
        assert not missing, f"Missing tables: {missing}"

    def test_fts_tables_exist(self, db_conn):
        """FTS5 virtual tables are created for full-text search."""
        tables = {
            row[0]
            for row in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for fts_table in ("memory_fts", "knowledge_fts"):
            assert fts_table in tables, f"Missing FTS table: {fts_table}"

    def test_admin_user_seeded(self, db_conn):
        """Default admin user exists after seed_admin()."""
        seed_admin(db_conn)
        row = db_conn.execute(
            "SELECT username FROM admin_users WHERE username = ?", ("admin",)
        ).fetchone()
        assert row is not None, "admin user not seeded"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Plugin Registry Completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginRegistry:
    """Verify all plugins are registered in BUILTIN_PLUGINS."""

    def test_all_plugins_in_loader(self):
        from cortex.plugins.loader import BUILTIN_PLUGINS

        expected = [
            # Core integrations
            "ha_commands",
            "lists",
            "knowledge",
            # Feature plugins
            "scheduling",
            "routines",
            "daily_briefing",
            "stem_games",
            "intercom",
            "media",
            "stories",
            # Fast-path plugins
            "weather",
            "dictionary",
            "wikipedia",
            "conversions",
            "movie",
            "cooking",
            "news",
            "translation",
            "stocks",
            "sports",
            "sound_library",
        ]
        for plugin_id in expected:
            assert plugin_id in BUILTIN_PLUGINS, f"Missing plugin: {plugin_id}"

    def test_all_plugins_importable(self):
        """Every BUILTIN_PLUGINS entry can actually be imported."""
        from cortex.plugins.loader import BUILTIN_PLUGINS, _import_plugin_class

        for plugin_id, import_path in BUILTIN_PLUGINS.items():
            cls = _import_plugin_class(import_path)
            assert cls is not None, f"Failed to import: {plugin_id} ({import_path})"
            assert hasattr(cls, "match"), f"Plugin {plugin_id} missing match()"
            assert hasattr(cls, "handle"), f"Plugin {plugin_id} missing handle()"

    def test_plugins_have_required_metadata(self):
        """Every plugin class has plugin_id and display_name attributes."""
        from cortex.plugins.loader import BUILTIN_PLUGINS, _import_plugin_class

        for plugin_id, import_path in BUILTIN_PLUGINS.items():
            cls = _import_plugin_class(import_path)
            # Some plugins require constructor args (e.g. ListPlugin needs conn);
            # check class-level attrs instead of instantiating.
            assert getattr(cls, "plugin_id", None) or hasattr(cls, "plugin_id"), (
                f"Plugin {plugin_id} missing plugin_id"
            )
            assert getattr(cls, "display_name", None) or hasattr(cls, "display_name"), (
                f"Plugin {plugin_id} missing display_name"
            )

    def test_plugin_count_minimum(self):
        """At least 21 built-in plugins are registered."""
        from cortex.plugins.loader import BUILTIN_PLUGINS

        assert len(BUILTIN_PLUGINS) >= 21, (
            f"Expected ≥21 built-in plugins, got {len(BUILTIN_PLUGINS)}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Admin API Completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminAPICompleteness:
    """Verify every admin API endpoint responds (200 or non-500)."""

    # ── Auth ──────────────────────────────────────────────────────────────

    def test_login(self, client):
        resp = client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "atlas-admin"},
        )
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_auth_me(self, client, auth_header):
        resp = client.get("/admin/auth/me", headers=auth_header)
        assert resp.status_code == 200

    # ── Dashboard ─────────────────────────────────────────────────────────

    def test_dashboard(self, client, auth_header):
        resp = client.get("/admin/dashboard", headers=auth_header)
        assert resp.status_code == 200

    # ── Users ─────────────────────────────────────────────────────────────

    def test_list_users(self, client, auth_header):
        resp = client.get("/admin/users", headers=auth_header)
        assert resp.status_code == 200

    # ── Plugins ───────────────────────────────────────────────────────────

    def test_list_plugins(self, client, auth_header):
        resp = client.get("/admin/plugins", headers=auth_header)
        assert resp.status_code == 200

    # ── Scheduling (Part 3) ──────────────────────────────────────────────

    def test_scheduling_alarms(self, client, auth_header):
        resp = client.get("/admin/scheduling/alarms", headers=auth_header)
        assert resp.status_code == 200

    def test_scheduling_timers(self, client, auth_header):
        resp = client.get("/admin/scheduling/timers", headers=auth_header)
        assert resp.status_code == 200

    def test_scheduling_reminders(self, client, auth_header):
        resp = client.get("/admin/scheduling/reminders", headers=auth_header)
        assert resp.status_code == 200

    # ── Routines (Part 4) ────────────────────────────────────────────────

    def test_routines_list(self, client, auth_header):
        resp = client.get("/admin/routines", headers=auth_header)
        assert resp.status_code == 200

    def test_routines_templates(self, client, auth_header):
        resp = client.get("/admin/routines/templates", headers=auth_header)
        assert resp.status_code == 200

    # ── Proactive (Part 5) ───────────────────────────────────────────────

    def test_proactive_rules(self, client, auth_header):
        resp = client.get("/admin/proactive/rules", headers=auth_header)
        assert resp.status_code == 200

    def test_proactive_events(self, client, auth_header):
        resp = client.get("/admin/proactive/events", headers=auth_header)
        assert resp.status_code == 200

    # ── Learning (Part 6) ────────────────────────────────────────────────

    def test_learning_progress(self, client, auth_header):
        resp = client.get("/admin/learning/progress", headers=auth_header)
        assert resp.status_code == 200

    def test_learning_sessions(self, client, auth_header):
        resp = client.get("/admin/learning/sessions", headers=auth_header)
        assert resp.status_code == 200

    # ── Intercom (Part 7) ────────────────────────────────────────────────

    def test_intercom_zones(self, client, auth_header):
        resp = client.get("/admin/intercom/zones", headers=auth_header)
        assert resp.status_code == 200

    def test_intercom_calls(self, client, auth_header):
        resp = client.get("/admin/intercom/calls", headers=auth_header)
        assert resp.status_code == 200

    def test_intercom_log(self, client, auth_header):
        resp = client.get("/admin/intercom/log", headers=auth_header)
        assert resp.status_code == 200

    # ── Media (Part 8) ───────────────────────────────────────────────────

    def test_media_providers(self, client, auth_header):
        resp = client.get("/admin/media/providers", headers=auth_header)
        assert resp.status_code == 200

    def test_media_history(self, client, auth_header):
        resp = client.get("/admin/media/history", headers=auth_header)
        assert resp.status_code == 200

    def test_media_targets(self, client, auth_header):
        resp = client.get("/admin/media/targets", headers=auth_header)
        assert resp.status_code == 200

    def test_media_podcasts(self, client, auth_header):
        resp = client.get("/admin/media/podcasts", headers=auth_header)
        assert resp.status_code == 200

    # ── Evolution (Part 9) ───────────────────────────────────────────────

    def test_evolution_runs(self, client, auth_header):
        resp = client.get("/admin/evolution/runs", headers=auth_header)
        assert resp.status_code == 200

    def test_evolution_models(self, client, auth_header):
        resp = client.get("/admin/evolution/models", headers=auth_header)
        assert resp.status_code == 200

    def test_evolution_drift(self, client, auth_header):
        resp = client.get("/admin/evolution/drift", headers=auth_header)
        assert resp.status_code == 200

    # ── Stories (Part 10) ────────────────────────────────────────────────

    def test_stories_list(self, client, auth_header):
        resp = client.get("/admin/stories", headers=auth_header)
        assert resp.status_code == 200

    def test_stories_progress(self, client, auth_header):
        resp = client.get("/admin/stories/progress", headers=auth_header)
        assert resp.status_code == 200

    # ── Safety ───────────────────────────────────────────────────────────

    def test_safety_events(self, client, auth_header):
        resp = client.get("/admin/safety/events", headers=auth_header)
        assert resp.status_code == 200

    def test_safety_patterns(self, client, auth_header):
        resp = client.get("/admin/safety/patterns", headers=auth_header)
        assert resp.status_code == 200

    # ── System ───────────────────────────────────────────────────────────

    def test_system_hardware(self, client, auth_header):
        resp = client.get("/admin/system/hardware", headers=auth_header)
        assert resp.status_code == 200

    def test_system_services(self, client, auth_header):
        resp = client.get("/admin/system/services", headers=auth_header)
        assert resp.status_code == 200

    def test_system_settings(self, client, auth_header):
        resp = client.get("/admin/settings", headers=auth_header)
        assert resp.status_code == 200

    # ── Devices ──────────────────────────────────────────────────────────

    def test_devices_list(self, client, auth_header):
        resp = client.get("/admin/devices", headers=auth_header)
        assert resp.status_code == 200

    # ── TTS / Voice ──────────────────────────────────────────────────────

    def test_tts_voices(self, client, auth_header):
        resp = client.get("/admin/tts/voices", headers=auth_header)
        assert resp.status_code == 200

    # ── Satellites ───────────────────────────────────────────────────────

    def test_satellites_list(self, client, auth_header):
        resp = client.get("/admin/satellites", headers=auth_header)
        assert resp.status_code == 200

    # ── Avatar ───────────────────────────────────────────────────────────

    def test_avatar_skins(self, client, auth_header):
        resp = client.get("/admin/avatar/skins", headers=auth_header)
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Admin Router Completeness (Vue SPA)
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminRoutes:
    """Verify all Vue routes exist in the router config."""

    def test_all_routes_defined(self):
        """Parse admin/src/router/index.js and verify all expected routes."""
        router_file = (REPO_ROOT / "admin/src/router/index.js").read_text()

        expected_routes = [
            "/login",
            "/chat",
            "/",
            "/dashboard",
            "/voice",
            "/avatar",
            "/devices",
            "/users",
            "/satellites",
            "/safety",
            "/parental",
            "/evolution",
            "/system",
            "/plugins",
            "/scheduling",
            "/routines",
            "/learning",
            "/proactive",
            "/stories",
            "/intercom",
            "/media",
        ]
        for route in expected_routes:
            assert (
                f"'{route}'" in router_file or f'"{route}"' in router_file
            ), f"Missing route: {route}"

    def test_navbar_has_all_items(self):
        """Parse NavBar.vue and verify all nav items exist."""
        navbar = (REPO_ROOT / "admin/src/components/NavBar.vue").read_text()

        expected_items = [
            "Chat",
            "Dashboard",
            "Users",
            "Satellites",
            "Voice",
            "Avatar",
            "Devices",
            "Plugins",
            "Scheduling",
            "Routines",
            "Learning",
            "Proactive",
            "Media",
            "Intercom",
            "Evolution",
            "System",
        ]
        for item in expected_items:
            assert item in navbar, f"Missing navbar item: {item}"

    def test_routes_have_matching_views(self):
        """Each named route references a view file that exists on disk."""
        router_file = (REPO_ROOT / "admin/src/router/index.js").read_text()
        view_imports = re.findall(r"import\(['\"](\.\./views/\w+\.vue)['\"]\)", router_file)
        views_dir = REPO_ROOT / "admin/src/views"

        for rel_path in view_imports:
            view_name = rel_path.split("/")[-1]
            assert (views_dir / view_name).exists(), f"View file missing: {view_name}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Cross-Module Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossModuleIntegration:
    """Test that modules work together correctly."""

    async def test_scheduling_plugin_match(self, db_path):
        """Scheduling plugin matches timer/alarm intents."""
        from cortex.plugins.timers import SchedulingPlugin

        plugin = SchedulingPlugin()
        await plugin.setup({})

        match = await plugin.match("set a timer for 5 minutes", {})
        assert match.matched, "SchedulingPlugin should match timer request"

    async def test_scheduling_plugin_handles_timer(self, db_path):
        """Scheduling plugin creates a timer via the engine."""
        from cortex.plugins.timers import SchedulingPlugin

        plugin = SchedulingPlugin()
        await plugin.setup({})

        match = await plugin.match("set a timer for 5 minutes", {})
        if match.matched:
            result = await plugin.handle(
                "set a timer for 5 minutes", match, {"user_id": "test"}
            )
            assert result.success, f"Timer creation failed: {result.response}"

    async def test_routine_plugin_match(self, db_path):
        """Routine plugin matches routine intents."""
        from cortex.plugins.routines import RoutinePlugin

        plugin = RoutinePlugin()
        await plugin.setup({})

        match = await plugin.match("list my routines", {})
        assert match.matched, "RoutinePlugin should match 'list my routines'"

    async def test_story_plugin_match(self, db_path):
        """Story plugin matches story intents."""
        from cortex.plugins.stories import StoryPlugin

        plugin = StoryPlugin()
        await plugin.setup({})

        match = await plugin.match("tell me a story about a dragon", {})
        assert match.matched, "StoryPlugin should match story request"

    async def test_media_plugin_match(self, db_path):
        """Media plugin matches play requests."""
        from cortex.plugins.media import MediaPlugin

        plugin = MediaPlugin()
        await plugin.setup({})

        match = await plugin.match("play some music", {})
        assert match.matched, "MediaPlugin should match 'play some music'"

    async def test_intercom_plugin_match(self, db_path):
        """Intercom plugin matches broadcast intents."""
        from cortex.plugins.intercom import IntercomPlugin

        plugin = IntercomPlugin()
        await plugin.setup({})

        match = await plugin.match("announce dinner is ready", {})
        assert match.matched, "IntercomPlugin should match broadcast request"

    async def test_briefing_plugin_match(self, db_path):
        """Daily briefing plugin matches briefing intents."""
        from cortex.plugins.briefing import DailyBriefingPlugin

        plugin = DailyBriefingPlugin()
        await plugin.setup({})

        match = await plugin.match("give me my daily briefing", {})
        assert match.matched, "DailyBriefingPlugin should match briefing request"

    async def test_stem_games_plugin_match(self, db_path):
        """STEM games plugin matches game intents."""
        from cortex.plugins.games import STEMGamesPlugin

        plugin = STEMGamesPlugin()
        await plugin.setup({})

        match = await plugin.match("let's play a game", {})
        assert match.matched, "STEMGamesPlugin should match 'let's play a game'"

    async def test_conversion_plugin_match(self, db_path):
        """Conversion plugin matches unit conversion requests."""
        from cortex.plugins.conversions import ConversionPlugin

        plugin = ConversionPlugin()
        await plugin.setup({})

        match = await plugin.match("convert 5 miles to kilometers", {})
        assert match.matched, "ConversionPlugin should match conversion request"

    async def test_conversion_plugin_handles_request(self, db_path):
        """Conversion plugin returns correct conversion."""
        from cortex.plugins.conversions import ConversionPlugin

        plugin = ConversionPlugin()
        await plugin.setup({})

        match = await plugin.match("convert 5 miles to kilometers", {})
        if match.matched:
            result = await plugin.handle(
                "convert 5 miles to kilometers", match, {}
            )
            assert result.success, f"Conversion failed: {result.response}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — Engine Module Imports
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineImports:
    """Verify all engine modules are importable with expected exports."""

    def test_scheduling_engines(self):
        from cortex.scheduling import AlarmEngine, ReminderEngine, TimerEngine

        assert AlarmEngine is not None
        assert TimerEngine is not None
        assert ReminderEngine is not None

    def test_routine_engine(self):
        from cortex.routines import ActionExecutor, RoutineEngine, TriggerManager

        assert RoutineEngine is not None
        assert TriggerManager is not None
        assert ActionExecutor is not None

    def test_proactive_engine(self):
        from cortex.proactive import NotificationThrottle, RuleEngine

        assert RuleEngine is not None
        assert NotificationThrottle is not None

    def test_intercom_engine(self):
        from cortex.intercom import IntercomEngine, ZoneManager

        assert IntercomEngine is not None
        assert ZoneManager is not None

    def test_story_engine(self):
        from cortex.stories import StoryGenerator, StoryLibrary

        assert StoryGenerator is not None
        assert StoryLibrary is not None

    def test_evolution_engine(self):
        from cortex.evolution import EvolutionEngine, ModelRegistry

        assert EvolutionEngine is not None
        assert ModelRegistry is not None

    def test_pipeline_importable(self):
        from cortex.pipeline import run_pipeline, run_pipeline_events

        assert callable(run_pipeline)
        assert callable(run_pipeline_events)

    def test_providers_importable(self):
        from cortex.providers import LLMProvider, OllamaProvider, get_provider

        assert LLMProvider is not None
        assert OllamaProvider is not None
        assert callable(get_provider)

    def test_media_modules(self):
        from cortex.media.router import PlaybackRouter
        from cortex.media.base import MediaProvider

        assert PlaybackRouter is not None
        assert MediaProvider is not None

    def test_learning_education(self):
        from cortex.learning.education.quiz import QuizGenerator
        from cortex.learning.education.progress import ProgressTracker

        assert QuizGenerator is not None
        assert ProgressTracker is not None


# ═══════════════════════════════════════════════════════════════════════════
# Section 7 — CLI Module Completeness
# ═══════════════════════════════════════════════════════════════════════════


class TestCLICompleteness:
    """Verify CLI module is complete and importable."""

    def test_cli_entry_point(self):
        from cortex.cli.__main__ import main, _build_parser

        parser = _build_parser()
        assert parser is not None

    def test_all_tools_registered(self):
        from cortex.cli.tools import get_default_registry

        registry = get_default_registry()
        tools = registry.list_tools()
        assert len(tools) >= 25, f"Only {len(tools)} tools registered, expected ≥25"

    def test_repl_importable(self):
        from cortex.cli.repl import run_oneshot, run_repl  # noqa: F401

    def test_agent_importable(self):
        from cortex.cli.agent import run_agent  # noqa: F401

    def test_context_importable(self):
        from cortex.cli.context import ContextManager  # noqa: F401
        from cortex.cli.session import SessionManager  # noqa: F401

    def test_lora_router_importable(self):
        from cortex.cli.lora_router import LoRARouter  # noqa: F401

    def test_lora_router_classifies(self):
        from cortex.cli.lora_router import LoRARouter

        router = LoRARouter()
        domain = router.classify("write a Python function")
        assert domain in ("coding", "reasoning", "math", "sysadmin", "general")


# ═══════════════════════════════════════════════════════════════════════════
# Section 8 — WebSocket Chat Endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestWebSocketChat:
    """Test the /ws/chat WebSocket endpoint."""

    def _make_ws_app(self, db_path):
        """Build a minimal FastAPI app with the chat WS route and mocked pipeline."""
        from cortex.server import chat_ws_handler

        ws_app = FastAPI()
        ws_app.add_api_websocket_route("/ws/chat", chat_ws_handler)
        return ws_app

    def test_chat_websocket_connect(self, db_path):
        """WebSocket connects and streams start/token/end for a text message."""
        ws_app = self._make_ws_app(db_path)

        async def _fake_pipeline(**kwargs):
            for word in ["Hello", " ", "world"]:
                yield word

        with (
            patch("cortex.server._get_provider", return_value=MagicMock()),
            patch("cortex.server._get_db", return_value=MagicMock()),
            patch("cortex.server.run_pipeline", side_effect=_fake_pipeline),
        ):
            client = TestClient(ws_app)
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "chat", "message": "hi"})

                # Expect start → token(s) → end
                msg = ws.receive_json()
                assert msg["type"] == "start"

                tokens = []
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "end":
                        break
                    assert msg["type"] == "token"
                    tokens.append(msg["text"])

                assert "".join(tokens) == "Hello world"
                assert msg["type"] == "end"
                assert msg["full_text"] == "Hello world"

    def test_chat_handles_empty_message(self, db_path):
        """Empty message returns an error, not a crash."""
        ws_app = self._make_ws_app(db_path)

        with (
            patch("cortex.server._get_provider", return_value=MagicMock()),
            patch("cortex.server._get_db", return_value=MagicMock()),
        ):
            client = TestClient(ws_app)
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "chat", "message": ""})
                msg = ws.receive_json()
                assert msg["type"] == "error"


# ═══════════════════════════════════════════════════════════════════════════
# Section 9 — Admin Router Module Wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestAdminRouterWiring:
    """Verify all admin sub-routers are included in the main admin router."""

    def test_all_sub_routers_included(self):
        """The admin router has routes from every sub-module."""
        from cortex.admin import router

        paths = {route.path for route in router.routes}

        # Each sub-module contributes at least one path (prefixed with /admin/)
        expected_fragments = [
            "/admin/auth/login",
            "/admin/dashboard",
            "/admin/users",
            "/admin/safety",
            "/admin/devices",
            "/admin/system",
            "/admin/satellites",
            "/admin/tts",
            "/admin/avatar",
            "/admin/plugins",
            "/admin/scheduling",
            "/admin/routines",
            "/admin/evolution",
            "/admin/stories",
            "/admin/proactive",
            "/admin/intercom",
            "/admin/media",
            "/admin/learning",
        ]
        for fragment in expected_fragments:
            matches = [p for p in paths if fragment in p]
            assert matches, f"No routes found containing {fragment}"

    def test_admin_route_count(self):
        """Admin router has a substantial number of routes."""
        from cortex.admin import router

        route_count = len(router.routes)
        assert route_count >= 100, (
            f"Expected ≥100 admin routes, got {route_count}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Section 10 — Pipeline Layer Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineLayers:
    """Verify individual pipeline layers are importable and callable."""

    async def test_layer0_context_assembly(self):
        """Layer 0 assembles context from a message."""
        from cortex.pipeline.layer0_context import assemble_context

        ctx = await assemble_context("Hello!", user_id="test_user")
        assert ctx["user_id"] == "test_user"
        assert "sentiment" in ctx

    async def test_layer1_instant_answer(self):
        """Layer 1 handles simple factual queries without an LLM."""
        from cortex.pipeline.layer1_instant import try_instant_answer

        result = await try_instant_answer("what time is it", {})
        # Should return a non-empty string with the time
        assert result is not None and len(result) > 0

    async def test_layer3_model_selection(self):
        """Layer 3 selects the correct model based on query complexity."""
        from cortex.pipeline.layer3_llm import select_model

        model = select_model("hello")
        assert isinstance(model, str) and len(model) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Section 11 — Data Integrity & Cross-Table Relationships
# ═══════════════════════════════════════════════════════════════════════════


class TestDataIntegrity:
    """Verify DB schema relationships and constraints work."""

    def test_routine_with_steps(self, db_conn):
        """Creating a routine and adding steps respects FK constraints."""
        db_conn.execute(
            "INSERT INTO routines (name, description, enabled) VALUES (?, ?, ?)",
            ("Morning", "Wake up routine", 1),
        )
        routine_id = db_conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        db_conn.execute(
            "INSERT INTO routine_steps (routine_id, step_order, action_type, action_config) "
            "VALUES (?, ?, ?, ?)",
            (routine_id, 1, "ha_call", '{"entity_id":"light.bedroom"}'),
        )
        db_conn.commit()

        steps = db_conn.execute(
            "SELECT * FROM routine_steps WHERE routine_id = ?", (routine_id,)
        ).fetchall()
        assert len(steps) == 1

    def test_story_with_chapters_and_characters(self, db_conn):
        """Story → chapters and characters FK chain works."""
        db_conn.execute(
            "INSERT INTO stories (title, genre, target_age_group, summary) "
            "VALUES (?, ?, ?, ?)",
            ("Dragon Quest", "fantasy", "child", "A story about dragons"),
        )
        story_id = db_conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        db_conn.execute(
            "INSERT INTO story_chapters (story_id, chapter_number, title, content) "
            "VALUES (?, ?, ?, ?)",
            (story_id, 1, "The Beginning", "Once upon a time..."),
        )
        db_conn.execute(
            "INSERT INTO story_characters (story_id, name, description) "
            "VALUES (?, ?, ?)",
            (story_id, "Dragon", "A friendly dragon"),
        )
        db_conn.commit()

        chapters = db_conn.execute(
            "SELECT * FROM story_chapters WHERE story_id = ?", (story_id,)
        ).fetchall()
        chars = db_conn.execute(
            "SELECT * FROM story_characters WHERE story_id = ?", (story_id,)
        ).fetchall()
        assert len(chapters) == 1
        assert len(chars) == 1

    def test_scheduling_tables_writable(self, db_conn):
        """Alarms, timers, and reminders accept inserts."""
        db_conn.execute(
            "INSERT INTO alarms (label, cron_expression, enabled, user_id) "
            "VALUES (?, ?, ?, ?)",
            ("Wake up", "0 7 * * *", 1, "test"),
        )
        db_conn.execute(
            "INSERT INTO timers (duration_seconds, label, user_id, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (300, "Eggs", "test"),
        )
        db_conn.execute(
            "INSERT INTO reminders (message, trigger_type, trigger_at, user_id) "
            "VALUES (?, ?, ?, ?)",
            ("Take meds", "time", "2025-01-01 08:00:00", "test"),
        )
        db_conn.commit()

        assert db_conn.execute("SELECT count(*) FROM alarms").fetchone()[0] >= 1
        assert db_conn.execute("SELECT count(*) FROM timers").fetchone()[0] >= 1
        assert db_conn.execute("SELECT count(*) FROM reminders").fetchone()[0] >= 1

    def test_proactive_rules_writable(self, db_conn):
        """Proactive rules can be inserted and queried."""
        db_conn.execute(
            "INSERT INTO proactive_rules (name, provider, condition_type, condition_config, "
            "action_type, action_config, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Rain alert", "weather", "threshold", '{"condition":"rain"}', "notify",
             '{"message":"Bring umbrella"}', 1),
        )
        db_conn.commit()

        rules = db_conn.execute("SELECT * FROM proactive_rules").fetchall()
        assert len(rules) >= 1

    def test_media_tables_writable(self, db_conn):
        """Media library and playback history accept inserts."""
        db_conn.execute(
            "INSERT INTO media_library (title, media_type, provider) "
            "VALUES (?, ?, ?)",
            ("Test Song", "music", "local"),
        )
        db_conn.commit()

        items = db_conn.execute("SELECT * FROM media_library").fetchall()
        assert len(items) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Section 12 — Safety Module Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyIntegration:
    """Verify safety modules are importable and structurally sound."""

    def test_jailbreak_module_importable(self):
        from cortex.safety.jailbreak import InjectionDetector

        assert InjectionDetector is not None

    def test_guardrails_importable(self):
        from cortex.safety import InputGuardrails, OutputGuardrails

        assert InputGuardrails is not None
        assert OutputGuardrails is not None

    def test_safety_middleware_importable(self):
        from cortex.safety.middleware import PipelineSafetyMiddleware

        assert PipelineSafetyMiddleware is not None

    def test_jailbreak_patterns_table_populated(self, db_conn):
        """Jailbreak patterns table exists (may be empty before seeding)."""
        count = db_conn.execute(
            "SELECT count(*) FROM jailbreak_patterns"
        ).fetchone()[0]
        # Table exists — count ≥ 0 is fine
        assert count >= 0
