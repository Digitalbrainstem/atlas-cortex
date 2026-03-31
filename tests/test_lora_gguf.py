"""Tests for LoRA GGUF conversion and management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cortex.evolution.lora_gguf import (
    DOMAIN_MAP,
    LoRAAdapter,
    LoRAGGUFManager,
    get_lora_gguf_manager,
    set_lora_gguf_manager,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level singleton between tests."""
    set_lora_gguf_manager(None)
    yield
    set_lora_gguf_manager(None)


def _make_adapter(base: Path, group: str, domain: str, size: int = 64) -> Path:
    """Create a fake adapter directory with safetensors + config."""
    adapter_dir = base / group / domain
    adapter_dir.mkdir(parents=True, exist_ok=True)
    (adapter_dir / "adapter_model.safetensors").write_bytes(b"\x00" * size)
    (adapter_dir / "adapter_config.json").write_text(
        json.dumps(
            {
                "r": 16,
                "lora_alpha": 32,
                "target_modules": ["q_proj", "v_proj"],
                "base_model_name_or_path": "Qwen3.5-4B",
            }
        )
    )
    return adapter_dir


@pytest.fixture()
def lora_tree(tmp_path: Path) -> Path:
    """Create a realistic LoRA directory tree."""
    for domain in (
        "coding", "ai_ml", "math_reasoning", "medicine",
        "biology_biomed", "creative_arts", "engineering",
    ):
        _make_adapter(tmp_path, "core-4b-h100", domain)
    for domain in (
        "coding", "ai_ml", "medicine", "math_reasoning",
        "physics_chemistry", "creative_arts", "engineering",
    ):
        _make_adapter(tmp_path, "ultra-9b-v2", domain, size=128)
    for domain in (
        "game_dev", "electronics", "history", "music",
        "network_engineering", "robotics_iot",
    ):
        _make_adapter(tmp_path, "focused-9b", domain, size=96)
    return tmp_path


@pytest.fixture()
def mgr(tmp_path: Path, lora_tree: Path) -> LoRAGGUFManager:
    """Manager pointed at the fake LoRA tree."""
    gguf_dir = tmp_path / "gguf_out"
    gguf_dir.mkdir()
    return LoRAGGUFManager(
        lora_dir=str(lora_tree), gguf_dir=str(gguf_dir)
    )


# ── Singleton ────────────────────────────────────────────────────────


class TestSingleton:
    def test_default_is_none(self):
        assert get_lora_gguf_manager() is None

    def test_set_and_get(self):
        m = LoRAGGUFManager()
        set_lora_gguf_manager(m)
        assert get_lora_gguf_manager() is m

    def test_reset_to_none(self):
        set_lora_gguf_manager(LoRAGGUFManager())
        set_lora_gguf_manager(None)
        assert get_lora_gguf_manager() is None


# ── Discovery ────────────────────────────────────────────────────────


class TestDiscovery:
    def test_discovers_all_adapters(self, mgr: LoRAGGUFManager):
        adapters = mgr.discover_loras()
        assert len(adapters) == 20

    def test_adapter_fields(self, mgr: LoRAGGUFManager):
        adapters = mgr.discover_loras()
        coding = [a for a in adapters if a.name == "coding" and a.group == "core-4b-h100"]
        assert len(coding) == 1
        a = coding[0]
        assert a.base_model == "Qwen3.5-4B"
        assert a.domain == "coding"
        assert a.size_bytes == 64
        assert a.safetensors_path.endswith("core-4b-h100/coding")
        assert a.gguf_path is None

    def test_discovers_existing_gguf(self, mgr: LoRAGGUFManager, lora_tree: Path):
        # Pre-create a GGUF file
        gguf_file = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        gguf_file.parent.mkdir(parents=True, exist_ok=True)
        gguf_file.write_bytes(b"\x00" * 32)

        adapters = mgr.discover_loras()
        coding = [a for a in adapters if a.name == "coding" and a.group == "core-4b-h100"][0]
        assert coding.gguf_path is not None
        assert coding.gguf_path.endswith("coding.gguf")

    def test_empty_directory(self, tmp_path: Path):
        m = LoRAGGUFManager(lora_dir=str(tmp_path))
        assert m.discover_loras() == []

    def test_missing_directory(self, tmp_path: Path):
        m = LoRAGGUFManager(lora_dir=str(tmp_path / "nonexistent"))
        assert m.discover_loras() == []

    def test_custom_lora_dir_arg(self, mgr: LoRAGGUFManager, lora_tree: Path):
        adapters = mgr.discover_loras(str(lora_tree))
        assert len(adapters) == 20

    def test_skips_flat_adapters(self, tmp_path: Path):
        """Adapters not in <group>/<domain>/ structure are skipped."""
        # Place adapter directly in root — no group dir
        adapter_dir = tmp_path / "orphan"
        adapter_dir.mkdir()
        (adapter_dir / "adapter_model.safetensors").write_bytes(b"\x00" * 16)

        m = LoRAGGUFManager(lora_dir=str(tmp_path))
        assert m.discover_loras() == []

    def test_9b_base_model(self, mgr: LoRAGGUFManager):
        adapters = mgr.discover_loras()
        ultra = [a for a in adapters if a.group == "ultra-9b-v2"]
        assert all(a.base_model == "Qwen3.5-9B" for a in ultra)

    def test_focused_base_model(self, mgr: LoRAGGUFManager):
        adapters = mgr.discover_loras()
        focused = [a for a in adapters if a.group == "focused-9b"]
        assert all(a.base_model == "Qwen3.5-9B" for a in focused)


