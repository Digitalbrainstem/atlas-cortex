"""Live documentation lookup tool for Atlas Cortex.

Fetches real, current documentation for any Python library — similar to
Context7.  Works offline after first fetch (SQLite-backed cache) and falls
back gracefully through multiple sources.

Sources (priority order):
1. SQLite cache (if fresh)
2. Installed package docstrings (importlib + inspect)
3. PyPI project page (httpx, parse description + metadata URLs)
4. ReadTheDocs / GitHub README (from package metadata)
5. help() output summary
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import inspect
import io
import logging
import sqlite3
import time
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import httpx

from cortex.db import get_db

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_DOC_LEN = 8_000  # chars per library
_MAX_FUNCTION_DOC_LEN = 4_000
_DEFAULT_TTL_HOURS = 168  # 1 week
_PYPI_JSON = "https://pypi.org/pypi/{lib}/json"
_HTTP_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DocResult:
    """Documentation result for a library or function."""

    library: str
    content: str
    source: str = ""  # "cache" | "installed" | "pypi" | "readthedocs" | "help" | "stub"
    cached: bool = False
    url: str = ""
    examples: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS doc_cache (
    library    TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    fetched_at REAL NOT NULL,
    ttl_hours  INTEGER NOT NULL DEFAULT 168
);
"""


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create the doc_cache table if it doesn't exist."""
    c = conn or get_db()
    c.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# DocLookup
# ---------------------------------------------------------------------------


