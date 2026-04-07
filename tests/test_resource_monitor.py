"""Tests for the resource monitor state machine."""

from __future__ import annotations

import asyncio
import time

import pytest

from cortex.system.resource_monitor import (
    MonitorState,
    ResourceLevels,
    ResourceMonitor,
    Thresholds,
)


# ──────────────────────────────────────────────────────────────────
# Threshold evaluation
# ──────────────────────────────────────────────────────────────────


class TestThresholdDetection:
    """Verify state evaluation against configurable thresholds."""

    def _make_monitor(self, **kw: float) -> ResourceMonitor:
        m = ResourceMonitor()
        for k, v in kw.items():
            setattr(m.thresholds, k, v)
        return m

    def test_healthy_when_all_low(self):
        m = self._make_monitor()
        m.levels = ResourceLevels(ram_pct=0.50, disk_pct=0.40, vram_pct=0.30)
        assert m._evaluate_state() == MonitorState.HEALTHY

    def test_warning_on_high_ram(self):
        m = self._make_monitor(ram_warn=0.80)
        m.levels = ResourceLevels(ram_pct=0.82, disk_pct=0.40, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.WARNING

    def test_warning_on_high_disk(self):
        m = self._make_monitor(disk_warn=0.80)
        m.levels = ResourceLevels(ram_pct=0.50, disk_pct=0.83, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.WARNING

    def test_warning_on_high_vram(self):
        m = self._make_monitor(vram_warn=0.80)
        m.levels = ResourceLevels(ram_pct=0.50, disk_pct=0.40, vram_pct=0.82)
        assert m._evaluate_state() == MonitorState.WARNING

    def test_critical_on_very_high_ram(self):
        m = self._make_monitor(ram_critical=0.90)
        m.levels = ResourceLevels(ram_pct=0.93, disk_pct=0.40, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.CRITICAL

    def test_critical_on_very_high_disk(self):
        m = self._make_monitor(disk_critical=0.95)
        m.levels = ResourceLevels(ram_pct=0.50, disk_pct=0.96, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.CRITICAL

    def test_critical_on_very_high_vram(self):
        m = self._make_monitor(vram_critical=0.90)
        m.levels = ResourceLevels(ram_pct=0.50, disk_pct=0.40, vram_pct=0.92)
        assert m._evaluate_state() == MonitorState.CRITICAL

    def test_zero_vram_ignored_for_warning(self):
        m = self._make_monitor(vram_warn=0.50)
        m.levels = ResourceLevels(ram_pct=0.30, disk_pct=0.30, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.HEALTHY

    def test_zero_vram_ignored_for_critical(self):
        m = self._make_monitor(vram_critical=0.50)
        m.levels = ResourceLevels(ram_pct=0.30, disk_pct=0.30, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.HEALTHY

    def test_critical_overrides_warning(self):
        m = self._make_monitor(ram_warn=0.80, ram_critical=0.90)
        m.levels = ResourceLevels(ram_pct=0.95, disk_pct=0.40, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.CRITICAL


# ──────────────────────────────────────────────────────────────────
# Hibernate and recovery
# ──────────────────────────────────────────────────────────────────


class TestHibernateResumeCycle:
    """Verify hibernate → recovery state transitions."""

    def test_stays_hibernating_if_still_high(self):
        m = ResourceMonitor()
        m.state = MonitorState.HIBERNATING
        m.levels = ResourceLevels(ram_pct=0.88, disk_pct=0.40, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.HIBERNATING

    def test_recovers_when_resources_free(self):
        m = ResourceMonitor()
        m.state = MonitorState.HIBERNATING
        m.thresholds.recovery_pct = 0.70
        m.levels = ResourceLevels(ram_pct=0.60, disk_pct=0.50, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.RECOVERING

    def test_stays_hibernating_if_one_resource_high(self):
        m = ResourceMonitor()
        m.state = MonitorState.HIBERNATING
        m.thresholds.recovery_pct = 0.70
        m.levels = ResourceLevels(ram_pct=0.60, disk_pct=0.75, vram_pct=0.0)
        assert m._evaluate_state() == MonitorState.HIBERNATING

    @pytest.mark.asyncio
    async def test_transition_warning_announces(self):
        announced: list[str] = []

        async def capture(msg: str) -> None:
            announced.append(msg)

        m = ResourceMonitor(announce_fn=capture)
        m.levels = ResourceLevels(ram_pct=0.87, disk_pct=0.40, vram_pct=0.0)
        await m._transition(MonitorState.WARNING)
        assert m.state == MonitorState.WARNING
        assert len(announced) == 1
        assert "memory" in announced[0]

    @pytest.mark.asyncio
    async def test_transition_critical_hibernates(self):
        announced: list[str] = []
        snapshot_called = False

        async def capture(msg: str) -> None:
            announced.append(msg)

        async def fake_snapshot() -> None:
            nonlocal snapshot_called
            snapshot_called = True

        m = ResourceMonitor(announce_fn=capture)
        m._snapshot_fn = fake_snapshot
        m.levels = ResourceLevels(ram_pct=0.95, disk_pct=0.40, vram_pct=0.0)
        await m._transition(MonitorState.CRITICAL)
        assert m.state == MonitorState.HIBERNATING
        assert snapshot_called
        assert any("hibernate" in a for a in announced)

    @pytest.mark.asyncio
    async def test_transition_recovering_resumes(self):
        announced: list[str] = []

        async def capture(msg: str) -> None:
            announced.append(msg)

        m = ResourceMonitor(announce_fn=capture)
        m.state = MonitorState.HIBERNATING
        await m._transition(MonitorState.RECOVERING)
        assert m.state == MonitorState.HEALTHY
        assert any("Resuming" in a for a in announced)


# ──────────────────────────────────────────────────────────────────
# Status & thresholds API
# ──────────────────────────────────────────────────────────────────


class TestStatusAPI:
    def test_get_status_structure(self):
        m = ResourceMonitor()
        status = m.get_status()
        assert status["state"] == "healthy"
        assert "levels" in status
        assert "thresholds" in status
        assert "stopped_services" in status
        assert isinstance(status["levels"]["ram_pct"], float)

    def test_update_thresholds(self):
        m = ResourceMonitor()
        result = m.update_thresholds(ram_warn=0.75, disk_critical=0.98)
        assert result["ram_warn"] == 0.75
        assert result["disk_critical"] == 0.98
        assert m.thresholds.ram_warn == 0.75

    def test_update_thresholds_ignores_unknown(self):
        m = ResourceMonitor()
        original_warn = m.thresholds.ram_warn
        m.update_thresholds(nonexistent_field=0.99)
        assert m.thresholds.ram_warn == original_warn


# ──────────────────────────────────────────────────────────────────
# Start / stop lifecycle
# ──────────────────────────────────────────────────────────────────


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        m = ResourceMonitor()
        m.check_interval = 600  # won't actually tick
        await m.start()
        assert m._running
        assert m._task is not None
        await m.stop()
        assert not m._running
