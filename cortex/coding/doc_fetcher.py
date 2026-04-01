"""Fetch real documentation for libraries before coding."""
from __future__ import annotations

import importlib
import inspect
import logging
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DocEntry:
    """Cached documentation for a single library."""

    library: str
    content: str
    source: str = ""  # "pypi" | "installed" | "readthedocs" | "stub"
    fetched_at: float = 0.0


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

_READTHEDOCS = "https://{lib}.readthedocs.io/en/latest/"
_PYPI_JSON = "https://pypi.org/pypi/{lib}/json"

# Max chars per library doc to keep context manageable
_MAX_DOC_LEN = 4_000
_CACHE_TTL = 86_400  # 1 day


class DocFetcher:
    """Fetch real documentation for libraries before coding.

    Resolution order per library:
    1. In-memory cache (if fresh)
    2. Installed package docstrings
    3. PyPI project description
    4. Stub fallback
    """

    def __init__(self, cache_ttl: int = _CACHE_TTL) -> None:
        self._cache: dict[str, DocEntry] = {}
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch(self, libraries: list[str]) -> dict[str, str]:
        """Fetch docs for every library. Returns ``{name: doc_text}``."""
        result: dict[str, str] = {}
        for lib in libraries:
            result[lib] = await self.fetch_one(lib)
        return result

    async def fetch_one(self, library: str) -> str:
        """Fetch docs for a single library, using cache when possible."""
        cached = self._cache.get(library)
        if cached and (time.time() - cached.fetched_at) < self._cache_ttl:
            log.debug("Doc cache hit: %s (source=%s)", library, cached.source)
            return cached.content

        # 1. Try installed package docstrings
        doc = self._from_installed(library)
        if doc:
            self._store(library, doc, "installed")
            return doc

        # 2. Try PyPI description
        doc = await self._from_pypi(library)
        if doc:
            self._store(library, doc, "pypi")
            return doc

        # 3. Stub
        stub = f"# {library}\n\nNo documentation found. Use best knowledge of the library."
        self._store(library, stub, "stub")
        return stub

    def clear_cache(self) -> None:
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _from_installed(self, library: str) -> str | None:
        """Extract docstrings from an installed package."""
        try:
            mod = importlib.import_module(library)
        except Exception:
            return None

        parts: list[str] = []
        mod_doc = inspect.getdoc(mod)
        if mod_doc:
            parts.append(f"# {library}\n\n{mod_doc}")

        # Collect public class/function docs
        for name in sorted(dir(mod)):
            if name.startswith("_"):
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
        text = "\n\n".join(parts)
        return text[:_MAX_DOC_LEN]

    async def _from_pypi(self, library: str) -> str | None:
        """Fetch the project description from PyPI JSON API."""
        url = _PYPI_JSON.format(lib=library)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                desc = data.get("info", {}).get("description", "")
                if not desc:
                    return None
                return f"# {library} (PyPI)\n\n{desc[:_MAX_DOC_LEN]}"
        except Exception as exc:
            log.debug("PyPI fetch failed for %s: %s", library, exc)
            return None

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _store(self, library: str, content: str, source: str) -> None:
        self._cache[library] = DocEntry(
            library=library,
            content=content,
            source=source,
            fetched_at=time.time(),
        )
