"""Dual-model hot loading for llama-server instances on the same GPU.

Runs TWO llama-server processes simultaneously so that one can generate
while the other reviews — e.g. Qwen3.5-4B Q4 (≈2.7 GB) on port 8080
as the fast/general model and Omnicoder-9B Q4 (≈5.7 GB) on port 8081
as the reviewer.  Total ≈8.4 GB leaves ~11.6 GB for KV cache on a
20 GB GPU.

Each instance uses ``--fit on`` so llama-server manages its own VRAM
slice; we simply ensure we don't overcommit by tracking allocations.

Falls back to single-model operation if the secondary server cannot
start.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from cortex.pipeline.model_manager import (
    ActiveModel,
    ModelConfig,
    ModelManager,
    VRAMStatus,
    _read_vram,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_HEALTH_POLL_INTERVAL: float = 0.5
_HEALTH_TIMEOUT: float = 120.0
_VRAM_HEADROOM_MIB: int = 512  # keep 512 MiB free for OS / desktop


@dataclass
class DualModelStatus:
    """Snapshot of both model slots."""

    primary_name: str | None = None
    primary_url: str | None = None
    primary_healthy: bool = False
    secondary_name: str | None = None
    secondary_url: str | None = None
    secondary_healthy: bool = False
    vram: VRAMStatus = field(default_factory=VRAMStatus)


# ---------------------------------------------------------------------------
# DualModelManager
# ---------------------------------------------------------------------------

class DualModelManager:
    """Run two models simultaneously on the same GPU.

    Typical usage::

        dm = DualModelManager()
        ok = await dm.start_dual(
            primary=ModelConfig(name="qwen35-4b", gguf_path="...", port=8080, priority=0),
            secondary=ModelConfig(name="omnicoder-9b", gguf_path="...", port=8081, priority=1),
        )
        gen_url = await dm.get_primary()     # "http://127.0.0.1:8080"
        rev_url = await dm.get_secondary()   # "http://127.0.0.1:8081"

        # Hot-swap primary from 4B → 9B for a complex task
        await dm.swap_primary(ModelConfig(name="qwen35-9b", ...))

        await dm.stop_all()
    """

    def __init__(self, *, manager: ModelManager | None = None) -> None:
        self._mgr = manager or ModelManager()
        self._primary_name: str | None = None
        self._secondary_name: str | None = None
        self._swap_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def manager(self) -> ModelManager:
        """Expose underlying ModelManager for direct access."""
        return self._mgr

    # ------------------------------------------------------------------
    # Dual start / stop
    # ------------------------------------------------------------------

    async def start_dual(
        self,
        primary: ModelConfig,
        secondary: ModelConfig,
    ) -> bool:
        """Start both models.  Primary gets first claim on VRAM.

        Returns ``True`` when **at least** the primary is healthy.
        If the secondary fails, we log a warning and continue in
        single-model mode (graceful degradation).
        """
        if primary.port == secondary.port:
            logger.error(
                "Primary and secondary cannot share port %d", primary.port,
            )
            return False

        self._mgr.register_model(primary)
        self._mgr.register_model(secondary)

        # Start primary first — it gets first VRAM allocation
        logger.info(
            "Starting primary model %s on port %d", primary.name, primary.port,
        )
        primary_ok = await self._mgr.start_model(primary)
        if not primary_ok:
            logger.error("Primary model %s failed to start", primary.name)
            return False
        self._primary_name = primary.name

        # Start secondary — may fail if VRAM is tight
        logger.info(
            "Starting secondary model %s on port %d",
            secondary.name,
            secondary.port,
        )
        secondary_ok = await self._mgr.start_model(secondary)
        if secondary_ok:
            self._secondary_name = secondary.name
            logger.info(
                "Dual-model ready: primary=%s secondary=%s",
                primary.name,
                secondary.name,
            )
        else:
            logger.warning(
                "Secondary model %s failed — running single-model mode",
                secondary.name,
            )

        return True  # primary is up

    async def stop_all(self) -> None:
        """Stop both models and reset state."""
        names = [
            n for n in (self._secondary_name, self._primary_name) if n
        ]
        for name in names:
            await self._mgr.stop_model(name)

        self._primary_name = None
        self._secondary_name = None
        logger.info("All dual-model instances stopped")

    # ------------------------------------------------------------------
    # URL accessors
    # ------------------------------------------------------------------

    async def get_primary(self) -> str:
        """Return the API base URL for the primary model.

        Raises ``RuntimeError`` if no primary is running.
        """
        if self._primary_name is None:
            raise RuntimeError("No primary model configured")
        active = self._mgr._active.get(self._primary_name)
        if active is None or not active.healthy:
            raise RuntimeError(
                f"Primary model {self._primary_name} is not healthy",
            )
        return active.url

    async def get_secondary(self) -> str:
        """Return the API base URL for the secondary model.

        Raises ``RuntimeError`` if no secondary is running.
        """
        if self._secondary_name is None:
            raise RuntimeError("No secondary model configured")
        active = self._mgr._active.get(self._secondary_name)
        if active is None or not active.healthy:
            raise RuntimeError(
                f"Secondary model {self._secondary_name} is not healthy",
            )
        return active.url

    # ------------------------------------------------------------------
    # Hot-swap primary
    # ------------------------------------------------------------------

    async def swap_primary(self, new_model: ModelConfig) -> bool:
        """Hot-swap the primary model while keeping the secondary alive.

        1. Stop current primary  →  free its VRAM
        2. Start *new_model* as new primary
        3. Secondary stays untouched throughout

        Returns ``True`` when the new primary is healthy.
        """
        async with self._swap_lock:
            if self._primary_name and self._primary_name == new_model.name:
                if self._mgr.is_model_ready(new_model.name):
                    logger.debug("swap_primary: %s already running", new_model.name)
                    return True

            self._mgr.register_model(new_model)
            old_primary = self._primary_name

            # Stop old primary to free VRAM
            if old_primary:
                logger.info(
                    "Swapping primary: %s → %s", old_primary, new_model.name,
                )
                await self._mgr.stop_model(old_primary)
                await asyncio.sleep(0.5)  # let VRAM release
            else:
                logger.info("Starting primary: %s (no previous)", new_model.name)

            ok = await self._mgr.start_model(new_model)
            if ok:
                self._primary_name = new_model.name
                logger.info("New primary %s ready", new_model.name)
            else:
                logger.error(
                    "Failed to start new primary %s — attempting rollback",
                    new_model.name,
                )
                # Try to restore old primary
                if old_primary:
                    old_cfg = self._mgr.configs.get(old_primary)
                    if old_cfg:
                        restored = await self._mgr.start_model(old_cfg)
                        if restored:
                            self._primary_name = old_primary
                            logger.info("Rolled back to %s", old_primary)
                        else:
                            self._primary_name = None
                            logger.error("Rollback to %s also failed", old_primary)
                    else:
                        self._primary_name = None

            return ok

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, bool]:
        """Check both models are responding.

        Returns a dict like ``{"primary": True, "secondary": False}``.
        """
        results: dict[str, bool] = {"primary": False, "secondary": False}

        async with httpx.AsyncClient(timeout=5.0) as client:
            checks = []
            if self._primary_name:
                active = self._mgr._active.get(self._primary_name)
                if active:
                    checks.append(("primary", f"{active.url}/health"))
            if self._secondary_name:
                active = self._mgr._active.get(self._secondary_name)
                if active:
                    checks.append(("secondary", f"{active.url}/health"))

            async def _check(role: str, url: str) -> tuple[str, bool]:
                try:
                    resp = await client.get(url)
                    return role, resp.status_code == 200
                except httpx.HTTPError:
                    return role, False

            if checks:
                tasks = [_check(role, url) for role, url in checks]
                for coro in asyncio.as_completed(tasks):
                    role, healthy = await coro
                    results[role] = healthy

        return results

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> DualModelStatus:
        """Return a full snapshot of both model slots."""
        vram = await _read_vram()
        health = await self.health_check()

        p_active = (
            self._mgr._active.get(self._primary_name)
            if self._primary_name
            else None
        )
        s_active = (
            self._mgr._active.get(self._secondary_name)
            if self._secondary_name
            else None
        )

        return DualModelStatus(
            primary_name=self._primary_name,
            primary_url=p_active.url if p_active else None,
            primary_healthy=health.get("primary", False),
            secondary_name=self._secondary_name,
            secondary_url=s_active.url if s_active else None,
            secondary_healthy=health.get("secondary", False),
            vram=vram,
        )

    def is_dual_mode(self) -> bool:
        """Return ``True`` if both models are running and healthy."""
        if not self._primary_name or not self._secondary_name:
            return False
        return (
            self._mgr.is_model_ready(self._primary_name)
            and self._mgr.is_model_ready(self._secondary_name)
        )

    def is_degraded(self) -> bool:
        """Return ``True`` if primary is healthy but secondary is not."""
        if not self._primary_name:
            return False
        primary_ok = self._mgr.is_model_ready(self._primary_name)
        secondary_ok = (
            self._mgr.is_model_ready(self._secondary_name)
            if self._secondary_name
            else False
        )
        return primary_ok and not secondary_ok
