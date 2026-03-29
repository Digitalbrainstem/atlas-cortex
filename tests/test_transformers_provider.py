"""Tests for the Transformers LLM provider (Memory Palace KV cache support)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Test provider import and registration regardless of torch availability
from cortex.providers import get_provider, _PROVIDERS
from cortex.providers.base import LLMProvider


# ── Provider Registration ────────────────────────────────────────

def test_base_provider_supports_kv_cache_default():
    """Base class defaults to no KV cache support."""
    class DummyProvider(LLMProvider):
        async def chat(self, messages, **kw): pass
        async def embed(self, text, **kw): return []
        async def list_models(self): return []
        async def health(self): return True

    p = DummyProvider()
    assert p.supports_kv_cache() is False


def test_ollama_no_kv_cache():
    from cortex.providers.ollama import OllamaProvider
    p = OllamaProvider()
    assert p.supports_kv_cache() is False


def test_transformers_in_providers_if_torch():
    """TransformersProvider registers when torch is available."""
    try:
        import torch
        assert "transformers" in _PROVIDERS
    except ImportError:
        assert "transformers" not in _PROVIDERS


def test_get_provider_ollama():
    """get_provider still works for ollama."""
    p = get_provider("ollama")
    assert p is not None
    assert p.supports_kv_cache() is False


# ── TransformersProvider Unit Tests ──────────────────────────────

try:
    import torch
    from cortex.providers.transformers_provider import TransformersProvider
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


@pytest.mark.skipif(not _HAS_TORCH, reason="torch not available")
class TestTransformersProvider:

    def test_init_defaults(self):
        p = TransformersProvider()
        assert p.model_name == "Qwen/Qwen3-4B"
        assert p.device == "cuda"
        assert p.supports_kv_cache() is True
        assert p.supports_thinking() is True

    def test_init_custom(self):
        p = TransformersProvider(model_name="meta-llama/Llama-3.2-3B", device="cpu")
        assert p.model_name == "meta-llama/Llama-3.2-3B"
        assert p.device == "cpu"
        assert p.supports_thinking() is False  # Not qwen3

    def test_not_loaded_initially(self):
        p = TransformersProvider()
        assert p._loaded is False

    async def test_health_without_load(self):
        p = TransformersProvider()
        # Health is True if torch available (even if not loaded yet)
        # but may be False if CUDA isn't available on this machine
        h = await p.health()
        assert isinstance(h, bool)

    async def test_list_models(self):
        p = TransformersProvider(model_name="Qwen/Qwen3-4B")
        models = await p.list_models()
        assert len(models) == 1
        assert models[0]["name"] == "Qwen/Qwen3-4B"
        assert models[0]["supports_kv_cache"] is True
        assert models[0]["supports_thinking"] is True

    def test_supports_embeddings_false(self):
        p = TransformersProvider()
        assert p.supports_embeddings() is False

    async def test_embed_raises(self):
        p = TransformersProvider()
        with pytest.raises(NotImplementedError):
            await p.embed("test text")

    def test_get_provider_transformers(self):
        p = get_provider("transformers")
        assert isinstance(p, TransformersProvider)
        assert p.supports_kv_cache() is True

    def test_get_provider_transformers_with_model(self):
        p = get_provider("transformers", model_name="test/model")
        assert p.model_name == "test/model"
