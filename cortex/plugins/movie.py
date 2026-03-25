"""Movie & TV plugin — concise lookups for Layer 2."""

# Module ownership: Fast-path movie/TV lookup via TMDb API

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, ConfigField, CortexPlugin

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("movie_lookup", re.compile(
        r"\b(?:movie|film)\b",
        re.IGNORECASE,
    )),
    ("movie_release", re.compile(
        r"\bwhen\s+did\s+(.+?)\s+come\s+out\b",
        re.IGNORECASE,
    )),
    ("movie_director", re.compile(
        r"\bwho\s+directed\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
    ("movie_cast", re.compile(
        r"\bwho\s+starred\s+in\s+(.+?)(?:\?|$)",
        re.IGNORECASE,
    )),
]

# Extract title from general queries
_TITLE_RE = re.compile(
    r"(?:movie|film)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)

_TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
_TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie"


def _extract_title(message: str) -> str | None:
    """Pull a movie title from the message."""
    for _, pattern in _PATTERNS[1:]:  # Skip the generic "movie" pattern
        m = pattern.search(message)
        if m:
            return m.group(1).strip().rstrip("?.,!")

    m = _TITLE_RE.search(message)
    if m:
        return m.group(1).strip().rstrip("?.,!")
    return None


def _detect_intent(message: str) -> tuple[str, str | None]:
    """Return (intent, title) pair."""
    for intent, pattern in _PATTERNS:
        m = pattern.search(message)
        if m:
            title = m.group(1).strip().rstrip("?.,!") if m.lastindex else _extract_title(message)
            return intent, title
    return "", None


def _format_concise(movie: dict[str, Any], intent: str) -> str:
    """Build a CONCISE answer — no synopsis unless asked."""
    title = movie.get("title", "Unknown")
    year = movie.get("release_date", "")[:4]
    rating = movie.get("vote_average")

    if intent == "movie_release":
        date = movie.get("release_date", "unknown date")
        return f"{title} was released on {date}."

    if intent == "movie_director":
        # Credits would need a separate API call; give what we have
        if year:
            return f"{title} ({year}) — director info requires a TMDb credits lookup."
        return f"{title} — director info requires a TMDb credits lookup."

    if intent == "movie_cast":
        if year:
            return f"{title} ({year}) — cast info requires a TMDb credits lookup."
        return f"{title} — cast info requires a TMDb credits lookup."

    # Default: title, year, rating
    parts = [title]
    if year:
        parts[0] += f" ({year})"
    if rating is not None:
        parts.append(f"Rating: {rating}/10")
    return " — ".join(parts) + "."


def _mock_response(title: str | None, intent: str) -> str:
    """Return a mock response when no API key is configured."""
    t = title or "that movie"
    if intent == "movie_release":
        return f"I don't have an API key for TMDb. Configure one to look up when {t} was released."
    return f"I don't have an API key for TMDb. Configure one to look up info about {t}."


# ── Plugin class ─────────────────────────────────────────────────

class MoviePlugin(CortexPlugin):
    """Layer 2 plugin for concise movie and TV lookups."""

    plugin_id = "movie"
    display_name = "Movie & TV"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"
    config_fields = [
        ConfigField(
            key="api_key",
            label="TMDb API Key",
            field_type="password",
            required=False,
            placeholder="Enter your TMDb API key...",
            help_text="Free at https://www.themoviedb.org/settings/api — without a key, movie questions fall through to AI knowledge.",
        ),
    ]

    def __init__(self) -> None:
        self._api_key: str = ""

    @property
    def health_message(self) -> str:
        if not self._api_key:
            return "No API key configured — optional, will use AI knowledge instead"
        return "Connected to TMDb"

    async def setup(self, config: dict[str, Any]) -> bool:
        self._api_key = config.get("api_key", "")
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        intent, title = _detect_intent(message)
        if intent:
            entities = [title] if title else []
            return CommandMatch(
                matched=True,
                intent=intent,
                entities=entities,
                confidence=0.85,
            )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        intent = match.intent
        title = match.entities[0] if match.entities else _extract_title(message)

        if not self._api_key:
            return CommandResult(
                success=False,
                response="",
                metadata={"no_api_key": True},
            )

        if not title:
            return CommandResult(
                success=False,
                response="Which movie are you asking about?",
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    _TMDB_SEARCH_URL,
                    params={"api_key": self._api_key, "query": title},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])
            if not results:
                return CommandResult(
                    success=False,
                    response=f"I couldn't find a movie called \"{title}\".",
                )

            movie = results[0]
            text = _format_concise(movie, intent)
            return CommandResult(success=True, response=text)
        except Exception as exc:
            logger.warning("TMDb lookup failed for '%s': %s", title, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't look up \"{title}\" right now.",
            )
