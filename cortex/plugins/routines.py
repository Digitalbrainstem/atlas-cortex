"""Routine plugin — Layer 2 voice control for routines & automations."""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.routines.engine import RoutineEngine
from cortex.routines.templates import TEMPLATES, instantiate_template

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_RUN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:run|start|do|execute|activate|trigger)\s+(?:the\s+|my\s+)?(.+?)\s+routine\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:run|start|do|execute)\s+(?:the\s+)?routine\s+(.+)",
        re.IGNORECASE,
    ),
]

_CREATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:create|make|add|set\s*up|build)\s+(?:a\s+)?(?:new\s+)?routine\b",
        re.IGNORECASE,
    ),
]

_WHEN_PATTERN = re.compile(
    r"\bwhen\s+I\s+say\s+[\"']?(.+?)[\"']?\s*,\s*(?:then\s+)?(.+)",
    re.IGNORECASE,
)

_LIST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:list|show|what)\s+(?:are\s+)?(?:my\s+)?routines\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+routines\s+do\s+I\s+have\b",
        re.IGNORECASE,
    ),
]

_DELETE_PATTERN = re.compile(
    r"\b(?:delete|remove)\s+(?:the\s+|my\s+)?(.+?)\s+routine\b",
    re.IGNORECASE,
)

_ENABLE_PATTERN = re.compile(
    r"\benable\s+(?:the\s+|my\s+)?(.+?)\s+routine\b",
    re.IGNORECASE,
)

_DISABLE_PATTERN = re.compile(
    r"\bdisable\s+(?:the\s+|my\s+)?(.+?)\s+routine\b",
    re.IGNORECASE,
)

_TEMPLATE_PATTERN = re.compile(
    r"\b(?:set\s*up|create)\s+(?:a\s+)?(?:the\s+)?(.+?)\s+routine\b",
    re.IGNORECASE,
)

# ── HA action parser ─────────────────────────────────────────────

_HA_ACTION_RE = re.compile(
    r"\bturn\s+(on|off)\s+(?:the\s+)?(.+)",
    re.IGNORECASE,
)

_HA_DIM_RE = re.compile(
    r"\bdim\s+(?:the\s+)?(.+?)(?:\s+to\s+(\d+)%?)?\s*$",
    re.IGNORECASE,
)

_TTS_ACTION_RE = re.compile(
    r"\b(?:say|announce|speak)\s+[\"']?(.+?)[\"']?\s*$",
    re.IGNORECASE,
)


def _slugify(name: str) -> str:
    """Convert a human name to an entity-id-like slug."""
    return re.sub(r"\s+", "_", name.strip().lower())


def _parse_action(text: str) -> dict | None:
    """Try to parse a natural-language action into an action config dict."""
    text = text.strip().rstrip(".")

    # Turn on/off
    m = _HA_ACTION_RE.match(text)
    if m:
        on_off = m.group(1).lower()
        entity_name = _slugify(m.group(2))
        return {
            "action_type": "ha_service",
            "action_config": {
                "domain": "light",
                "service": f"turn_{on_off}",
                "entity_id": f"light.{entity_name}",
            },
        }

    # Dim
    m = _HA_DIM_RE.match(text)
    if m:
        entity_name = _slugify(m.group(1))
        brightness = int(m.group(2)) if m.group(2) else 50
        return {
            "action_type": "ha_service",
            "action_config": {
                "domain": "light",
                "service": "turn_on",
                "entity_id": f"light.{entity_name}",
                "data": {"brightness": int(brightness * 255 / 100)},
            },
        }

    # TTS announce
    m = _TTS_ACTION_RE.match(text)
    if m:
        return {
            "action_type": "tts_announce",
            "action_config": {"message": m.group(1)},
        }

    return None


