"""Tests for fast-path Layer 2 plugins.

Covers match() with positive/negative cases and handle() with mocked APIs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.plugins.base import CommandMatch

# ── Helpers ──────────────────────────────────────────────────────

CTX: dict = {"user_id": "test", "room": "main"}


def _mock_response(status_code: int = 200, json_data: dict | list | None = None) -> MagicMock:
    """Build a fake httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    return resp


# =====================================================================
# Weather Plugin
# =====================================================================

class TestWeatherPlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.weather import WeatherPlugin, _cache
        _cache.clear()
        p = WeatherPlugin()
        await p.setup({"default_city": "Austin"})
        return p

    # ── match ────────────────────────────────────────────────────

    async def test_match_weather(self, plugin):
        m = await plugin.match("what's the weather?", CTX)
        assert m.matched

    async def test_match_temperature(self, plugin):
        m = await plugin.match("what's the temperature outside?", CTX)
        assert m.matched

    async def test_match_forecast(self, plugin):
        m = await plugin.match("give me the forecast", CTX)
        assert m.matched

    async def test_match_rain(self, plugin):
        m = await plugin.match("is it going to rain?", CTX)
        assert m.matched

    async def test_match_how_hot(self, plugin):
        m = await plugin.match("how hot is it?", CTX)
        assert m.matched

    async def test_no_match(self, plugin):
        m = await plugin.match("tell me a joke", CTX)
        assert not m.matched

    # ── handle (mock — no API key) ───────────────────────────────

    async def test_handle_mock(self, plugin):
        match = CommandMatch(matched=True, intent="weather", entities=["Austin"])
        r = await plugin.handle("what's the weather?", match, CTX)
        assert r.success
        assert "Austin" in r.response
        assert "°F" in r.response

    # ── handle (with API key, mocked httpx) ──────────────────────

    async def test_handle_api(self, plugin):
        plugin._api_key = "test-key"
        api_data = {
            "name": "Dallas",
            "main": {"temp": 80, "temp_max": 90},
            "weather": [{"description": "clear sky"}],
        }
        mock_resp = _mock_response(200, api_data)

        with patch("cortex.plugins.weather.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="weather", entities=["Dallas"])
            r = await plugin.handle("weather in Dallas", match, CTX)
            assert r.success
            assert "Dallas" in r.response

    # ── cache ────────────────────────────────────────────────────

    async def test_cache(self, plugin):
        match = CommandMatch(matched=True, intent="weather", entities=["Austin"])
        r1 = await plugin.handle("weather", match, CTX)
        r2 = await plugin.handle("weather", match, CTX)
        assert r1.response == r2.response

    # ── city extraction ──────────────────────────────────────────

    async def test_city_extraction(self, plugin):
        m = await plugin.match("weather in New York", CTX)
        assert m.matched
        assert "New York" in m.entities


# =====================================================================
# Dictionary Plugin
# =====================================================================

class TestDictionaryPlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.dictionary import DictionaryPlugin
        p = DictionaryPlugin()
        await p.setup({})
        return p

    async def test_match_define(self, plugin):
        m = await plugin.match("define serendipity", CTX)
        assert m.matched
        assert "serendipity" in m.entities

    async def test_match_meaning(self, plugin):
        m = await plugin.match("what does ephemeral mean", CTX)
        assert m.matched

    async def test_match_definition_of(self, plugin):
        m = await plugin.match("definition of ubiquitous", CTX)
        assert m.matched

    async def test_no_match(self, plugin):
        m = await plugin.match("set a timer for 5 minutes", CTX)
        assert not m.matched

    async def test_handle_success(self, plugin):
        api_data = [{
            "word": "test",
            "phonetic": "/tɛst/",
            "meanings": [
                {
                    "partOfSpeech": "noun",
                    "definitions": [{"definition": "A procedure for evaluation."}],
                },
                {
                    "partOfSpeech": "verb",
                    "definitions": [{"definition": "To assess the quality of."}],
                },
            ],
        }]
        mock_resp = _mock_response(200, api_data)

        with patch("cortex.plugins.dictionary.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="define", entities=["test"])
            r = await plugin.handle("define test", match, CTX)
            assert r.success
            assert "Test" in r.response
            assert "noun" in r.response

    async def test_handle_not_found(self, plugin):
        mock_resp = _mock_response(404)

        with patch("cortex.plugins.dictionary.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="define", entities=["xyznotaword"])
            r = await plugin.handle("define xyznotaword", match, CTX)
            assert not r.success


# =====================================================================
# Wikipedia Plugin
# =====================================================================

class TestWikipediaPlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.wikipedia import WikipediaPlugin
        p = WikipediaPlugin()
        await p.setup({})
        return p

    async def test_match_who_is(self, plugin):
        m = await plugin.match("who is Albert Einstein?", CTX)
        assert m.matched

    async def test_match_tell_me(self, plugin):
        m = await plugin.match("tell me about photosynthesis", CTX)
        assert m.matched

    async def test_match_what_is(self, plugin):
        m = await plugin.match("what is quantum computing?", CTX)
        assert m.matched

    async def test_no_match_weather(self, plugin):
        m = await plugin.match("what is the weather today?", CTX)
        assert not m.matched

    async def test_no_match_define(self, plugin):
        m = await plugin.match("what is the definition of love?", CTX)
        assert not m.matched

    async def test_handle_success(self, plugin):
        api_data = {
            "title": "Albert Einstein",
            "extract": "Albert Einstein was a theoretical physicist. He developed the theory of relativity. He received the Nobel Prize in 1921.",
        }
        mock_resp = _mock_response(200, api_data)

        with patch("cortex.plugins.wikipedia.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="lookup", entities=["Albert Einstein"])
            r = await plugin.handle("who is Albert Einstein?", match, CTX)
            assert r.success
            assert "Einstein" in r.response
            # Verify conciseness (max 3 sentences)
            sentences = [s.strip() for s in r.response.split(".") if s.strip()]
            assert len(sentences) <= 3

    async def test_handle_not_found(self, plugin):
        mock_resp = _mock_response(404)

        with patch("cortex.plugins.wikipedia.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="lookup", entities=["xyznonexistent"])
            r = await plugin.handle("who is xyznonexistent?", match, CTX)
            assert not r.success


# =====================================================================
# Conversion Plugin
# =====================================================================

class TestConversionPlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.conversions import ConversionPlugin
        p = ConversionPlugin()
        await p.setup({})
        return p

    # ── match ────────────────────────────────────────────────────

    async def test_match_convert(self, plugin):
        m = await plugin.match("convert 5 miles to km", CTX)
        assert m.matched

    async def test_match_how_many(self, plugin):
        m = await plugin.match("how many cups in a gallon", CTX)
        assert m.matched

    async def test_match_to(self, plugin):
        m = await plugin.match("100 fahrenheit to celsius", CTX)
        assert m.matched

    async def test_no_match(self, plugin):
        m = await plugin.match("what time is it", CTX)
        assert not m.matched

    # ── math accuracy ────────────────────────────────────────────

    async def test_miles_to_km(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["5", "miles", "km"])
        r = await plugin.handle("convert 5 miles to km", match, CTX)
        assert r.success
        assert "8.04" in r.response

    async def test_km_to_miles(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["10", "km", "miles"])
        r = await plugin.handle("convert 10 km to miles", match, CTX)
        assert r.success
        assert "6.21" in r.response

    async def test_lbs_to_kg(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["150", "lbs", "kg"])
        r = await plugin.handle("convert 150 lbs to kg", match, CTX)
        assert r.success
        assert "68" in r.response

    async def test_cups_to_ml(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["2", "cups", "ml"])
        r = await plugin.handle("convert 2 cups to ml", match, CTX)
        assert r.success
        assert "473" in r.response

    async def test_fahrenheit_to_celsius(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["212", "fahrenheit", "celsius"])
        r = await plugin.handle("convert 212 fahrenheit to celsius", match, CTX)
        assert r.success
        assert "100" in r.response

    async def test_celsius_to_fahrenheit(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["0", "celsius", "fahrenheit"])
        r = await plugin.handle("convert 0 celsius to fahrenheit", match, CTX)
        assert r.success
        assert "32" in r.response

    async def test_incompatible_units(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["5", "miles", "kg"])
        r = await plugin.handle("convert 5 miles to kg", match, CTX)
        assert not r.success

    async def test_feet_to_meters(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["100", "feet", "meters"])
        r = await plugin.handle("convert 100 feet to meters", match, CTX)
        assert r.success
        assert "30.48" in r.response

    async def test_gallons_to_liters(self, plugin):
        match = CommandMatch(matched=True, intent="convert", entities=["1", "gallon", "liters"])
        r = await plugin.handle("convert 1 gallon to liters", match, CTX)
        assert r.success
        assert "3.78" in r.response


# =====================================================================
# Movie Plugin
# =====================================================================

class TestMoviePlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.movie import MoviePlugin
        p = MoviePlugin()
        await p.setup({})
        return p

    async def test_match_movie(self, plugin):
        m = await plugin.match("tell me about the movie Inception", CTX)
        assert m.matched

    async def test_match_when_release(self, plugin):
        m = await plugin.match("when did The Matrix come out?", CTX)
        assert m.matched

    async def test_match_who_directed(self, plugin):
        m = await plugin.match("who directed Pulp Fiction?", CTX)
        assert m.matched

    async def test_match_who_starred(self, plugin):
        m = await plugin.match("who starred in Titanic?", CTX)
        assert m.matched

    async def test_no_match(self, plugin):
        m = await plugin.match("set a reminder for tomorrow", CTX)
        assert not m.matched

    # ── handle (no API key → mock) ───────────────────────────────

    async def test_handle_no_key(self, plugin):
        match = CommandMatch(matched=True, intent="movie_lookup", entities=["Inception"])
        r = await plugin.handle("movie Inception", match, CTX)
        assert r.success
        assert "API key" in r.response or "TMDb" in r.response

    # ── handle (with API key, mocked) ────────────────────────────

    async def test_handle_concise(self, plugin):
        """Verify response is CONCISE — no synopsis."""
        plugin._api_key = "test-key"
        api_data = {
            "results": [{
                "title": "The Matrix",
                "release_date": "1999-03-31",
                "vote_average": 8.7,
                "overview": "A computer hacker learns about the true nature of reality.",
            }],
        }
        mock_resp = _mock_response(200, api_data)

        with patch("cortex.plugins.movie.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="movie_release", entities=["The Matrix"])
            r = await plugin.handle("when did The Matrix come out?", match, CTX)
            assert r.success
            assert "1999" in r.response
            # Must NOT include the synopsis/overview
            assert "computer hacker" not in r.response
            # Should be concise (1-2 sentences max)
            sentences = [s.strip() for s in r.response.split(".") if s.strip()]
            assert len(sentences) <= 2

    async def test_handle_not_found(self, plugin):
        plugin._api_key = "test-key"
        api_data = {"results": []}
        mock_resp = _mock_response(200, api_data)

        with patch("cortex.plugins.movie.httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            match = CommandMatch(matched=True, intent="movie_lookup", entities=["xyzfakemovie"])
            r = await plugin.handle("movie xyzfakemovie", match, CTX)
            assert not r.success


# =====================================================================
# Cooking Plugin
# =====================================================================

class TestCookingPlugin:
    @pytest.fixture
    async def plugin(self):
        from cortex.plugins.cooking import CookingPlugin
        p = CookingPlugin()
        await p.setup({})
        return p

    # ── match ────────────────────────────────────────────────────

    async def test_match_temp(self, plugin):
        m = await plugin.match("cooking temp for chicken", CTX)
        assert m.matched
        assert m.intent == "cooking_temp"

    async def test_match_substitute(self, plugin):
        m = await plugin.match("substitute for butter", CTX)
        assert m.matched
        assert m.intent == "substitute"

    async def test_match_measurement(self, plugin):
        m = await plugin.match("how many cups in a gallon?", CTX)
        assert m.matched
        assert m.intent == "measurement"

    async def test_match_cook_time(self, plugin):
        m = await plugin.match("how long to cook chicken breast?", CTX)
        assert m.matched
        assert m.intent == "cook_time"

    async def test_no_match(self, plugin):
        m = await plugin.match("play some music", CTX)
        assert not m.matched

    # ── handle: cooking temperatures ─────────────────────────────

    async def test_chicken_temp(self, plugin):
        match = CommandMatch(matched=True, intent="cooking_temp", entities=["chicken"])
        r = await plugin.handle("cooking temp for chicken", match, CTX)
        assert r.success
        assert "165°F" in r.response

    async def test_beef_temp(self, plugin):
        match = CommandMatch(matched=True, intent="cooking_temp", entities=["beef"])
        r = await plugin.handle("cooking temp for beef", match, CTX)
        assert r.success
        assert "145°F" in r.response

    async def test_pork_temp(self, plugin):
        match = CommandMatch(matched=True, intent="cooking_temp", entities=["pork"])
        r = await plugin.handle("cooking temp for pork", match, CTX)
        assert r.success
        assert "145°F" in r.response

    async def test_fish_temp(self, plugin):
        match = CommandMatch(matched=True, intent="cooking_temp", entities=["fish"])
        r = await plugin.handle("cooking temp for fish", match, CTX)
        assert r.success
        assert "145°F" in r.response

    async def test_unknown_temp(self, plugin):
        match = CommandMatch(matched=True, intent="cooking_temp", entities=["unicorn"])
        r = await plugin.handle("cooking temp for unicorn", match, CTX)
        assert not r.success

    # ── handle: substitutions ────────────────────────────────────

    async def test_butter_substitute(self, plugin):
        match = CommandMatch(matched=True, intent="substitute", entities=["butter"])
        r = await plugin.handle("substitute for butter", match, CTX)
        assert r.success
        assert "oil" in r.response.lower() or "applesauce" in r.response.lower()

    async def test_egg_substitute(self, plugin):
        match = CommandMatch(matched=True, intent="substitute", entities=["egg"])
        r = await plugin.handle("substitute for egg", match, CTX)
        assert r.success
        assert "flaxseed" in r.response.lower() or "applesauce" in r.response.lower()

    async def test_unknown_substitute(self, plugin):
        match = CommandMatch(matched=True, intent="substitute", entities=["plutonium"])
        r = await plugin.handle("substitute for plutonium", match, CTX)
        assert not r.success

    # ── handle: measurements ─────────────────────────────────────

    async def test_cups_in_gallon(self, plugin):
        match = CommandMatch(matched=True, intent="measurement", entities=["cups in a gallon"])
        r = await plugin.handle("how many cups in a gallon?", match, CTX)
        assert r.success
        assert "16" in r.response

    async def test_tsp_in_tbsp(self, plugin):
        match = CommandMatch(matched=True, intent="measurement", entities=["teaspoons in a tablespoon"])
        r = await plugin.handle("how many teaspoons in a tablespoon?", match, CTX)
        assert r.success
        assert "3" in r.response

    # ── handle: cook times ───────────────────────────────────────

    async def test_chicken_breast_time(self, plugin):
        match = CommandMatch(matched=True, intent="cook_time", entities=["chicken breast"])
        r = await plugin.handle("how long to cook chicken breast?", match, CTX)
        assert r.success
        assert "400°F" in r.response or "min" in r.response

    async def test_rice_time(self, plugin):
        match = CommandMatch(matched=True, intent="cook_time", entities=["rice"])
        r = await plugin.handle("how long to cook rice?", match, CTX)
        assert r.success
        assert "min" in r.response or "minutes" in r.response.lower()
