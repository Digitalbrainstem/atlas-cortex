"""HA device bootstrap — fetches devices from HA, populates DB, generates patterns (Phase I2.1)."""

from __future__ import annotations

import logging
import re
from typing import Any

from .client import HAClient

logger = logging.getLogger(__name__)

# Domains we care about
_SUPPORTED_DOMAINS = {
    "light", "switch", "climate", "lock", "cover", "fan",
    "media_player", "sensor", "binary_sensor", "scene",
    "automation", "input_boolean",
}

# Pattern templates keyed by domain
_DOMAIN_PATTERN_TEMPLATES: dict[str, list[tuple[str, str, str]]] = {
    "light": [
        (
            r"(?i)turn (on|off) (?:the )?{name}(?:\s+lights?)?$",
            "toggle",
            "Done — {name} turned {value}.",
        ),
        (
            r"(?i)(?:set|dim|brighten) (?:the )?{name}(?:\s+lights?)? to (\d+)\s*%?$",
            "set_brightness",
            "{name} set to {value}%.",
        ),
    ],
    "switch": [
        (
            r"(?i)turn (on|off) (?:the )?{name}(?:\s+switch)?$",
            "toggle",
            "Done — {name} turned {value}.",
        ),
    ],
    "climate": [
        (
            r"(?i)set (?:the )?{name}(?: thermostat)? to (\d+)\s*(?:degrees?|°)?$",
            "set_temperature",
            "{name} set to {value}°.",
        ),
    ],
    "lock": [
        (
            r"(?i)(lock|unlock) (?:the )?{name}(?:\s+(?:door|lock))?$",
            "lock",
            "{name} {value}ed.",
        ),
    ],
    "cover": [
        (
            r"(?i)(open|close) (?:the )?{name}(?:\s+(?:door|blind|curtain|shade))?$",
            "cover",
            "{name} {value}d.",
        ),
    ],
    "fan": [
        (
            r"(?i)turn (on|off) (?:the )?{name}(?:\s+fan)?$",
            "toggle",
            "{name} fan turned {value}.",
        ),
    ],
    "media_player": [
        (
            r"(?i)(pause|play|stop|next|skip) (?:the )?{name}$",
            "media_control",
            "Media {value} on {name}.",
        ),
    ],
    "scene": [
        (
            r"(?i)(?:activate|run|trigger) (?:the )?{name}(?:\s+scene)?$",
            "activate_scene",
            "Activated {name}.",
        ),
    ],
    "input_boolean": [
        (
            r"(?i)turn (on|off) (?:the )?{name}$",
            "toggle",
            "Done — {name} turned {value}.",
        ),
    ],
    "automation": [
        (
            r"(?i)(?:run|trigger|start) (?:the )?{name}(?:\s+automation)?$",
            "activate_scene",
            "Triggered {name}.",
        ),
    ],
}


def _make_alias(friendly_name: str) -> str:
    """Convert a friendly name to a lowercase_underscore alias."""
    return re.sub(r"\s+", "_", friendly_name.strip().lower())