class RoutinePlugin(CortexPlugin):
    """Routines & Automations — voice-driven routine management."""

    plugin_id = "routines"
    display_name = "Routines & Automations"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        super().__init__()
        self._engine = RoutineEngine()

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        msg = message.strip()

        # "when I say X, do Y" — conversational builder
        if _WHEN_PATTERN.search(msg):
            return CommandMatch(matched=True, intent="create_when", confidence=0.95)

        # Run routine
        for pat in _RUN_PATTERNS:
            m = pat.search(msg)
            if m:
                return CommandMatch(
                    matched=True, intent="run",
                    confidence=0.90,
                    metadata={"routine_name": m.group(1).strip()},
                )

        # Create
        for pat in _CREATE_PATTERNS:
            if pat.search(msg):
                return CommandMatch(matched=True, intent="create", confidence=0.85)

        # List
        for pat in _LIST_PATTERNS:
            if pat.search(msg):
                return CommandMatch(matched=True, intent="list", confidence=0.90)

        # Delete
        m = _DELETE_PATTERN.search(msg)
        if m:
            return CommandMatch(
                matched=True, intent="delete", confidence=0.90,
                metadata={"routine_name": m.group(1).strip()},
            )

        # Enable / disable
        m = _ENABLE_PATTERN.search(msg)
        if m:
            return CommandMatch(
                matched=True, intent="enable", confidence=0.90,
                metadata={"routine_name": m.group(1).strip()},
            )

        m = _DISABLE_PATTERN.search(msg)
        if m:
            return CommandMatch(
                matched=True, intent="disable", confidence=0.90,
                metadata={"routine_name": m.group(1).strip()},
            )

        # Template — "set up good morning routine"
        m = _TEMPLATE_PATTERN.search(msg)
        if m:
            name = m.group(1).strip().lower()
            template_id = _slugify(name)
            if template_id in TEMPLATES:
                return CommandMatch(
                    matched=True, intent="template", confidence=0.90,
                    metadata={"template_id": template_id},
                )

        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        intent = match.intent

        if intent == "run":
            return await self._handle_run(match, context)
        elif intent == "create_when":
            return await self._handle_create_when(message, context)
        elif intent == "create":
            return await self._handle_create(context)
        elif intent == "list":
            return await self._handle_list(context)
        elif intent == "delete":
            return await self._handle_delete(match, context)
        elif intent == "enable":
            return await self._handle_enable(match, context)
        elif intent == "disable":
            return await self._handle_disable(match, context)
        elif intent == "template":
            return await self._handle_template(match, context)

        return CommandResult(success=False, response="I'm not sure what to do with that routine command.")

    # ── Intent handlers ──────────────────────────────────────────

    async def _handle_run(self, match: CommandMatch, context: dict[str, Any]) -> CommandResult:
        name = match.metadata.get("routine_name", "")
        routine = await self._find_routine_by_name(name)
        if routine is None:
            return CommandResult(success=False, response=f"I couldn't find a routine called \"{name}\".")

        try:
            run_id = await self._engine.run_routine(routine["id"], context)
            return CommandResult(
                success=True,
                response=f"Running routine \"{routine['name']}\".",
                metadata={"routine_id": routine["id"], "run_id": run_id},
            )
        except ValueError as exc:
            return CommandResult(success=False, response=str(exc))

    async def _handle_create_when(self, message: str, context: dict[str, Any]) -> CommandResult:
        m = _WHEN_PATTERN.search(message)
        if not m:
            return CommandResult(success=False, response="I couldn't parse that. Try: \"when I say X, do Y\".")

        trigger_phrase = m.group(1).strip()
        action_text = m.group(2).strip()

        action = _parse_action(action_text)
        if action is None:
            return CommandResult(
                success=False,
                response=(
                    f"I understood the trigger \"{trigger_phrase}\" but couldn't parse "
                    f"the action \"{action_text}\". Try something like \"turn on the bedroom lights\" "
                    f"or \"say good morning\"."
                ),
            )

        user_id = context.get("user_id", "")
        rid = await self._engine.create_routine(
            name=f"Voice: {trigger_phrase}",
            description=f"When I say '{trigger_phrase}', {action_text}",
            user_id=user_id,
        )
        await self._engine.add_step(
            rid, action["action_type"], action["action_config"],
        )
        await self._engine.add_trigger(
            rid, "voice_phrase", {"phrase": trigger_phrase},
        )

        return CommandResult(
            success=True,
            response=f"Done! I created a routine that will {action_text} when you say \"{trigger_phrase}\".",
            metadata={"routine_id": rid},
        )

    async def _handle_create(self, context: dict[str, Any]) -> CommandResult:
        return CommandResult(
            success=True,
            response=(
                "To create a routine, you can say \"when I say X, do Y\" for a quick setup, "
                "or use the admin panel for more complex routines with multiple steps."
            ),
        )

    async def _handle_list(self, context: dict[str, Any]) -> CommandResult:
        user_id = context.get("user_id", "")
        routines = await self._engine.list_routines(user_id=user_id)
        if not routines:
            all_routines = await self._engine.list_routines()
            if not all_routines:
                return CommandResult(
                    success=True,
                    response="You don't have any routines yet. Say \"create a routine\" to get started.",
                )
            routines = all_routines

        lines = [f"You have {len(routines)} routine(s):"]
        for r in routines:
            status = "✅" if r.get("enabled") else "⏸️"
            runs = r.get("run_count", 0)
            lines.append(f"  {status} {r['name']} — {runs} run(s)")

        return CommandResult(success=True, response="\n".join(lines))

    async def _handle_delete(self, match: CommandMatch, context: dict[str, Any]) -> CommandResult:
        name = match.metadata.get("routine_name", "")
        routine = await self._find_routine_by_name(name)
        if routine is None:
            return CommandResult(success=False, response=f"I couldn't find a routine called \"{name}\".")

        await self._engine.delete_routine(routine["id"])
        return CommandResult(success=True, response=f"Deleted routine \"{routine['name']}\".")

    async def _handle_enable(self, match: CommandMatch, context: dict[str, Any]) -> CommandResult:
        name = match.metadata.get("routine_name", "")
        routine = await self._find_routine_by_name(name)
        if routine is None:
            return CommandResult(success=False, response=f"I couldn't find a routine called \"{name}\".")

        await self._engine.enable_routine(routine["id"])
        return CommandResult(success=True, response=f"Enabled routine \"{routine['name']}\".")

    async def _handle_disable(self, match: CommandMatch, context: dict[str, Any]) -> CommandResult:
        name = match.metadata.get("routine_name", "")
        routine = await self._find_routine_by_name(name)
        if routine is None:
            return CommandResult(success=False, response=f"I couldn't find a routine called \"{name}\".")

        await self._engine.disable_routine(routine["id"])
        return CommandResult(success=True, response=f"Disabled routine \"{routine['name']}\".")

    async def _handle_template(self, match: CommandMatch, context: dict[str, Any]) -> CommandResult:
        template_id = match.metadata.get("template_id", "")
        if template_id not in TEMPLATES:
            return CommandResult(
                success=False,
                response=f"Template \"{template_id}\" not found. Available: {', '.join(TEMPLATES.keys())}",
            )

        user_id = context.get("user_id", "")
        try:
            rid = await instantiate_template(self._engine, template_id, user_id=user_id)
            tmpl = TEMPLATES[template_id]
            return CommandResult(
                success=True,
                response=f"Created routine \"{tmpl['name']}\" from template. It's ready to use!",
                metadata={"routine_id": rid, "template_id": template_id},
            )
        except KeyError:
            return CommandResult(success=False, response=f"Template \"{template_id}\" not found.")

    # ── Helpers ───────────────────────────────────────────────────

    async def _find_routine_by_name(self, name: str) -> dict | None:
        """Find a routine by fuzzy name matching."""
        routines = await self._engine.list_routines()
        name_lower = name.lower().strip()

        # Exact match first
        for r in routines:
            if r["name"].lower() == name_lower:
                return r

        # Partial match
        for r in routines:
            if name_lower in r["name"].lower() or r["name"].lower() in name_lower:
                return r

        return None
