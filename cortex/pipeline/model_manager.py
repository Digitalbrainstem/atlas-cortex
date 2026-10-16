"""VRAM-aware Model Manager for llama-server processes.

Orchestrates multiple llama-server instances on the same GPU without
artificial context caps.  The 4B model stays hot as the primary; larger
models (9B/27B) load on-demand when the Model Router escalates.

Allocation modes:
    1. Single — one model gets ALL VRAM  (--ctx-size 0 --fit on)
    2. Swap   — stop primary → start requested with full VRAM → serve → restore
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration & status dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """Static configuration for a model that *can* be launched."""

    name: str                        # e.g. "qwen35-4b", "qwen35-9b"
    gguf_path: str                   # absolute path to the GGUF file
    port: int                        # llama-server listen port
    priority: int                    # 0 = primary (always hot), 1 = expert, 2 = reasoning
    ctx_size: int = 0                # 0 → model's full training context (no cap)
    kv_type: str = "q4_0"           # KV cache quantisation (-ctk / -ctv)
    gpu_layers: int = 999            # offload all layers to GPU
    reasoning_format: str = "none"   # --reasoning-format flag


@dataclass
class ActiveModel:
    """Runtime state for a model whose llama-server process is alive."""

    config: ModelConfig
    process: asyncio.subprocess.Process | None = None
    started_at: float = 0.0
    healthy: bool = False
    url: str = ""


@dataclass
class VRAMStatus:
    """Snapshot of GPU VRAM usage (MiB)."""

    total_mib: int = 0
    used_mib: int = 0
    free_mib: int = 0
    gpu_vendor: str = "unknown"

    @property
    def utilisation_pct(self) -> float:
        if self.total_mib == 0:
            return 0.0
        return (self.used_mib / self.total_mib) * 100.0


# ---------------------------------------------------------------------------
# Helpers — GPU detection & VRAM parsing
# ---------------------------------------------------------------------------

def _detect_gpu_vendor() -> str:
    """Return 'amd', 'nvidia', or 'unknown'."""
    if shutil.which("rocm-smi"):
        return "amd"
    if shutil.which("nvidia-smi"):
        return "nvidia"
    return "unknown"


async def _read_vram() -> VRAMStatus:
    """Query GPU VRAM via rocm-smi or nvidia-smi (best-effort)."""
    vendor = _detect_gpu_vendor()
    try:
        if vendor == "nvidia":
            return await _read_vram_nvidia()
        if vendor == "amd":
            return await _read_vram_amd()
    except Exception as exc:  # noqa: BLE001
        logger.debug("VRAM read failed (%s): %s", vendor, exc)
    return VRAMStatus(gpu_vendor=vendor)


async def _read_vram_nvidia() -> VRAMStatus:
    proc = await asyncio.create_subprocess_exec(
        "nvidia-smi",
        "--query-gpu=memory.total,memory.used,memory.free",
        "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    parts = stdout.decode().strip().split(",")
    if len(parts) >= 3:
        total, used, free = (int(p.strip()) for p in parts[:3])
        return VRAMStatus(
            total_mib=total, used_mib=used, free_mib=free, gpu_vendor="nvidia",
        )
    return VRAMStatus(gpu_vendor="nvidia")


async def _read_vram_amd() -> VRAMStatus:
    proc = await asyncio.create_subprocess_exec(
        "rocm-smi", "--showmeminfo", "vram", "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode()
    # rocm-smi JSON output varies; fall back to regex parsing
    total = used = 0
    m_total = re.search(r'"Total Memory \(B\)"\s*:\s*"?(\d+)"?', text)
    m_used = re.search(r'"Total Used Memory \(B\)"\s*:\s*"?(\d+)"?', text)
    if m_total:
        total = int(m_total.group(1)) // (1024 * 1024)
    if m_used:
        used = int(m_used.group(1)) // (1024 * 1024)
    return VRAMStatus(
        total_mib=total,
        used_mib=used,
        free_mib=max(total - used, 0),
        gpu_vendor="amd",
    )


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------

# Health-check constants
_HEALTH_POLL_INTERVAL: float = 0.5   # seconds between /health polls
_HEALTH_TIMEOUT: float = 120.0        # max wait for healthy status
_GRACEFUL_STOP_TIMEOUT: float = 10.0  # wait before force-killing a server


class ModelManager:
    """Manages llama-server processes with VRAM-aware allocation.

    Typical lifecycle::

        mgr = ModelManager()
        mgr.register_model(ModelConfig(name="qwen35-4b", ...))
        mgr.register_model(ModelConfig(name="qwen35-9b", ...))

        await mgr.start_model(mgr.configs["qwen35-4b"])  # primary, always hot
        url = await mgr.ensure_model_available("qwen35-9b")  # swap
        # ... use url ...
        await mgr.release_model("qwen35-9b")              # restore primary
    """

    def __init__(self) -> None:
        self.configs: dict[str, ModelConfig] = {}
        self._active: dict[str, ActiveModel] = {}
        self._primary_name: str | None = None
        self._swap_lock = asyncio.Lock()
        self._llamacpp_dir = os.environ.get("LLAMACPP_DIR", "")
        self._model_dir = os.environ.get("MODEL_DIR", "")
        self._hsa_override = os.environ.get("HSA_OVERRIDE_GFX_VERSION", "")

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_model(self, config: ModelConfig) -> None:
        """Register a model configuration (does NOT start it)."""
        self.configs[config.name] = config
        if config.priority == 0:
            self._primary_name = config.name

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    async def start_model(self, model_config: ModelConfig) -> bool:
        """Launch a llama-server process for *model_config*.

        Returns ``True`` when the server passes its health check.
        """
        name = model_config.name
        if name in self._active and self._active[name].healthy:
            logger.info("Model %s already running and healthy", name)
            return True

        cmd = self._build_cmd(model_config)
        logger.info("Starting model %s on port %d", name, model_config.port)
        logger.debug("Command: %s", " ".join(cmd))

        env = self._build_env()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            logger.exception("Failed to launch llama-server for %s", name)
            return False

        url = f"http://127.0.0.1:{model_config.port}"
        active = ActiveModel(
            config=model_config,
            process=process,
            started_at=time.monotonic(),
            url=url,
        )
        self._active[name] = active

        healthy = await self._wait_healthy(active)
        if not healthy:
            logger.error("Model %s failed health check — stopping", name)
            await self.stop_model(name)
            return False

        active.healthy = True
        logger.info("Model %s ready at %s", name, url)
        return True

    async def stop_model(self, model_name: str) -> bool:
        """Gracefully terminate the llama-server for *model_name*."""
        active = self._active.pop(model_name, None)
        if active is None:
            logger.debug("stop_model(%s): not running", model_name)
            return False

        active.healthy = False
        proc = active.process
        if proc is None or proc.returncode is not None:
            return True

        logger.info("Stopping model %s (pid %s)", model_name, proc.pid)
        try:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=_GRACEFUL_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Model %s did not exit in time — killing", model_name)
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
        except ProcessLookupError:
            pass

        logger.info("Model %s stopped", model_name)
        return True

    async def swap_model(self, from_model: str, to_model: str) -> bool:
        """Stop *from_model*, start *to_model* with full VRAM.

        Returns ``True`` when *to_model* is healthy.
        """
        if from_model == to_model:
            logger.debug("swap_model: from == to (%s), no-op", from_model)
            return self.is_model_ready(to_model)

        to_config = self.configs.get(to_model)
        if to_config is None:
            logger.error("swap_model: unknown model %s", to_model)
            return False

        logger.info("Swapping %s → %s", from_model, to_model)
        await self.stop_model(from_model)

        # Short pause to let VRAM release
        await asyncio.sleep(0.5)

        ok = await self.start_model(to_config)
        if not ok:
            logger.error("Swap failed: could not start %s", to_model)
        return ok

    # ------------------------------------------------------------------
    # Smart allocation
    # ------------------------------------------------------------------

    async def ensure_model_available(self, model_name: str) -> str:
        """Return the API URL for *model_name*, loading it if necessary.

        If the requested model is not running, the manager swaps out the
        current primary model to free VRAM, starts the requested model
        with full GPU resources, and returns its URL.

        Raises ``RuntimeError`` if the model cannot be made available.
        """
        # Already running?
        active = self._active.get(model_name)
        if active and active.healthy:
            return active.url

        config = self.configs.get(model_name)
        if config is None:
            raise RuntimeError(f"Unknown model: {model_name}")

        async with self._swap_lock:
            # Re-check under lock
            active = self._active.get(model_name)
            if active and active.healthy:
                return active.url

            # Find the lowest-priority running model to evict
            victim = self._pick_eviction_candidate(model_name)
            if victim:
                ok = await self.swap_model(victim, model_name)
            else:
                ok = await self.start_model(config)

            if not ok:
                raise RuntimeError(f"Failed to start model {model_name}")

            return self._active[model_name].url

    async def release_model(self, model_name: str) -> None:
        """Called when the caller is done with an escalated model.

        If the released model is NOT the primary, it is stopped and the
        primary is restored.
        """
        if model_name == self._primary_name:
            return

        async with self._swap_lock:
            if model_name not in self._active:
                return

            primary_config = (
                self.configs.get(self._primary_name)
                if self._primary_name
                else None
            )

            await self.stop_model(model_name)

            if primary_config and self._primary_name not in self._active:
                logger.info("Restoring primary model %s", self._primary_name)
                await self.start_model(primary_config)

    # ------------------------------------------------------------------
    # Status / queries
    # ------------------------------------------------------------------

    def get_active_models(self) -> list[ActiveModel]:
        return list(self._active.values())

    def get_vram_usage(self) -> VRAMStatus:
        """Synchronous convenience — fires-and-forgets an async read.

        Prefer ``get_vram_usage_async()`` in async code paths.
        """
        try:
            loop = asyncio.get_running_loop()
            # We're inside a running loop — can't call run_until_complete.
            # Return a placeholder; callers should use the async variant.
            return VRAMStatus()
        except RuntimeError:
            return asyncio.run(_read_vram())

    async def get_vram_usage_async(self) -> VRAMStatus:
        return await _read_vram()

    def is_model_ready(self, model_name: str) -> bool:
        active = self._active.get(model_name)
        return active is not None and active.healthy

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_cmd(self, cfg: ModelConfig) -> list[str]:
        server_bin = str(
            Path(self._llamacpp_dir) / "llama-server"
        ) if self._llamacpp_dir else "llama-server"

        gguf = cfg.gguf_path
        if not os.path.isabs(gguf) and self._model_dir:
            gguf = str(Path(self._model_dir) / gguf)

        cmd = [
            server_bin,
            "--model", gguf,
            "--port", str(cfg.port),
            "--ctx-size", str(cfg.ctx_size),
            "--n-gpu-layers", str(cfg.gpu_layers),
            "--fit", "on",
            "-ctk", cfg.kv_type,
            "-ctv", cfg.kv_type,
        ]
        if cfg.reasoning_format != "none":
            cmd += ["--reasoning-format", cfg.reasoning_format]
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._hsa_override:
            env["HSA_OVERRIDE_GFX_VERSION"] = self._hsa_override
        return env

    async def _wait_healthy(self, active: ActiveModel) -> bool:
        """Poll ``/health`` until 200 or timeout."""
        url = f"{active.url}/health"
        deadline = time.monotonic() + _HEALTH_TIMEOUT
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                # If the process died, bail early
                if active.process and active.process.returncode is not None:
                    logger.error(
                        "llama-server for %s exited with code %s during startup",
                        active.config.name,
                        active.process.returncode,
                    )
                    return False
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(_HEALTH_POLL_INTERVAL)
        return False

    def _pick_eviction_candidate(self, needed: str) -> str | None:
        """Choose which running model to stop to make room for *needed*.

        Returns the model name with the **lowest** priority value
        (i.e. least important to keep) among running models, or ``None``
        if nothing is running.
        """
        candidates = [
            (name, am.config.priority)
            for name, am in self._active.items()
            if name != needed
        ]
        if not candidates:
            return None
        # highest priority *number* → least important
        candidates.sort(key=lambda c: c[1], reverse=True)
        return candidates[0][0]