class HABootstrap:
    """Bootstrap HA devices into the local DB and generate command patterns."""

    def __init__(self, client: HAClient, conn: Any) -> None:
        self._client = client
        self._conn = conn

    async def sync_devices(self) -> dict[str, int]:
        """Fetch states + areas from HA, upsert into ha_devices + device_aliases.

        Returns a dict with keys ``added``, ``updated``, ``removed``.
        """
        states = await self._client.get_states()
        areas_list = await self._client.get_areas()

        # Build area lookup: area_id → area_name (not used for filtering, just stored)
        area_by_id: dict[str, str] = {}
        for area in areas_list:
            aid = area.get("area_id") or area.get("id")
            if aid:
                area_by_id[aid] = area.get("name", aid)

        added = updated = 0
        seen: set[str] = set()

        for state in states:
            entity_id: str = state.get("entity_id", "")
            domain = entity_id.split(".")[0] if "." in entity_id else ""
            if domain not in _SUPPORTED_DOMAINS:
                continue

            attrs: dict = state.get("attributes", {})
            friendly_name: str = attrs.get("friendly_name") or entity_id
            area_id: str | None = attrs.get("area_id") or state.get("area_id")
            current_state: str = state.get("state", "unknown")

            seen.add(entity_id)

            existing = self._conn.execute(
                "SELECT entity_id FROM ha_devices WHERE entity_id = ?", (entity_id,)
            ).fetchone()

            if existing:
                self._conn.execute(
                    """UPDATE ha_devices
                       SET friendly_name=?, domain=?, area_id=?, state=?,
                           last_seen=CURRENT_TIMESTAMP
                       WHERE entity_id=?""",
                    (friendly_name, domain, area_id, current_state, entity_id),
                )
                updated += 1
            else:
                self._conn.execute(
                    """INSERT INTO ha_devices
                       (entity_id, friendly_name, domain, area_id, state)
                       VALUES (?, ?, ?, ?, ?)""",
                    (entity_id, friendly_name, domain, area_id, current_state),
                )
                added += 1

            # Upsert alias
            alias = _make_alias(friendly_name)
            self._conn.execute(
                "DELETE FROM device_aliases WHERE entity_id=? AND source='nightly'",
                (entity_id,),
            )
            self._conn.execute(
                "INSERT INTO device_aliases (entity_id, alias, source) VALUES (?, ?, 'nightly')",
                (entity_id, alias),
            )

        # Mark devices no longer in HA as removed (delete them)
        all_db = self._conn.execute(
            "SELECT entity_id FROM ha_devices"
        ).fetchall()
        removed = 0
        for row in all_db:
            if row[0] not in seen:
                self._conn.execute(
                    "DELETE FROM ha_devices WHERE entity_id=?", (row[0],)
                )
                removed += 1

        self._conn.commit()
        logger.info("sync_devices: added=%d updated=%d removed=%d", added, updated, removed)
        return {"added": added, "updated": updated, "removed": removed}

    def generate_patterns(self, domains: list[str] | None = None) -> int:
        """Generate command_patterns for discovered devices.

        Args:
            domains: Limit to these domains; if None, all supported domains.

        Returns the number of patterns inserted.
        """
        target_domains = set(domains) if domains else _SUPPORTED_DOMAINS
        devices = self._conn.execute(
            "SELECT entity_id, friendly_name, domain FROM ha_devices"
        ).fetchall()

        count = 0
        for row in devices:
            entity_id, friendly_name, domain = row[0], row[1], row[2]
            if domain not in target_domains:
                continue
            templates = _DOMAIN_PATTERN_TEMPLATES.get(domain, [])
            # Escape regex metacharacters in the device name (pattern uses (?i) for case-insensitivity)
            name_escaped = re.escape(friendly_name)
            for tmpl_pattern, intent, response_tmpl in templates:
                pattern = tmpl_pattern.replace("{name}", name_escaped)
                # Skip if identical pattern already exists
                exists = self._conn.execute(
                    "SELECT id FROM command_patterns WHERE pattern=?", (pattern,)
                ).fetchone()
                if exists:
                    continue
                self._conn.execute(
                    """INSERT INTO command_patterns
                       (pattern, intent, entity_domain, response_template, source, confidence)
                       VALUES (?, ?, ?, ?, 'discovered', 0.85)""",
                    (pattern, intent, domain, response_tmpl),
                )
                count += 1

        self._conn.commit()
        logger.info("generate_patterns: inserted %d patterns", count)
        return count

    async def full_bootstrap(self) -> dict:
        """Run sync_devices then generate_patterns; return combined stats."""
        sync_stats = await self.sync_devices()
        patterns_generated = self.generate_patterns()
        return {**sync_stats, "patterns_generated": patterns_generated}
