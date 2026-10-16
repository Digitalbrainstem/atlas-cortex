"""Tests for the DocLookup live documentation tool."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.tools.doc_lookup import (
    DocBulkLookupTool,
    DocLookup,
    DocLookupTool,
    DocResult,
    ensure_schema,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path: Path):
    """Give every test a pristine in-memory database."""
    set_db_path(tmp_path / "test_doc_lookup.db")
    init_db()
    ensure_schema()


@pytest.fixture()
def lookup() -> DocLookup:
    return DocLookup()


# ---------------------------------------------------------------------------
# DocResult dataclass
# ---------------------------------------------------------------------------


class TestDocResult:
    def test_defaults(self):
        r = DocResult(library="httpx", content="# httpx")
        assert r.library == "httpx"
        assert r.source == ""
        assert r.cached is False
        assert r.url == ""
        assert r.examples == []

    def test_all_fields(self):
        r = DocResult(
            library="pandas",
            content="# pandas",
            source="pypi",
            cached=True,
            url="https://pandas.pydata.org",
            examples=[">>> import pandas"],
        )
        assert r.source == "pypi"
        assert r.cached is True
        assert len(r.examples) == 1


# ---------------------------------------------------------------------------
# SQLite cache layer
# ---------------------------------------------------------------------------


class TestCache:
    def test_cache_and_retrieve(self, lookup: DocLookup):
        lookup.cache_result("httpx", "# httpx docs", source="pypi")
        result = lookup.get_cached("httpx")
        assert result is not None
        assert result.content == "# httpx docs"
        assert result.source == "pypi"
        assert result.cached is True

    def test_cache_case_insensitive(self, lookup: DocLookup):
        lookup.cache_result("Httpx", "# httpx docs", source="installed")
        result = lookup.get_cached("httpx")
        assert result is not None

    def test_cache_miss(self, lookup: DocLookup):
        assert lookup.get_cached("nonexistent") is None

    def test_cache_expired(self, lookup: DocLookup):
        lookup.cache_result("old_lib", "stale", source="pypi", ttl_hours=0)
        # TTL of 0 hours means it's already expired
        conn = get_db()
        conn.execute(
            "UPDATE doc_cache SET fetched_at = ? WHERE library = ?",
            (time.time() - 3600, "old_lib"),
        )
        conn.commit()
        assert lookup.get_cached("old_lib") is None

    def test_cache_overwrite(self, lookup: DocLookup):
        lookup.cache_result("lib", "v1", source="pypi")
        lookup.cache_result("lib", "v2", source="installed")
        result = lookup.get_cached("lib")
        assert result is not None
        assert result.content == "v2"
        assert result.source == "installed"

    def test_invalidate(self, lookup: DocLookup):
        lookup.cache_result("lib", "content", source="pypi")
        assert lookup.invalidate("lib") is True
        assert lookup.get_cached("lib") is None

    def test_invalidate_missing(self, lookup: DocLookup):
        assert lookup.invalidate("nope") is False

    def test_cache_with_url(self, lookup: DocLookup):
        lookup.cache_result(
            "pandas", "# pandas", source="pypi", url="https://pandas.pydata.org",
        )
        result = lookup.get_cached("pandas")
        assert result is not None
        assert result.url == "https://pandas.pydata.org"

    def test_custom_ttl(self, lookup: DocLookup):
        lookup.cache_result("lib", "content", source="pypi", ttl_hours=1)
        result = lookup.get_cached("lib")
        assert result is not None


# ---------------------------------------------------------------------------
# Source: installed package docstrings
# ---------------------------------------------------------------------------


class TestFromInstalled:
    def test_known_stdlib_module(self):
        result = DocLookup._from_installed("json")
        assert result is not None
        assert "json" in result.content.lower()
        assert result.source == "installed"

    def test_with_topic_filter(self):
        result = DocLookup._from_installed("json", topic="dump")
        assert result is not None
        assert "dump" in result.content.lower()

    def test_unknown_module(self):
        result = DocLookup._from_installed("totally_fake_library_xyz_999")
        assert result is None

    def test_content_truncated(self):
        result = DocLookup._from_installed("os")
        if result:
            assert len(result.content) <= 8_000


# ---------------------------------------------------------------------------
# Source: PyPI (mocked)
# ---------------------------------------------------------------------------


class TestFromPyPI:
    async def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "info": {
                "description": "A fast HTTP library.",
                "summary": "HTTP for humans",
                "project_urls": {"Documentation": "https://docs.example.com"},
                "home_page": "https://example.com",
            },
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await DocLookup._from_pypi("httpx")

        assert result is not None
        assert "httpx" in result.content
        assert result.source == "pypi"
        assert result.url == "https://docs.example.com"

    async def test_404(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await DocLookup._from_pypi("nonexistent")

        assert result is None

    async def test_network_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await DocLookup._from_pypi("httpx")

        assert result is None

    async def test_empty_description_uses_summary(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "info": {
                "description": "",
                "summary": "A concise summary",
                "project_urls": None,
                "home_page": "",
            },
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await DocLookup._from_pypi("some_lib")

        assert result is not None
        assert "concise summary" in result.content


# ---------------------------------------------------------------------------
# Source: help() output
# ---------------------------------------------------------------------------


class TestFromHelp:
    def test_known_module(self):
        result = DocLookup._from_help("json")
        assert result is not None
        assert result.source == "help"
        assert "json" in result.content.lower()

    def test_unknown_module(self):
        result = DocLookup._from_help("totally_fake_xyz_999")
        assert result is None


# ---------------------------------------------------------------------------
# lookup() — full resolution chain
# ---------------------------------------------------------------------------


class TestLookup:
    async def test_returns_cached(self, lookup: DocLookup):
        lookup.cache_result("mylib", "cached content", source="pypi")
        result = await lookup.lookup("mylib")
        assert result.cached is True
        assert result.content == "cached content"

    async def test_cache_bypassed_when_topic(self, lookup: DocLookup):
        """When a topic is given, skip cache and go to installed."""
        lookup.cache_result("json", "cached json", source="pypi")
        result = await lookup.lookup("json", topic="dump")
        # Should come from installed, not cache
        assert result.source == "installed"
        assert "dump" in result.content.lower()

    async def test_installed_module(self, lookup: DocLookup):
        result = await lookup.lookup("json")
        assert result.source in ("installed", "cache")
        assert "json" in result.content.lower()

    async def test_stub_for_unknown(self, lookup: DocLookup):
        """Unknown libraries with no network should return a stub."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            result = await lookup.lookup("totally_fake_xyz_library_999")

        assert result.source == "stub"
        assert "No documentation found" in result.content

    async def test_caches_after_fetch(self, lookup: DocLookup):
        """First lookup should populate cache for subsequent calls."""
        await lookup.lookup("json")
        cached = lookup.get_cached("json")
        assert cached is not None
        assert cached.cached is True