class DocLookup:
    """Fetch real, current documentation for any Python library."""

    def __init__(self, *, ttl_hours: int = _DEFAULT_TTL_HOURS) -> None:
        self._ttl_hours = ttl_hours
        self._schema_ready = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        if not self._schema_ready:
            ensure_schema()
            self._schema_ready = True

    # ------------------------------------------------------------------
    # Cache layer (SQLite)
    # ------------------------------------------------------------------

    def cache_result(
        self,
        library: str,
        content: str,
        *,
        source: str = "",
        url: str = "",
        ttl_hours: int | None = None,
    ) -> None:
        """Cache docs in SQLite. Default 1-week TTL."""
        self._ensure_schema()
        ttl = ttl_hours if ttl_hours is not None else self._ttl_hours
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO doc_cache "
            "(library, content, source, url, fetched_at, ttl_hours) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (library.lower(), content, source, url, time.time(), ttl),
        )
        conn.commit()

    def get_cached(self, library: str) -> DocResult | None:
        """Return cached docs if fresh, else None."""
        self._ensure_schema()
        conn = get_db()
        row = conn.execute(
            "SELECT library, content, source, url, fetched_at, ttl_hours "
            "FROM doc_cache WHERE library = ?",
            (library.lower(),),
        ).fetchone()
        if row is None:
            return None
        age_hours = (time.time() - row["fetched_at"]) / 3600
        if age_hours > row["ttl_hours"]:
            return None
        return DocResult(
            library=row["library"],
            content=row["content"],
            source=row["source"],
            cached=True,
            url=row["url"],
        )

    def invalidate(self, library: str) -> bool:
        """Remove a library from cache. Returns True if it existed."""
        self._ensure_schema()
        conn = get_db()
        cur = conn.execute(
            "DELETE FROM doc_cache WHERE library = ?", (library.lower(),)
        )
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Source 2: installed package docstrings
    # ------------------------------------------------------------------

    @staticmethod
    def _from_installed(library: str, topic: str | None = None) -> DocResult | None:
        """Extract docstrings from an installed package."""
        try:
            mod = importlib.import_module(library)
        except Exception:
            return None

        parts: list[str] = []
        mod_doc = inspect.getdoc(mod)
        if mod_doc:
            parts.append(f"# {library}\n\n{mod_doc}")

        topic_lower = topic.lower() if topic else None

        for name in sorted(dir(mod)):
            if name.startswith("_"):
                continue
            # When a topic is given, skip unrelated symbols
            if topic_lower and topic_lower not in name.lower():
                continue
            obj = getattr(mod, name, None)
            if obj is None:
                continue
            doc = inspect.getdoc(obj)
            if not doc:
                continue
            kind = "class" if inspect.isclass(obj) else "function"
            sig = ""
            try:
                sig = str(inspect.signature(obj))
            except (ValueError, TypeError):
                pass
            parts.append(f"## {kind} {name}{sig}\n\n{doc}")

        if not parts:
            return None
        text = "\n\n".join(parts)[:_MAX_DOC_LEN]
        return DocResult(library=library, content=text, source="installed")

    # ------------------------------------------------------------------
    # Source 3: PyPI project page
    # ------------------------------------------------------------------

    @staticmethod
    async def _from_pypi(library: str) -> DocResult | None:
        """Fetch the project description from PyPI JSON API."""
        url = _PYPI_JSON.format(lib=library)
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                info = data.get("info", {})
                desc = info.get("description", "")
                if not desc:
                    desc = info.get("summary", "")
                if not desc:
                    return None

                # Extract useful URLs from metadata
                project_urls = info.get("project_urls") or {}
                doc_url = (
                    project_urls.get("Documentation")
                    or project_urls.get("documentation")
                    or project_urls.get("Docs")
                    or project_urls.get("Homepage")
                    or info.get("home_page", "")
                    or ""
                )

                content = f"# {library} (PyPI)\n\n{desc[:_MAX_DOC_LEN]}"
                return DocResult(
                    library=library, content=content, source="pypi", url=doc_url,
                )
        except Exception as exc:
            log.debug("PyPI fetch failed for %s: %s", library, exc)
            return None

    # ------------------------------------------------------------------
    # Source 4: ReadTheDocs / GitHub README
    # ------------------------------------------------------------------

    @staticmethod
    async def _from_docs_url(library: str, url: str) -> DocResult | None:
        """Fetch content from a docs URL (ReadTheDocs or GitHub README)."""
        if not url:
            return None
        try:
            # For GitHub repos, try the raw README
            if "github.com" in url:
                # Convert github.com/owner/repo to raw README URL
                parts = url.rstrip("/").split("/")
                if len(parts) >= 5:
                    owner, repo = parts[3], parts[4]
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
                    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                        resp = await client.get(raw_url)
                        if resp.status_code == 200:
                            content = f"# {library} (GitHub README)\n\n{resp.text[:_MAX_DOC_LEN]}"
                            return DocResult(
                                library=library, content=content,
                                source="readthedocs", url=url,
                            )

            # Generic docs page fetch
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and "text/" in resp.headers.get(
                    "content-type", ""
                ):
                    text = resp.text[:_MAX_DOC_LEN]
                    return DocResult(
                        library=library,
                        content=f"# {library} (docs)\n\n{text}",
                        source="readthedocs",
                        url=url,
                    )
        except Exception as exc:
            log.debug("Docs URL fetch failed for %s (%s): %s", library, url, exc)
        return None

    # ------------------------------------------------------------------
    # Source 5: help() output
    # ------------------------------------------------------------------

    @staticmethod
    def _from_help(library: str) -> DocResult | None:
        """Generate summary from installed package's help() output."""
        try:
            mod = importlib.import_module(library)
        except Exception:
            return None
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                help(mod)
        except Exception:
            return None
        text = buf.getvalue()
        if not text:
            return None
        content = f"# {library} (help)\n\n{text[:_MAX_DOC_LEN]}"
        return DocResult(library=library, content=content, source="help")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup(
        self, library: str, topic: str | None = None,
    ) -> DocResult:
        """Fetch docs for a library. Optional topic narrows the search.

        Sources (in priority order):
        1. Local cache (SQLite, checked first)
        2. Installed package docstrings (importlib + inspect)
        3. PyPI project page (httpx fetch, parse description)
        4. ReadTheDocs / GitHub README (if URL in package metadata)
        5. Generate summary from installed package's help() output
        """
        lib = library.lower()

        # 1. Cache
        cached = self.get_cached(lib)
        if cached and not topic:
            return cached

        # 2. Installed docstrings
        result = self._from_installed(lib, topic)
        if result:
            if not topic:
                self.cache_result(lib, result.content, source=result.source)
            return result

        # 3. PyPI
        result = await self._from_pypi(lib)
        doc_url = ""
        if result:
            doc_url = result.url
            self.cache_result(
                lib, result.content, source=result.source, url=doc_url,
            )
            return result

        # 4. ReadTheDocs / GitHub README — try metadata URL
        if not doc_url:
            doc_url = self._package_docs_url(lib)
        result = await self._from_docs_url(lib, doc_url)
        if result:
            self.cache_result(
                lib, result.content, source=result.source, url=doc_url,
            )
            return result

        # 5. help() fallback
        result = self._from_help(lib)
        if result:
            self.cache_result(lib, result.content, source=result.source)
            return result

        # 6. Stub
        stub_content = (
            f"# {lib}\n\nNo documentation found. "
            "Use best knowledge of the library."
        )
        return DocResult(library=lib, content=stub_content, source="stub")

    async def lookup_function(
        self, library: str, function_name: str,
    ) -> str:
        """Get docs for a specific function/class."""
        try:
            mod = importlib.import_module(library)
        except Exception:
            return f"Could not import {library}."

        obj = getattr(mod, function_name, None)
        if obj is None:
            # Try nested lookup (e.g. "path.join" → module.subattr)
            parts = function_name.split(".")
            obj = mod
            for part in parts:
                obj = getattr(obj, part, None)
                if obj is None:
                    return f"{function_name} not found in {library}."

        doc = inspect.getdoc(obj) or "No docstring available."
        sig = ""
        try:
            sig = str(inspect.signature(obj))
        except (ValueError, TypeError):
            pass

        kind = "class" if inspect.isclass(obj) else "function"
        return f"## {kind} {function_name}{sig}\n\n{doc}"[:_MAX_FUNCTION_DOC_LEN]

    async def lookup_examples(self, library: str) -> list[str]:
        """Get usage examples for a library from its docstrings."""
        try:
            mod = importlib.import_module(library)
        except Exception:
            return []

        examples: list[str] = []
        for name in sorted(dir(mod)):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name, None)
            if obj is None:
                continue
            doc = inspect.getdoc(obj) or ""
            # Extract doctest-style examples
            in_example = False
            current: list[str] = []
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(">>>"):
                    in_example = True
                    current.append(stripped)
                elif in_example:
                    if stripped and not stripped.startswith(">>>"):
                        current.append(stripped)
                    else:
                        if current:
                            examples.append("\n".join(current))
                            current = []
                        if stripped.startswith(">>>"):
                            current.append(stripped)
                        else:
                            in_example = False
            if current:
                examples.append("\n".join(current))

        return examples

    async def bulk_lookup(
        self, libraries: list[str],
    ) -> dict[str, DocResult]:
        """Fetch docs for multiple libraries in parallel."""
        tasks = [self.lookup(lib) for lib in libraries]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, DocResult] = {}
        for lib, res in zip(libraries, results):
            if isinstance(res, Exception):
                log.warning("bulk_lookup failed for %s: %s", lib, res)
                out[lib] = DocResult(
                    library=lib,
                    content=f"# {lib}\n\nLookup failed: {res}",
                    source="stub",
                )
            else:
                out[lib] = res
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _package_docs_url(library: str) -> str:
        """Try to extract a documentation URL from installed package metadata."""
        try:
            meta = importlib.metadata.metadata(library)
            # project-url entries look like "Documentation, https://..."
            urls = meta.get_all("Project-URL") or []
            for entry in urls:
                label, _, url = entry.partition(",")
                label = label.strip().lower()
                url = url.strip()
                if label in ("documentation", "docs", "homepage"):
                    return url
            home = meta.get("Home-page", "")
            if home:
                return home
        except Exception:
            pass
        return ""