# ── Inventory ────────────────────────────────────────────────────────


class TestInventory:
    def test_get_available_gguf_none_converted(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        assert mgr.get_available_gguf_loras() == []

    def test_get_available_gguf_some_converted(self, mgr: LoRAGGUFManager):
        # Pre-create GGUF files for two adapters
        for group, domain in [("core-4b-h100", "coding"), ("focused-9b", "game_dev")]:
            gguf_file = mgr.gguf_dir / group / f"{domain}.gguf"
            gguf_file.parent.mkdir(parents=True, exist_ok=True)
            gguf_file.write_bytes(b"\x00" * 32)

        mgr.discover_loras()
        available = mgr.get_available_gguf_loras()
        assert len(available) == 2
        names = {a.name for a in available}
        assert names == {"coding", "game_dev"}

    def test_auto_discovers_on_first_access(self, mgr: LoRAGGUFManager):
        """get_available_gguf_loras triggers discover if inventory empty."""
        assert mgr._inventory == {}
        mgr.get_available_gguf_loras()
        assert len(mgr._inventory) == 20


# ── Domain mapping ───────────────────────────────────────────────────


class TestDomainMapping:
    def test_all_domain_map_entries_have_values(self):
        for domain, paths in DOMAIN_MAP.items():
            assert len(paths) >= 1, f"Domain {domain} has no adapter paths"

    def test_domain_map_paths_format(self):
        for domain, paths in DOMAIN_MAP.items():
            for p in paths:
                parts = p.split("/")
                assert len(parts) == 2, f"Expected group/domain format: {p}"

    def test_get_lora_for_known_domain(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        adapter = mgr.get_lora_for_domain("coding", model_size="4b")
        assert adapter is not None
        assert adapter.name == "coding"
        assert adapter.group == "core-4b-h100"

    def test_get_lora_prefers_model_size(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        adapter = mgr.get_lora_for_domain("coding", model_size="9b")
        assert adapter is not None
        assert adapter.group == "ultra-9b-v2"

    def test_get_lora_falls_back(self, mgr: LoRAGGUFManager):
        """Falls back to any available adapter if preferred size missing."""
        mgr.discover_loras()
        # game_dev only has focused-9b, but ask for 4b
        adapter = mgr.get_lora_for_domain("game_dev", model_size="4b")
        assert adapter is not None
        assert adapter.group == "focused-9b"

    def test_get_lora_unknown_domain(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        assert mgr.get_lora_for_domain("alchemy") is None

    def test_get_lora_auto_discovers(self, mgr: LoRAGGUFManager):
        """Triggers discovery if inventory is empty."""
        adapter = mgr.get_lora_for_domain("coding")
        assert adapter is not None

    def test_all_standard_domains_resolve(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        for domain in DOMAIN_MAP:
            adapter = mgr.get_lora_for_domain(domain)
            assert adapter is not None, f"No adapter found for domain: {domain}"


# ── Server args ──────────────────────────────────────────────────────


class TestServerArgs:
    def test_no_adapter_returns_empty(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        assert mgr.get_server_args("alchemy") == []

    def test_no_gguf_returns_empty(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()
        # Adapter exists but no GGUF conversion
        assert mgr.get_server_args("coding") == []

    def test_lora_arg(self, mgr: LoRAGGUFManager):
        # Pre-create the GGUF
        gguf_file = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        gguf_file.parent.mkdir(parents=True, exist_ok=True)
        gguf_file.write_bytes(b"\x00" * 32)

        mgr.discover_loras()
        args = mgr.get_server_args("coding")
        assert args[0] == "--lora"
        assert args[1].endswith("coding.gguf")

    def test_lora_scaled_arg(self, mgr: LoRAGGUFManager):
        gguf_file = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        gguf_file.parent.mkdir(parents=True, exist_ok=True)
        gguf_file.write_bytes(b"\x00" * 32)

        mgr.discover_loras()
        args = mgr.get_server_args("coding", scale=0.75)
        assert args == ["--lora-scaled", str(gguf_file), "0.75"]

    def test_9b_server_args(self, mgr: LoRAGGUFManager):
        gguf_file = mgr.gguf_dir / "ultra-9b-v2" / "coding.gguf"
        gguf_file.parent.mkdir(parents=True, exist_ok=True)
        gguf_file.write_bytes(b"\x00" * 32)

        mgr.discover_loras()
        args = mgr.get_server_args("coding", model_size="9b")
        assert args[0] == "--lora"
        assert "ultra-9b-v2" in args[1]


# ── Conversion ───────────────────────────────────────────────────────


class TestConversion:
    async def test_convert_caches_result(self, mgr: LoRAGGUFManager, lora_tree: Path):
        """If GGUF already exists, skip conversion."""
        adapter_dir = lora_tree / "core-4b-h100" / "coding"
        out = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 32)

        result = await mgr.convert_lora(str(adapter_dir))
        assert result == str(out)

    async def test_convert_missing_adapter(self, mgr: LoRAGGUFManager, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Adapter directory not found"):
            await mgr.convert_lora(str(tmp_path / "nonexistent"))

    async def test_convert_missing_safetensors(self, mgr: LoRAGGUFManager, tmp_path: Path):
        empty_dir = tmp_path / "empty_adapter"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="No adapter_model.safetensors"):
            await mgr.convert_lora(str(empty_dir))

    async def test_convert_creates_output_dir(self, mgr: LoRAGGUFManager, lora_tree: Path):
        """Output directory is created if it doesn't exist."""
        adapter_dir = lora_tree / "core-4b-h100" / "coding"
        custom_out = mgr.gguf_dir / "deep" / "nested" / "output.gguf"

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            # Will fail because no real file created, but we test dir creation
            custom_out.parent.mkdir(parents=True, exist_ok=True)
            custom_out.write_bytes(b"\x00" * 16)

            result = await mgr.convert_lora(str(adapter_dir), str(custom_out))
            assert result == str(custom_out)

    async def test_convert_script_failure(self, mgr: LoRAGGUFManager, lora_tree: Path):
        adapter_dir = lora_tree / "core-4b-h100" / "coding"

        fake_proc = AsyncMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(return_value=(b"", b"error details"))

        with patch.object(mgr, "_find_convert_script", return_value=Path("/fake/script.py")), \
             patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            with pytest.raises(RuntimeError, match="LoRA GGUF conversion failed"):
                await mgr.convert_lora(str(adapter_dir))

    async def test_convert_updates_inventory(self, mgr: LoRAGGUFManager, lora_tree: Path):
        mgr.discover_loras()
        adapter_dir = lora_tree / "core-4b-h100" / "coding"
        out = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 32)

        result = await mgr.convert_lora(str(adapter_dir))
        assert mgr._inventory["core-4b-h100/coding"].gguf_path == result

    async def test_convert_with_custom_output(self, mgr: LoRAGGUFManager, lora_tree: Path):
        adapter_dir = lora_tree / "core-4b-h100" / "coding"
        custom = mgr.gguf_dir / "custom_coding.gguf"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_bytes(b"\x00" * 32)

        result = await mgr.convert_lora(str(adapter_dir), str(custom))
        assert result == str(custom)

    async def test_convert_with_base_model(self, mgr: LoRAGGUFManager, lora_tree: Path):
        adapter_dir = lora_tree / "core-4b-h100" / "coding"
        out = mgr.gguf_dir / "core-4b-h100" / "coding.gguf"
        out.parent.mkdir(parents=True, exist_ok=True)

        async def _fake_exec(*args, **kwargs):
            # Create the output file to simulate the script
            out.write_bytes(b"\x00" * 16)
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            return proc

        with patch.object(mgr, "_find_convert_script", return_value=Path("/fake/script.py")), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec) as mock_exec:
            await mgr.convert_lora(
                str(adapter_dir), base_model="/models/Qwen3.5-4B"
            )
            # Verify --base was passed
            call_args = mock_exec.call_args
            cmd_parts = [str(a) for a in call_args[0]]
            assert "--base" in cmd_parts
            assert "/models/Qwen3.5-4B" in cmd_parts


# ── Convert all ──────────────────────────────────────────────────────


class TestConvertAll:
    async def test_convert_all_filters_by_size(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()

        # Pre-create GGUF for all core-4b adapters so they're "already done"
        for adapter in mgr._inventory.values():
            if adapter.group == "core-4b-h100":
                gguf = mgr._gguf_path_for(adapter.group, adapter.name)
                gguf.parent.mkdir(parents=True, exist_ok=True)
                gguf.write_bytes(b"\x00" * 16)
                adapter.gguf_path = str(gguf)

        results = await mgr.convert_all(model_size="4b")
        # All core-4b-h100 were already converted, so all should be paths
        assert len(results) == 7
        for key, val in results:
            assert "core-4b-h100" in key
            assert isinstance(val, str)

    async def test_convert_all_handles_errors(self, mgr: LoRAGGUFManager):
        mgr.discover_loras()

        # Mock convert_lora to fail
        with patch.object(
            mgr,
            "convert_lora",
            side_effect=RuntimeError("boom"),
        ):
            results = await mgr.convert_all(model_size="4b")

        assert len(results) == 7
        for _key, val in results:
            assert isinstance(val, Exception)


# ── Find convert script ──────────────────────────────────────────────


class TestFindConvertScript:
    def test_finds_in_llamacpp_dir(self, tmp_path: Path):
        script = tmp_path / "convert_lora_to_gguf.py"
        script.write_text("# stub")
        m = LoRAGGUFManager(llamacpp_dir=str(tmp_path))
        assert m._find_convert_script() == script

    def test_raises_if_not_found(self):
        m = LoRAGGUFManager(llamacpp_dir="/nonexistent/dir")
        with pytest.raises(FileNotFoundError, match="convert_lora_to_gguf.py not found"):
            m._find_convert_script()


# ── GGUF path helper ─────────────────────────────────────────────────


class TestGGUFPath:
    def test_path_format(self, mgr: LoRAGGUFManager):
        p = mgr._gguf_path_for("core-4b-h100", "coding")
        assert p == mgr.gguf_dir / "core-4b-h100" / "coding.gguf"


# ── Dataclass ────────────────────────────────────────────────────────


class TestLoRAAdapter:
    def test_defaults(self):
        a = LoRAAdapter(
            name="coding",
            group="core-4b-h100",
            base_model="Qwen3.5-4B",
            safetensors_path="/path/to/adapter",
        )
        assert a.gguf_path is None
        assert a.size_bytes == 0
        assert a.domain == ""

    def test_all_fields(self):
        a = LoRAAdapter(
            name="coding",
            group="core-4b-h100",
            base_model="Qwen3.5-4B",
            safetensors_path="/path",
            gguf_path="/path/coding.gguf",
            size_bytes=1024,
            domain="coding",
        )
        assert a.gguf_path == "/path/coding.gguf"
        assert a.size_bytes == 1024


# ── Default constructor ──────────────────────────────────────────────


class TestDefaults:
    def test_default_gguf_dir_is_subdir(self):
        m = LoRAGGUFManager(lora_dir="/fake/loras")
        assert m.gguf_dir == Path("/fake/loras/gguf")

    def test_explicit_gguf_dir(self, tmp_path: Path):
        m = LoRAGGUFManager(lora_dir="/fake", gguf_dir=str(tmp_path))
        assert m.gguf_dir == tmp_path

    def test_no_llamacpp_dir(self):
        m = LoRAGGUFManager()
        assert m.llamacpp_dir is None
