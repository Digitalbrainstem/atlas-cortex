"""Intercom & broadcasting Layer 2 plugin.

Matches natural-language commands for announce, broadcast, zone
broadcast, two-way calls, drop-in, and hang-up.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Pattern definitions ──────────────────────────────────────────

_ANNOUNCE_RE = re.compile(
    r"(?:tell\s+the\s+|announce\s+(?:in\s+)?(?:the\s+)?|say\s+.+?\s+in\s+(?:the\s+)?)"
    r"(\w[\w\s]*?)(?:\s+(?:that|to)\s+(.+)|$)",
    re.IGNORECASE,
)
_ANNOUNCE_SAY_RE = re.compile(
    r"say\s+[\"']?(.+?)[\"']?\s+in\s+(?:the\s+)?(\w[\w\s]*?)$",
    re.IGNORECASE,
)

_BROADCAST_RE = re.compile(
    r"(?:announce\s+everywhere|broadcast|tell\s+everyone|announce\s+to\s+(?:all|everyone))"
    r"(?:\s+(?:that\s+)?(.+))?",
    re.IGNORECASE,
)

_ZONE_RE = re.compile(
    r"(?:announce|tell|broadcast)\s+(?:to\s+)?(?:the\s+)?(\w[\w\s]*?)\s+(?:zone|area)"
    r"(?:\s+(?:that\s+)?(.+))?",
    re.IGNORECASE,
)

_CALL_RE = re.compile(
    r"(?:call|intercom\s+(?:to\s+)?)\s*(?:the\s+)?(\w[\w\s]*?)$",
    re.IGNORECASE,
)

_DROP_IN_RE = re.compile(
    r"(?:listen\s+to|check\s+on|drop\s*in\s+(?:on\s+)?(?:the\s+)?)\s*(?:the\s+)?(\w[\w\s]*?)$",
    re.IGNORECASE,
)

_END_RE = re.compile(
    r"(?:hang\s*up|end\s+(?:the\s+)?call|stop\s+listening|end\s+(?:the\s+)?intercom)",
    re.IGNORECASE,
)


class IntercomPlugin(CortexPlugin):
    """Intercom & broadcasting plugin for Layer 2 dispatch."""

    plugin_id = "intercom"
    display_name = "Intercom & Broadcasting"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        self._engine: Any = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            from cortex.intercom.engine import IntercomEngine
            self._engine = IntercomEngine()
        return self._engine

    async def setup(self, config: dict[str, Any]) -> bool:  # noqa: ARG002
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self, message: str, context: dict[str, Any]
    ) -> CommandMatch:
        msg = message.strip()

        if _END_RE.search(msg):
            return CommandMatch(matched=True, intent="intercom_end")

        if _BROADCAST_RE.match(msg):
            m = _BROADCAST_RE.match(msg)
            body = (m.group(1) or "").strip() if m else ""
            return CommandMatch(
                matched=True, intent="intercom_broadcast",
                metadata={"message": body},
            )

        if _ZONE_RE.match(msg):
            m = _ZONE_RE.match(msg)
            zone = (m.group(1) or "").strip() if m else ""
            body = (m.group(2) or "").strip() if m else ""
            return CommandMatch(
                matched=True, intent="intercom_zone",
                metadata={"zone": zone, "message": body},
            )

        if _DROP_IN_RE.search(msg):
            m = _DROP_IN_RE.search(msg)
            room = (m.group(1) or "").strip() if m else ""
            return CommandMatch(
                matched=True, intent="intercom_drop_in",
                metadata={"room": room},
            )

        if _CALL_RE.search(msg):
            m = _CALL_RE.search(msg)
            room = (m.group(1) or "").strip() if m else ""
            return CommandMatch(
                matched=True, intent="intercom_call",
                metadata={"room": room},
            )

        m = _ANNOUNCE_SAY_RE.match(msg)
        if m:
            body = m.group(1).strip()
            room = m.group(2).strip()
            return CommandMatch(
                matched=True, intent="intercom_announce",
                metadata={"room": room, "message": body},
            )

        m = _ANNOUNCE_RE.match(msg)
        if m:
            room = m.group(1).strip()
            body = (m.group(2) or "").strip()
            return CommandMatch(
                matched=True, intent="intercom_announce",
                metadata={"room": room, "message": body},
            )

        return CommandMatch(matched=False)

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any]
    ) -> CommandResult:
        engine = self._get_engine()
        intent = match.intent
        meta = match.metadata
        user_id = context.get("user_id", "")
        source_room = context.get("room", "")

        try:
            if intent == "intercom_announce":
                room = meta.get("room", "")
                body = meta.get("message", "") or message
                ok = await engine.announce(body, room, user_id=user_id)
                if ok:
                    return CommandResult(success=True, response=f"Announced in {room}.")
                return CommandResult(
                    success=False,
                    response=f"Could not reach the {room}. No satellite available.",
                )

            if intent == "intercom_broadcast":
                body = meta.get("message", "") or message
                count = await engine.broadcast(body, user_id=user_id)
                return CommandResult(
                    success=True,
                    response=f"Broadcast sent to {count} satellite{'s' if count != 1 else ''}.",
                )

            if intent == "intercom_zone":
                zone = meta.get("zone", "")
                body = meta.get("message", "") or message
                count = await engine.zone_broadcast(body, zone, user_id=user_id)
                return CommandResult(
                    success=True,
                    response=f"Zone broadcast to {zone}: reached {count} satellite{'s' if count != 1 else ''}.",
                )

            if intent == "intercom_call":
                target = meta.get("room", "")
                call_id = await engine.start_call(source_room, target)
                return CommandResult(
                    success=True,
                    response=f"Calling the {target}. Call ID: {call_id}.",
                )

            if intent == "intercom_drop_in":
                target = meta.get("room", "")
                call_id = await engine.start_drop_in(target, source_room)
                return CommandResult(
                    success=True,
                    response=f"Drop-in started on {target}. Call ID: {call_id}.",
                )

            if intent == "intercom_end":
                calls = await engine.get_active_calls()
                if not calls:
                    return CommandResult(
                        success=True, response="No active calls to end."
                    )
                ended = 0
                for c in calls:
                    if await engine.end_call(c["id"]):
                        ended += 1
                return CommandResult(
                    success=True,
                    response=f"Ended {ended} active call{'s' if ended != 1 else ''}.",
                )

        except ValueError as exc:
            return CommandResult(success=False, response=str(exc))
        except Exception:
            logger.exception("Intercom plugin error")
            return CommandResult(
                success=False,
                response="Sorry, the intercom ran into an error.",
            )

        return CommandResult(success=False, response="Unknown intercom command.")
