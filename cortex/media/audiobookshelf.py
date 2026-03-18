"""Audiobookshelf provider — audiobook search, streaming, and progress sync.

# Module ownership: Audiobookshelf REST API integration
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from cortex.media.base import MediaItem, MediaProvider

logger = logging.getLogger(__name__)


class AudiobookshelfProvider(MediaProvider):
    """Provider for Audiobookshelf audiobook servers."""

    provider_id = "audiobookshelf"
    display_name = "Audiobookshelf"
    supports_streaming = True

    def __init__(self) -> None:
        self._base_url: str = ""
        self._token: str = ""

    async def setup(self, base_url: str = "", token: str = "") -> bool:
        """Connect to an Audiobookshelf server."""
        self._base_url = (base_url or os.environ.get("ABS_URL", "")).rstrip("/")
        self._token = token or os.environ.get("ABS_TOKEN", "")

        if not self._base_url or not self._token:
            logger.info("Audiobookshelf not configured (set ABS_URL and ABS_TOKEN)")
            return False

        ok = await self.health()
        if ok:
            logger.info("Connected to Audiobookshelf at %s", self._base_url)
        return ok

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    # ── Search ───────────────────────────────────────────────────

    async def search(self, query: str, media_type: str = "audiobook") -> list[MediaItem]:
        """Search Audiobookshelf library."""
        if not self._base_url:
            return []

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/api/libraries",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                libraries = resp.json().get("libraries", [])
                if not libraries:
                    return []

                lib_id = libraries[0].get("id", "")
                resp = await client.get(
                    f"{self._base_url}/api/libraries/{lib_id}/search",
                    headers=self._headers(),
                    params={"q": query},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("book", data.get("podcast", []))
            items: list[MediaItem] = []
            for entry in results[:20]:
                book = entry.get("libraryItem", entry)
                media = book.get("media", {})
                meta = media.get("metadata", {})
                items.append(MediaItem(
                    id=book.get("id", ""),
                    title=meta.get("title", ""),
                    artist=meta.get("authorName", ""),
                    duration_seconds=media.get("duration", 0),
                    provider=self.provider_id,
                    media_type="audiobook",
                    metadata={"narrator": meta.get("narratorName", "")},
                ))
            return items
        except Exception as exc:
            logger.error("Audiobookshelf search error: %s", exc)
            return []

    # ── Stream URL ───────────────────────────────────────────────

    async def get_stream_url(self, item_id: str) -> str | None:
        """Return stream URL for an audiobook."""
        if not self._base_url:
            return None
        return f"{self._base_url}/api/items/{item_id}/play"

    # ── Progress sync ────────────────────────────────────────────

    async def get_progress(self, item_id: str, user_id: str = "") -> dict:
        """Get current listening position for resume."""
        if not self._base_url:
            return {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/api/me/progress/{item_id}",
                    headers=self._headers(),
                )
                if resp.status_code == 404:
                    return {"progress": 0, "currentTime": 0, "isFinished": False}
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Audiobookshelf progress fetch error: %s", exc)
            return {}

    async def report_progress(
        self,
        item_id: str,
        position_seconds: float,
        user_id: str = "",
    ) -> bool:
        """Report listening position for sync."""
        if not self._base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.patch(
                    f"{self._base_url}/api/me/progress/{item_id}",
                    headers=self._headers(),
                    json={"currentTime": position_seconds},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("Audiobookshelf progress report error: %s", exc)
            return False

    async def get_chapters(self, item_id: str) -> list[dict]:
        """Get chapter list with timestamps."""
        if not self._base_url:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/api/items/{item_id}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            media = data.get("media", {})
            chapters = media.get("chapters", [])
            return [
                {
                    "id": ch.get("id", i),
                    "title": ch.get("title", f"Chapter {i + 1}"),
                    "start": ch.get("start", 0),
                    "end": ch.get("end", 0),
                }
                for i, ch in enumerate(chapters)
            ]
        except Exception as exc:
            logger.error("Audiobookshelf chapters error: %s", exc)
            return []

    # ── Health ───────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check Audiobookshelf server connectivity."""
        if not self._base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self._base_url}/api/authorize",
                    headers=self._headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False
