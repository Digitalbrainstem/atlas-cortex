"""Plex media server provider — search, stream, playlists.

# Module ownership: Plex integration via plexapi
"""

from __future__ import annotations

import logging
import os
from typing import Any

from cortex.media.base import MediaItem, MediaProvider

logger = logging.getLogger(__name__)


class PlexProvider(MediaProvider):
    """Provider for Plex media servers."""

    provider_id = "plex"
    display_name = "Plex"
    supports_streaming = True

    def __init__(self) -> None:
        self._server: Any = None  # plexapi.server.PlexServer
        self._base_url: str = ""
        self._token: str = ""

    async def setup(self, base_url: str = "", token: str = "") -> bool:
        """Connect to Plex server using plexapi."""
        self._base_url = base_url or os.environ.get("PLEX_URL", "")
        self._token = token or os.environ.get("PLEX_TOKEN", "")

        if not self._base_url or not self._token:
            logger.info("Plex not configured (set PLEX_URL and PLEX_TOKEN)")
            return False

        try:
            from plexapi.server import PlexServer  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("plexapi not installed — Plex unavailable")
            return False

        try:
            self._server = PlexServer(self._base_url, self._token)
            logger.info("Connected to Plex: %s", self._server.friendlyName)
            return True
        except Exception as exc:
            logger.warning("Plex connection failed: %s", exc)
            return False

    # ── Search ───────────────────────────────────────────────────

    async def search(self, query: str, media_type: str = "") -> list[MediaItem]:
        """Search Plex music library."""
        if not self._server:
            return []
        try:
            results = self._server.search(query)
            items: list[MediaItem] = []
            for r in results[:20]:
                if media_type and hasattr(r, "type") and r.type != media_type:
                    continue
                items.append(self._to_media_item(r))
            return items
        except Exception as exc:
            logger.error("Plex search error: %s", exc)
            return []

    def _to_media_item(self, plex_item: Any) -> MediaItem:
        """Convert a plexapi object to a MediaItem."""
        title = getattr(plex_item, "title", "")
        artist = ""
        album = ""
        item_type = getattr(plex_item, "type", "track")

        if hasattr(plex_item, "artist"):
            a = plex_item.artist()
            artist = getattr(a, "title", "") if a else ""
        if hasattr(plex_item, "album"):
            a = plex_item.album()
            album = getattr(a, "title", "") if a else ""

        duration = getattr(plex_item, "duration", 0) or 0
        rating_key = str(getattr(plex_item, "ratingKey", ""))

        return MediaItem(
            id=rating_key,
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration / 1000 if duration else 0,
            provider=self.provider_id,
            media_type=item_type,
        )

    # ── Stream URL ───────────────────────────────────────────────

    async def get_stream_url(self, item_id: str) -> str | None:
        """Return stream URL for a Plex item."""
        if not self._server:
            return None
        try:
            item = self._server.fetchItem(int(item_id))
            parts = item.media[0].parts if item.media else []
            if parts:
                key = parts[0].key
                return f"{self._base_url}{key}?X-Plex-Token={self._token}"
            return None
        except Exception as exc:
            logger.error("Plex stream URL error: %s", exc)
            return None

    # ── Playlists ────────────────────────────────────────────────

    async def get_playlists(self) -> list[dict]:
        """List Plex playlists."""
        if not self._server:
            return []
        try:
            playlists = self._server.playlists()
            return [
                {
                    "id": str(p.ratingKey),
                    "title": p.title,
                    "count": len(p.items()) if hasattr(p, "items") else 0,
                }
                for p in playlists
            ]
        except Exception as exc:
            logger.error("Plex playlists error: %s", exc)
            return []

    # ── Health ───────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check Plex server connectivity."""
        if not self._server:
            return False
        try:
            _ = self._server.friendlyName
            return True
        except Exception:
            return False
