"""Tests for cortex.pipeline.dual_model."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cortex.pipeline.model_manager import ActiveModel, ModelConfig, ModelManager, VRAMStatus
from cortex.pipeline.dual_model import DualModelManager, DualModelStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    name: str = "qwen35-4b",
    port: int = 8080,
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


def _fake_process(*, returncode=None, pid=12345):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def primary_cfg() -> ModelConfig:
    return _make_config("qwen35-4b", port=8080, priority=0)


@pytest.fixture()
def secondary_cfg() -> ModelConfig:
    return _make_config("omnicoder-9b", port=8081, priority=1)


@pytest.fixture()
def upgrade_cfg() -> ModelConfig:
    return _make_config("qwen35-9b", port=8080, priority=0)


@pytest.fixture()
def dual(primary_cfg, secondary_cfg) -> DualModelManager:
    """A DualModelManager with no models started."""
    mgr = ModelManager()
    return DualModelManager(manager=mgr)


def _inject_active(
    dm: DualModelManager,
    cfg: ModelConfig,
    *,
    healthy: bool = True,
    pid: int = 12345,
) -> ActiveModel:
    """Put a model into the manager's active dict without starting a process."""
    proc = _fake_process(pid=pid)
    active = ActiveModel(
        config=cfg,
        process=proc,
        healthy=healthy,
        url=f"http://127.0.0.1:{cfg.port}",
    )
    dm.manager._active[cfg.name] = active
    dm.manager.register_model(cfg)
    return active


# ---------------------------------------------------------------------------
# DualModelStatus
# ---------------------------------------------------------------------------

class TestDualModelStatus:
    def test_defaults(self):
        s = DualModelStatus()
        assert s.primary_name is None
        assert s.secondary_name is None
        assert s.primary_healthy is False
        assert s.secondary_healthy is False
        assert s.vram.total_mib == 0


# ---------------------------------------------------------------------------
# start_dual
# ---------------------------------------------------------------------------

class TestStartDual:
    @pytest.mark.asyncio
    async def test_both_start_successfully(self, dual, primary_cfg, secondary_cfg):
        proc_p = _fake_process(pid=100)
        proc_s = _fake_process(pid=200)

        procs = iter([proc_p, proc_s])

        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: next(procs),
        ), patch.object(
            dual.manager, "_wait_healthy", new_callable=AsyncMock, return_value=True,
        ):
            ok = await dual.start_dual(primary_cfg, secondary_cfg)

        assert ok is True
        assert dual._primary_name == "qwen35-4b"
        assert dual._secondary_name == "omnicoder-9b"
        assert dual.is_dual_mode() is True
        assert dual.is_degraded() is False

    @pytest.mark.asyncio
    async def test_secondary_failure_degrades_gracefully(
        self, dual, primary_cfg, secondary_cfg,
    ):
        proc_p = _fake_process(pid=100)
        proc_s = _fake_process(pid=200)

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return proc_p
            return proc_s

        health_calls = 0

        async def _health_side_effect(active):
            nonlocal health_calls
            health_calls += 1
            # Primary succeeds, secondary fails
            return health_calls == 1

        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ), patch.object(
            dual.manager, "_wait_healthy",
            new_callable=AsyncMock,
            side_effect=_health_side_effect,
        ):
            ok = await dual.start_dual(primary_cfg, secondary_cfg)

        assert ok is True  # primary worked
        assert dual._primary_name == "qwen35-4b"
        assert dual._secondary_name is None
        assert dual.is_dual_mode() is False
        assert dual.is_degraded() is True

    @pytest.mark.asyncio
    async def test_primary_failure_returns_false(
        self, dual, primary_cfg, secondary_cfg,
    ):
        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("no binary"),
        ):
            ok = await dual.start_dual(primary_cfg, secondary_cfg)

        assert ok is False
        assert dual._primary_name is None
        assert dual._secondary_name is None

    @pytest.mark.asyncio
    async def test_same_port_rejected(self, dual):
        cfg_a = _make_config("model-a", port=8080, priority=0)
        cfg_b = _make_config("model-b", port=8080, priority=1)
        ok = await dual.start_dual(cfg_a, cfg_b)
        assert ok is False

    @pytest.mark.asyncio
    async def test_registers_both_configs(self, dual, primary_cfg, secondary_cfg):
        proc = _fake_process()
        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ), patch.object(
            dual.manager, "_wait_healthy", new_callable=AsyncMock, return_value=True,
        ):
            await dual.start_dual(primary_cfg, secondary_cfg)

        assert "qwen35-4b" in dual.manager.configs
        assert "omnicoder-9b" in dual.manager.configs


# ---------------------------------------------------------------------------
# stop_all
# ---------------------------------------------------------------------------

