"""Tests for fast-path Layer 2 plugins — batch 2.

Covers: news, translation, stocks, sports, sound_library.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from cortex.plugins.base import CommandMatch, CommandResult
from cortex.plugins.news import NewsPlugin
from cortex.plugins.translation import (
    LANGUAGE_CODES,
    TranslationPlugin,
    _parse_request,
)
from cortex.plugins.stocks import StocksPlugin, _extract_tickers
from cortex.plugins.sports import SPORT_LEAGUES, SportsPlugin, _detect_league
from cortex.plugins.sound_library import SOUND_CATALOG, SoundLibraryPlugin

CTX: dict = {}


# ─── News Plugin ─────────────────────────────────────────────────


class TestNewsMatch:
    async def test_matches_news(self):
        p = NewsPlugin()
        await p.setup({})
        assert (await p.match("what's the latest news?", CTX)).matched

    async def test_matches_headlines(self):
        p = NewsPlugin()
        await p.setup({})
        assert (await p.match("show me today's headlines", CTX)).matched

    async def test_matches_current_events(self):
        p = NewsPlugin()
        await p.setup({})
        assert (await p.match("tell me about current events", CTX)).matched

    async def test_matches_whats_happening(self):
        p = NewsPlugin()
        await p.setup({})
        assert (await p.match("what's happening in the world", CTX)).matched

    async def test_no_match_unrelated(self):
        p = NewsPlugin()
        await p.setup({})
        assert not (await p.match("what time is it", CTX)).matched

    async def test_no_match_cooking(self):
        p = NewsPlugin()
        await p.setup({})
        assert not (await p.match("how do I cook pasta", CTX)).matched


class TestNewsHandle:
    async def test_rss_fallback(self):
        rss_xml = """<?xml version="1.0"?>
        <rss><channel>
            <item><title>Headline One</title></item>
            <item><title>Headline Two</title></item>
            <item><title>Headline Three</title></item>
        </channel></rss>"""

        p = NewsPlugin()
        await p.setup({})
        match = CommandMatch(matched=True, intent="get_news")

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = rss_xml
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.news.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("latest news", match, CTX)

        assert result.success
        assert "Headline One" in result.response
        assert "Headline Two" in result.response

    async def test_newsapi_with_key(self):
        api_resp = {
            "articles": [
                {"title": "API Article 1"},
                {"title": "API Article 2"},
            ],
        }

        p = NewsPlugin()
        await p.setup({"api_key": "test-key"})
        match = CommandMatch(matched=True, intent="get_news")

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_resp
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.news.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("latest news", match, CTX)

        assert result.success
        assert "API Article 1" in result.response

    async def test_cache_prevents_refetch(self):
        rss_xml = """<?xml version="1.0"?>
        <rss><channel>
            <item><title>Cached Headline</title></item>
        </channel></rss>"""

        p = NewsPlugin()
        await p.setup({})
        match = CommandMatch(matched=True, intent="get_news")

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = rss_xml
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.news.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            r1 = await p.handle("news", match, CTX)
            r2 = await p.handle("news", match, CTX)

        assert r1.success and r2.success
        # Second call should reuse cache — only one client context entered
        assert mock_client_cls.return_value.__aenter__.call_count == 1


# ─── Translation Plugin ─────────────────────────────────────────


class TestTranslationLanguageCodes:
    def test_common_languages_mapped(self):
        assert LANGUAGE_CODES["spanish"] == "es"
        assert LANGUAGE_CODES["french"] == "fr"
        assert LANGUAGE_CODES["german"] == "de"
        assert LANGUAGE_CODES["japanese"] == "ja"
        assert LANGUAGE_CODES["chinese"] == "zh"

    def test_parse_translate_to(self):
        text, src, tgt = _parse_request("translate hello to Spanish")
        assert text == "hello"
        assert tgt == "es"

    def test_parse_how_do_you_say(self):
        text, src, tgt = _parse_request("how do you say goodbye in French")
        assert text == "goodbye"
        assert src == "en"
        assert tgt == "fr"

    def test_parse_what_does_mean(self):
        text, src, tgt = _parse_request("what does ciao mean in Italian")
        assert text == "ciao"
        assert tgt == "it"

    def test_parse_translate_from_to(self):
        text, src, tgt = _parse_request(
            "translate bonjour from French to English",
        )
        assert text == "bonjour"
        assert src == "fr"
        assert tgt == "en"


class TestTranslationMatch:
    async def test_matches_translate(self):
        p = TranslationPlugin()
        await p.setup({})
        m = await p.match("translate hello to Spanish", CTX)
        assert m.matched
        assert m.metadata["text"] == "hello"
        assert m.metadata["target"] == "es"

    async def test_matches_how_say(self):
        p = TranslationPlugin()
        await p.setup({})
        m = await p.match("how do you say cat in German", CTX)
        assert m.matched
        assert m.metadata["target"] == "de"

    async def test_no_match_unrelated(self):
        p = TranslationPlugin()
        await p.setup({})
        assert not (await p.match("set a timer for 5 minutes", CTX)).matched


class TestTranslationHandle:
    async def test_no_host_returns_fallback(self):
        p = TranslationPlugin()
        await p.setup({})  # no host
        match = CommandMatch(
            matched=True, intent="translate",
            metadata={"text": "hello", "source": "en", "target": "es"},
        )
        result = await p.handle("translate hello to Spanish", match, CTX)
        assert not result.success
        assert "configured" in result.response.lower()

    async def test_successful_translation(self):
        p = TranslationPlugin()
        await p.setup({"host": "http://localhost:5000"})
        match = CommandMatch(
            matched=True, intent="translate",
            metadata={"text": "hello", "source": "en", "target": "es"},
        )

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"translatedText": "hola"}
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.translation.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("translate hello to Spanish", match, CTX)

        assert result.success
        assert "hola" in result.response

    async def test_missing_text_returns_help(self):
        p = TranslationPlugin()
        await p.setup({"host": "http://localhost:5000"})
        match = CommandMatch(matched=True, intent="translate", metadata={})
        result = await p.handle("translate", match, CTX)
        assert not result.success
        assert "couldn't figure out" in result.response.lower()


# ─── Stocks Plugin ───────────────────────────────────────────────


class TestStocksTickerExtraction:
    def test_extract_known_tickers(self):
        assert "AAPL" in _extract_tickers("How is AAPL doing today?")
        assert "MSFT" in _extract_tickers("What's the stock price for MSFT?")

    def test_filters_stopwords(self):
        tickers = _extract_tickers("HOW IS THE STOCK DOING")
        # HOW, THE, STOCK, DOING are stopwords
        assert tickers == []

    def test_multiple_tickers(self):
        tickers = _extract_tickers("Compare AAPL and GOOG")
        assert "AAPL" in tickers
        assert "GOOG" in tickers


class TestStocksMatch:
    async def test_matches_stock_price(self):
        p = StocksPlugin()
        await p.setup({})
        m = await p.match("stock price for AAPL", CTX)
        assert m.matched
        assert "AAPL" in m.metadata["tickers"]

    async def test_matches_share_price(self):
        p = StocksPlugin()
        await p.setup({})
        assert (await p.match("what's the share price of TSLA", CTX)).matched

    async def test_no_match_unrelated(self):
        p = StocksPlugin()
        await p.setup({})
        assert not (await p.match("what is the weather", CTX)).matched


class TestStocksHandle:
    async def test_successful_quote(self):
        p = StocksPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="stock_quote",
            entities=["AAPL"], metadata={"tickers": ["AAPL"]},
        )

        yahoo_resp = {
            "chart": {
                "result": [{
                    "meta": {
                        "regularMarketPrice": 187.44,
                        "chartPreviousClose": 185.22,
                        "currency": "USD",
                    },
                }],
            },
        }

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = yahoo_resp
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.stocks.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("stock price for AAPL", match, CTX)

        assert result.success
        assert "AAPL" in result.response
        assert "187.44" in result.response
        assert "%" in result.response

    async def test_no_tickers_returns_help(self):
        p = StocksPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="stock_quote",
            metadata={"tickers": []},
        )
        result = await p.handle("stock price", match, CTX)
        assert not result.success
        assert "ticker" in result.response.lower()


# ─── Sports Plugin ───────────────────────────────────────────────


class TestSportsLeagueMapping:
    def test_nfl_mapping(self):
        assert _detect_league("NFL scores") == ("football", "nfl")

    def test_basketball_mapping(self):
        assert _detect_league("basketball game tonight") == ("basketball", "nba")

    def test_hockey_mapping(self):
        assert _detect_league("NHL scores please") == ("hockey", "nhl")

    def test_baseball_mapping(self):
        assert _detect_league("MLB results") == ("baseball", "mlb")

    def test_soccer_mapping(self):
        assert _detect_league("soccer scores") == ("soccer", "usa.1")

    def test_no_match(self):
        assert _detect_league("what time is it") is None


class TestSportsMatch:
    async def test_matches_scores(self):
        p = SportsPlugin()
        await p.setup({})
        m = await p.match("what are the NBA scores", CTX)
        assert m.matched
        assert m.metadata["league"] == "nba"

    async def test_matches_who_won(self):
        p = SportsPlugin()
        await p.setup({})
        assert (await p.match("who won the NFL game last night", CTX)).matched

    async def test_no_match_unrelated(self):
        p = SportsPlugin()
        await p.setup({})
        assert not (await p.match("translate hello to Spanish", CTX)).matched


class TestSportsHandle:
    async def test_successful_scores(self):
        p = SportsPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="get_scores",
            metadata={"sport": "basketball", "league": "nba"},
        )

        espn_resp = {
            "events": [{
                "shortName": "LAL @ BOS",
                "competitions": [{
                    "status": {"type": {"shortDetail": "Final"}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"abbreviation": "BOS"},
                            "score": "112",
                        },
                        {
                            "homeAway": "away",
                            "team": {"abbreviation": "LAL"},
                            "score": "105",
                        },
                    ],
                }],
            }],
        }

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = espn_resp
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.sports.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("NBA scores", match, CTX)

        assert result.success
        assert "LAL" in result.response
        assert "BOS" in result.response
        assert "105" in result.response
        assert "112" in result.response

    async def test_no_games(self):
        p = SportsPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="get_scores",
            metadata={"sport": "hockey", "league": "nhl"},
        )

        mock_resp = AsyncMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"events": []}
        mock_resp.raise_for_status = lambda: None

        with patch("cortex.plugins.sports.httpx.AsyncClient") as mock_client_cls:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await p.handle("NHL scores", match, CTX)

        assert result.success
        assert "no" in result.response.lower()


# ─── Sound Library Plugin ────────────────────────────────────────


class TestSoundLibraryMatch:
    async def test_matches_what_sound_like(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        m = await p.match("what does a cat sound like", CTX)
        assert m.matched
        assert m.metadata["subject"] == "cat"

    async def test_matches_play_sound(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        m = await p.match("play rain sound", CTX)
        assert m.matched
        assert m.metadata["subject"] == "rain"

    async def test_matches_sound_of(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        assert (await p.match("sound of the ocean", CTX)).matched

    async def test_matches_animal_sounds(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        assert (await p.match("play animal sounds", CTX)).matched

    async def test_no_match_unrelated(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        assert not (await p.match("set a timer for 5 minutes", CTX)).matched


class TestSoundLibraryHandle:
    async def test_known_animal(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="describe_sound",
            metadata={"subject": "cat"},
        )
        result = await p.handle("what does a cat sound like", match, CTX)
        assert result.success
        assert "purring" in result.response.lower() or "meow" in result.response.lower()
        assert result.metadata.get("found") is True
        assert result.metadata.get("category") == "animals"

    async def test_known_nature_sound(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="describe_sound",
            metadata={"subject": "rain"},
        )
        result = await p.handle("play rain sound", match, CTX)
        assert result.success
        assert result.metadata.get("found") is True

    async def test_unknown_sound(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="describe_sound",
            metadata={"subject": "alien"},
        )
        result = await p.handle("what does an alien sound like", match, CTX)
        assert result.success
        assert "don't have" in result.response.lower()

    async def test_category_listing(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="describe_sound",
            metadata={"subject": "animal"},
        )
        result = await p.handle("animal sounds", match, CTX)
        assert result.success
        assert "cat" in result.response.lower()
        assert "dog" in result.response.lower()

    async def test_catalog_has_all_categories(self):
        categories = {info["category"] for info in SOUND_CATALOG.values()}
        assert "animals" in categories
        assert "nature" in categories
        assert "vehicles" in categories
        assert "instruments" in categories

    async def test_no_subject_returns_help(self):
        p = SoundLibraryPlugin()
        await p.setup({})
        match = CommandMatch(
            matched=True, intent="describe_sound",
            metadata={"subject": ""},
        )
        result = await p.handle("sounds", match, CTX)
        assert result.success
        assert "animals" in result.response.lower()