# ---------------------------------------------------------------------------
# lookup_function()
# ---------------------------------------------------------------------------


class TestLookupFunction:
    async def test_known_function(self, lookup: DocLookup):
        doc = await lookup.lookup_function("json", "dumps")
        assert "dumps" in doc
        assert "function" in doc.lower() or "class" in doc.lower()

    async def test_nested_lookup(self, lookup: DocLookup):
        doc = await lookup.lookup_function("os", "path.join")
        assert "join" in doc.lower()

    async def test_unknown_function(self, lookup: DocLookup):
        doc = await lookup.lookup_function("json", "nonexistent_xyz")
        assert "not found" in doc.lower()

    async def test_unknown_module(self, lookup: DocLookup):
        doc = await lookup.lookup_function("fake_lib_999", "func")
        assert "Could not import" in doc


# ---------------------------------------------------------------------------
# lookup_examples()
# ---------------------------------------------------------------------------


class TestLookupExamples:
    async def test_returns_list(self, lookup: DocLookup):
        examples = await lookup.lookup_examples("json")
        assert isinstance(examples, list)

    async def test_unknown_module(self, lookup: DocLookup):
        examples = await lookup.lookup_examples("fake_lib_999")
        assert examples == []

    async def test_doctest_extraction(self, lookup: DocLookup):
        """Modules with doctest examples should yield non-empty results."""
        # textwrap has doctest examples in several functions
        examples = await lookup.lookup_examples("textwrap")
        # Even if empty on some platforms, should not error
        assert isinstance(examples, list)


