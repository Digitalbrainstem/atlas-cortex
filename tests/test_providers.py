"""Tests for LLM provider interfaces."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cortex.providers import get_provider, LLMProvider, OllamaProvider, OpenAICompatibleProvider


class TestGetProvider:
    def test_default_returns_ollama(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        provider = get_provider()
        assert isinstance(provider, OllamaProvider)

    def test_explicit_ollama(self):
        provider = get_provider("ollama")
        assert isinstance(provider, OllamaProvider)

    def test_explicit_openai_compat(self):
        provider = get_provider("openai_compatible")
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
        provider = get_provider()
        assert isinstance(provider, OpenAICompatibleProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("not_a_real_provider")

    def test_base_url_kwarg(self):
        provider = get_provider("ollama", base_url="http://test:11434")
        assert provider.base_url == "http://test:11434"

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_URL", "http://envhost:11434")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        provider = get_provider()
        assert provider.base_url == "http://envhost:11434"


class TestOllamaProvider:
    def test_supports_embeddings(self):
        p = OllamaProvider()
        assert p.supports_embeddings() is True

    def test_supports_thinking(self):
        p = OllamaProvider()
        assert p.supports_thinking() is True

    @pytest.mark.asyncio
    async def test_health_true_on_200(self):
        p = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(p._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = await p.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_false_on_exception(self):
        p = OllamaProvider()
        with patch.object(p._client, "get", side_effect=Exception("connection refused")):
            result = await p.health()
        assert result is False

    @pytest.mark.asyncio
    async def test_list_models(self):
        p = OllamaProvider()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "models": [
                {"name": "qwen2.5:14b", "size": 9_000_000_000},
                {"name": "qwen3:30b-a3b", "size": 18_000_000_000},
                {"name": "nomic-embed-text", "size": 274_000_000},
            ]
        })
        with patch.object(p._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            models = await p.list_models()
        assert len(models) == 3
        assert models[0]["name"] == "qwen2.5:14b"
        assert models[1]["supports_thinking"] is True  # qwen3 in name


class TestOpenAICompatibleProvider:
    def test_supports_embeddings(self):
        p = OpenAICompatibleProvider()
        assert p.supports_embeddings() is True

    @pytest.mark.asyncio
    async def test_health_false_on_exception(self):
        p = OpenAICompatibleProvider()
        with patch.object(p._client, "get", side_effect=Exception("refused")):
            result = await p.health()
        assert result is False

    @pytest.mark.asyncio
    async def test_list_models_empty_on_error(self):
        p = OpenAICompatibleProvider()
        with patch.object(p._client, "get", side_effect=Exception("refused")):
            models = await p.list_models()
        assert models == []
