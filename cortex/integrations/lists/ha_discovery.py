"""Home Assistant to-do list discovery — auto-register HA lists in Atlas (Phase I6.2)."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class HAListDiscovery:
    """Discover Home Assistant to-do list entities and register in Atlas."""

    def __init__(self, ha_client: Any, list_registry: Any) -> None:
        self.ha_client = ha_client
        self.list_registry = list_registry

    async def discover(self, conn: sqlite3.Connection) -> dict:
        """Discover HA to-do entities and create corresponding Atlas lists.

        Uses the HA REST API to find entities in the ``todo`` domain.
        Creates ``list_registry`` entries with ``backend='ha_todo'``.

        Returns: ``{discovered, new, existing}``
        """
        stats = {"discovered": 0, "new": 0, "existing": 0}

        try:
            states = await self.ha_client.get_states()
        except Exception as exc:
            logger.error("Failed to fetch HA states for list discovery: %s", exc)
            return stats

        todo_entities = [
            s for s in states if s.get("entity_id", "").startswith("todo.")
        ]
        stats["discovered"] = len(todo_entities)

        for entity in todo_entities:
            entity_id = entity["entity_id"]
            friendly_name = entity.get("attributes", {}).get(
                "friendly_name", entity_id
            )

            # Check if already registered
            existing = conn.execute(
                "SELECT id FROM list_registry WHERE backend = 'ha_todo' "
                "AND backend_config LIKE ?",
                (f'%"{entity_id}"%',),
            ).fetchone()

            if existing:
                stats["existing"] += 1
                continue

            # Create a new list entry
            list_id = str(uuid.uuid4())
            backend_config = json.dumps({"entity_id": entity_id})

            conn.execute(
                "INSERT INTO list_registry "
                "(id, display_name, backend, backend_config, owner_id, access_level, category) "
                "VALUES (?, ?, 'ha_todo', ?, 'system', 'household', 'home-assistant')",
                (list_id, friendly_name, backend_config),
            )

            # Add alias matching the entity_id suffix
            alias = entity_id.replace("todo.", "").replace("_", " ")
            conn.execute(
                "INSERT OR IGNORE INTO list_aliases (list_id, alias) VALUES (?, ?)",
                (list_id, alias),
            )

            stats["new"] += 1

        conn.commit()
        return stats

    async def sync_items(
        self, conn: sqlite3.Connection, entity_id: str
    ) -> dict:
        """Sync items from a HA to-do entity to the Atlas list.

        Returns: ``{added, removed, unchanged}``
        """
        stats = {"added": 0, "removed": 0, "unchanged": 0}

        # Find the Atlas list for this entity
        row = conn.execute(
            "SELECT id FROM list_registry WHERE backend = 'ha_todo' "
            "AND backend_config LIKE ?",
            (f'%"{entity_id}"%',),
        ).fetchone()

        if not row:
            logger.warning("No Atlas list found for HA entity %s", entity_id)
            return stats

        list_id = row["id"]

        # Fetch items from HA
        try:
            result = await self.ha_client.call_service(
                "todo", "get_items", {"entity_id": entity_id}
            )
        except Exception as exc:
            logger.error("Failed to fetch to-do items from HA: %s", exc)
            return stats

        # Parse HA response — items are in result[entity_id]["items"]
        ha_items_raw = result.get(entity_id, result) if isinstance(result, dict) else {}
        if isinstance(ha_items_raw, dict):
            ha_items = ha_items_raw.get("items", [])
        elif isinstance(ha_items_raw, list):
            ha_items = ha_items_raw
        else:
            ha_items = []

        # Build a set of HA item texts (lowered for comparison)
        ha_item_map: dict[str, dict] = {}
        for item in ha_items:
            summary = item.get("summary", item.get("content", ""))
            if summary:
                ha_item_map[summary.lower()] = item

        # Get current Atlas items
        atlas_items = conn.execute(
            "SELECT id, content, done FROM list_items WHERE list_id = ?",
            (list_id,),
        ).fetchall()
        atlas_map = {r["content"].lower(): dict(r) for r in atlas_items}

        # Add new items from HA
        for key, ha_item in ha_item_map.items():
            if key not in atlas_map:
                item_id = str(uuid.uuid4())
                content = ha_item.get("summary", ha_item.get("content", ""))
                done = ha_item.get("status", "") == "completed"
                conn.execute(
                    "INSERT INTO list_items (id, list_id, content, done, added_by) "
                    "VALUES (?, ?, ?, ?, 'ha_sync')",
                    (item_id, list_id, content, done),
                )
                stats["added"] += 1
            else:
                stats["unchanged"] += 1

        # Remove Atlas items not in HA
        for key, atlas_item in atlas_map.items():
            if key not in ha_item_map:
                conn.execute(
                    "DELETE FROM list_items WHERE id = ?", (atlas_item["id"],)
                )
                stats["removed"] += 1

        conn.commit()
        return stats
