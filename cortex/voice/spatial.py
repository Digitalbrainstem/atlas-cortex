"""Satellite Room Registry & Spatial Engine (Phase I3.2).

Resolves which room/area a user is in by combining satellite placement,
presence-sensor state, and speaker history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class SpatialEngine:
    """Resolves which room a user is in from available signals.

    Parameters
    ----------
    conn:
        A :class:`sqlite3.Connection` pointing at the Atlas Cortex DB
        (tables ``satellite_rooms``, ``presence_sensors``, ``room_context_log``
        must already exist).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------ #
    # Satellite management
    # ------------------------------------------------------------------ #

    def register_satellite(
        self,
        satellite_id: str,
        area_id: str,
        area_name: str,
        floor: str | None = None,
    ) -> None:
        """Register or update a satellite's room mapping."""
        self.conn.execute(
            """INSERT INTO satellite_rooms (satellite_id, area_id, area_name, floor)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(satellite_id) DO UPDATE
               SET area_id = excluded.area_id,
                   area_name = excluded.area_name,
                   floor = excluded.floor""",
            (satellite_id, area_id, area_name, floor),
        )
        self.conn.commit()
        logger.info("Registered satellite %s → %s (%s)", satellite_id, area_id, area_name)

    def unregister_satellite(self, satellite_id: str) -> None:
        """Remove a satellite from the registry."""
        self.conn.execute(
            "DELETE FROM satellite_rooms WHERE satellite_id = ?", (satellite_id,)
        )
        self.conn.commit()

    def list_satellites(self) -> list[dict]:
        """Return all registered satellites as dicts."""
        rows = self.conn.execute(
            "SELECT satellite_id, area_id, area_name, floor FROM satellite_rooms"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Presence-sensor management
    # ------------------------------------------------------------------ #

    def register_presence_sensor(
        self,
        entity_id: str,
        area_id: str,
        sensor_type: str = "motion",
        priority: int = 1,
        indicates_presence_when: str = "on",
    ) -> None:
        """Register or update a presence sensor for an area."""
        self.conn.execute(
            """INSERT INTO presence_sensors
               (entity_id, area_id, sensor_type, priority, indicates_presence_when)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE
               SET area_id = excluded.area_id,
                   sensor_type = excluded.sensor_type,
                   priority = excluded.priority,
                   indicates_presence_when = excluded.indicates_presence_when""",
            (entity_id, area_id, sensor_type, priority, indicates_presence_when),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Room resolution
    # ------------------------------------------------------------------ #

    async def resolve_room(
        self,
        satellite_id: str | None = None,
        speaker_id: str | None = None,
        ha_client: Any = None,
    ) -> dict:
        """Resolve the user's room using available signals.

        Resolution priority:
            1. Satellite area (if *satellite_id* is registered)
            2. Presence sensor signals (query HA for live states)
            3. Speaker's last-known room (from ``room_context_log``)
            4. Unknown

        Returns
        -------
        dict
            ``{area_id, area_name, confidence, method}``
        """
        # 1. Satellite lookup
        if satellite_id:
            row = self.conn.execute(
                "SELECT area_id, area_name FROM satellite_rooms WHERE satellite_id = ?",
                (satellite_id,),
            ).fetchone()
            if row:
                return {
                    "area_id": row["area_id"],
                    "area_name": row["area_name"],
                    "confidence": 0.95,
                    "method": "satellite",
                }

        # 2. Presence sensors
        if ha_client is not None:
            result = await self._resolve_by_presence(ha_client)
            if result:
                return result

        # 3. Speaker history
        if speaker_id:
            row = self.conn.execute(
                """SELECT resolved_area, satellite_area
                   FROM room_context_log
                   WHERE speaker_id = ? AND resolved_area IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (speaker_id,),
            ).fetchone()
            if row:
                return {
                    "area_id": row["resolved_area"],
                    "area_name": row["satellite_area"] or row["resolved_area"],
                    "confidence": 0.4,
                    "method": "speaker_history",
                }

        # 4. Unknown
        return {
            "area_id": None,
            "area_name": None,
            "confidence": 0.0,
            "method": "unknown",
        }

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #

    def log_resolution(
        self,
        interaction_id: int | None,
        result: dict,
        satellite_id: str | None = None,
        speaker_id: str | None = None,
        presence_signals: list[dict] | None = None,
    ) -> None:
        """Persist a room-resolution result to ``room_context_log``."""
        self.conn.execute(
            """INSERT INTO room_context_log
               (interaction_id, resolved_area, confidence, satellite_id,
                satellite_area, presence_signals, speaker_id, resolution_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                interaction_id,
                result.get("area_id"),
                result.get("confidence"),
                satellite_id,
                result.get("area_name"),
                json.dumps(presence_signals) if presence_signals else None,
                speaker_id,
                result.get("method"),
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Floor / area helpers
    # ------------------------------------------------------------------ #

    def expand_floor_areas(self, floor: str) -> list[str]:
        """Return all ``area_id`` values on a given floor."""
        rows = self.conn.execute(
            "SELECT DISTINCT area_id FROM satellite_rooms WHERE floor = ?", (floor,)
        ).fetchall()
        return [r["area_id"] for r in rows]

    def expand_all_areas(self) -> list[str]:
        """Return all registered ``area_id`` values."""
        rows = self.conn.execute(
            "SELECT DISTINCT area_id FROM satellite_rooms"
        ).fetchall()
        return [r["area_id"] for r in rows]

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    async def _resolve_by_presence(self, ha_client: Any) -> dict | None:
        """Query HA for presence-sensor states and pick the best area."""
        sensors = self.conn.execute(
            """SELECT entity_id, area_id, sensor_type, priority,
                      indicates_presence_when
               FROM presence_sensors
               ORDER BY priority DESC"""
        ).fetchall()

        if not sensors:
            return None

        # Build set of entity_ids to check
        try:
            states = await ha_client.get_states()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to query HA for presence sensors")
            return None

        state_map = {s["entity_id"]: s for s in states if "entity_id" in s}

        # Find active sensors (highest-priority first, already sorted)
        for sensor in sensors:
            eid = sensor["entity_id"]
            if eid not in state_map:
                continue
            current = state_map[eid].get("state", "")
            expected = sensor["indicates_presence_when"]
            if current == expected:
                # Fetch area_name from satellite_rooms if available
                area_row = self.conn.execute(
                    "SELECT area_name FROM satellite_rooms WHERE area_id = ?",
                    (sensor["area_id"],),
                ).fetchone()
                area_name = area_row["area_name"] if area_row else sensor["area_id"]
                return {
                    "area_id": sensor["area_id"],
                    "area_name": area_name,
                    "confidence": 0.7,
                    "method": "presence",
                }

        return None
