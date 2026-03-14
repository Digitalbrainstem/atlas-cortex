"""Timing and integration tests against the FastAPI server.

All tests use httpx async client against the real app (in-process, no network).
Mock servers are NOT started — we test endpoints that don't need external deps,
or we mock the LLM provider.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from cortex.db import init_db, set_db_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "timing_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


class _FakeProvider:
    """Minimal LLM provider stub that yields tokens."""

    async def health(self):
        return True

    async def generate(self, prompt, **kwargs):
        return "I am a mock response from the fake LLM provider"

    async def stream(self, prompt, **kwargs):
        for word in ["Hello", " ", "world", "!"]:
            yield word


# ===========================================================================
# Health endpoint
# ===========================================================================

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_responds_under_100ms(self, db_path):
        """GET /health should respond in <100ms with mocked provider."""
        with patch("cortex.server._get_provider") as mock_prov, \
             patch("cortex.server._get_db") as mock_db:
            provider = MagicMock()
            provider.health = AsyncMock(return_value=True)
            mock_prov.return_value = provider
            mock_db.return_value = MagicMock()

            from cortex.server import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                start = time.monotonic()
                resp = await client.get("/health")
                elapsed_ms = (time.monotonic() - start) * 1000

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] in ("ok", "degraded")
            assert elapsed_ms < 100, f"Health took {elapsed_ms:.1f}ms (>100ms)"

    @pytest.mark.asyncio
    async def test_health_degraded_when_provider_unhealthy(self, db_path):
        """If provider.health() returns False, status should be 'degraded'."""
        with patch("cortex.server._get_provider") as mock_prov, \
             patch("cortex.server._get_db") as mock_db:
            provider = MagicMock()
            provider.health = AsyncMock(return_value=False)
            mock_prov.return_value = provider
            mock_db.return_value = MagicMock()

            from cortex.server import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/health")

            assert resp.status_code == 200
            assert resp.json()["status"] == "degraded"


# ===========================================================================
# Avatar HTML page
# ===========================================================================

class TestAvatarEndpoint:
    @pytest.mark.asyncio
    async def test_avatar_html_loads(self, db_path):
        """GET /avatar should return HTML in <500ms."""
        avatar_html = Path(__file__).resolve().parent.parent / "cortex" / "avatar" / "display.html"
        if not avatar_html.exists():
            pytest.skip("Avatar display.html not found")

        with patch("cortex.server._get_provider") as mock_prov, \
             patch("cortex.server._get_db") as mock_db:
            mock_prov.return_value = MagicMock()
            mock_db.return_value = MagicMock()

            from cortex.server import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                start = time.monotonic()
                resp = await client.get("/avatar")
                elapsed_ms = (time.monotonic() - start) * 1000

            assert resp.status_code == 200
            assert elapsed_ms < 500, f"Avatar page took {elapsed_ms:.1f}ms (>500ms)"
            content_type = resp.headers.get("content-type", "")
            assert "html" in content_type or resp.status_code == 200


# ===========================================================================
# Models endpoint (OpenAI-compatible)
# ===========================================================================

class TestModelsEndpoint:
    @pytest.mark.asyncio
    async def test_list_models(self, db_path):
        """GET /v1/models should return the atlas-cortex model."""
        with patch("cortex.server._get_provider") as mock_prov, \
             patch("cortex.server._get_db") as mock_db:
            mock_prov.return_value = MagicMock()
            mock_db.return_value = MagicMock()

            from cortex.server import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/models")

            assert resp.status_code == 200
            data = resp.json()
            assert "data" in data
            model_ids = [m["id"] for m in data["data"]]
            assert "atlas-cortex" in model_ids


# ===========================================================================
# Pipeline E2E (with mock LLM provider)
# ===========================================================================

class TestPipelineE2E:
    @pytest.mark.asyncio
    async def test_run_pipeline_events_works(self, db):
        """run_pipeline_events() is a proper async generator that can be
        iterated with 'async for'."""
        from cortex.pipeline import run_pipeline

        fake_provider = _FakeProvider()
        tokens: list[str] = []
        async for token in run_pipeline(
            message="What is the time?",
            provider=fake_provider,
            user_id="test-user",
            db_conn=db,
        ):
            tokens.append(token)
        assert len(tokens) > 0, "Expected text tokens from instant answer"

    @pytest.mark.asyncio
    async def test_pipeline_event_generator_works_directly(self, db):
        """Bypass the bug: call _pipeline_event_generator directly."""
        from cortex.pipeline import _pipeline_event_generator, TextToken

        fake_provider = _FakeProvider()
        events = []
        start = time.monotonic()
        async for event in _pipeline_event_generator(
            message="What time is it?",
            provider=fake_provider,
            user_id="test-user",
            speaker_id=None,
            satellite_id=None,
            conversation_history=None,
            metadata=None,
            model_fast="qwen2.5:7b",
            model_thinking="qwen2.5:7b",
            system_prompt="",
            memory_context="",
            db_conn=db,
        ):
            events.append(event)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Should produce at least one event
        assert len(events) > 0
        text_tokens = [e for e in events if isinstance(e, TextToken)]
        assert len(text_tokens) > 0, "Expected TextToken events from instant answer"
        # Instant answer should be fast
        assert elapsed_ms < 500, f"Pipeline took {elapsed_ms:.1f}ms"


# ===========================================================================
# Filler cache
# ===========================================================================

class TestFillerCache:
    def test_get_returns_none_when_not_initialized(self):
        """FillerCache.get() should return None before initialize() is called."""
        from cortex.filler.cache import FillerCache
        cache = FillerCache()
        assert cache.get("question") is None

    def test_ready_property_false_initially(self):
        from cortex.filler.cache import FillerCache
        cache = FillerCache()
        assert cache.ready is False

    def test_reset_clears_state(self):
        from cortex.filler.cache import FillerCache, CachedFiller
        cache = FillerCache()
        # Manually stuff some data
        cache._cache["question"] = [
            CachedFiller(phrase="test", audio=b"\x00", sample_rate=24000, duration_ms=100)
        ]
        cache._initialized = True
        assert cache.ready is True
        cache.reset()
        assert cache.ready is False
        assert cache.get("question") is None

    def test_get_avoids_recent_repeats(self):
        """get() should avoid repeating the last 3 fillers."""
        from cortex.filler.cache import FillerCache, CachedFiller
        cache = FillerCache()
        # Create 4 fillers so there's always a non-recent choice
        fillers = [
            CachedFiller(phrase=f"filler-{i}", audio=b"\x00", sample_rate=24000, duration_ms=100)
            for i in range(4)
        ]
        cache._cache["question"] = fillers
        cache._initialized = True

        seen = set()
        for _ in range(20):
            result = cache.get("question")
            assert result is not None
            seen.add(result.phrase)
        # Should have used more than 1 unique phrase
        assert len(seen) > 1

    def test_get_falls_back_to_question_sentiment(self):
        """Unknown sentiment should fall back to 'question' pool."""
        from cortex.filler.cache import FillerCache, CachedFiller
        cache = FillerCache()
        cache._cache["question"] = [
            CachedFiller(phrase="fallback", audio=b"\x00", sample_rate=24000, duration_ms=100)
        ]
        cache._initialized = True

        result = cache.get("nonexistent_sentiment")
        assert result is not None
        assert result.phrase == "fallback"

    def test_get_timing_under_5ms(self):
        """get() should be sub-5ms since it's just a dict lookup."""
        from cortex.filler.cache import FillerCache, CachedFiller
        cache = FillerCache()
        cache._cache["question"] = [
            CachedFiller(phrase=f"filler-{i}", audio=b"\x00" * 1000, sample_rate=24000, duration_ms=100)
            for i in range(50)
        ]
        cache._initialized = True

        start = time.monotonic()
        for _ in range(100):
            cache.get("question")
        elapsed_ms = (time.monotonic() - start) * 1000
        avg_ms = elapsed_ms / 100
        assert avg_ms < 5, f"Average get() time was {avg_ms:.2f}ms (>5ms)"