class TestStopAll:
    @pytest.mark.asyncio
    async def test_stops_both_models(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        await dual.stop_all()

        assert dual._primary_name is None
        assert dual._secondary_name is None
        assert len(dual.manager._active) == 0

    @pytest.mark.asyncio
    async def test_stop_all_when_empty(self, dual):
        await dual.stop_all()  # should not raise
        assert dual._primary_name is None

    @pytest.mark.asyncio
    async def test_stop_all_primary_only(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        dual._primary_name = primary_cfg.name

        await dual.stop_all()

        assert dual._primary_name is None
        assert primary_cfg.name not in dual.manager._active


# ---------------------------------------------------------------------------
# get_primary / get_secondary
# ---------------------------------------------------------------------------

class TestGetURLs:
    @pytest.mark.asyncio
    async def test_get_primary_url(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg)
        dual._primary_name = primary_cfg.name

        url = await dual.get_primary()
        assert url == "http://127.0.0.1:8080"

    @pytest.mark.asyncio
    async def test_get_secondary_url(self, dual, secondary_cfg):
        _inject_active(dual, secondary_cfg)
        dual._secondary_name = secondary_cfg.name

        url = await dual.get_secondary()
        assert url == "http://127.0.0.1:8081"

    @pytest.mark.asyncio
    async def test_get_primary_not_configured(self, dual):
        with pytest.raises(RuntimeError, match="No primary model configured"):
            await dual.get_primary()

    @pytest.mark.asyncio
    async def test_get_secondary_not_configured(self, dual):
        with pytest.raises(RuntimeError, match="No secondary model configured"):
            await dual.get_secondary()

    @pytest.mark.asyncio
    async def test_get_primary_unhealthy(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg, healthy=False)
        dual._primary_name = primary_cfg.name

        with pytest.raises(RuntimeError, match="not healthy"):
            await dual.get_primary()

    @pytest.mark.asyncio
    async def test_get_secondary_unhealthy(self, dual, secondary_cfg):
        _inject_active(dual, secondary_cfg, healthy=False)
        dual._secondary_name = secondary_cfg.name

        with pytest.raises(RuntimeError, match="not healthy"):
            await dual.get_secondary()


# ---------------------------------------------------------------------------
# swap_primary
# ---------------------------------------------------------------------------

class TestSwapPrimary:
    @pytest.mark.asyncio
    async def test_swap_primary_keeps_secondary(
        self, dual, primary_cfg, secondary_cfg, upgrade_cfg,
    ):
        """The core test: secondary stays alive when primary is swapped."""
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        proc_new = _fake_process(pid=300)
        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc_new,
        ), patch.object(
            dual.manager, "_wait_healthy", new_callable=AsyncMock, return_value=True,
        ):
            ok = await dual.swap_primary(upgrade_cfg)

        assert ok is True
        assert dual._primary_name == "qwen35-9b"
        # Secondary must still be alive
        assert dual._secondary_name == "omnicoder-9b"
        assert dual.manager.is_model_ready("omnicoder-9b") is True

    @pytest.mark.asyncio
    async def test_swap_primary_same_model_noop(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg)
        dual._primary_name = primary_cfg.name

        ok = await dual.swap_primary(primary_cfg)
        assert ok is True
        assert dual._primary_name == "qwen35-4b"

    @pytest.mark.asyncio
    async def test_swap_primary_failure_rolls_back(
        self, dual, primary_cfg, secondary_cfg, upgrade_cfg,
    ):
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        call_count = 0

        async def _create_proc(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _fake_process(pid=300 + call_count)

        health_count = 0

        async def _health(active):
            nonlocal health_count
            health_count += 1
            # First call (new primary) fails, second (rollback) succeeds
            return health_count != 1

        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=_create_proc,
        ), patch.object(
            dual.manager, "_wait_healthy",
            new_callable=AsyncMock,
            side_effect=_health,
        ):
            ok = await dual.swap_primary(upgrade_cfg)

        assert ok is False
        # Should have rolled back to original primary
        assert dual._primary_name == "qwen35-4b"

    @pytest.mark.asyncio
    async def test_swap_primary_no_previous(self, dual, upgrade_cfg):
        """Start a primary when none was running."""
        proc = _fake_process(pid=100)
        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ), patch.object(
            dual.manager, "_wait_healthy", new_callable=AsyncMock, return_value=True,
        ):
            ok = await dual.swap_primary(upgrade_cfg)

        assert ok is True
        assert dual._primary_name == "qwen35-9b"

    @pytest.mark.asyncio
    async def test_swap_primary_serialised(self, dual, primary_cfg, upgrade_cfg):
        """Concurrent swap_primary calls are serialised by the lock."""
        _inject_active(dual, primary_cfg)
        dual._primary_name = primary_cfg.name

        proc = _fake_process(pid=100)

        async def _slow_start(cfg):
            await asyncio.sleep(0.05)
            dual.manager._active[cfg.name] = ActiveModel(
                config=cfg, process=proc, healthy=True,
                url=f"http://127.0.0.1:{cfg.port}",
            )
            return True

        with patch.object(
            dual.manager, "start_model",
            new_callable=AsyncMock,
            side_effect=_slow_start,
        ), patch.object(
            dual.manager, "stop_model", new_callable=AsyncMock, return_value=True,
        ):
            t1 = asyncio.create_task(dual.swap_primary(upgrade_cfg))
            t2 = asyncio.create_task(dual.swap_primary(upgrade_cfg))
            r1, r2 = await asyncio.gather(t1, t2)

        assert r1 is True
        assert r2 is True


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_both_healthy(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("cortex.pipeline.dual_model.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dual.health_check()

        assert result["primary"] is True
        assert result["secondary"] is True

    @pytest.mark.asyncio
    async def test_secondary_unhealthy(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        async def _get(url):
            resp = MagicMock()
            if "8080" in url:
                resp.status_code = 200
            else:
                resp.status_code = 503
            return resp

        with patch("cortex.pipeline.dual_model.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=_get)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dual.health_check()

        assert result["primary"] is True
        assert result["secondary"] is False

    @pytest.mark.asyncio
    async def test_health_check_network_error(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        dual._primary_name = primary_cfg.name

        with patch("cortex.pipeline.dual_model.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dual.health_check()

        assert result["primary"] is False
        assert result["secondary"] is False

    @pytest.mark.asyncio
    async def test_health_check_no_models(self, dual):
        result = await dual.health_check()
        assert result == {"primary": False, "secondary": False}


# ---------------------------------------------------------------------------
# status / is_dual_mode / is_degraded
# ---------------------------------------------------------------------------

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_dual_mode(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, pid=100)
        _inject_active(dual, secondary_cfg, pid=200)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "cortex.pipeline.dual_model._read_vram",
            new_callable=AsyncMock,
            return_value=VRAMStatus(
                total_mib=20480, used_mib=8400, free_mib=12080, gpu_vendor="nvidia",
            ),
        ), patch("cortex.pipeline.dual_model.httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            s = await dual.status()

        assert s.primary_name == "qwen35-4b"
        assert s.secondary_name == "omnicoder-9b"
        assert s.primary_url == "http://127.0.0.1:8080"
        assert s.secondary_url == "http://127.0.0.1:8081"
        assert s.primary_healthy is True
        assert s.secondary_healthy is True
        assert s.vram.total_mib == 20480
        assert s.vram.free_mib == 12080

    def test_is_dual_mode_true(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg)
        _inject_active(dual, secondary_cfg)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        assert dual.is_dual_mode() is True

    def test_is_dual_mode_false_no_secondary(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg)
        dual._primary_name = primary_cfg.name

        assert dual.is_dual_mode() is False

    def test_is_dual_mode_false_no_models(self, dual):
        assert dual.is_dual_mode() is False

    def test_is_degraded_true(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, healthy=True)
        _inject_active(dual, secondary_cfg, healthy=False)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        assert dual.is_degraded() is True

    def test_is_degraded_false_both_healthy(self, dual, primary_cfg, secondary_cfg):
        _inject_active(dual, primary_cfg, healthy=True)
        _inject_active(dual, secondary_cfg, healthy=True)
        dual._primary_name = primary_cfg.name
        dual._secondary_name = secondary_cfg.name

        assert dual.is_degraded() is False

    def test_is_degraded_false_no_primary(self, dual):
        assert dual.is_degraded() is False

    def test_is_degraded_no_secondary_configured(self, dual, primary_cfg):
        _inject_active(dual, primary_cfg, healthy=True)
        dual._primary_name = primary_cfg.name

        assert dual.is_degraded() is True


# ---------------------------------------------------------------------------
# manager property
# ---------------------------------------------------------------------------

class TestManagerProperty:
    def test_exposes_underlying_manager(self, dual):
        assert dual.manager is dual._mgr

    def test_uses_provided_manager(self):
        mgr = ModelManager()
        dm = DualModelManager(manager=mgr)
        assert dm.manager is mgr

    def test_creates_default_manager(self):
        dm = DualModelManager()
        assert isinstance(dm.manager, ModelManager)


# ---------------------------------------------------------------------------
# Integration-style: full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_start_use_swap_stop(self, dual, primary_cfg, secondary_cfg):
        """End-to-end: start dual → get URLs → swap primary → stop all."""
        procs = [_fake_process(pid=i) for i in range(100, 104)]
        proc_iter = iter(procs)

        with patch(
            "cortex.pipeline.model_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: next(proc_iter),
        ), patch.object(
            dual.manager, "_wait_healthy", new_callable=AsyncMock, return_value=True,
        ):
            # 1. Start dual
            ok = await dual.start_dual(primary_cfg, secondary_cfg)
            assert ok is True

            # 2. Get URLs
            p_url = await dual.get_primary()
            s_url = await dual.get_secondary()
            assert p_url == "http://127.0.0.1:8080"
            assert s_url == "http://127.0.0.1:8081"

            # 3. Swap primary to 9B
            upgrade = _make_config("qwen35-9b", port=8080, priority=0)
            ok = await dual.swap_primary(upgrade)
            assert ok is True
            assert dual._primary_name == "qwen35-9b"

            # Secondary still accessible
            s_url2 = await dual.get_secondary()
            assert s_url2 == "http://127.0.0.1:8081"

        # 4. Stop all
        await dual.stop_all()
        assert dual._primary_name is None
        assert dual._secondary_name is None
