"""Eye tracking — polls HA sensors for position data, pushes to avatar.

Module ownership: Avatar eye tracking from presence sensors (mmWave/camera).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


class EyeTracker:
    """Polls a presence sensor for position data and sends eye_target to avatar."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_target: tuple[float, float] | None = None
        # Configurable sensor entity
        self.sensor_entity = os.environ.get("ATLAS_EYE_SENSOR", "")
        self.poll_interval = 0.3  # 300ms — smooth but not overwhelming

    async def start(self) -> None:
        """Start polling the sensor."""
        if not self.sensor_entity:
            logger.debug("No eye tracking sensor configured (set ATLAS_EYE_SENSOR)")
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Eye tracking started: %s", self.sensor_entity)

    async def stop(self) -> None:
        """Stop the poll loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        """Poll sensor and broadcast eye target."""
        while self._running:
            try:
                target = await self._get_sensor_position()
                if target != self._last_target:
                    self._last_target = target
                    await self._broadcast_target(target)
            except Exception as e:
                logger.debug("Eye tracking poll error: %s", e)
            await asyncio.sleep(self.poll_interval)

    async def _get_sensor_position(self) -> tuple[float, float] | None:
        """Query HA for sensor position. Returns (x, y) normalised to -1..1 or None."""
        try:
            from cortex.integrations.ha.client import HAClient

            base_url = os.environ.get("HA_URL", "")
            token = os.environ.get("HA_TOKEN", "")
            if not base_url or not token:
                return None

            async with HAClient(base_url, token, timeout=5.0) as client:
                states = await client.get_states()

            state = _find_entity(states, self.sensor_entity)
            if state is None:
                return None

            attrs = state.get("attributes", {})
            return _parse_position(attrs)
        except Exception:
            return None

    async def _broadcast_target(self, target: tuple[float, float] | None) -> None:
        """Send eye_target to all connected avatar WebSocket clients."""
        try:
            from cortex.avatar.broadcast import broadcast_to_avatars

            if target is None:
                await broadcast_to_avatars(
                    {"type": "eye_target", "x": 0, "y": 0, "tracking": False}
                )
            else:
                x, y = _clamp(target[0]), _clamp(target[1])
                await broadcast_to_avatars(
                    {"type": "eye_target", "x": x, "y": y, "tracking": True}
                )
        except Exception as e:
            logger.debug("Eye target broadcast failed: %s", e)

    async def set_target_manual(self, x: float, y: float) -> None:
        """Set eye target manually (for testing or non-HA sources)."""
        await self._broadcast_target((x, y))


# ── Helpers ──────────────────────────────────────────────────────


def _clamp(v: float) -> float:
    """Clamp value to -1..1."""
    return max(-1.0, min(1.0, v))


def _find_entity(states: list[dict[str, Any]], entity_id: str) -> dict[str, Any] | None:
    """Find a single entity dict from the full states list."""
    for s in states:
        if s.get("entity_id") == entity_id:
            return s
    return None


def _parse_position(attrs: dict[str, Any]) -> tuple[float, float] | None:
    """Extract (x, y) normalised to -1..1 from sensor attributes.

    Supports:
      1. Coordinate-based: x, y  (already -1..1)
      2. Zone-based (Aqara FP2): zone_x, zone_y (grid coords)
      3. Angle-based: angle (degrees from centre)
    """
    if "x" in attrs and "y" in attrs:
        return (float(attrs["x"]), float(attrs["y"]))

    if "zone_x" in attrs and "zone_y" in attrs:
        zx = float(attrs["zone_x"])
        zy = float(attrs["zone_y"])
        max_zone = float(attrs.get("max_zone", 4))
        return (_clamp((zx / max_zone) * 2 - 1), _clamp((zy / max_zone) * 2 - 1))

    if "angle" in attrs:
        angle_rad = math.radians(float(attrs["angle"]))
        return (_clamp(math.sin(angle_rad)), 0.0)

    return None


# ── Singleton ────────────────────────────────────────────────────

_tracker: EyeTracker | None = None


def get_eye_tracker() -> EyeTracker:
    """Return the global EyeTracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = EyeTracker()
    return _tracker
