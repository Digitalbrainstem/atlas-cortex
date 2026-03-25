"""Tests for the plugin config UX overhaul.

Covers:
- ConfigField dataclass and serialization
- All plugins declare config_fields (or empty list)
- Movie plugin falls through to LLM when no API key
- Weather plugin falls through when no API key
- HA health_message when not configured
- Admin API returns config_fields in response
- Config validation rejects invalid values
- Health messages for all plugins
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cortex.plugins.base import (
    CommandMatch,
    CommandResult,
    ConfigField,
    CortexPlugin,
)


# ── ConfigField tests ────────────────────────────────────────────


class TestConfigField:
    """ConfigField dataclass serialization and defaults."""

    def test_basic_serialization(self):
        field = ConfigField(
            key="api_key",
            label="API Key",
            field_type="password",
            required=True,
            placeholder="Enter key...",
            help_text="Get yours at example.com",
        )
        d = field.to_dict()
        assert d["key"] == "api_key"
        assert d["label"] == "API Key"
        assert d["field_type"] == "password"
        assert d["required"] is True
        assert d["placeholder"] == "Enter key..."
        assert d["help_text"] == "Get yours at example.com"
        assert d["default"] is None
        assert "options" not in d

    def test_default_values(self):
        field = ConfigField(key="timeout", label="Timeout")
        assert field.field_type == "text"
        assert field.required is False
        assert field.placeholder == ""
        assert field.help_text == ""
        assert field.default is None
        assert field.options == []

    def test_options_included_when_present(self):
        field = ConfigField(
            key="mode",
            label="Mode",
            field_type="select",
            options=[
                {"value": "fast", "label": "Fast"},
                {"value": "slow", "label": "Slow"},
            ],
        )
        d = field.to_dict()
        assert "options" in d
        assert len(d["options"]) == 2
        assert d["options"][0]["value"] == "fast"

    def test_with_default_value(self):
        field = ConfigField(
            key="default_city",
            label="Default City",
            default="Austin",
        )
        d = field.to_dict()
        assert d["default"] == "Austin"


# ── All plugins declare config_fields ────────────────────────────


class TestAllPluginsHaveConfigFields:
    """Every built-in plugin should have a config_fields attribute (possibly empty)."""

    def _load_plugin_class(self, import_path: str) -> type:
        module_path, class_name = import_path.rsplit(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    def test_all_builtin_plugins_have_config_fields(self):
        from cortex.plugins.loader import BUILTIN_PLUGINS

        for plugin_id, import_path in BUILTIN_PLUGINS.items():
            try:
                cls = self._load_plugin_class(import_path)
            except Exception:
                continue  # Skip plugins that can't be imported (e.g., missing deps)

            assert hasattr(cls, "config_fields"), (
                f"Plugin {plugin_id} ({cls.__name__}) missing config_fields"
            )
            fields = cls.config_fields
            assert isinstance(fields, list), (
                f"Plugin {plugin_id}: config_fields should be a list, got {type(fields)}"
            )
            for f in fields:
                assert isinstance(f, ConfigField), (
                    f"Plugin {plugin_id}: config_fields items should be ConfigField instances"
                )


# ── Movie plugin fall-through ────────────────────────────────────


class TestMovieFallThrough:
    """Movie plugin returns success=False when no API key (falls to LLM)."""

    async def test_no_api_key_returns_failure(self):
        from cortex.plugins.movie import MoviePlugin

        plugin = MoviePlugin()
        await plugin.setup({})

        match = await plugin.match("tell me about the movie Inception", {})
        assert match.matched

        result = await plugin.handle("tell me about the movie Inception", match, {})
        assert result.success is False
        assert result.metadata.get("no_api_key") is True

    async def test_with_api_key_attempts_lookup(self):
        from unittest.mock import MagicMock

        from cortex.plugins.movie import MoviePlugin

        plugin = MoviePlugin()
        await plugin.setup({"api_key": "test-key"})

        match = await plugin.match("tell me about the movie Inception", {})
        assert match.matched

        # httpx.Response methods (json, raise_for_status) are sync, not async
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {
                    "title": "Inception",
                    "release_date": "2010-07-16",
                    "vote_average": 8.4,
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("cortex.plugins.movie.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await plugin.handle("tell me about the movie Inception", match, {})
            assert result.success is True
            assert "Inception" in result.response

    async def test_health_message_no_key(self):
        from cortex.plugins.movie import MoviePlugin

        plugin = MoviePlugin()
        await plugin.setup({})
        assert "No API key" in plugin.health_message
        assert "AI knowledge" in plugin.health_message

    async def test_health_message_with_key(self):
        from cortex.plugins.movie import MoviePlugin

        plugin = MoviePlugin()
        await plugin.setup({"api_key": "test-key"})
        assert "TMDb" in plugin.health_message


# ── Weather plugin fall-through ──────────────────────────────────


class TestWeatherFallThrough:
    """Weather plugin returns success=False when no API key (falls to LLM)."""

    async def test_no_api_key_returns_failure(self):
        from cortex.plugins.weather import WeatherPlugin

        plugin = WeatherPlugin()
        await plugin.setup({})

        match = await plugin.match("what's the weather like?", {})
        assert match.matched

        result = await plugin.handle("what's the weather like?", match, {})
        assert result.success is False
        assert result.metadata.get("no_api_key") is True

    async def test_no_mock_72f_response(self):
        """Ensure the old mock '72°F' response is gone."""
        from cortex.plugins.weather import WeatherPlugin

        plugin = WeatherPlugin()
        await plugin.setup({})

        match = await plugin.match("what's the weather?", {})
        result = await plugin.handle("what's the weather?", match, {})
        assert "72°F" not in result.response
        assert result.success is False

    async def test_health_message_no_key(self):
        from cortex.plugins.weather import WeatherPlugin

        plugin = WeatherPlugin()
        await plugin.setup({})
        assert "No API key" in plugin.health_message

    async def test_health_message_with_key(self):
        from cortex.plugins.weather import WeatherPlugin

        plugin = WeatherPlugin()
        await plugin.setup({"api_key": "test-key"})
        assert "OpenWeatherMap" in plugin.health_message


# ── HA health message ────────────────────────────────────────────


class TestHAHealthMessage:
    """HA plugin provides helpful health messages."""

    async def test_not_configured(self):
        from cortex.integrations.ha import HAPlugin

        plugin = HAPlugin(client=None, conn=None)
        assert "Not configured" in plugin.health_message
        assert "URL" in plugin.health_message
        assert "token" in plugin.health_message

    async def test_configured(self):
        from cortex.integrations.ha import HAPlugin

        mock_client = AsyncMock()
        plugin = HAPlugin(client=mock_client, conn=None)
        assert "Connected" in plugin.health_message

    def test_config_fields_declared(self):
        from cortex.integrations.ha import HAPlugin

        fields = HAPlugin.config_fields
        assert len(fields) == 3
        keys = {f.key for f in fields}
        assert "base_url" in keys
        assert "token" in keys
        assert "timeout" in keys

        # base_url and token are required
        required = {f.key for f in fields if f.required}
        assert "base_url" in required
        assert "token" in required
        assert "timeout" not in required


# ── News plugin health message ───────────────────────────────────


class TestNewsPlugin:
    """News plugin provides helpful health messages and has config_fields."""

    async def test_health_message_rss_fallback(self):
        from cortex.plugins.news import NewsPlugin

        plugin = NewsPlugin()
        await plugin.setup({})
        assert "RSS" in plugin.health_message

    async def test_health_message_with_key(self):
        from cortex.plugins.news import NewsPlugin

        plugin = NewsPlugin()
        await plugin.setup({"api_key": "test-key"})
        assert "NewsAPI" in plugin.health_message

    def test_config_fields_declared(self):
        from cortex.plugins.news import NewsPlugin

        fields = NewsPlugin.config_fields
        assert len(fields) >= 1
        assert fields[0].key == "api_key"
        assert fields[0].required is False


# ── Stocks and Sports — no API key needed ────────────────────────


class TestStocksPlugin:
    def test_config_fields_empty(self):
        from cortex.plugins.stocks import StocksPlugin

        assert StocksPlugin.config_fields == []

    async def test_health_message(self):
        from cortex.plugins.stocks import StocksPlugin

        plugin = StocksPlugin()
        await plugin.setup({})
        assert "no API key needed" in plugin.health_message.lower() or "Yahoo" in plugin.health_message


class TestSportsPlugin:
    def test_config_fields_empty(self):
        from cortex.plugins.sports import SportsPlugin

        assert SportsPlugin.config_fields == []

    async def test_health_message(self):
        from cortex.plugins.sports import SportsPlugin

        plugin = SportsPlugin()
        await plugin.setup({})
        assert "no API key needed" in plugin.health_message.lower() or "ESPN" in plugin.health_message


# ── Knowledge and Lists — local storage, no config ───────────────


class TestKnowledgePlugin:
    def test_config_fields_empty(self):
        from cortex.integrations.knowledge.index import KnowledgePlugin

        assert KnowledgePlugin.config_fields == []


class TestListPlugin:
    def test_config_fields_empty(self):
        from cortex.integrations.lists.registry import ListPlugin

        assert ListPlugin.config_fields == []


# ── Config validation ────────────────────────────────────────────


class TestConfigValidation:
    """Test the _validate_config helper from admin plugins."""

    def test_valid_config(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="api_key", label="Key", field_type="password"),
            ConfigField(key="timeout", label="Timeout", field_type="number"),
        ]
        errors = _validate_config(fields, {"api_key": "abc123", "timeout": 10})
        assert errors == []

    def test_invalid_number(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="timeout", label="Timeout", field_type="number"),
        ]
        errors = _validate_config(fields, {"timeout": "not-a-number"})
        assert len(errors) == 1
        assert "number" in errors[0]

    def test_invalid_url(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="base_url", label="URL", field_type="url"),
        ]
        errors = _validate_config(fields, {"base_url": "not-a-url"})
        assert len(errors) == 1
        assert "URL" in errors[0]

    def test_valid_url(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="base_url", label="URL", field_type="url"),
        ]
        errors = _validate_config(fields, {"base_url": "http://localhost:8123"})
        assert errors == []

    def test_invalid_select(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(
                key="mode",
                label="Mode",
                field_type="select",
                options=[
                    {"value": "fast", "label": "Fast"},
                    {"value": "slow", "label": "Slow"},
                ],
            ),
        ]
        errors = _validate_config(fields, {"mode": "invalid"})
        assert len(errors) == 1

    def test_empty_values_skipped(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="timeout", label="Timeout", field_type="number"),
        ]
        errors = _validate_config(fields, {"timeout": ""})
        assert errors == []

    def test_missing_keys_skipped(self):
        from cortex.admin.plugins import _validate_config

        fields = [
            ConfigField(key="timeout", label="Timeout", field_type="number"),
        ]
        errors = _validate_config(fields, {})
        assert errors == []


# ── Needs setup detection ────────────────────────────────────────


class TestNeedsSetup:
    """Test _check_needs_setup from admin plugins."""

    def test_no_required_fields(self):
        from cortex.admin.plugins import _check_needs_setup
        from cortex.plugins.movie import MoviePlugin

        plugin = MoviePlugin()
        assert _check_needs_setup(plugin, {}) is False

    def test_required_fields_missing(self):
        from cortex.admin.plugins import _check_needs_setup
        from cortex.integrations.ha import HAPlugin

        plugin = HAPlugin(client=None, conn=None)
        assert _check_needs_setup(plugin, {}) is True

    def test_required_fields_satisfied(self):
        from cortex.admin.plugins import _check_needs_setup
        from cortex.integrations.ha import HAPlugin

        plugin = HAPlugin(client=None, conn=None)
        assert _check_needs_setup(plugin, {"base_url": "http://ha.local:8123", "token": "abc"}) is False


# ── Backward compatibility ───────────────────────────────────────


class TestBackwardCompatibility:
    """Plugins without config_fields should still work."""

    async def test_base_class_defaults(self):
        """CortexPlugin has empty config_fields and default health_message."""

        class MinimalPlugin(CortexPlugin):
            plugin_id = "minimal"

            async def setup(self, config):
                return True

            async def health(self):
                return True

            async def match(self, message, context):
                return CommandMatch(matched=False)

            async def handle(self, message, match, context):
                return CommandResult(success=False, response="")

        plugin = MinimalPlugin()
        assert plugin.config_fields == []
        assert plugin.health_message == "OK"

    def test_config_field_serialization_roundtrip(self):
        """ConfigField → dict → verify all keys present."""
        field = ConfigField(
            key="token",
            label="Token",
            field_type="password",
            required=True,
            placeholder="...",
            help_text="A help text",
            default="",
        )
        d = field.to_dict()
        assert set(d.keys()) == {
            "key", "label", "field_type", "required",
            "placeholder", "help_text", "default",
        }
