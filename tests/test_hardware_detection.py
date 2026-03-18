"""Tests for multi-GPU detection and deployment recommendation engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════════
# detect_gpus — multi-vendor detection
# ═══════════════════════════════════════════════════════════════════


class TestDetectGpusMultiVendor:
    """detect_gpus() must probe ALL vendors, not short-circuit."""

    def test_finds_nvidia_and_amd(self):
        from cortex.install.hardware import detect_gpus

        nvidia_out = "NVIDIA GeForce RTX 4060, 8188\n"
        amd_lspci = (
            "06:00.0 VGA compatible controller [0300]: "
            "Advanced Micro Devices [AMD/ATI] Navi 31 [Radeon RX 7900 XT] [1002:73df]\n"
        )

        def fake_run(cmd, timeout=5):
            prog = cmd[0]
            if prog == "nvidia-smi":
                return nvidia_out.strip()
            if prog == "rocm-smi":
                return ""  # not installed
            if prog == "lspci":
                return amd_lspci.strip()
            return ""

        with patch("cortex.install.hardware._run", side_effect=fake_run):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    gpus = detect_gpus()

        vendors = {g["vendor"] for g in gpus}
        assert "nvidia" in vendors, "NVIDIA GPU should be detected"
        assert "amd" in vendors, "AMD GPU should be detected alongside NVIDIA"

    def test_finds_all_three_vendors(self):
        from cortex.install.hardware import detect_gpus

        lspci_out = (
            "01:00.0 VGA compatible controller: NVIDIA GeForce RTX 4060 [10de:xxxx]\n"
            "06:00.0 VGA compatible controller: AMD Radeon RX 7900 XT [1002:73df]\n"
            "00:02.0 VGA compatible controller: Intel Corporation Arc A770 [8086:xxxx]\n"
        )

        nvidia_out = "NVIDIA GeForce RTX 4060, 8188"

        def fake_run(cmd, timeout=5):
            prog = cmd[0]
            if prog == "nvidia-smi":
                return nvidia_out
            if prog == "rocm-smi":
                return ""
            if prog == "lspci":
                return lspci_out.strip()
            return ""

        with patch("cortex.install.hardware._run", side_effect=fake_run):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    gpus = detect_gpus()

        vendors = {g["vendor"] for g in gpus}
        assert vendors == {"nvidia", "amd", "intel"}

    def test_no_gpus_returns_empty(self):
        from cortex.install.hardware import detect_gpus

        with patch("cortex.install.hardware._run", return_value=""):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    with patch("platform.system", return_value="Linux"):
                        gpus = detect_gpus()

        assert gpus == []


# ═══════════════════════════════════════════════════════════════════
# detect_intel_gpus — discrete vs integrated
# ═══════════════════════════════════════════════════════════════════


class TestDetectIntelGpus:
    def test_arc_a770_discrete(self):
        from cortex.install.hardware import detect_intel_gpus

        lspci = "03:00.0 VGA compatible controller: Intel Corporation Arc A770 [8086:56a0]"
        with patch("cortex.install.hardware._run", return_value=lspci):
            gpus = detect_intel_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is False
        assert gpus[0]["vram_mb"] == 16384
        assert gpus[0]["compute_api"] == "sycl"

    def test_b580_discrete(self):
        from cortex.install.hardware import detect_intel_gpus

        lspci = "03:00.0 VGA compatible controller: Intel Corporation Arc B580 [8086:xxxx]"
        with patch("cortex.install.hardware._run", return_value=lspci):
            gpus = detect_intel_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is False
        assert gpus[0]["vram_mb"] == 12288

    def test_uhd_integrated(self):
        from cortex.install.hardware import detect_intel_gpus

        lspci = "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 770 [8086:4680]"
        with patch("cortex.install.hardware._run", return_value=lspci):
            gpus = detect_intel_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is True
        assert gpus[0]["vram_mb"] == 0
        assert gpus[0]["compute_api"] == "none"

    def test_no_intel_gpus(self):
        from cortex.install.hardware import detect_intel_gpus

        lspci = "01:00.0 VGA compatible controller: NVIDIA GeForce RTX 4060 [10de:xxxx]"
        with patch("cortex.install.hardware._run", return_value=lspci):
            gpus = detect_intel_gpus()

        assert gpus == []


# ═══════════════════════════════════════════════════════════════════
# detect_nvidia_gpus — improved iGPU detection
# ═══════════════════════════════════════════════════════════════════


class TestDetectNvidiaIgpu:
    def test_tegra_is_igpu(self):
        from cortex.install.hardware import detect_nvidia_gpus

        out = "NVIDIA Tegra X1, 512"
        with patch("cortex.install.hardware._run", return_value=out):
            gpus = detect_nvidia_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is True

    def test_orin_is_igpu(self):
        from cortex.install.hardware import detect_nvidia_gpus

        out = "Orin NX 16GB, 900"
        with patch("cortex.install.hardware._run", return_value=out):
            gpus = detect_nvidia_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is True

    def test_rtx_4090_is_not_igpu(self):
        from cortex.install.hardware import detect_nvidia_gpus

        out = "NVIDIA GeForce RTX 4090, 24564"
        with patch("cortex.install.hardware._run", return_value=out):
            gpus = detect_nvidia_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is False

    def test_low_vram_is_igpu(self):
        from cortex.install.hardware import detect_nvidia_gpus

        out = "NVIDIA GPU, 512"
        with patch("cortex.install.hardware._run", return_value=out):
            gpus = detect_nvidia_gpus()

        assert len(gpus) == 1
        assert gpus[0]["is_igpu"] is True


# ═══════════════════════════════════════════════════════════════════
# detect_amd_gpus — lspci fallback + iGPU detection
# ═══════════════════════════════════════════════════════════════════


class TestDetectAmdGpus:
    def test_lspci_fallback(self):
        from cortex.install.hardware import detect_amd_gpus

        lspci = (
            "06:00.0 VGA compatible controller: "
            "Advanced Micro Devices [AMD/ATI] Navi 31 [Radeon RX 7900 XT] [1002:73df]"
        )

        def fake_run(cmd, timeout=5):
            prog = cmd[0]
            if prog == "rocm-smi":
                return ""
            if prog == "lspci":
                return lspci
            return ""

        with patch("cortex.install.hardware._run", side_effect=fake_run):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    gpus = detect_amd_gpus()

        assert len(gpus) == 1
        assert gpus[0]["vendor"] == "amd"
        assert gpus[0]["vram_mb"] == 20480  # 7900 XT = 20GB
        assert gpus[0]["is_igpu"] is False

    def test_apu_is_igpu(self):
        from cortex.install.hardware import _is_amd_igpu

        assert _is_amd_igpu("AMD Radeon Vega 8", 512) is True
        assert _is_amd_igpu("AMD Radeon 780M", 0) is True
        assert _is_amd_igpu("AMD Raphael iGPU", 512) is True

    def test_discrete_is_not_igpu(self):
        from cortex.install.hardware import _is_amd_igpu

        assert _is_amd_igpu("Radeon RX 7900 XT", 20480) is False
        assert _is_amd_igpu("Radeon RX 6800 XT", 16384) is False


# ═══════════════════════════════════════════════════════════════════
# recommend_deployment — tier assignment
# ═══════════════════════════════════════════════════════════════════


class TestRecommendDeployment:
    def test_dual_gpu_tier(self):
        from cortex.install.hardware import recommend_deployment

        hw = {
            "gpus": [
                {"vendor": "amd", "name": "RX 7900 XT", "vram_mb": 20480, "is_igpu": False, "compute_api": "rocm"},
                {"vendor": "nvidia", "name": "RTX 4060", "vram_mb": 8192, "is_igpu": False, "compute_api": "cuda"},
            ],
        }
        dep = recommend_deployment(hw)

        assert dep["tier"] == "dual-gpu"
        assert dep["llm_device"]["name"] == "RX 7900 XT"
        assert dep["tts_device"]["name"] == "RTX 4060"
        assert dep["docker_compose_variant"] == "gpu-both"

    def test_bigger_gpu_becomes_llm(self):
        from cortex.install.hardware import recommend_deployment

        hw = {
            "gpus": [
                {"vendor": "nvidia", "name": "RTX 4060", "vram_mb": 8192, "is_igpu": False, "compute_api": "cuda"},
                {"vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24564, "is_igpu": False, "compute_api": "cuda"},
            ],
        }
        dep = recommend_deployment(hw)

        assert dep["tier"] == "dual-gpu"
        assert dep["llm_device"]["name"] == "RTX 4090"
        assert dep["tts_device"]["name"] == "RTX 4060"
        assert dep["docker_compose_variant"] == "gpu-nvidia"

    def test_single_gpu_tier(self):
        from cortex.install.hardware import recommend_deployment

        hw = {
            "gpus": [
                {"vendor": "nvidia", "name": "RTX 4060", "vram_mb": 8192, "is_igpu": False, "compute_api": "cuda"},
            ],
        }
        dep = recommend_deployment(hw)

        assert dep["tier"] == "single-gpu"
        assert dep["llm_device"]["name"] == "RTX 4060"
        assert dep["tts_device"]["name"] == "RTX 4060"
        assert dep["specialist_device"] is None
        assert dep["docker_compose_variant"] == "gpu-nvidia"

    def test_cpu_only_tier(self):
        from cortex.install.hardware import recommend_deployment

        hw = {"gpus": []}
        dep = recommend_deployment(hw)

        assert dep["tier"] == "cpu-only"
        assert dep["llm_device"] is None
        assert dep["tts_device"] is None
        assert dep["docker_compose_variant"] == "cpu"
        assert any("CPU-only" in n or "cpu" in n.lower() for n in dep["notes"])

    def test_igpu_ignored_for_tier(self):
        """iGPUs should not count as discrete for tier selection."""
        from cortex.install.hardware import recommend_deployment

        hw = {
            "gpus": [
                {"vendor": "intel", "name": "UHD 770", "vram_mb": 0, "is_igpu": True, "compute_api": "none"},
                {"vendor": "nvidia", "name": "RTX 3060", "vram_mb": 12288, "is_igpu": False, "compute_api": "cuda"},
            ],
        }
        dep = recommend_deployment(hw)

        assert dep["tier"] == "single-gpu"
        assert dep["llm_device"]["name"] == "RTX 3060"

    def test_only_igpus_is_cpu_only(self):
        from cortex.install.hardware import recommend_deployment

        hw = {
            "gpus": [
                {"vendor": "intel", "name": "UHD 770", "vram_mb": 0, "is_igpu": True, "compute_api": "none"},
            ],
        }
        dep = recommend_deployment(hw)
        assert dep["tier"] == "cpu-only"

    def test_cpu_only_uses_atlas_core(self):
        """CPU-only tier should default to atlas-core:2b."""
        from cortex.install.hardware import recommend_deployment

        hw = {"gpus": []}
        dep = recommend_deployment(hw)
        assert dep["models"]["fast"] == "atlas-core:2b"
        assert dep["models"]["fast_fallback"] == "qwen2.5:1.5b"


# ═══════════════════════════════════════════════════════════════════
# _docker_variant
# ═══════════════════════════════════════════════════════════════════


class TestDockerVariant:
    def test_nvidia_only(self):
        from cortex.install.hardware import _docker_variant

        assert _docker_variant({"vendor": "nvidia"}) == "gpu-nvidia"

    def test_amd_only(self):
        from cortex.install.hardware import _docker_variant

        assert _docker_variant({"vendor": "amd"}) == "gpu-amd"

    def test_mixed_amd_nvidia(self):
        from cortex.install.hardware import _docker_variant

        result = _docker_variant({"vendor": "amd"}, {"vendor": "nvidia"})
        assert result == "gpu-both"

    def test_intel(self):
        from cortex.install.hardware import _docker_variant

        assert _docker_variant({"vendor": "intel"}) == "gpu-intel"


# ═══════════════════════════════════════════════════════════════════
# Atlas model recommendations
# ═══════════════════════════════════════════════════════════════════


class TestAtlasModels:
    def test_high_vram_recommends_atlas_ultra(self):
        from cortex.install.hardware import recommend_models

        hw = {"gpus": [{"vendor": "nvidia", "name": "RTX 4090", "vram_mb": 24564, "is_igpu": False}]}
        rec = recommend_models(hw)
        assert rec["fast"] == "atlas-ultra:9b"
        assert rec["fast_fallback"] == "qwen2.5:14b"
        assert "coding.lora" in rec["loras"]

    def test_mid_vram_recommends_atlas_ultra(self):
        from cortex.install.hardware import recommend_models

        hw = {"gpus": [{"vendor": "nvidia", "name": "RTX 4060", "vram_mb": 8192, "is_igpu": False}]}
        rec = recommend_models(hw)
        assert rec["fast"] == "atlas-ultra:9b"
        assert rec["fast_fallback"] == "qwen2.5:7b"

    def test_low_vram_recommends_atlas_core(self):
        from cortex.install.hardware import recommend_models

        hw = {"gpus": [{"vendor": "nvidia", "name": "GTX 1650", "vram_mb": 4096, "is_igpu": False}]}
        rec = recommend_models(hw)
        assert rec["fast"] == "atlas-core:2b"
        assert rec["fast_fallback"] == "qwen2.5:3b"

    def test_cpu_only_recommends_atlas_core(self):
        from cortex.install.hardware import recommend_models

        hw = {"gpus": []}
        rec = recommend_models(hw)
        assert rec["fast"] == "atlas-core:2b"
        assert rec["fast_fallback"] == "qwen2.5:1.5b"
        assert rec["loras"] == []

    def test_all_tiers_have_fallback(self):
        from cortex.install.hardware import _VRAM_TIERS

        for min_vram, tier in _VRAM_TIERS:
            assert "fast_fallback" in tier, f"Tier {min_vram}MB missing fast_fallback"
            assert "loras" in tier, f"Tier {min_vram}MB missing loras"

    def test_recommend_models_returns_copy(self):
        """Modifying returned dict must not corrupt the tier table."""
        from cortex.install.hardware import recommend_models

        hw = {"gpus": []}
        rec1 = recommend_models(hw)
        rec1["fast"] = "MODIFIED"
        rec2 = recommend_models(hw)
        assert rec2["fast"] != "MODIFIED"


class TestResolveModel:
    def test_atlas_available(self):
        from cortex.install.hardware import resolve_fast_model

        rec = {"fast": "atlas-ultra:9b", "fast_fallback": "qwen2.5:14b"}
        with patch("cortex.install.hardware.check_atlas_model", return_value=True):
            assert resolve_fast_model(rec) == "atlas-ultra:9b"

    def test_atlas_not_available_falls_back(self):
        from cortex.install.hardware import resolve_fast_model

        rec = {"fast": "atlas-ultra:9b", "fast_fallback": "qwen2.5:14b"}
        with patch("cortex.install.hardware.check_atlas_model", return_value=False):
            assert resolve_fast_model(rec) == "qwen2.5:14b"

    def test_non_atlas_model_passes_through(self):
        """If fast model is not atlas-*, return it directly."""
        from cortex.install.hardware import resolve_fast_model

        rec = {"fast": "qwen2.5:7b", "fast_fallback": "qwen2.5:3b"}
        result = resolve_fast_model(rec)
        assert result == "qwen2.5:3b"


class TestCheckAtlasModel:
    def test_available(self):
        from cortex.install.hardware import check_atlas_model

        with patch("cortex.install.hardware._run", return_value="Model: atlas-ultra:9b\nParameters: 9B"):
            assert check_atlas_model("atlas-ultra:9b") is True

    def test_not_available(self):
        from cortex.install.hardware import check_atlas_model

        with patch("cortex.install.hardware._run", return_value=""):
            assert check_atlas_model("atlas-ultra:9b") is False


# ═══════════════════════════════════════════════════════════════════
# detect_hardware — integration
# ═══════════════════════════════════════════════════════════════════


class TestDetectHardwareIntegration:
    def test_includes_deployment_key(self):
        from cortex.install.hardware import detect_hardware

        with patch("cortex.install.hardware._run", return_value=""):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    hw = detect_hardware()

        assert "deployment" in hw
        assert "tier" in hw["deployment"]
        assert "recommended_models" in hw

    def test_deployment_models_match_recommendation(self):
        from cortex.install.hardware import detect_hardware

        nvidia_out = "NVIDIA GeForce RTX 4060, 8188"

        def fake_run(cmd, timeout=5):
            if cmd[0] == "nvidia-smi":
                return nvidia_out
            return ""

        with patch("cortex.install.hardware._run", side_effect=fake_run):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.glob", return_value=[]):
                    hw = detect_hardware()

        assert hw["deployment"]["tier"] == "single-gpu"
        assert hw["deployment"]["models"]["fast"] == hw["recommended_models"]["fast"]


# ═══════════════════════════════════════════════════════════════════
# deploy/install.sh — syntax check
# ═══════════════════════════════════════════════════════════════════


class TestInstallShellScript:
    def test_syntax_valid(self):
        import subprocess
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"
        if not script.exists():
            pytest.skip("deploy/install.sh not found")
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Syntax error in install.sh:\n{result.stderr}"
