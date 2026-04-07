"""Resource monitor with automatic hibernate and recovery.

Monitors RAM, disk, and GPU VRAM. Transitions through:
    HEALTHY → WARNING → CRITICAL → HIBERNATING → RECOVERING → HEALTHY
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class MonitorState(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    HIBERNATING = "hibernating"
    RECOVERING = "recovering"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ResourceLevels:
    ram_pct: float = 0.0
    disk_pct: float = 0.0
    vram_pct: float = 0.0
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Thresholds:
    ram_warn: float = 0.85
    ram_critical: float = 0.92
    disk_warn: float = 0.85
    disk_critical: float = 0.95
    vram_warn: float = 0.85
    vram_critical: float = 0.92
    recovery_pct: float = 0.70


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class ResourceMonitor:
    """Monitors RAM, disk, GPU VRAM. Warns then hibernates before crash."""

    def __init__(self, *, announce_fn: Any | None = None) -> None:
        self.thresholds = Thresholds(
            ram_warn=float(os.environ.get("RESOURCE_RAM_WARN_PCT", "0.85")),
            ram_critical=float(os.environ.get("RESOURCE_RAM_CRITICAL_PCT", "0.92")),
            disk_warn=float(os.environ.get("RESOURCE_DISK_WARN_PCT", "0.85")),
            disk_critical=float(os.environ.get("RESOURCE_DISK_CRITICAL_PCT", "0.95")),
            vram_warn=float(os.environ.get("RESOURCE_VRAM_WARN_PCT", "0.85")),
            vram_critical=float(os.environ.get("RESOURCE_VRAM_CRITICAL_PCT", "0.92")),
            recovery_pct=float(os.environ.get("RESOURCE_RECOVERY_PCT", "0.70")),
        )
        self.check_interval = int(os.environ.get("RESOURCE_CHECK_INTERVAL", "30"))
        self.state = MonitorState.HEALTHY
        self.levels = ResourceLevels()
        self._stopped_services: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._announce_fn = announce_fn
        self._snapshot_fn: Any | None = None

    # -- public API ---------------------------------------------------------

    def set_snapshot_fn(self, fn: Any) -> None:
        """Wire the snapshot callback after construction."""
        self._snapshot_fn = fn

    def get_status(self) -> dict[str, Any]:
        """Return current status dict (for admin API)."""
        return {
            "state": self.state.value,
            "levels": {
                "ram_pct": round(self.levels.ram_pct, 4),
                "disk_pct": round(self.levels.disk_pct, 4),
                "vram_pct": round(self.levels.vram_pct, 4),
                "ram_total_gb": round(self.levels.ram_total_gb, 2),
                "ram_used_gb": round(self.levels.ram_used_gb, 2),
                "disk_total_gb": round(self.levels.disk_total_gb, 2),
                "disk_used_gb": round(self.levels.disk_used_gb, 2),
                "vram_total_mb": round(self.levels.vram_total_mb, 1),
                "vram_used_mb": round(self.levels.vram_used_mb, 1),
                "timestamp": self.levels.timestamp,
            },
            "thresholds": {
                "ram_warn": self.thresholds.ram_warn,
                "ram_critical": self.thresholds.ram_critical,
                "disk_warn": self.thresholds.disk_warn,
                "disk_critical": self.thresholds.disk_critical,
                "vram_warn": self.thresholds.vram_warn,
                "vram_critical": self.thresholds.vram_critical,
                "recovery_pct": self.thresholds.recovery_pct,
            },
            "stopped_services": list(self._stopped_services),
        }

    def update_thresholds(self, **kwargs: float) -> dict[str, float]:
        """Update thresholds. Returns the full thresholds dict."""
        for key, val in kwargs.items():
            if hasattr(self.thresholds, key):
                setattr(self.thresholds, key, val)
        return {
            "ram_warn": self.thresholds.ram_warn,
            "ram_critical": self.thresholds.ram_critical,
            "disk_warn": self.thresholds.disk_warn,
            "disk_critical": self.thresholds.disk_critical,
            "vram_warn": self.thresholds.vram_warn,
            "vram_critical": self.thresholds.vram_critical,
            "recovery_pct": self.thresholds.recovery_pct,
        }

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Resource monitor started (interval=%ds)", self.check_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Resource monitor stopped")

    # -- main loop ----------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                self.levels = self._read_resources()
                new_state = self._evaluate_state()
                if new_state != self.state:
                    await self._transition(new_state)
            except Exception:
                logger.exception("Resource monitor tick failed")
            await asyncio.sleep(self.check_interval)

    # -- resource reading ---------------------------------------------------

    def _read_resources(self) -> ResourceLevels:
        mem = psutil.virtual_memory()
        disk = _disk_usage()
        vram_used, vram_total = _gpu_vram()
        vram_pct = (vram_used / vram_total) if vram_total > 0 else 0.0
        return ResourceLevels(
            ram_pct=mem.percent / 100.0,
            disk_pct=disk.percent / 100.0,
            vram_pct=vram_pct,
            ram_total_gb=mem.total / (1024 ** 3),
            ram_used_gb=mem.used / (1024 ** 3),
            disk_total_gb=disk.total / (1024 ** 3),
            disk_used_gb=disk.used / (1024 ** 3),
            vram_total_mb=vram_total,
            vram_used_mb=vram_used,
            timestamp=time.time(),
        )

    # -- state evaluation ---------------------------------------------------

    def _evaluate_state(self) -> MonitorState:
        lvl = self.levels
        th = self.thresholds

        if self.state == MonitorState.HIBERNATING:
            # Only leave hibernation when ALL resources below recovery line
            if (lvl.ram_pct < th.recovery_pct
                    and lvl.disk_pct < th.recovery_pct
                    and (lvl.vram_pct < th.recovery_pct or lvl.vram_pct == 0.0)):
                return MonitorState.RECOVERING
            return MonitorState.HIBERNATING

        # Check for critical
        if (lvl.ram_pct >= th.ram_critical
                or lvl.disk_pct >= th.disk_critical
                or (lvl.vram_pct >= th.vram_critical and lvl.vram_pct > 0)):
            return MonitorState.CRITICAL

        # Check for warning
        if (lvl.ram_pct >= th.ram_warn
                or lvl.disk_pct >= th.disk_warn
                or (lvl.vram_pct >= th.vram_warn and lvl.vram_pct > 0)):
            return MonitorState.WARNING

        return MonitorState.HEALTHY

    # -- state transitions --------------------------------------------------

    async def _transition(self, new_state: MonitorState) -> None:
        old = self.state
        self.state = new_state
        logger.info("Resource monitor: %s → %s", old.value, new_state.value)

        if new_state == MonitorState.WARNING:
            await self._on_warning()
        elif new_state == MonitorState.CRITICAL:
            await self._on_critical()
        elif new_state == MonitorState.RECOVERING:
            await self._on_recovering()
        elif new_state == MonitorState.HEALTHY and old == MonitorState.RECOVERING:
            logger.info("Resource monitor: fully recovered")

    async def _on_warning(self) -> None:
        parts: list[str] = []
        if self.levels.ram_pct >= self.thresholds.ram_warn:
            parts.append("memory")
        if self.levels.disk_pct >= self.thresholds.disk_warn:
            parts.append("disk")
        if self.levels.vram_pct >= self.thresholds.vram_warn and self.levels.vram_pct > 0:
            parts.append("GPU")
        resource_str = ", ".join(parts) or "resources"
        msg = f"I'm running low on {resource_str}. You might want to close some things."
        logger.warning("Resource warning: %s", msg)
        await self._announce(msg)

    async def _on_critical(self) -> None:
        logger.critical(
            "Resource CRITICAL — RAM=%.0f%% DISK=%.0f%% VRAM=%.0f%%",
            self.levels.ram_pct * 100,
            self.levels.disk_pct * 100,
            self.levels.vram_pct * 100,
        )

        # Take emergency snapshot
        if self._snapshot_fn:
            try:
                await self._snapshot_fn()
                logger.info("Emergency snapshot taken")
            except Exception:
                logger.exception("Emergency snapshot failed")

        # Stop non-essential services
        await self._stop_non_essential_services()

        msg = "I need to hibernate to protect my data. I'll resume when resources are available."
        await self._announce(msg)
        self.state = MonitorState.HIBERNATING
        logger.info("Entered HIBERNATING state")

    async def _on_recovering(self) -> None:
        logger.info("Resources below recovery threshold — resuming services")
        await self._resume_services()
        msg = "Resources are available again. Resuming normal operation."
        await self._announce(msg)
        self.state = MonitorState.HEALTHY

    # -- service control ----------------------------------------------------

    _NON_ESSENTIAL = [
        "nightly-evolution",
        "knowledge-sync",
        "eye-tracker",
        "filler-cache",
        "lora-discover",
    ]

    async def _stop_non_essential_services(self) -> None:
        """Stop non-essential background services to free resources."""
        try:
            from cortex.scheduler import _background_services
            for svc in _background_services:
                if svc.name in self._NON_ESSENTIAL:
                    try:
                        await svc.stop_fn()
                        self._stopped_services.append(svc.name)
                        logger.info("Hibernation: stopped service %s", svc.name)
                    except Exception:
                        logger.warning("Failed to stop %s during hibernation", svc.name)
        except ImportError:
            logger.debug("Scheduler not available — skipping service shutdown")

    async def _resume_services(self) -> None:
        """Restart services that were stopped during hibernation."""
        try:
            from cortex.scheduler import _background_services
            for svc in _background_services:
                if svc.name in self._stopped_services:
                    try:
                        await svc.start_fn()
                        logger.info("Resumed service: %s", svc.name)
                    except Exception:
                        logger.warning("Failed to resume %s", svc.name)
            self._stopped_services.clear()
        except ImportError:
            logger.debug("Scheduler not available — skipping service resume")

    # -- announcements ------------------------------------------------------

    async def _announce(self, message: str) -> None:
        if self._announce_fn:
            try:
                await self._announce_fn(message)
            except Exception:
                logger.debug("TTS announcement failed: %s", message)
        logger.info("Announcement: %s", message)


# ---------------------------------------------------------------------------
# Helpers (module-level for testability)
# ---------------------------------------------------------------------------

def _disk_usage() -> Any:
    """Return disk usage for the data directory."""
    data_dir = os.environ.get("CORTEX_DATA_DIR", "./data")
    try:
        return shutil.disk_usage(data_dir)
    except OSError:
        return shutil.disk_usage("/")


def _gpu_vram() -> tuple[float, float]:
    """Return (used_mb, total_mb) for the first GPU, or (0, 0)."""
    # Try torch first
    try:
        import torch
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated(0) / (1024 ** 2)
            total = torch.cuda.get_device_properties(0).total_mem / (1024 ** 2)
            return (used, total)
    except Exception:
        pass

    # Fallback: nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            timeout=5,
            text=True,
        )
        parts = out.strip().split("\n")[0].split(",")
        return (float(parts[0].strip()), float(parts[1].strip()))
    except Exception:
        pass

    return (0.0, 0.0)
