"""Home Assistant Layer 2 plugin (Phase I2.2)."""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from .client import HAClient, HAClientError, HAConnectionError

logger = logging.getLogger(__name__)


class HAPlugin(CortexPlugin):
    """CortexPlugin implementation for Home Assistant commands."""

    plugin_id = "ha_commands"
    display_name = "Home Assistant"
    plugin_type = "action"

    def __init__(self, client: HAClient | None = None, conn: Any = None) -> None:
        self._client: HAClient | None = client
        self._conn: Any = conn
        # Cache: list of (compiled_re, row_dict)
        self._patterns: list[tuple[re.Pattern, dict]] | None = None

    # ------------------------------------------------------------------ #
    # CortexPlugin interface
    # ------------------------------------------------------------------ #

    async def setup(self, config: dict[str, Any]) -> bool:
        """Configure the plugin from a config dict with base_url and token."""
        try:
            base_url: str = config["base_url"]
            token: str = config["token"]
            timeout: float = float(config.get("timeout", 10.0))
            self._client = HAClient(base_url, token, timeout)
            logger.info("HAPlugin configured for %s", base_url)
            return True
        except (KeyError, ValueError) as exc:
            logger.error("HAPlugin.setup failed: %s", exc)
            return False

    async def health(self) -> bool:
        """Return True if HA is reachable; False otherwise."""
        if self._client is None:
            return False
        try:
            return await self._client.health()
        except Exception as exc:
            logger.warning("HAPlugin.health check failed: %s", exc)
            return False

    async def match(self, message: str, context: dict[str, Any]) -> CommandMatch:
        """Match message against cached command_patterns from DB."""
        if self._conn is None:
            return CommandMatch(matched=False)

        if self._patterns is None:
            self._load_patterns()

        room: str | None = context.get("room")

        for compiled, row in (self._patterns or []):
            m = compiled.search(message)
            if not m:
                continue

            entity_match_group: int | None = row["entity_match_group"]
            entity_fragment: str = ""
            if entity_match_group:
                try:
                    entity_fragment = m.group(entity_match_group)
                except IndexError:
                    entity_fragment = ""

            # If room context is set, prefer entities in that area; skip non-matching
            # areas only when we have a clear entity fragment to compare
            if room and entity_fragment:
                area_match = self._entity_in_area(entity_fragment, room)
                confidence = row["confidence"] * (1.1 if area_match else 0.8)
            else:
                confidence = row["confidence"]

            return CommandMatch(
                matched=True,
                intent=row["intent"],
                entities=[entity_fragment] if entity_fragment else [],
                confidence=min(1.0, confidence),
                metadata={
                    "pattern_id": row["id"],
                    "entity_domain": row["entity_domain"],
                    "value_match_group": row["value_match_group"],
                    "response_template": row["response_template"],
                    "regex_match": m,
                },
            )

        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        """Execute the HA command and return a natural-language response."""
        if self._client is None:
            return CommandResult(
                success=False,
                response="Home Assistant is not configured.",
            )

        metadata = match.metadata
        domain: str = metadata.get("entity_domain") or ""
        intent: str = match.intent
        response_template: str = metadata.get("response_template") or ""
        regex_match = metadata.get("regex_match")
        pattern_id: int | None = metadata.get("pattern_id")

        # Resolve the entity_id from the matched name fragment
        entity_fragment = match.entities[0] if match.entities else ""
        entity_id, friendly_name = self._resolve_entity(entity_fragment, domain)

        # Determine service + data from intent
        service_domain, service_name, service_data = self._build_service_call(
            intent, entity_id, regex_match, metadata
        )

        try:
            if service_domain and service_name and entity_id:
                await self._client.call_service(service_domain, service_name, service_data)
        except HAConnectionError:
            return CommandResult(
                success=False,
                response="Home Assistant is not reachable right now.",
            )
        except HAClientError as exc:
            logger.error("HAPlugin.handle service call failed: %s", exc)
            return CommandResult(success=False, response=f"Could not complete that: {exc}")

        # Update hit stats
        if pattern_id and self._conn is not None:
            try:
                self._conn.execute(
                    """UPDATE command_patterns
                       SET hit_count = hit_count + 1, last_hit = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (pattern_id,),
                )
                self._conn.commit()
            except Exception as exc:
                logger.warning("Failed to update pattern hit count: %s", exc)

        # Build response
        value_group: int | None = metadata.get("value_match_group")
        value = ""
        if regex_match and value_group:
            try:
                value = regex_match.group(value_group)
            except IndexError:
                pass

        response = (
            response_template
            .replace("{entity}", friendly_name or entity_fragment)
            .replace("{name}", friendly_name or entity_fragment)
            .replace("{value}", value)
        ) if response_template else f"Done."

        return CommandResult(
            success=True,
            response=response,
            entities_used=[entity_id] if entity_id else [],
        )

    async def discover_entities(self) -> list[dict[str, Any]]:
        """Return all ha_devices rows from the DB."""
        if self._conn is None:
            return []
        rows = self._conn.execute(
            "SELECT entity_id, friendly_name, domain, area_id, area_name, state FROM ha_devices"
        ).fetchall()
        return [
            {
                "entity_id": r[0],
                "friendly_name": r[1],
                "domain": r[2],
                "area_id": r[3],
                "area_name": r[4],
                "state": r[5],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _load_patterns(self) -> None:
        """Load and compile all command_patterns from DB."""
        if self._conn is None:
            self._patterns = []
            return
        rows = self._conn.execute(
            """SELECT id, pattern, intent, entity_domain,
                      entity_match_group, value_match_group,
                      response_template, confidence
               FROM command_patterns
               ORDER BY confidence DESC"""
        ).fetchall()
        result: list[tuple[re.Pattern, dict]] = []
        for row in rows:
            try:
                compiled = re.compile(row[1], re.IGNORECASE)
                result.append((
                    compiled,
                    {
                        "id": row[0],
                        "pattern": row[1],
                        "intent": row[2],
                        "entity_domain": row[3],
                        "entity_match_group": row[4],
                        "value_match_group": row[5],
                        "response_template": row[6],
                        "confidence": row[7] or 1.0,
                    },
                ))
            except re.error as exc:
                logger.warning("Invalid pattern id=%s: %s", row[0], exc)
        self._patterns = result
        logger.debug("HAPlugin loaded %d patterns", len(result))

    def _resolve_entity(
        self, fragment: str, domain: str
    ) -> tuple[str, str]:
        """Look up entity_id and friendly_name for a name fragment."""
        if not fragment or self._conn is None:
            return "", fragment

        fragment_lower = fragment.lower()

        # Try alias exact match
        row = self._conn.execute(
            """SELECT d.entity_id, d.friendly_name
               FROM device_aliases a
               JOIN ha_devices d ON a.entity_id = d.entity_id
               WHERE a.alias = ?""",
            (fragment_lower.replace(" ", "_"),),
        ).fetchone()
        if row:
            return row[0], row[1]

        # Try friendly_name LIKE match, filtered by domain if provided
        if domain:
            row = self._conn.execute(
                """SELECT entity_id, friendly_name FROM ha_devices
                   WHERE domain = ? AND lower(friendly_name) LIKE ?
                   LIMIT 1""",
                (domain, f"%{fragment_lower}%"),
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT entity_id, friendly_name FROM ha_devices
                   WHERE lower(friendly_name) LIKE ?
                   LIMIT 1""",
                (f"%{fragment_lower}%",),
            ).fetchone()

        if row:
            return row[0], row[1]
        return "", fragment

    def _entity_in_area(self, entity_fragment: str, room: str) -> bool:
        """Return True if a device matching fragment is in the given area (by name)."""
        if self._conn is None:
            return False
        row = self._conn.execute(
            """SELECT d.area_name FROM ha_devices d
               WHERE lower(d.friendly_name) LIKE ?
               LIMIT 1""",
            (f"%{entity_fragment.lower()}%",),
        ).fetchone()
        if not row or not row[0]:
            return False
        return room.lower() in row[0].lower()

    def _build_service_call(
        self,
        intent: str,
        entity_id: str,
        regex_match: re.Match | None,
        metadata: dict,
    ) -> tuple[str, str, dict]:
        """Map intent + regex groups to (domain, service, data)."""
        domain: str = metadata.get("entity_domain") or ""
        data: dict = {}
        if entity_id:
            data["entity_id"] = entity_id

        if intent == "toggle":
            value_group: int | None = metadata.get("value_match_group")
            # For toggle the on/off is usually group 1
            state = ""
            if regex_match:
                try:
                    state = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    pass
            service = "turn_on" if state == "on" else "turn_off"
            return domain, service, data

        if intent == "set_brightness":
            value_group = metadata.get("value_match_group")
            brightness_pct = 0
            if regex_match and value_group:
                try:
                    brightness_pct = int(regex_match.group(value_group))
                except (IndexError, ValueError):
                    pass
            data["brightness_pct"] = brightness_pct
            return domain, "turn_on", data

        if intent == "set_temperature":
            value_group = metadata.get("value_match_group")
            temp = 0
            if regex_match and value_group:
                try:
                    temp = float(regex_match.group(value_group))
                except (IndexError, ValueError):
                    pass
            data["temperature"] = temp
            return domain, "set_temperature", data

        if intent == "lock":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    action = "lock"
            else:
                action = "lock"
            return domain, action, data

        if intent == "cover":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    action = "open_cover"
                service = "open_cover" if action == "open" else "close_cover"
            else:
                service = "open_cover"
            return domain, service, data

        if intent == "media_control":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    action = "media_play"
                _media_map = {
                    "play": "media_play",
                    "pause": "media_pause",
                    "stop": "media_stop",
                    "next": "media_next_track",
                    "previous": "media_previous_track",
                    "skip": "media_next_track",
                }
                service = _media_map.get(action, "media_play")
            else:
                service = "media_play"
            return domain, service, data

        if intent == "activate_scene":
            return domain, "turn_on", data

        if intent == "get_state":
            return "", "", {}

        # Fallback
        return domain, "turn_on", data
