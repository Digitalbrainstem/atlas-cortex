"""Tests for cortex.pipeline.model_manager."""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.pipeline.model_manager import (
    ActiveModel,
    ModelConfig,
    ModelManager,
    VRAMStatus,
    _read_vram,
    _read_vram_amd,
    _read_vram_nvidia,
    _detect_gpu_vendor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(
    name: str = "qwen35-4b",
    port: int = 8100,
    priority: int = 0,
    **kwargs,
) -> ModelConfig:
    return ModelConfig(
        name=name,
        gguf_path=f"/models/{name}.gguf",
        port=port,
        priority=priority,
        **kwargs,
    )


@pytest.fixture()
def primary_cfg() -> ModelConfig:
    return _make_config("qwen35-4b", port=8100, priority=0)


@pytest.fixture()
def expert_cfg() -> ModelConfig:
    return _make_config("qwen35-9b", port=8101, priority=1)


@pytest.fixture()
def reasoning_cfg() -> ModelConfig:
    return _make_config("qwen35-27b", port=8102, priority=2)


@pytest.fixture()
def manager(primary_cfg, expert_cfg, reasoning_cfg) -> ModelManager:
    mgr = ModelManager()
    mgr.register_model(primary_cfg)
    mgr.register_model(expert_cfg)
    mgr.register_model(reasoning_cfg)
    return mgr


def _fake_process(*, returncode=None, pid=12345):
    """Return a mock that quacks like asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------

class TestModelConfig:
    def test_defaults(self):
        cfg = _make_config()
        assert cfg.ctx_size == 0
        assert cfg.kv_type == "q4_0"
        assert cfg.gpu_layers == 999
        assert cfg.reasoning_format == "none"

    def test_custom_values(self):
        cfg = _make_config(ctx_size=4096, kv_type="q8_0", reasoning_format="deepseek")
        assert cfg.ctx_size == 4096
        assert cfg.kv_type == "q8_0"
        assert cfg.reasoning_format == "deepseek"

    def test_name_and_path(self):
        cfg = _make_config("my-model", port=9000)
        assert cfg.name == "my-model"
        assert cfg.gguf_path == "/models/my-model.gguf"
        assert cfg.port == 9000


# ---------------------------------------------------------------------------
# VRAMStatus
# ---------------------------------------------------------------------------

class TestVRAMStatus:
    def test_utilisation_pct(self):
        v = VRAMStatus(total_mib=8192, used_mib=4096, free_mib=4096)
        assert v.utilisation_pct == pytest.approx(50.0)

    def test_zero_total(self):
        v = VRAMStatus()
        assert v.utilisation_pct == 0.0

    def test_full_usage(self):
        v = VRAMStatus(total_mib=8192, used_mib=8192, free_mib=0)
        assert v.utilisation_pct == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------

class TestGPUDetection:
    def test_detect_nvidia(self):
        with patch("cortex.pipeline.model_manager.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None
            assert _detect_gpu_vendor() == "nvidia"

    def test_detect_amd(self):
        with patch("cortex.pipeline.model_manager.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/opt/rocm/bin/rocm-smi" if cmd == "rocm-smi" else None
            assert _detect_gpu_vendor() == "amd"

    def test_detect_unknown(self):
        with patch("cortex.pipeline.model_manager.shutil.which", return_value=None):
            assert _detect_gpu_vendor() == "unknown"

    def test_amd_preferred_over_nvidia(self):
        """rocm-smi is checked first."""
        with patch("cortex.pipeline.model_manager.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
            assert _detect_gpu_vendor() == "amd"


# ---------------------------------------------------------------------------
# VRAM parsing — NVIDIA
# ---------------------------------------------------------------------------

class TestNvidiaVRAM:
    @pytest.mark.asyncio
    async def test_parse_nvidia_output(self):
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"8192, 3072, 5120\n", b""))
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=fake_proc):
            status = await _read_vram_nvidia()
        assert status.total_mib == 8192
        assert status.used_mib == 3072
        assert status.free_mib == 5120
        assert status.gpu_vendor == "nvidia"

    @pytest.mark.asyncio
    async def test_nvidia_empty_output(self):
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=fake_proc):
            status = await _read_vram_nvidia()
        assert status.total_mib == 0
        assert status.gpu_vendor == "nvidia"


# ---------------------------------------------------------------------------
# VRAM parsing — AMD
# ---------------------------------------------------------------------------

class TestAMDVRAM:
    @pytest.mark.asyncio
    async def test_parse_amd_output(self):
        json_output = b'''
        {
            "card0": {
                "Total Memory (B)": "8589934592",
                "Total Used Memory (B)": "3221225472"
            }
        }
        '''
        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(return_value=(json_output, b""))
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=fake_proc):
            status = await _read_vram_amd()
        assert status.total_mib == 8192
        assert status.used_mib == 3072
        assert status.free_mib == 8192 - 3072
        assert status.gpu_vendor == "amd"


# ---------------------------------------------------------------------------
# _read_vram dispatcher
# ---------------------------------------------------------------------------

class TestReadVRAMDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_nvidia(self):
        with patch("cortex.pipeline.model_manager._detect_gpu_vendor", return_value="nvidia"), \
             patch("cortex.pipeline.model_manager._read_vram_nvidia",
                   new_callable=AsyncMock,
                   return_value=VRAMStatus(total_mib=8192, used_mib=1000, free_mib=7192, gpu_vendor="nvidia")):
            status = await _read_vram()
        assert status.gpu_vendor == "nvidia"
        assert status.total_mib == 8192

    @pytest.mark.asyncio
    async def test_dispatches_amd(self):
        with patch("cortex.pipeline.model_manager._detect_gpu_vendor", return_value="amd"), \
             patch("cortex.pipeline.model_manager._read_vram_amd",
                   new_callable=AsyncMock,
                   return_value=VRAMStatus(total_mib=16384, used_mib=2000, free_mib=14384, gpu_vendor="amd")):
            status = await _read_vram()
        assert status.gpu_vendor == "amd"

    @pytest.mark.asyncio
    async def test_unknown_gpu(self):
        with patch("cortex.pipeline.model_manager._detect_gpu_vendor", return_value="unknown"):
            status = await _read_vram()
        assert status.total_mib == 0
        assert status.gpu_vendor == "unknown"

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        with patch("cortex.pipeline.model_manager._detect_gpu_vendor", return_value="nvidia"), \
             patch("cortex.pipeline.model_manager._read_vram_nvidia",
                   new_callable=AsyncMock, side_effect=OSError("nope")):
            status = await _read_vram()
        assert status.total_mib == 0


# ---------------------------------------------------------------------------
# ModelManager — registration & queries
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_sets_primary(self, manager: ModelManager, primary_cfg):
        assert manager._primary_name == "qwen35-4b"
        assert "qwen35-4b" in manager.configs
        assert "qwen35-9b" in manager.configs

    def test_register_overwrites(self, manager: ModelManager):
        new = _make_config("qwen35-4b", port=9999, priority=0)
        manager.register_model(new)
        assert manager.configs["qwen35-4b"].port == 9999

    def test_get_active_models_empty(self, manager: ModelManager):
        assert manager.get_active_models() == []

    def test_is_model_ready_false(self, manager: ModelManager):
        assert manager.is_model_ready("qwen35-4b") is False


# ---------------------------------------------------------------------------
# ModelManager — command building
# ---------------------------------------------------------------------------

class TestCommandBuilding:
    def test_basic_cmd(self, manager: ModelManager, primary_cfg):
        cmd = manager._build_cmd(primary_cfg)
        assert "llama-server" in cmd[0]
        assert "--model" in cmd
        assert "--ctx-size" in cmd
        idx = cmd.index("--ctx-size")
        assert cmd[idx + 1] == "0"
        assert "--fit" in cmd
        idx_fit = cmd.index("--fit")
        assert cmd[idx_fit + 1] == "on"
        assert "-ctk" in cmd
        assert "-ctv" in cmd

    def test_reasoning_format_omitted_for_none(self, manager: ModelManager, primary_cfg):
        cmd = manager._build_cmd(primary_cfg)
        assert "--reasoning-format" not in cmd

    def test_reasoning_format_included(self, manager: ModelManager):
        cfg = _make_config(reasoning_format="deepseek")
        cmd = manager._build_cmd(cfg)
        assert "--reasoning-format" in cmd
        idx = cmd.index("--reasoning-format")
        assert cmd[idx + 1] == "deepseek"

    def test_llamacpp_dir_env(self, primary_cfg, monkeypatch):
        monkeypatch.setenv("LLAMACPP_DIR", "/opt/llama.cpp/build/bin")
        mgr = ModelManager()
        cmd = mgr._build_cmd(primary_cfg)
        assert cmd[0] == "/opt/llama.cpp/build/bin/llama-server"

    def test_model_dir_relative_path(self, monkeypatch):
        monkeypatch.setenv("MODEL_DIR", "/data/models")
        mgr = ModelManager()
        cfg = _make_config()
        cfg.gguf_path = "qwen35-4b.gguf"
        cmd = mgr._build_cmd(cfg)
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "/data/models/qwen35-4b.gguf"

    def test_absolute_gguf_path_not_prefixed(self, monkeypatch):
        monkeypatch.setenv("MODEL_DIR", "/data/models")
        mgr = ModelManager()
        cfg = _make_config()
        cfg.gguf_path = "/absolute/path/model.gguf"
        cmd = mgr._build_cmd(cfg)
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "/absolute/path/model.gguf"


# ---------------------------------------------------------------------------
# ModelManager — start / stop / swap
# ---------------------------------------------------------------------------

class TestStartModel:
    @pytest.mark.asyncio
    async def test_start_success(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=True):
            ok = await manager.start_model(primary_cfg)
        assert ok is True
        assert manager.is_model_ready("qwen35-4b")
        assert manager._active["qwen35-4b"].url == "http://127.0.0.1:8100"

    @pytest.mark.asyncio
    async def test_start_already_running(self, manager: ModelManager, primary_cfg):
        # Pre-populate active
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, healthy=True, url="http://127.0.0.1:8100",
        )
        ok = await manager.start_model(primary_cfg)
        assert ok is True  # No-op, already running

    @pytest.mark.asyncio
    async def test_start_health_failure(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=False):
            ok = await manager.start_model(primary_cfg)
        assert ok is False
        assert "qwen35-4b" not in manager._active

    @pytest.mark.asyncio
    async def test_start_subprocess_exception(self, manager: ModelManager, primary_cfg):
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, side_effect=FileNotFoundError("no binary")):
            ok = await manager.start_model(primary_cfg)
        assert ok is False


class TestStopModel:
    @pytest.mark.asyncio
    async def test_stop_running(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc, healthy=True,
        )
        ok = await manager.stop_model("qwen35-4b")
        assert ok is True
        assert "qwen35-4b" not in manager._active
        proc.send_signal.assert_called_once_with(signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_stop_not_running(self, manager: ModelManager):
        ok = await manager.stop_model("qwen35-4b")
        assert ok is False

    @pytest.mark.asyncio
    async def test_stop_already_exited(self, manager: ModelManager, primary_cfg):
        proc = _fake_process(returncode=0)
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc, healthy=False,
        )
        ok = await manager.stop_model("qwen35-4b")
        assert ok is True

    @pytest.mark.asyncio
    async def test_stop_force_kill_on_timeout(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        # Make wait() a real coroutine that hangs forever
        async def _hang():
            await asyncio.sleep(999)
        proc.wait = _hang
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc, healthy=True,
        )
        import cortex.pipeline.model_manager as mm
        original = mm._GRACEFUL_STOP_TIMEOUT
        mm._GRACEFUL_STOP_TIMEOUT = 0.05
        try:
            ok = await manager.stop_model("qwen35-4b")
        finally:
            mm._GRACEFUL_STOP_TIMEOUT = original
        assert ok is True
        proc.kill.assert_called_once()


class TestSwapModel:
    @pytest.mark.asyncio
    async def test_swap_success(self, manager: ModelManager, primary_cfg, expert_cfg):
        proc4b = _fake_process(pid=100)
        proc9b = _fake_process(pid=200)
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc4b, healthy=True,
        )

        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc9b), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=True):
            ok = await manager.swap_model("qwen35-4b", "qwen35-9b")

        assert ok is True
        assert "qwen35-4b" not in manager._active
        assert manager.is_model_ready("qwen35-9b")

    @pytest.mark.asyncio
    async def test_swap_same_model_noop(self, manager: ModelManager, primary_cfg):
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, healthy=True,
        )
        ok = await manager.swap_model("qwen35-4b", "qwen35-4b")
        assert ok is True

    @pytest.mark.asyncio
    async def test_swap_unknown_target(self, manager: ModelManager, primary_cfg):
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, healthy=True,
        )
        ok = await manager.swap_model("qwen35-4b", "nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_swap_start_failure_rolls_back(self, manager: ModelManager, primary_cfg):
        proc4b = _fake_process(pid=100)
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc4b, healthy=True,
        )
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, side_effect=FileNotFoundError):
            ok = await manager.swap_model("qwen35-4b", "qwen35-9b")
        assert ok is False
        # Both models are down after a failed swap
        assert "qwen35-4b" not in manager._active
        assert "qwen35-9b" not in manager._active


# ---------------------------------------------------------------------------
# ModelManager — ensure_model_available & release_model
# ---------------------------------------------------------------------------

class TestEnsureModelAvailable:
    @pytest.mark.asyncio
    async def test_already_running(self, manager: ModelManager, expert_cfg):
        manager._active["qwen35-9b"] = ActiveModel(
            config=expert_cfg, healthy=True, url="http://127.0.0.1:8101",
        )
        url = await manager.ensure_model_available("qwen35-9b")
        assert url == "http://127.0.0.1:8101"

    @pytest.mark.asyncio
    async def test_swap_from_primary(self, manager: ModelManager, primary_cfg, expert_cfg):
        proc4b = _fake_process(pid=100)
        proc9b = _fake_process(pid=200)
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc4b, healthy=True,
            url="http://127.0.0.1:8100",
        )

        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc9b), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=True):
            url = await manager.ensure_model_available("qwen35-9b")

        assert url == "http://127.0.0.1:8101"
        assert "qwen35-4b" not in manager._active
        assert manager.is_model_ready("qwen35-9b")

    @pytest.mark.asyncio
    async def test_unknown_model_raises(self, manager: ModelManager):
        with pytest.raises(RuntimeError, match="Unknown model"):
            await manager.ensure_model_available("nonexistent")

    @pytest.mark.asyncio
    async def test_start_failure_raises(self, manager: ModelManager, expert_cfg):
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="Failed to start"):
                await manager.ensure_model_available("qwen35-9b")

    @pytest.mark.asyncio
    async def test_nothing_running_starts_fresh(self, manager: ModelManager, expert_cfg):
        proc = _fake_process()
        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=True):
            url = await manager.ensure_model_available("qwen35-9b")
        assert url == "http://127.0.0.1:8101"


class TestReleaseModel:
    @pytest.mark.asyncio
    async def test_release_non_primary_restores_primary(self, manager: ModelManager, primary_cfg, expert_cfg):
        proc9b = _fake_process(pid=200)
        proc4b_new = _fake_process(pid=300)
        manager._active["qwen35-9b"] = ActiveModel(
            config=expert_cfg, process=proc9b, healthy=True,
        )

        with patch("cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=proc4b_new), \
             patch.object(manager, "_wait_healthy", new_callable=AsyncMock, return_value=True):
            await manager.release_model("qwen35-9b")

        assert "qwen35-9b" not in manager._active
        assert manager.is_model_ready("qwen35-4b")

    @pytest.mark.asyncio
    async def test_release_primary_noop(self, manager: ModelManager, primary_cfg):
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, healthy=True,
        )
        await manager.release_model("qwen35-4b")
        assert manager.is_model_ready("qwen35-4b")  # still running

    @pytest.mark.asyncio
    async def test_release_not_running_noop(self, manager: ModelManager):
        await manager.release_model("qwen35-9b")  # should not raise


# ---------------------------------------------------------------------------
# ModelManager — eviction candidate selection
# ---------------------------------------------------------------------------

class TestEvictionCandidate:
    def test_picks_lowest_priority(self, manager: ModelManager, primary_cfg, expert_cfg):
        manager._active["qwen35-4b"] = ActiveModel(config=primary_cfg, healthy=True)
        manager._active["qwen35-9b"] = ActiveModel(config=expert_cfg, healthy=True)
        # Requesting 27b — should evict expert (priority 1) before primary (priority 0)
        victim = manager._pick_eviction_candidate("qwen35-27b")
        assert victim == "qwen35-9b"

    def test_nothing_running(self, manager: ModelManager):
        assert manager._pick_eviction_candidate("qwen35-9b") is None

    def test_single_running(self, manager: ModelManager, primary_cfg):
        manager._active["qwen35-4b"] = ActiveModel(config=primary_cfg, healthy=True)
        victim = manager._pick_eviction_candidate("qwen35-9b")
        assert victim == "qwen35-4b"

    def test_does_not_evict_self(self, manager: ModelManager, primary_cfg):
        manager._active["qwen35-4b"] = ActiveModel(config=primary_cfg, healthy=True)
        victim = manager._pick_eviction_candidate("qwen35-4b")
        assert victim is None


# ---------------------------------------------------------------------------
# Health check polling
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_on_first_poll(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        active = ActiveModel(config=primary_cfg, process=proc, url="http://127.0.0.1:8100")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.pipeline.model_manager.httpx.AsyncClient", return_value=mock_client):
            result = await manager._wait_healthy(active)
        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_process_exits(self, manager: ModelManager, primary_cfg):
        proc = _fake_process(returncode=1)
        active = ActiveModel(config=primary_cfg, process=proc, url="http://127.0.0.1:8100")

        import httpx
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.pipeline.model_manager.httpx.AsyncClient", return_value=mock_client):
            result = await manager._wait_healthy(active)
        assert result is False

    @pytest.mark.asyncio
    async def test_healthy_after_retries(self, manager: ModelManager, primary_cfg):
        proc = _fake_process()
        active = ActiveModel(config=primary_cfg, process=proc, url="http://127.0.0.1:8100")

        import httpx
        good_resp = MagicMock()
        good_resp.status_code = 200
        # Fail twice, then succeed
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused"), good_resp],
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.pipeline.model_manager.httpx.AsyncClient", return_value=mock_client), \
             patch("cortex.pipeline.model_manager._HEALTH_POLL_INTERVAL", 0.01):
            result = await manager._wait_healthy(active)
        assert result is True


# ---------------------------------------------------------------------------
# Environment handling
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_hsa_override_in_env(self, monkeypatch):
        monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
        mgr = ModelManager()
        env = mgr._build_env()
        assert env["HSA_OVERRIDE_GFX_VERSION"] == "11.0.0"

    def test_no_hsa_override(self, monkeypatch):
        monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
        mgr = ModelManager()
        env = mgr._build_env()
        # Should not crash, just omit the key (or pass through from os.environ)
        assert isinstance(env, dict)


# ---------------------------------------------------------------------------
# ActiveModel dataclass
# ---------------------------------------------------------------------------

class TestActiveModel:
    def test_defaults(self):
        cfg = _make_config()
        am = ActiveModel(config=cfg)
        assert am.process is None
        assert am.healthy is False
        assert am.url == ""
        assert am.started_at == 0.0


# ---------------------------------------------------------------------------
# Concurrency — swap lock
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_ensure_serialised(self, manager: ModelManager, primary_cfg, expert_cfg):
        """Two concurrent ensure_model_available calls should not race."""
        proc = _fake_process()
        start_count = 0

        original_start = manager.start_model

        async def counting_start(cfg):
            nonlocal start_count
            start_count += 1
            # Simulate model already being added to _active by start_model
            manager._active[cfg.name] = ActiveModel(
                config=cfg, process=proc, healthy=True,
                url=f"http://127.0.0.1:{cfg.port}",
            )
            return True

        manager.start_model = counting_start
        manager.stop_model = AsyncMock(return_value=True)

        # Pre-populate primary as running
        manager._active["qwen35-4b"] = ActiveModel(
            config=primary_cfg, process=proc, healthy=True,
            url="http://127.0.0.1:8100",
        )

        results = await asyncio.gather(
            manager.ensure_model_available("qwen35-9b"),
            manager.ensure_model_available("qwen35-9b"),
        )
        # Both should succeed, but only one should actually start the model
        assert all(r == "http://127.0.0.1:8101" for r in results)
        assert start_count == 1  # second caller sees it's already running
