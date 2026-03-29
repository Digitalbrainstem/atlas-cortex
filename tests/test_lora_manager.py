"""Tests for LoRA manager and routing integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    return LoRAManager()


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


# ── Compose tests ────────────────────────────────────────────────


async def test_compose_registers_adapter(mgr: LoRAManager, lora_dir: Path):
    result = await mgr.compose("coding", "qwen3.5:9b", lora_dir / "coding")

    assert result is not None
    assert result.model_name == "atlas-coding"
    assert result.domain == "coding"
    assert result.base_model == "qwen3.5:9b"


async def test_compose_returns_none_for_missing_adapter(mgr: LoRAManager, tmp_path: Path):
    d = tmp_path / "bad"
    d.mkdir()
    # No adapter_config.json
    result = await mgr.compose("bad", "qwen3.5:9b", d)
    assert result is None


async def test_compose_registers_in_memory(mgr: LoRAManager, lora_dir: Path):
    await mgr.compose("coding", "qwen3.5:9b", lora_dir / "coding")
    assert mgr.get_model_for_domain("coding") == "atlas-coding"


# ── get_model_for_domain tests ───────────────────────────────────


def test_get_model_for_domain_returns_none_when_empty(mgr: LoRAManager):
    assert mgr.get_model_for_domain("coding") is None


def test_get_model_for_domain_returns_model(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding",
        model_name="atlas-coding",
        base_model="qwen3.5:9b",
        adapter_path="/fake/path",
    )
    assert mgr.get_model_for_domain("coding") == "atlas-coding"
    assert mgr.get_model_for_domain("math") is None


# ── list_active tests ────────────────────────────────────────────


def test_list_active_empty(mgr: LoRAManager):
    assert mgr.list_active() == []


def test_list_active_returns_composed(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding", model_name="atlas-coding",
        base_model="b", adapter_path="/p",
    )
    mgr._composed["math"] = ComposedModel(
        domain="math", model_name="atlas-math",
        base_model="b", adapter_path="/p",
    )
    assert len(mgr.list_active()) == 2


# ── compose_all tests ────────────────────────────────────────────


async def test_compose_all_processes_all(mgr: LoRAManager, lora_dir: Path):
    with patch.object(mgr, "_list_registered_models", return_value=[]), \
         patch.object(LoRAManager, "_register_in_db"):
        composed = await mgr.compose_all("qwen3.5:9b", lora_dir)

    assert len(composed) == 2
    domains = {c.domain for c in composed}
    assert domains == {"coding", "math"}


async def test_compose_all_empty_dir(mgr: LoRAManager, empty_lora_dir: Path):
    with patch.object(mgr, "_list_registered_models", return_value=[]):
        composed = await mgr.compose_all("qwen3.5:9b", empty_lora_dir)
    assert composed == []


async def test_compose_all_picks_up_existing_registered_models(
    mgr: LoRAManager, tmp_path: Path,
):
    """Pre-existing atlas-* models in the DB are added to the active set."""
    with patch.object(
        mgr, "_list_registered_models",
        return_value=["atlas-creative"],
    ):
        await mgr.compose_all("qwen3.5:9b", tmp_path)

    assert mgr.get_model_for_domain("creative") == "atlas-creative"


# ── remove tests ─────────────────────────────────────────────────


async def test_remove_unregisters_model(mgr: LoRAManager):
    mgr._composed["coding"] = ComposedModel(
        domain="coding", model_name="atlas-coding",
        base_model="b", adapter_path="/p",
    )
    ok = await mgr.remove("coding")
    assert ok is True
    assert mgr.get_model_for_domain("coding") is None


async def test_remove_nonexistent_returns_false(mgr: LoRAManager):
    ok = await mgr.remove("nonexistent")
    assert ok is False


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
            domain="coding", model_name="atlas-coding",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)

        r = LoRARouter()
        result = await r.route("Write Python code", MagicMock())
        assert result == "atlas-coding"

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
            domain="coding", model_name="atlas-coding",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)

        r = LoRARouter()
        assert r.resolve_model("Debug this Python code", "qwen3.5:9b") == "atlas-coding"

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
            domain="coding", model_name="atlas-coding",
            base_model="b", adapter_path="/p",
        )
        set_lora_manager(mgr)
        assert LoRARouter().available_loras == ["atlas-coding"]


# ── Singleton tests ──────────────────────────────────────────────


def test_singleton_lifecycle():
    assert get_lora_manager() is None

    mgr = LoRAManager()
    set_lora_manager(mgr)
    assert get_lora_manager() is mgr

    set_lora_manager(None)
    assert get_lora_manager() is None


# ── Graceful degradation ─────────────────────────────────────────


async def test_compose_all_graceful_when_no_adapters(lora_dir: Path):
    """When adapter_config.json is missing, compose_all skips gracefully."""
    # Remove adapter_config.json to simulate broken adapters
    for cfg in lora_dir.rglob("adapter_config.json"):
        cfg.unlink()

    mgr = LoRAManager()
    with patch.object(mgr, "_list_registered_models", return_value=[]):
        composed = await mgr.compose_all("qwen3.5:9b", lora_dir)

    assert composed == []
    assert mgr.list_active() == []


async def test_pipeline_lora_override_skipped_when_no_manager():
    """The pipeline should not crash when no LoRA manager is set."""
    set_lora_manager(None)

    from cortex.pipeline.layer3_llm import select_model

    model = select_model("Debug this Python code")
    assert model  # Should still select a model normally
