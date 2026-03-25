"""Weather plugin — current conditions and forecast for Layer 2."""

# Module ownership: Fast-path weather lookup via OpenWeatherMap

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, ConfigField, CortexPlugin

logger = logging.getLogger(__name__)

# ── Intent detection patterns ────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("weather", re.compile(
        r"\b(?:weather|forecast|temperature)\b",
        re.IGNORECASE,
    )),
    ("weather", re.compile(
        r"\b(?:is\s+it\s+(?:going\s+to\s+)?rain|will\s+it\s+rain|chance\s+of\s+rain)\b",
        re.IGNORECASE,
    )),
    ("weather", re.compile(
        r"\bhow\s+(?:hot|cold|warm|cool|humid|windy)\b",
        re.IGNORECASE,
    )),
    ("weather", re.compile(
        r"\bwhat(?:'s|s| is)\s+(?:the\s+)?(?:temp|weather|forecast)\b",
        re.IGNORECASE,
    )),
]

# Cache: city → (timestamp, response_text)
_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 900  # 15 minutes

# ── City extraction ──────────────────────────────────────────────

_CITY_RE = re.compile(
    r"(?:weather|forecast|temperature|temp)\s+(?:in|for|at)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)

_CITY_RE_ALT = re.compile(
    r"(?:how\s+(?:hot|cold|warm|cool))\s+(?:is\s+it\s+)?(?:in|at)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)


def _extract_city(message: str) -> str | None:
    """Pull a city name from the message, or return None."""
    for pattern in (_CITY_RE, _CITY_RE_ALT):
        m = pattern.search(message)
        if m:
            return m.group(1).strip().rstrip("?.,!")
    return None


def _format_response(data: dict[str, Any]) -> str:
    """Build a concise weather string from OpenWeatherMap JSON."""
    name = data.get("name", "your area")
    main = data.get("main", {})
    weather = data.get("weather", [{}])[0]
    temp = main.get("temp")
    high = main.get("temp_max")
    desc = weather.get("description", "")

    parts = []
    if temp is not None:
        parts.append(f"{temp:.0f}°F")
    if desc:
        parts.append(desc)
    parts.append(f"in {name}")

    line = ", ".join(parts[:2]) + f" in {name}" if len(parts) >= 3 else ", ".join(parts)
    if high is not None and temp is not None and abs(high - temp) > 1:
        line += f". High of {high:.0f}°F today."
    else:
        line += "."
    return line


# ── Plugin class ─────────────────────────────────────────────────

class WeatherPlugin(CortexPlugin):
    """Layer 2 plugin for current weather lookups."""

    plugin_id = "weather"
    display_name = "Weather"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"
    config_fields = [
        ConfigField(
            key="api_key",
            label="OpenWeatherMap API Key",
            field_type="password",
            required=False,
            placeholder="Enter your OpenWeatherMap API key...",
            help_text="Free at https://openweathermap.org/api — without a key, weather questions fall through to AI knowledge.",
        ),
        ConfigField(
            key="default_city",
            label="Default City",
            field_type="text",
            required=False,
            placeholder="Austin",
            help_text="City used when no location is mentioned.",
            default="Austin",
        ),
    ]

    def __init__(self) -> None:
        self._api_key: str = ""
        self._default_city: str = "Austin"
        self._base_url = "https://api.openweathermap.org/data/2.5/weather"

    @property
    def health_message(self) -> str:
        if not self._api_key:
            return "No API key configured — optional, will use AI knowledge instead"
        return "Connected to OpenWeatherMap"

    async def setup(self, config: dict[str, Any]) -> bool:
        self._api_key = config.get("api_key", "")
        self._default_city = config.get("default_city", "Austin")
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        for intent, pattern in _PATTERNS:
            if pattern.search(message):
                city = _extract_city(message) or self._default_city
                return CommandMatch(
                    matched=True,
                    intent=intent,
                    entities=[city],
                    confidence=0.9,
                )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        city = match.entities[0] if match.entities else self._default_city

        # Check cache
        now = time.time()
        cache_key = city.lower()
        if cache_key in _cache:
            cached_time, cached_text = _cache[cache_key]
            if now - cached_time < _CACHE_TTL:
                return CommandResult(success=True, response=cached_text)

        if not self._api_key:
            return CommandResult(
                success=False,
                response="",
                metadata={"no_api_key": True},
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    self._base_url,
                    params={
                        "q": city,
                        "appid": self._api_key,
                        "units": "imperial",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            text = _format_response(data)
            _cache[cache_key] = (now, text)
            return CommandResult(success=True, response=text)
        except httpx.HTTPStatusError as exc:
            logger.warning("Weather API error: %s", exc)
            return CommandResult(
                success=False,
                response=f"Couldn't get weather for {city} — the API returned an error.",
            )
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)
            return CommandResult(
                success=False,
                response=f"Couldn't fetch weather for {city} right now.",
            )
