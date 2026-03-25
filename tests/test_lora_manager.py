"""Tests for LoRA manager and routing integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cortex.evolution.lora_manager import (
    ComposedModel,
    LoRAManager,
    get_lora_manager,
    set_lora_manager,
)
from cortex.cli.lora_router import LoRARouter


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def lora_dir(tmp_path: Path) -> Path:
    """Create a fake LoRA directory with two adapters."""
    for domain in ("coding", "math"):
        d = tmp_path / domain
        d.mkdir()
        (d / "adapter_config.json").write_text(json.dumps({
            "r": 16,
            "lora_alpha": 32,
            "target_modules": ["q_proj", "v_proj"],
            "base_model_name_or_path": "qwen3.5:9b",
            "lora_dropout": 0.05,
        }))
        (d / "adapter_model.safetensors").write_bytes(b"\x00" * 64)
    return tmp_path


@pytest.fixture()
def empty_lora_dir(tmp_path: Path) -> Path:
    """An empty directory with no adapters."""
    return tmp_path / "empty"


@pytest.fixture()
def mgr() -> LoRAManager:
    return LoRAManager(ollama_url="http://localhost:11434")


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton between tests."""
    set_lora_manager(None)
    yield
    set_lora_manager(None)


# ── Discovery tests ──────────────────────────────────────────────


def test_discover_finds_adapters(mgr: LoRAManager, lora_dir: Path):
    loras = mgr.discover(lora_dir)
    assert "coding" in loras
    assert "math" in loras
    assert len(loras) == 2


def test_discover_returns_empty_for_missing_dir(mgr: LoRAManager, empty_lora_dir: Path):
    loras = mgr.discover(empty_lora_dir)
    assert loras == {}


def test_discover_returns_empty_for_no_adapters(mgr: LoRAManager, tmp_path: Path):
    (tmp_path / "some_file.txt").write_text("nothing")
    loras = mgr.discover(tmp_path)
    assert loras == {}


# ── Modelfile building ───────────────────────────────────────────


def test_build_modelfile_safetensors(lora_dir: Path):
    mf = LoRAManager.build_modelfile("qwen3.5:9b", str(lora_dir / "coding"))
    assert mf.startswith("FROM qwen3.5:9b\n")
    assert "adapter_model.safetensors" in mf


def test_build_modelfile_bin(tmp_path: Path):
    d = tmp_path / "test-lora"
    d.mkdir()
    (d / "adapter_config.json").write_text("{}")
    (d / "adapter_model.bin").write_bytes(b"\x00")
    mf = LoRAManager.build_modelfile("base:latest", str(d))
    assert "adapter_model.bin" in mf


def test_build_modelfile_fallback_to_dir(tmp_path: Path):
    d = tmp_path / "bare-lora"
    d.mkdir()
    (d / "adapter_config.json").write_text("{}")
    mf = LoRAManager.build_modelfile("base:latest", str(d))
    assert f"ADAPTER {d}" in mf


# ── Compose tests ────────────────────────────────────────────────


async def test_compose_creates_model(mgr: LoRAManager, lora_dir: Path):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        result = await mgr.compose("coding", "qwen3.5:9b", lora_dir / "coding")

    assert result is not None
    assert result.model_name == "atlas-coding:latest"
    assert result.domain == "coding"
    assert result.base_model == "qwen3.5:9b"


async def test_compose_handles_ollama_error(mgr: LoRAManager, lora_dir: Path):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        result = await mgr.compose("coding", "qwen3.5:9b", lora_dir / "coding")

    assert result is None


async def test_compose_registers_in_memory(mgr: LoRAManager, lora_dir: Path):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        await mgr.compose("coding", "qwen3.5:9b", lora_dir / "coding")

    assert mgr.get_model_for_domain("coding") == "atlas-coding:latest"


# ── get_model_for_domain tests ───────────────────────────────────


def test_get_model_for_domain_returns_none_when_empty(mgr: LoRAManager):
    assert mgr.get_model_for_domain("coding") is None


def test_get_model_for_domain_returns_model(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding",
        model_name="atlas-coding:latest",
        base_model="qwen3.5:9b",
        adapter_path="/fake/path",
    )
    assert mgr.get_model_for_domain("coding") == "atlas-coding:latest"
    assert mgr.get_model_for_domain("math") is None


# ── list_active tests ────────────────────────────────────────────


def test_list_active_empty(mgr: LoRAManager):
    assert mgr.list_active() == []


def test_list_active_returns_composed(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding", model_name="atlas-coding:latest",
        base_model="b", adapter_path="/p",
    )
    mgr._composed["math"] = ComposedModel(
        domain="math", model_name="atlas-math:latest",
        base_model="b", adapter_path="/p",
    )
    assert len(mgr.list_active()) == 2


# ── compose_all tests ────────────────────────────────────────────


async def test_compose_all_processes_all(mgr: LoRAManager, lora_dir: Path):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    mock_tags_resp = MagicMock()
    mock_tags_resp.raise_for_status = MagicMock()
    mock_tags_resp.json.return_value = {"models": []}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_tags_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx, \
         patch("cortex.evolution.lora_manager.LoRAManager._register_in_db"):
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        composed = await mgr.compose_all("qwen3.5:9b", lora_dir)

    assert len(composed) == 2
    domains = {c.domain for c in composed}
    assert domains == {"coding", "math"}