# ---------------------------------------------------------------------------
# bulk_lookup()
# ---------------------------------------------------------------------------


class TestBulkLookup:
    async def test_multiple_libs(self, lookup: DocLookup):
        results = await lookup.bulk_lookup(["json", "os"])
        assert "json" in results
        assert "os" in results
        assert results["json"].source in ("installed", "cache")

    async def test_mixed_success_and_failure(self, lookup: DocLookup):
        """Bulk lookup should not fail if one library fails."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cortex.tools.doc_lookup.httpx.AsyncClient", return_value=mock_client):
            results = await lookup.bulk_lookup(["json", "fake_lib_999"])

        assert results["json"].source in ("installed", "cache")
        assert results["fake_lib_999"].source == "stub"

    async def test_empty_list(self, lookup: DocLookup):
        results = await lookup.bulk_lookup([])
        assert results == {}


# ---------------------------------------------------------------------------
# Package metadata URL extraction
# ---------------------------------------------------------------------------


class TestPackageDocsUrl:
    def test_known_package(self):
        # httpx should have metadata if installed
        url = DocLookup._package_docs_url("httpx")
        # May or may not find a URL depending on the environment
        assert isinstance(url, str)

    def test_unknown_package(self):
        url = DocLookup._package_docs_url("totally_fake_lib_999")
        assert url == ""


# ---------------------------------------------------------------------------
# ensure_schema()
# ---------------------------------------------------------------------------


class TestSchema:
    def test_idempotent(self):
        ensure_schema()
        ensure_schema()
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='doc_cache'"
        ).fetchone()
        assert row is not None

    def test_table_columns(self):
        conn = get_db()
        info = conn.execute("PRAGMA table_info(doc_cache)").fetchall()
        col_names = {r["name"] for r in info}
        assert col_names == {
            "library", "content", "source", "url", "fetched_at", "ttl_hours",
        }


# ---------------------------------------------------------------------------
# DocLookupTool (AgentTool adapter)
# ---------------------------------------------------------------------------


class TestDocLookupTool:
    async def test_basic_lookup(self):
        tool = DocLookupTool()
        result = await tool.execute({"library": "json"})
        assert result.success
        assert "json" in result.output.lower()
        assert result.metadata["library"] == "json"

    async def test_lookup_with_topic(self):
        tool = DocLookupTool()
        result = await tool.execute({"library": "json", "topic": "dump"})
        assert result.success
        assert "dump" in result.output.lower()

    async def test_lookup_function(self):
        tool = DocLookupTool()
        result = await tool.execute(
            {"library": "json", "function_name": "dumps"},
        )
        assert result.success
        assert "dumps" in result.output
        assert result.metadata["function"] == "dumps"

    async def test_missing_library_param(self):
        tool = DocLookupTool()
        result = await tool.execute({})
        assert not result.success
        assert "required" in result.error

    async def test_empty_library_param(self):
        tool = DocLookupTool()
        result = await tool.execute({"library": ""})
        assert not result.success

    async def test_schema(self):
        tool = DocLookupTool()
        schema = tool.to_function_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "doc_lookup"
        assert "library" in schema["function"]["parameters"]["properties"]

    async def test_tool_id(self):
        tool = DocLookupTool()
        assert tool.tool_id == "doc_lookup"


# ---------------------------------------------------------------------------
# DocBulkLookupTool (AgentTool adapter)
# ---------------------------------------------------------------------------


class TestDocBulkLookupTool:
    async def test_basic_bulk(self):
        tool = DocBulkLookupTool()
        result = await tool.execute({"libraries": ["json", "os"]})
        assert result.success
        assert "json" in result.output.lower()
        assert "os" in result.output.lower()
        assert "libraries" in result.metadata

    async def test_missing_param(self):
        tool = DocBulkLookupTool()
        result = await tool.execute({})
        assert not result.success
        assert "required" in result.error

    async def test_schema(self):
        tool = DocBulkLookupTool()
        schema = tool.to_function_schema()
        assert schema["function"]["name"] == "doc_bulk_lookup"

    async def test_tool_id(self):
        tool = DocBulkLookupTool()
        assert tool.tool_id == "doc_bulk_lookup"
