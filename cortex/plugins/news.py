"""News Headlines plugin — fetches top headlines from RSS feeds or NewsAPI.

Module ownership: Layer 2 fast-path plugin for news/headlines queries.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Match patterns ───────────────────────────────────────────────
_NEWS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:latest\s+)?news\b", re.I),
    re.compile(r"\bheadlines?\b", re.I),
    re.compile(r"\bwhat(?:'s| is) happening\b", re.I),
    re.compile(r"\bcurrent events?\b", re.I),
    re.compile(r"\btop stories\b", re.I),
]

# ── Default RSS sources ─────────────────────────────────────────
_DEFAULT_RSS_SOURCES: list[dict[str, str]] = [
    {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"},
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    {"name": "Reuters", "url": "https://feeds.reuters.com/reuters/topNews"},
]

_NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"
_CACHE_TTL = 30 * 60  # 30 minutes


@dataclass
class _CacheEntry:
    headlines: list[str]
    timestamp: float


class NewsPlugin(CortexPlugin):
    """Fetch top news headlines from RSS feeds or NewsAPI."""

    plugin_id = "news"
    display_name = "News Headlines"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str = ""
        self._sources: list[dict[str, str]] = list(_DEFAULT_RSS_SOURCES)
        self._cache: _CacheEntry | None = None

    # ── Lifecycle ────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        self._api_key = config.get("api_key", "")
        sources = config.get("sources")
        if sources and isinstance(sources, list):
            self._sources = sources
        return True

    async def health(self) -> bool:
        return True

    # ── Match ────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        for pat in _NEWS_PATTERNS:
            if pat.search(message):
                return CommandMatch(
                    matched=True, intent="get_news", confidence=0.90,
                )
        return CommandMatch(matched=False)

    # ── Handle ───────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        now = time.monotonic()
        if self._cache and (now - self._cache.timestamp) < _CACHE_TTL:
            return self._format(self._cache.headlines)

        try:
            if self._api_key:
                headlines = await self._fetch_newsapi()
            else:
                headlines = await self._fetch_rss()
        except Exception as exc:
            logger.warning("News fetch failed: %s", exc)
            return CommandResult(
                success=False,
                response="I couldn't fetch the latest news right now. Try again later.",
            )

        self._cache = _CacheEntry(headlines=headlines, timestamp=now)
        return self._format(headlines)

    # ── Internal ─────────────────────────────────────────────

    def _format(self, headlines: list[str]) -> CommandResult:
        if not headlines:
            return CommandResult(success=False, response="No headlines available right now.")
        lines = [f"• {h}" for h in headlines[:5]]
        body = "Here are the latest headlines:\n" + "\n".join(lines)
        return CommandResult(success=True, response=body)

    async def _fetch_newsapi(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NEWSAPI_URL,
                params={"country": "us", "pageSize": 5, "apiKey": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            return [a["title"] for a in data.get("articles", []) if a.get("title")]

    async def _fetch_rss(self) -> list[str]:
        headlines: list[str] = []
        async with httpx.AsyncClient(timeout=10) as client:
            for source in self._sources[:3]:
                try:
                    resp = await client.get(source["url"])
                    resp.raise_for_status()
                    root = ET.fromstring(resp.text)
                    for item in root.iter("item"):
                        title_el = item.find("title")
                        if title_el is not None and title_el.text:
                            headlines.append(title_el.text.strip())
                        if len(headlines) >= 5:
                            break
                    if len(headlines) >= 5:
                        break
                except Exception as exc:
                    logger.debug("RSS source %s failed: %s", source.get("name"), exc)
        return headlines[:5]