# ---------------------------------------------------------------------------
# AgentTool adapter — makes DocLookup usable via ToolRegistry
# ---------------------------------------------------------------------------

# Import here to avoid circular imports at module level
from cortex.cli.tools import AgentTool, ToolResult  # noqa: E402


class DocLookupTool(AgentTool):
    """Agent tool wrapper for DocLookup."""

    tool_id = "doc_lookup"
    description = (
        "Fetch real, current documentation for a Python library. "
        "Returns condensed API docs, optionally filtered by topic or function name. "
        "Works offline after first fetch."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "library": {
                "type": "string",
                "description": "Python library/package name (e.g. 'httpx', 'pandas').",
            },
            "topic": {
                "type": "string",
                "description": "Optional topic to narrow the search (e.g. 'async', 'DataFrame').",
            },
            "function_name": {
                "type": "string",
                "description": "Specific function or class name to look up.",
            },
        },
        "required": ["library"],
    }

    def __init__(self) -> None:
        self._lookup = DocLookup()

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute the doc_lookup tool."""
        library = params.get("library", "").strip()
        if not library:
            return ToolResult(
                success=False, output="", error="library parameter is required",
            )

        function_name = params.get("function_name", "")
        topic = params.get("topic", "")

        try:
            if function_name:
                content = await self._lookup.lookup_function(library, function_name)
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={"library": library, "function": function_name},
                )

            result = await self._lookup.lookup(library, topic=topic or None)
            return ToolResult(
                success=True,
                output=result.content,
                metadata={
                    "library": result.library,
                    "source": result.source,
                    "cached": result.cached,
                    "url": result.url,
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False, output="", error=f"Doc lookup failed: {exc}",
            )


class DocBulkLookupTool(AgentTool):
    """Agent tool for fetching docs for multiple libraries at once."""

    tool_id = "doc_bulk_lookup"
    description = (
        "Fetch documentation for multiple Python libraries in parallel. "
        "Returns condensed API docs for each library."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "libraries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of library names to look up.",
            },
        },
        "required": ["libraries"],
    }

    def __init__(self) -> None:
        self._lookup = DocLookup()

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute the doc_bulk_lookup tool."""
        libraries = params.get("libraries", [])
        if not libraries:
            return ToolResult(
                success=False, output="", error="libraries parameter is required",
            )

        try:
            results = await self._lookup.bulk_lookup(libraries)
            parts = []
            for lib, result in results.items():
                parts.append(f"--- {lib} ({result.source}) ---\n{result.content}")
            output = "\n\n".join(parts)
            return ToolResult(
                success=True,
                output=output,
                metadata={
                    "libraries": list(results.keys()),
                    "sources": {lib: r.source for lib, r in results.items()},
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False, output="", error=f"Bulk doc lookup failed: {exc}",
            )
