"""Sports Scores plugin — fetch live scores from ESPN.

Module ownership: Layer 2 fast-path plugin for sports score queries.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, ConfigField, CortexPlugin

logger = logging.getLogger(__name__)

# ── Sport → ESPN path mapping ────────────────────────────────────
SPORT_LEAGUES: dict[str, tuple[str, str]] = {
    # keyword → (sport, league)
    "nfl": ("football", "nfl"),
    "football": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "basketball": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "baseball": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "hockey": ("hockey", "nhl"),
    "soccer": ("soccer", "usa.1"),
    "mls": ("soccer", "usa.1"),
    "premier league": ("soccer", "eng.1"),
    "epl": ("soccer", "eng.1"),
}

_ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
)
_CACHE_TTL = 10 * 60  # 10 minutes

# ── Match patterns ───────────────────────────────────────────────
_SPORT_KEYWORDS = "|".join(SPORT_LEAGUES.keys())
_SCORE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bscores?\b", re.I),
    re.compile(r"\bgames?\b.*\b(?:today|tonight|yesterday|last\s+night)\b", re.I),
    re.compile(r"\bwho\s+won\b", re.I),
    re.compile(r"\bwho(?:'s| is) (?:winning|playing)\b", re.I),
    re.compile(rf"\b({_SPORT_KEYWORDS})\b", re.I),
]


@dataclass
class _ScoreCache:
    lines: list[str]
    timestamp: float


def _detect_league(message: str) -> tuple[str, str] | None:
    """Return (sport, league) for the first matching keyword."""
    lower = message.lower()
    # Check multi-word keys first
    for keyword in sorted(SPORT_LEAGUES.keys(), key=len, reverse=True):
        if keyword in lower:
            return SPORT_LEAGUES[keyword]
    return None


class SportsPlugin(CortexPlugin):
    """Fetch latest sports scores from ESPN."""

    plugin_id = "sports"
    display_name = "Sports Scores"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"
    # No API key needed — uses free ESPN API
    config_fields = []

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[str, _ScoreCache] = {}

    @property
    def health_message(self) -> str:
        return "Ready — uses ESPN API (no API key needed)"

    # ── Lifecycle ────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    # ── Match ────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        for pat in _SCORE_PATTERNS:
            if pat.search(message):
                league_info = _detect_league(message)
                sport, league = league_info if league_info else ("", "")
                return CommandMatch(
                    matched=True,
                    intent="get_scores",
                    confidence=0.88,
                    metadata={"sport": sport, "league": league},
                )
        return CommandMatch(matched=False)

    # ── Handle ───────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        sport = match.metadata.get("sport", "")
        league = match.metadata.get("league", "")

        if not sport or not league:
            league_info = _detect_league(message)
            if league_info:
                sport, league = league_info
            else:
                # Default to NFL
                sport, league = "football", "nfl"

        cache_key = f"{sport}/{league}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached.timestamp) < _CACHE_TTL:
            return self._format(league.upper(), cached.lines)

        try:
            lines = await self._fetch_scores(sport, league)
        except Exception as exc:
            logger.warning("ESPN fetch failed for %s/%s: %s", sport, league, exc)
            return CommandResult(
                success=False,
                response=f"Couldn't fetch {league.upper()} scores right now.",
            )

        self._cache[cache_key] = _ScoreCache(lines=lines, timestamp=now)
        return self._format(league.upper(), lines)

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    def _format(league: str, lines: list[str]) -> CommandResult:
        if not lines:
            return CommandResult(
                success=True,
                response=f"No {league} games on the scoreboard right now.",
            )
        header = f"Latest {league} scores:"
        body = "\n".join(f"• {l}" for l in lines[:5])
        return CommandResult(success=True, response=f"{header}\n{body}")

    @staticmethod
    async def _fetch_scores(sport: str, league: str) -> list[str]:
        url = _ESPN_SCOREBOARD.format(sport=sport, league=league)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        events = data.get("events", [])
        lines: list[str] = []
        for event in events[:5]:
            name = event.get("shortName", event.get("name", "Unknown"))
            competitions = event.get("competitions", [])
            if not competitions:
                lines.append(name)
                continue
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            if len(competitors) >= 2:
                away = competitors[1] if competitors[0].get("homeAway") == "home" else competitors[0]
                home = competitors[0] if competitors[0].get("homeAway") == "home" else competitors[1]
                away_name = away.get("team", {}).get("abbreviation", "AWAY")
                home_name = home.get("team", {}).get("abbreviation", "HOME")
                away_score = away.get("score", "?")
                home_score = home.get("score", "?")
                status_type = comp.get("status", {}).get("type", {})
                state = status_type.get("shortDetail", "")
                lines.append(
                    f"{away_name} {away_score} – {home_name} {home_score}  {state}".strip(),
                )
            else:
                lines.append(name)
        return lines
