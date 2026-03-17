"""Action executors for routine steps."""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Outcome of a single action execution."""

    success: bool
    message: str = ""
    variables: dict[str, Any] = field(default_factory=dict)


class ActionExecutor(abc.ABC):
    """Abstract base class for routine action executors."""

    @abc.abstractmethod
    async def execute(self, config: dict, context: dict) -> ActionResult:
        """Execute the action with the given config and runtime context."""
        raise NotImplementedError


class TTSAnnounceAction(ActionExecutor):
    """Speak a message via TTS to a room/satellite."""

    async def execute(self, config: dict, context: dict) -> ActionResult:
        message = config.get("message", "")
        if not message:
            return ActionResult(success=False, message="No message to announce")

        room = config.get("room", context.get("room", ""))

        try:
            from cortex.avatar.broadcast import stream_tts_to_avatar
            await stream_tts_to_avatar(room, message)
            logger.info("TTS announce in room=%s: %s", room, message[:80])
            return ActionResult(success=True, message=f"Announced: {message}")
        except Exception as exc:
            logger.warning("TTS announce failed: %s", exc)
            return ActionResult(success=True, message=f"TTS unavailable, would say: {message}")


class HAServiceAction(ActionExecutor):
    """Call a Home Assistant service."""

    async def execute(self, config: dict, context: dict) -> ActionResult:
        domain = config.get("domain", "")
        service = config.get("service", "")
        entity_id = config.get("entity_id", "")
        data = config.get("data", {})

        if not domain or not service:
            return ActionResult(success=False, message="Missing domain or service")

        if entity_id:
            data["entity_id"] = entity_id

        try:
            from cortex.integrations.ha.client import HAClient
            import os
            ha_url = os.environ.get("HA_URL", "")
            ha_token = os.environ.get("HA_TOKEN", "")
            if not ha_url or not ha_token:
                return ActionResult(success=False, message="Home Assistant not configured")

            async with HAClient(ha_url, ha_token) as client:
                await client.call_service(domain, service, data)
            logger.info("HA service %s.%s called", domain, service)
            return ActionResult(success=True, message=f"Called {domain}.{service}")
        except Exception as exc:
            logger.warning("HA service call failed: %s", exc)
            return ActionResult(success=False, message=f"HA call failed: {exc}")


class DelayAction(ActionExecutor):
    """Wait for a specified duration."""

    async def execute(self, config: dict, context: dict) -> ActionResult:
        seconds = config.get("seconds", 0)
        if seconds <= 0:
            return ActionResult(success=True, message="No delay")
        await asyncio.sleep(seconds)
        return ActionResult(success=True, message=f"Waited {seconds}s")


class ConditionAction(ActionExecutor):
    """Evaluate a condition; returns success=False to signal skip remaining steps."""

    async def execute(self, config: dict, context: dict) -> ActionResult:
        condition_type = config.get("type", "")

        if condition_type == "time_between":
            return self._check_time_between(config)
        elif condition_type == "ha_state":
            return await self._check_ha_state(config)
        else:
            return ActionResult(success=False, message=f"Unknown condition type: {condition_type}")

    def _check_time_between(self, config: dict) -> ActionResult:
        start = config.get("start", "00:00")
        end = config.get("end", "23:59")
        now = datetime.now().strftime("%H:%M")

        if start <= end:
            in_range = start <= now <= end
        else:
            # Wraps midnight (e.g. 22:00 - 06:00)
            in_range = now >= start or now <= end

        if in_range:
            return ActionResult(success=True, message=f"Time {now} is between {start} and {end}")
        return ActionResult(success=False, message=f"Time {now} is NOT between {start} and {end}")

    async def _check_ha_state(self, config: dict) -> ActionResult:
        entity_id = config.get("entity_id", "")
        expected = config.get("state", "")

        if not entity_id or not expected:
            return ActionResult(success=False, message="Missing entity_id or state for ha_state condition")

        try:
            from cortex.integrations.ha.client import HAClient
            import os
            ha_url = os.environ.get("HA_URL", "")
            ha_token = os.environ.get("HA_TOKEN", "")
            if not ha_url or not ha_token:
                return ActionResult(success=False, message="HA not configured")

            async with HAClient(ha_url, ha_token) as client:
                states = await client.get_states()
                for state in states:
                    if state.get("entity_id") == entity_id:
                        actual = state.get("state", "")
                        if actual == expected:
                            return ActionResult(success=True, message=f"{entity_id} is {expected}")
                        return ActionResult(
                            success=False,
                            message=f"{entity_id} is {actual}, expected {expected}",
                        )
            return ActionResult(success=False, message=f"{entity_id} not found")
        except Exception as exc:
            logger.warning("HA state check failed: %s", exc)
            return ActionResult(success=False, message=f"HA state check failed: {exc}")


class SetVariableAction(ActionExecutor):
    """Set a variable in the routine context for use in subsequent steps."""

    async def execute(self, config: dict, context: dict) -> ActionResult:
        name = config.get("name", "")
        value = config.get("value", "")
        if not name:
            return ActionResult(success=False, message="No variable name specified")
        return ActionResult(success=True, message=f"Set {name}={value}", variables={name: value})


# ── Action registry ──────────────────────────────────────────────

ACTION_EXECUTORS: dict[str, type[ActionExecutor]] = {
    "tts_announce": TTSAnnounceAction,
    "ha_service": HAServiceAction,
    "delay": DelayAction,
    "condition": ConditionAction,
    "set_variable": SetVariableAction,
}


def get_executor(action_type: str) -> ActionExecutor | None:
    """Return an executor instance for the given action type, or None."""
    cls = ACTION_EXECUTORS.get(action_type)
    if cls is None:
        return None
    return cls()
