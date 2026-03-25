"""Stock Prices plugin — fetch quotes from Yahoo Finance.

Module ownership: Layer 2 fast-path plugin for stock price queries.
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

# ── Match patterns ───────────────────────────────────────────────
_STOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bstock\s*price\b", re.I),
    re.compile(r"\bshare\s*price\b", re.I),
    re.compile(r"\bstock\s+for\b", re.I),
    re.compile(r"\bhow\s+is\s+[A-Z]{1,5}\s+doing\b", re.I),
    re.compile(r"\bmarket\b", re.I),
    re.compile(r"\bstock\s+quote\b", re.I),
    re.compile(r"\bticker\b", re.I),
]

_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_CACHE_TTL = 5 * 60  # 5 minutes

# Common words that look like tickers but aren't
_TICKER_STOPWORDS = frozenset({
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO",
    "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON",
    "OR", "SO", "TO", "UP", "US", "WE", "CEO", "CFO", "CTO",
    "FOR", "HOW", "THE", "ARE", "AND", "HAS", "HIS", "HER",
    "ITS", "NOT", "BUT", "CAN", "DID", "GET", "HAD", "HAS",
    "LET", "MAY", "NEW", "NOW", "OLD", "OUR", "OUT", "OWN",
    "SAY", "SHE", "TOO", "USE", "DAD", "MOM", "WHO", "WHY",
    "ALL", "ANY", "BIG", "DAY", "END", "FAR", "FEW", "GOT",
    "SET", "TOP", "TRY", "WAY", "YES", "YET", "NFL", "NBA",
    "MLB", "NHL", "BBC", "CNN", "STOCK", "PRICE", "SHARE",
    "WHAT", "DOES", "MUCH", "TELL", "SHOW", "WITH", "FROM",
    "THAT", "THIS", "HAVE", "JUST", "BEEN", "WILL", "YOUR",
    "THAN", "THEM", "THEN", "THEY", "WERE", "WHEN", "ALSO",
    "BACK", "BEEN", "COME", "EACH", "EVEN", "GIVE", "GOOD",
    "LIKE", "LONG", "LOOK", "MADE", "MAKE", "MANY", "MOST",
    "MUCH", "MUST", "NAME", "ONLY", "OVER", "SAME", "SOME",
    "SUCH", "TAKE", "VERY", "WELL", "WORK", "YEAR", "ABOUT",
    "COULD", "DOING", "GOING", "TODAY",
})


@dataclass
class _QuoteCache:
    data: dict[str, dict[str, Any]]  # ticker → quote data
    timestamp: float


def _extract_tickers(message: str) -> list[str]:
    """Pull plausible ticker symbols from *message*."""
    found = _TICKER_RE.findall(message)
    return [t for t in found if t not in _TICKER_STOPWORDS]


class StocksPlugin(CortexPlugin):
    """Fetch stock prices from Yahoo Finance."""

    plugin_id = "stocks"
    display_name = "Stock Prices"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"
    # No API key needed — uses free Yahoo Finance endpoint
    config_fields = []

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[str, _QuoteCache] = {}

    @property
    def health_message(self) -> str:
        return "Ready — uses Yahoo Finance (no API key needed)"

    # ── Lifecycle ────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    # ── Match ────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        for pat in _STOCK_PATTERNS:
            if pat.search(message):
                tickers = _extract_tickers(message)
                return CommandMatch(
                    matched=True,
                    intent="stock_quote",
                    confidence=0.90,
                    entities=tickers,
                    metadata={"tickers": tickers},
                )
        return CommandMatch(matched=False)

    # ── Handle ───────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        tickers = match.metadata.get("tickers", [])
        if not tickers:
            return CommandResult(
                success=False,
                response=(
                    "I didn't catch a ticker symbol. "
                    'Try something like "stock price for AAPL".'
                ),
            )

        quotes: list[str] = []
        now = time.monotonic()

        async with httpx.AsyncClient(timeout=10) as client:
            for ticker in tickers[:5]:
                cached = self._cache.get(ticker)
                if cached and (now - cached.timestamp) < _CACHE_TTL:
                    quote_line = self._format_quote(ticker, cached.data.get(ticker, {}))
                    if quote_line:
                        quotes.append(quote_line)
                    continue

                try:
                    data = await self._fetch_quote(client, ticker)
                    self._cache[ticker] = _QuoteCache(
                        data={ticker: data}, timestamp=now,
                    )
                    line = self._format_quote(ticker, data)
                    if line:
                        quotes.append(line)
                except Exception as exc:
                    logger.debug("Quote fetch for %s failed: %s", ticker, exc)
                    quotes.append(f"{ticker}: unavailable")

        if not quotes:
            return CommandResult(success=False, response="Couldn't fetch stock data.")

        return CommandResult(
            success=True,
            response="\n".join(quotes),
            entities_used=tickers,
        )

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    async def _fetch_quote(
        client: httpx.AsyncClient, symbol: str,
    ) -> dict[str, Any]:
        resp = await client.get(
            _YAHOO_CHART_URL.format(symbol=symbol),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return {}
        meta = result[0].get("meta", {})
        return {
            "price": meta.get("regularMarketPrice"),
            "prev_close": meta.get("chartPreviousClose")
                          or meta.get("previousClose"),
            "currency": meta.get("currency", "USD"),
        }

    @staticmethod
    def _format_quote(ticker: str, data: dict[str, Any]) -> str:
        price = data.get("price")
        if price is None:
            return ""
        prev = data.get("prev_close")
        currency = data.get("currency", "USD")
        symbol = "$" if currency == "USD" else f"{currency} "
        line = f"{ticker}: {symbol}{price:,.2f}"
        if prev and prev > 0:
            change_pct = ((price - prev) / prev) * 100
            sign = "+" if change_pct >= 0 else ""
            line += f" ({sign}{change_pct:.1f}% today)"
        return line