async def test_compose_all_empty_dir(mgr: LoRAManager, empty_lora_dir: Path):
    composed = await mgr.compose_all("qwen3.5:9b", empty_lora_dir)
    assert composed == []


async def test_compose_all_picks_up_existing_ollama_models(
    mgr: LoRAManager, tmp_path: Path,
):
    """Pre-existing atlas-* models in Ollama are added to the active set."""
    mock_tags_resp = MagicMock()
    mock_tags_resp.raise_for_status = MagicMock()
    mock_tags_resp.json.return_value = {
        "models": [
            {"name": "atlas-creative:latest"},
            {"name": "qwen3.5:9b"},
        ],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_tags_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        await mgr.compose_all("qwen3.5:9b", tmp_path)

    assert mgr.get_model_for_domain("creative") == "atlas-creative:latest"


# ── remove tests ─────────────────────────────────────────────────


async def test_remove_deletes_model(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding", model_name="atlas-coding:latest",
        base_model="b", adapter_path="/p",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        ok = await mgr.remove("coding")

    assert ok is True
    assert mgr.get_model_for_domain("coding") is None


async def test_remove_nonexistent_returns_false(mgr: LoRAManager):
    ok = await mgr.remove("nonexistent")
    assert ok is False


async def test_remove_restores_on_failure(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding", model_name="atlas-coding:latest",
        base_model="b", adapter_path="/p",
    )

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx:
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        ok = await mgr.remove("coding")

    assert ok is False
    # Model should still be in the composed dict
    assert mgr.get_model_for_domain("coding") == "atlas-coding:latest"


# ── LoRARouter tests ─────────────────────────────────────────────


class TestLoRARouterClassify:
    def test_classify_coding(self):
        r = LoRARouter()
        assert r.classify("Write a Python function to sort a list") == "coding"

    def test_classify_math(self):
        r = LoRARouter()
        assert r.classify("Calculate the integral of x^2") == "math"

    def test_classify_reasoning(self):
        r = LoRARouter()
        assert r.classify("Analyze the pros and cons of this approach") == "reasoning"

    def test_classify_sysadmin(self):
        r = LoRARouter()
        assert r.classify("Configure the nginx server") == "sysadmin"

    def test_classify_general(self):
        r = LoRARouter()
        assert r.classify("What is the weather today?") == "general"


class TestLoRARouterRoute:
    async def test_route_returns_model_when_available(self):
        mgr = LoRAManager()
        mgr._composed["coding"] = ComposedModel(
            domain="coding", model_name="atlas-coding:latest",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)

        r = LoRARouter()
        result = await r.route("Write Python code", MagicMock())
        assert result == "atlas-coding:latest"

    async def test_route_returns_domain_when_no_manager(self):
        r = LoRARouter()
        result = await r.route("Write Python code", MagicMock())
        assert result == "coding"

    async def test_route_returns_general_for_unmatched(self):
        r = LoRARouter()
        result = await r.route("Tell me a joke", MagicMock())
        assert result == "general"


class TestLoRARouterResolveModel:
    def test_resolve_returns_lora_model(self):
        mgr = LoRAManager()
        mgr._composed["coding"] = ComposedModel(
            domain="coding", model_name="atlas-coding:latest",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)

        r = LoRARouter()
        assert r.resolve_model("Debug this Python code", "qwen3.5:9b") == "atlas-coding:latest"

    def test_resolve_returns_base_for_general(self):
        r = LoRARouter()
        assert r.resolve_model("Tell me a joke", "qwen3.5:9b") == "qwen3.5:9b"

    def test_resolve_returns_base_when_no_composed(self):
        mgr = LoRAManager()
        set_lora_manager(mgr)

        r = LoRARouter()
        assert r.resolve_model("Debug this Python code", "qwen3.5:9b") == "qwen3.5:9b"


class TestLoRARouterAvailableLoras:
    def test_no_manager(self):
        assert LoRARouter().available_loras == []

    def test_with_manager(self):
        mgr = LoRAManager()
        mgr._composed["coding"] = ComposedModel(
            domain="coding", model_name="atlas-coding:latest",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)
        assert LoRARouter().available_loras == ["atlas-coding:latest"]


# ── Singleton tests ──────────────────────────────────────────────


def test_singleton_lifecycle():
    assert get_lora_manager() is None

    mgr = LoRAManager()
    set_lora_manager(mgr)
    assert get_lora_manager() is mgr

    set_lora_manager(None)
    assert get_lora_manager() is None


# ── Graceful degradation ─────────────────────────────────────────


async def test_compose_all_graceful_when_ollama_unreachable(lora_dir: Path):
    """When Ollama is unreachable, compose_all returns empty without raising."""
    mgr = LoRAManager(ollama_url="http://localhost:99999")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("cortex.evolution.lora_manager._get_httpx") as mock_httpx, \
         patch("cortex.evolution.lora_manager.LoRAManager._register_in_db"):
        mock_httpx.return_value.AsyncClient.return_value = mock_client
        composed = await mgr.compose_all("qwen3.5:9b", lora_dir)

    assert composed == []
    # No models should be active after failed composition
    assert mgr.list_active() == []


async def test_pipeline_lora_override_skipped_when_no_manager():
    """The pipeline should not crash when no LoRA manager is set."""
    set_lora_manager(None)

    from cortex.pipeline.layer3_llm import select_model

    model = select_model("Debug this Python code")
    assert model  # Should still select a model normally
