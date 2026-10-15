"""Home Assistant Layer 2 plugin (Phase I2.2)."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, ConfigField, CortexPlugin
from cortex.plugins.intent import correction_fragment
from .client import HAClient, HAClientError, HAConnectionError
from .commands import CommandContext, HACommand

logger = logging.getLogger(__name__)


class HAPlugin(CortexPlugin):
    """CortexPlugin implementation for Home Assistant commands."""

    plugin_id = "ha_commands"
    display_name = "Home Assistant"
    plugin_type = "action"
    config_fields = [
        ConfigField(
            key="base_url",
            label="Home Assistant URL",
            field_type="url",
            required=True,
            placeholder="http://homeassistant.local:8123",
            help_text="The URL of your Home Assistant instance.",
        ),
        ConfigField(
            key="token",
            label="Long-Lived Access Token",
            field_type="password",
            required=True,
            placeholder="Enter your HA access token...",
            help_text="Create at Home Assistant → Profile → Security → Long-Lived Access Tokens.",
        ),
        ConfigField(
            key="timeout",
            label="Request Timeout",
            field_type="number",
            required=False,
            placeholder="10",
            help_text="HTTP timeout in seconds for HA API calls.",
            default=10.0,
        ),
    ]

    def __init__(self, client: HAClient | None = None, conn: Any = None) -> None:
        self._client: HAClient | None = client
        self._conn: Any = conn
        self._patterns: list[tuple[re.Pattern, dict]] | None = None
        self._configured: bool = client is not None
        self._commands = CommandContext(conn)

    @property
    def health_message(self) -> str:
        if not self._configured:
            return "Not configured — add your Home Assistant URL and token"
        if self._client is None:
            return "Configuration error — check your URL and token"
        return "Connected to Home Assistant"

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
            self._configured = True
            logger.info("HAPlugin configured for %s", base_url)
            return True
        except (KeyError, ValueError) as exc:
            logger.error("HAPlugin.setup failed: %s", exc)
            self._configured = False
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
        try:
            return await self._match(message, context)
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("Could not read HA command context or patterns")
            return CommandMatch(True, metadata={
                "failure": "I couldn't read the device command state. No device action was started.",
            })

    async def _match(self, message: str, context: dict[str, Any]) -> CommandMatch:
        """Match message against cached command_patterns from DB."""
        if self._conn is None:
            return CommandMatch(matched=False)

        previous_outcome: str | None = None
        fragment = correction_fragment(message)
        followup = fragment is not None
        text = fragment if fragment is not None else message
        pronoun = re.fullmatch(
            r"\s*(?:turn|switch)\s+(?:(on|off)\s+(it|them|that)|"
            r"(it|them|that)\s+(on|off))\s*[.!]?\s*",
            text, re.IGNORECASE,
        )
        if (followup or pronoun) and (
            context.get("recent_plugin_id") != self.plugin_id or context.get("recent_command_failed")
        ):
            if pronoun:
                return CommandMatch(True, metadata={
                    "clarification": "Which device do you mean? Please restate the full command.",
                })
            if re.match(
                r"^(?:turn|switch|set|dim|brighten|lock|unlock|open|close|"
                r"activate|run|trigger|play|pause|stop)\b", text, re.IGNORECASE,
            ):
                return await self._match(text, context)
            if context.get("recent_plugin_id") is not None:
                return CommandMatch(False)
        if followup or pronoun:
            recent = (
                self._commands.recent(context)
                if context.get("recent_plugin_id") == self.plugin_id and not context.get("recent_command_failed")
                else None
            )
            if recent is None:
                if followup and re.match(
                    r"^(?:turn|switch|set|dim|brighten|lock|unlock|open|close|"
                    r"activate|run|trigger|play|pause|stop)\b", text, re.IGNORECASE,
                ) and not pronoun:
                    return await self._match(text, context)
                if not pronoun and not self._resolve_entity(
                    re.sub(r"^the\s+", "", text, flags=re.IGNORECASE), "",
                    context.get("room"),
                )[0]:
                    return CommandMatch(False)
                return CommandMatch(True, metadata={
                    "clarification": "Which device and action do you mean? Please name them explicitly.",
                })
            previous, outcome = recent
            previous_outcome = outcome
            if outcome in ("uncertain", "ambiguous"):
                return CommandMatch(True, metadata={
                    "clarification": "The earlier device command has an uncertain or ambiguous outcome. "
                    "Please check it and specify the device and action explicitly.",
                })
            if pronoun:
                if previous.domain not in ("light", "switch", "fan", "input_boolean"):
                    return CommandMatch(True, metadata={
                        "clarification": "Please name the device and action explicitly.",
                    })
                state = pronoun.group(1) or pronoun.group(4)
                command = HACommand(
                    previous.domain, f"turn_{state.lower()}", previous.target,
                )
                return CommandMatch(True, "toggle", metadata={
                    "command": command, "correction": True, "previous_outcome": outcome,
                })
            # A full explicit command after "no"/"actually" is independent parsing,
            # not a target fragment that could accidentally become a device name.
            if not re.match(
                r"^(?:turn|switch|set|dim|brighten|lock|unlock|open|close|"
                r"activate|run|trigger|play|pause|stop)\b", text, re.IGNORECASE,
            ):
                target = re.sub(r"^(?:the\s+)", "", text, flags=re.IGNORECASE)
                target = re.sub(r"\s+instead$", "", target, flags=re.IGNORECASE)
                return CommandMatch(True, "correction", metadata={
                    "command": HACommand(previous.domain, previous.service, target, previous.data),
                    "correction": True, "previous_outcome": outcome, "target_correction": True,
                })
            message = text

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
                    "value_match_group": row["value_match_group"] or (
                        1 if m.lastindex and row["intent"] in (
                            "toggle", "set_brightness", "set_temperature", "lock", "cover", "media_control",
                        ) else None
                    ),
                    "response_template": row["response_template"],
                    "regex_match": m,
                    "correction": bool(followup),
                    "previous_outcome": previous_outcome,
                },
            )

        # Generic syntax also recognizes unknown/ambiguous targets. Device-specific
        # discovered patterns alone cannot ask a clarification for a missing name.
        toggle = re.fullmatch(
            r"\s*(?:turn|switch)\s+(on|off)\s+(?:the\s+)?(.+?)\s*[.!]?\s*",
            message, re.IGNORECASE,
        )
        if toggle:
            target = toggle.group(2)
            domain = "light" if re.search(r"\blights?\b", target, re.IGNORECASE) else ""
            return CommandMatch(True, "toggle", metadata={
                "command": HACommand(domain, f"turn_{toggle.group(1).lower()}", target),
                "correction": bool(followup),
                "previous_outcome": previous_outcome,
            })
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        """Execute the HA command and return a natural-language response."""
        if match.metadata.get("failure"):
            return CommandResult(False, match.metadata["failure"],
                                 metadata={"action_outcome": "none"})
        if match.metadata.get("clarification"):
            return CommandResult(
                False, match.metadata["clarification"], clarification_required=True,
            )
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

        command = metadata.get("command")
        entity_fragment = match.entities[0] if match.entities else ""
        if command is not None:
            domain, entity_fragment = command.domain, command.target
        elif not entity_fragment and pattern_id:
            # Discovered patterns contain literal device names, not entity groups.
            candidates = await self.discover_entities()
            names = [d for d in candidates if d["domain"] == domain
                     and d["friendly_name"].casefold() in message.casefold()]
            if len(names) == 1:
                entity_fragment = names[0]["entity_id"]
        entity_id, friendly_name = self._resolve_entity(entity_fragment, domain, context.get("room"))
        if not entity_id:
            if command is None and entity_fragment:
                service_domain, service_name, service_data = self._build_service_call(
                    intent, "", regex_match, metadata,
                )
                if service_name:
                    command = HACommand(service_domain, service_name, entity_fragment, service_data)
            if command is not None:
                try:
                    self._commands.remember_clarification(command, context)
                except (sqlite3.Error, RuntimeError, ValueError):
                    logger.exception("Could not save device clarification context")
                    return CommandResult(
                        False, "I couldn't save the device clarification state. No action was started.",
                        metadata={"action_outcome": "none"},
                    )
            return CommandResult(
                False, "Which device do you mean? Please use its full name or room.",
                clarification_required=True,
            )
        domain = entity_id.split(".", 1)[0]
        if intent == "toggle" and domain not in (
            "light", "switch", "fan", "input_boolean", "media_player",
        ):
            return CommandResult(
                False, "That device does not support an on/off command.",
                metadata={"action_outcome": "none"},
            )
        if command is None:
            service_domain, service_name, service_data = self._build_service_call(
                intent, entity_id, regex_match, {**metadata, "entity_domain": domain},
            )
            if not service_domain or not service_name:
                if intent == "get_state":
                    row = self._conn.execute(
                        "SELECT state FROM ha_devices WHERE entity_id=?", (entity_id,),
                    ).fetchone()
                    return CommandResult(True, f"{friendly_name} is {row['state']}.",
                                         metadata={"action_outcome": "none"})
                return CommandResult(False, "That device action is not supported.",
                                     metadata={"action_outcome": "none"})
            command = HACommand(service_domain, service_name, entity_id, {
                key: value for key, value in service_data.items() if key != "entity_id"
            })
        else:
            command = HACommand(domain, command.service, entity_id, command.data)
        try:
            request = self._commands.enqueue(
                command, context, correction=metadata.get("correction", False),
            )
        except (sqlite3.Error, RuntimeError, ValueError):
            logger.exception("Could not commit HA command intent; no action started")
            return CommandResult(
                False, "I couldn't save the device command state. No device action was started.",
                metadata={"action_outcome": "none"},
            )

        async def execute() -> CommandResult:
            try:
                await self._client.call_service(command.domain, command.service, {
                    **command.data, "entity_id": entity_id,
                })
            except HAConnectionError:
                logger.warning("HA service outcome is uncertain after a connection failure")
                return CommandResult(
                    False, "Home Assistant is not reachable right now. The command outcome is "
                    "uncertain; please check the device before retrying.",
                    metadata={"action_outcome": "uncertain"},
                )
            except HAClientError as exc:
                logger.error("HAPlugin.handle service call failed: %s", exc)
                return CommandResult(False, f"Could not complete that: {exc}")
            return CommandResult(
                True, f"{friendly_name}: {command.service.replace('_', ' ')}.",
                entities_used=[entity_id], metadata={"action_outcome": "succeeded"},
            )

        try:
            result = await self._commands.execute(request, execute)
        except (sqlite3.Error, RuntimeError, ValueError):
            logger.exception("Could not persist HA command execution state")
            return CommandResult(
                False, "I couldn't save the device command outcome. "
                + ("Please check the device before retrying." if request.started
                   else "No device action was started."),
                metadata={"action_outcome": "uncertain" if request.started else "none"},
            )
        if metadata.get("correction"):
            previous_outcome = (
                request.previous.outcome if request.previous is not None
                else metadata.get("previous_outcome")
            )
            if previous_outcome == "succeeded":
                suffix = "; I did not undo it. " if metadata.get("target_correction") else ". "
                result.response = "The earlier command already completed" + suffix + result.response
            elif previous_outcome == "uncertain":
                result.response = "The earlier command's outcome is uncertain. " + result.response
        if not result.success or request.replaced:
            return result

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
        if not value:
            value = {"turn_on": "on", "turn_off": "off"}.get(command.service, command.service)

        response = (
            response_template
            .replace("{entity}", friendly_name or entity_fragment)
            .replace("{name}", friendly_name or entity_fragment)
            .replace("{value}", value)
        ) if response_template and not metadata.get("correction") else result.response

        return CommandResult(
            success=True,
            response=response,
            entities_used=[entity_id] if entity_id else [],
            metadata={"action_outcome": "succeeded"},
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
               WHERE entity_domain IS NOT NULL AND entity_domain != ''
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
        self, fragment: str, domain: str, room: str | None = None,
    ) -> tuple[str, str]:
        """Look up entity_id and friendly_name for a name fragment."""
        if not fragment or self._conn is None:
            return "", fragment

        fragment_lower = fragment.casefold().strip()
        rows = self._conn.execute(
            "SELECT entity_id, friendly_name, area_name FROM ha_devices "
            "WHERE (? = '' OR domain = ?)", (domain, domain),
        ).fetchall()
        aliases = self._conn.execute(
            "SELECT entity_id FROM device_aliases WHERE lower(alias) = ?",
            (fragment_lower.replace(" ", "_"),),
        ).fetchall()
        alias_ids = {row[0] for row in aliases}
        exact = [row for row in rows if row["entity_id"].casefold() == fragment_lower
                 or row["friendly_name"].casefold() == fragment_lower or row["entity_id"] in alias_ids]
        candidates = exact or [
            row for row in rows if fragment_lower in row["friendly_name"].casefold()
        ]
        if not candidates and domain:
            fragment_lower = re.sub(r"\s+lights?$", "", fragment_lower)
            candidates = [row for row in rows if fragment_lower and (
                fragment_lower in row["friendly_name"].casefold()
                or fragment_lower == (row["area_name"] or "").casefold()
            )]
        if len(candidates) > 1 and room:
            local = [row for row in candidates if (row["area_name"] or "").casefold() == room.casefold()]
            if len(local) == 1:
                candidates = local
        if len(candidates) == 1:
            return candidates[0]["entity_id"], candidates[0]["friendly_name"]
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
            service = {"on": "turn_on", "off": "turn_off"}.get(state, "")
            return domain, service, data

        if intent == "set_brightness":
            value_group = metadata.get("value_match_group")
            brightness_pct = None
            if regex_match and value_group:
                try:
                    brightness_pct = int(regex_match.group(value_group))
                except (IndexError, ValueError):
                    return "", "", {}
            if brightness_pct is None or not 0 <= brightness_pct <= 100:
                return "", "", {}
            data["brightness_pct"] = brightness_pct
            return domain, "turn_on", data

        if intent == "set_temperature":
            value_group = metadata.get("value_match_group")
            temp = None
            if regex_match and value_group:
                try:
                    temp = float(regex_match.group(value_group))
                except (IndexError, ValueError):
                    return "", "", {}
            if temp is None:
                return "", "", {}
            data["temperature"] = temp
            return domain, "set_temperature", data

        if intent == "lock":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    return "", "", {}
            else:
                return "", "", {}
            if action not in ("lock", "unlock"):
                return "", "", {}
            return domain, action, data

        if intent == "cover":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    return "", "", {}
                service = {"open": "open_cover", "close": "close_cover"}.get(action, "")
            else:
                return "", "", {}
            return domain, service, data

        if intent == "media_control":
            if regex_match:
                try:
                    action = regex_match.group(1).lower()
                except (IndexError, AttributeError):
                    return "", "", {}
                _media_map = {
                    "play": "media_play",
                    "pause": "media_pause",
                    "stop": "media_stop",
                    "next": "media_next_track",
                    "previous": "media_previous_track",
                    "skip": "media_next_track",
                }
                service = _media_map.get(action, "")
            else:
                return "", "", {}
            return domain, service, data

        if intent == "activate_scene":
            return domain, "turn_on", data

        if intent == "get_state":
            return "", "", {}

        return "", "", {}
