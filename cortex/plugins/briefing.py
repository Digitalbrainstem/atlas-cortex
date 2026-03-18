"""Daily briefing plugin — Layer 2 plugin for morning briefings."""

# Module ownership: Daily briefing plugin

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.proactive.briefing import DailyBriefing

logger = logging.getLogger(__name__)

_BRIEFING_PATTERNS = [
    re.compile(r"\b(?:daily|morning|evening)\s+briefing\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is) my day look(?:ing)? like\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is) on my schedule\b", re.IGNORECASE),
    re.compile(r"\bwhat do I have today\b", re.IGNORECASE),
    re.compile(r"\bgive me (?:a |my )?briefing\b", re.IGNORECASE),
    re.compile(r"\bbrief me\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is) (?:coming )?up today\b", re.IGNORECASE),
]


class DailyBriefingPlugin(CortexPlugin):
    """Layer 2 plugin: daily briefing on demand."""

    plugin_id = "daily_briefing"
    display_name = "Daily Briefing"
    plugin_type = "action"
    supports_learning = False
    version = "1.0.0"
    author = "Atlas"
    config_schema: dict[str, Any] = {}

    def __init__(self) -> None:
        self._briefing = DailyBriefing()

    async def setup(self, config: dict[str, Any] | None = None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(self, message: str, context: dict[str, Any] | None = None) -> CommandMatch:
        for pattern in _BRIEFING_PATTERNS:
            if pattern.search(message):
                return CommandMatch(
                    matched=True,
                    intent="daily_briefing",
                    confidence=0.9,
                )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any] | None = None,
    ) -> CommandResult:
        user_id = (context or {}).get("user_id", "")
        try:
            summary = await self._briefing.generate(user_id)
            return CommandResult(success=True, response=summary)
        except Exception:
            logger.exception("Failed to generate daily briefing")
            return CommandResult(
                success=False,
                response="Sorry, I couldn't generate your briefing right now.",
            )
