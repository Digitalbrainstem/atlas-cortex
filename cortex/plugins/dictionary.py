"""Dictionary plugin — word definitions for Layer 2."""

# Module ownership: Fast-path dictionary lookup via Free Dictionary API

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("define", re.compile(
        r"\b(?:define|definition\s+of|meaning\s+of)\s+(\w[\w\s-]*)",
        re.IGNORECASE,
    )),
    ("define", re.compile(
        r"\bwhat\s+does\s+[\"']?(\w[\w\s-]*?)[\"']?\s+mean\b",
        re.IGNORECASE,
    )),
    ("define", re.compile(
        r"\bwhat\s+(?:is|are)\s+the\s+(?:definition|meaning)\s+of\s+[\"']?(\w[\w\s-]*)",
        re.IGNORECASE,
    )),
]

_BASE_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"


def _extract_word(message: str) -> str | None:
    """Pull the target word from the message."""
    for _, pattern in _PATTERNS:
        m = pattern.search(message)
        if m:
            return m.group(1).strip().lower()
    return None


def _format_entry(data: list[dict[str, Any]]) -> str:
    """Build a concise definition string from the API response."""
    entry = data[0]
    word = entry.get("word", "")
    phonetic = entry.get("phonetic", "")

    parts: list[str] = []
    header = word.capitalize()
    if phonetic:
        header += f"  {phonetic}"
    parts.append(header)

    meanings = entry.get("meanings", [])
    for meaning in meanings[:2]:
        pos = meaning.get("partOfSpeech", "")
        definitions = meaning.get("definitions", [])
        if definitions:
            defn = definitions[0].get("definition", "")
            parts.append(f"({pos}) {defn}")

    return "\n".join(parts)


# ── Plugin class ─────────────────────────────────────────────────

class DictionaryPlugin(CortexPlugin):
    """Layer 2 plugin for word definitions."""

    plugin_id = "dictionary"
    display_name = "Dictionary"
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
        word = _extract_word(message)
        if word:
            return CommandMatch(
                matched=True,
                intent="define",
                entities=[word],
                confidence=0.9,
            )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        word = match.entities[0] if match.entities else _extract_word(message) or ""
        if not word:
            return CommandResult(success=False, response="I couldn't determine which word to define.")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{_BASE_URL}/{word}")
                if resp.status_code == 404:
                    return CommandResult(
                        success=False,
                        response=f"I couldn't find a definition for \"{word}\".",
                    )
                resp.raise_for_status()
                data = resp.json()
            text = _format_entry(data)
            return CommandResult(success=True, response=text)
        except httpx.HTTPStatusError as exc:
            logger.warning("Dictionary API error for '%s': %s", word, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't look up \"{word}\" — the dictionary API returned an error.",
            )
        except Exception as exc:
            logger.warning("Dictionary fetch failed for '%s': %s", word, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't look up \"{word}\" right now.",
            )
