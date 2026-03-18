"""Zone management for satellite broadcasting groups.

Zones group satellites so broadcasts can target logical areas
(e.g. "upstairs", "bedrooms") rather than individual rooms.
"""

from __future__ import annotations

import json
import logging

from cortex.db import get_db

logger = logging.getLogger(__name__)


class ZoneManager:
    """CRUD operations for satellite zones."""

    async def create_zone(
        self, name: str, satellite_ids: list[str], description: str = ""
    ) -> int:
        """Create a new zone. Returns the zone id."""
        db = get_db()
        cur = db.execute(
            "INSERT INTO satellite_zones (name, satellite_ids, description) "
            "VALUES (?, ?, ?)",
            (name, json.dumps(satellite_ids), description),
        )
        db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_zone(self, zone_id: int) -> bool:
        """Delete a zone by id. Returns True if a row was removed."""
        db = get_db()
        cur = db.execute("DELETE FROM satellite_zones WHERE id = ?", (zone_id,))
        db.commit()
        return cur.rowcount > 0

    async def update_zone(
        self,
        zone_id: int,
        name: str = "",
        satellite_ids: list[str] | None = None,
        description: str = "",
    ) -> bool:
        """Update zone fields. Only non-empty values are changed."""
        db = get_db()
        parts: list[str] = []
        params: list[object] = []
        if name:
            parts.append("name = ?")
            params.append(name)
        if satellite_ids is not None:
            parts.append("satellite_ids = ?")
            params.append(json.dumps(satellite_ids))
        if description:
            parts.append("description = ?")
            params.append(description)
        if not parts:
            return False
        params.append(zone_id)
        cur = db.execute(
            f"UPDATE satellite_zones SET {', '.join(parts)} WHERE id = ?",
            params,
        )
        db.commit()
        return cur.rowcount > 0

    async def list_zones(self) -> list[dict]:
        """Return all zones."""
        db = get_db()
        rows = db.execute(
            "SELECT id, name, description, satellite_ids, created_at "
            "FROM satellite_zones ORDER BY name"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "satellite_ids": json.loads(r["satellite_ids"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def get_zone(self, name: str) -> dict | None:
        """Get a zone by name."""
        db = get_db()
        r = db.execute(
            "SELECT id, name, description, satellite_ids, created_at "
            "FROM satellite_zones WHERE name = ?",
            (name,),
        ).fetchone()
        if r is None:
            return None
        return {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "satellite_ids": json.loads(r["satellite_ids"]),
            "created_at": r["created_at"],
        }

    async def get_satellites_for_zone(self, zone_name: str) -> list[str]:
        """Return satellite IDs belonging to *zone_name*."""
        zone = await self.get_zone(zone_name)
        if zone is None:
            return []
        return zone["satellite_ids"]
