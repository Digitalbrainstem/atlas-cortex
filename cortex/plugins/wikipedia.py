"""Wikipedia plugin — concise summaries for Layer 2."""

# Module ownership: Fast-path Wikipedia summary lookup

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("lookup", re.compile(
        r"\bwho\s+(?:is|was|are|were)\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
    ("lookup", re.compile(
        r"\btell\s+me\s+about\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
    ("lookup", re.compile(
        r"\bwikipedia\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
    ("lookup", re.compile(
        r"\bwhat\s+(?:is|are|was|were)\s+(?:a\s+|an\s+|the\s+)?(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
]

# Phrases that should NOT trigger Wikipedia (they're conversational)
_EXCLUSIONS = re.compile(
    r"\b(?:weather|temperature|forecast|time|date|timer|alarm"
    r"|define|definition|meaning|convert|movie|film"
    r"|how\s+(?:hot|cold|many|much|long)|cooking\s+temp)\b",
    re.IGNORECASE,
)

_BASE_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"


def _extract_topic(message: str) -> str | None:
    """Pull the lookup topic from the message."""
    for _, pattern in _PATTERNS:
        m = pattern.search(message)
        if m:
            topic = m.group(1).strip().rstrip("?.,!")
            if topic:
                return topic
    return None


def _truncate_summary(extract: str, max_sentences: int = 3) -> str:
    """Limit to *max_sentences* for conciseness."""
    sentences = re.split(r'(?<=[.!?])\s+', extract)
    return " ".join(sentences[:max_sentences])


# ── Plugin class ─────────────────────────────────────────────────

class WikipediaPlugin(CortexPlugin):
    """Layer 2 plugin for concise Wikipedia summaries."""

    plugin_id = "wikipedia"
    display_name = "Wikipedia"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        # Skip if another fast-path plugin is more appropriate
        if _EXCLUSIONS.search(message):
            return CommandMatch(matched=False)

        topic = _extract_topic(message)
        if topic:
            return CommandMatch(
                matched=True,
                intent="lookup",
                entities=[topic],
                confidence=0.7,
            )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        topic = match.entities[0] if match.entities else _extract_topic(message) or ""
        if not topic:
            return CommandResult(success=False, response="I couldn't determine what to look up.")

        encoded = quote(topic.replace(" ", "_"), safe="")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{_BASE_URL}/{encoded}",
                    headers={"Accept": "application/json"},
                    follow_redirects=True,
                )
                if resp.status_code == 404:
                    return CommandResult(
                        success=False,
                        response=f"I couldn't find a Wikipedia article for \"{topic}\".",
                    )
                resp.raise_for_status()
                data = resp.json()

            extract = data.get("extract", "")
            title = data.get("title", topic)
            if not extract:
                return CommandResult(
                    success=False,
                    response=f"The Wikipedia article for \"{topic}\" doesn't have a summary.",
                )

            summary = _truncate_summary(extract)
            return CommandResult(success=True, response=summary)
        except httpx.HTTPStatusError as exc:
            logger.warning("Wikipedia API error for '%s': %s", topic, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't look up \"{topic}\" on Wikipedia right now.",
            )
        except Exception as exc:
            logger.warning("Wikipedia fetch failed for '%s': %s", topic, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't look up \"{topic}\" on Wikipedia right now.",
            )
