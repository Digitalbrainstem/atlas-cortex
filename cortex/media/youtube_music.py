"""YouTube Music provider — search, stream, playlists, recommendations.

# Module ownership: YouTube Music integration via ytmusicapi + yt-dlp
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from cortex.media.base import MediaItem, MediaProvider

logger = logging.getLogger(__name__)

_SEARCH_CACHE_TTL = 900  # 15 minutes
_STREAM_CACHE_TTL = 21600  # 6 hours


class YouTubeMusicProvider(MediaProvider):
    """YouTube Music provider using ytmusicapi and yt-dlp."""

    provider_id = "youtube_music"
    display_name = "YouTube Music"
    supports_streaming = True

    def __init__(self) -> None:
        self._client: Any = None  # ytmusicapi.YTMusic
        self._cache: dict[str, tuple[Any, float]] = {}  # key → (data, expiry)

    async def setup(self, auth_file: str = "oauth.json") -> bool:
        """Initialise ytmusicapi with OAuth credentials."""
        try:
            import ytmusicapi  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("ytmusicapi not installed — YouTube Music unavailable")
            return False

        try:
            self._client = ytmusicapi.YTMusic(auth_file)
            logger.info("YouTube Music connected via %s", auth_file)
            return True
        except Exception as exc:
            logger.warning("YouTube Music setup failed: %s", exc)
            return False

    # ── Cache helpers ────────────────────────────────────────────

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and time.time() < entry[1]:
            return entry[0]
        self._cache.pop(key, None)
        return None

    def _cache_set(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = (value, time.time() + ttl)

    # ── Search ───────────────────────────────────────────────────

    async def search(self, query: str, media_type: str = "songs") -> list[MediaItem]:
        """Search YouTube Music. Results cached for 15 minutes."""
        if not self._client:
            return []

        cache_key = f"search:{media_type}:{query.lower()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            loop = asyncio.get_event_loop()
            filter_type = media_type if media_type in (
                "songs", "videos", "albums", "artists", "playlists",
            ) else "songs"
            results = await loop.run_in_executor(
                None, lambda: self._client.search(query, filter=filter_type),
            )
        except Exception as exc:
            logger.error("YouTube Music search error: %s", exc)
            return []

        items = self._parse_results(results, media_type)
        self._cache_set(cache_key, items, _SEARCH_CACHE_TTL)
        return items

    def _parse_results(self, results: list[dict], media_type: str) -> list[MediaItem]:
        """Convert ytmusicapi results to MediaItem list."""
        items: list[MediaItem] = []
        for r in results[:20]:
            vid = r.get("videoId") or r.get("browseId") or ""
            title = r.get("title", "")
            artists = r.get("artists", [])
            artist = ", ".join(a.get("name", "") for a in artists) if artists else ""
            album_info = r.get("album")
            album = album_info.get("name", "") if isinstance(album_info, dict) else ""
            duration = r.get("duration_seconds", 0) or 0

            items.append(MediaItem(
                id=vid,
                title=title,
                artist=artist,
                album=album,
                duration_seconds=duration,
                provider=self.provider_id,
                media_type=media_type.rstrip("s") if media_type.endswith("s") else media_type,
                metadata={"source": "youtube_music"},
            ))
        return items

    # ── Stream URL ───────────────────────────────────────────────

    async def get_stream_url(self, video_id: str) -> str | None:
        """Extract audio stream URL using yt-dlp. Cached for ~6 hours."""
        cache_key = f"stream:{video_id}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            import yt_dlp  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("yt-dlp not installed — cannot extract stream URL")
            return None

        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        try:
            loop = asyncio.get_event_loop()
            url = f"https://music.youtube.com/watch?v={video_id}"

            def _extract() -> str | None:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get("url") if info else None

            stream_url = await loop.run_in_executor(None, _extract)
            if stream_url:
                self._cache_set(cache_key, stream_url, _STREAM_CACHE_TTL)
            return stream_url
        except Exception as exc:
            logger.error("yt-dlp extraction failed for %s: %s", video_id, exc)
            return None

    # ── Playlists ────────────────────────────────────────────────

    async def get_playlists(self) -> list[dict]:
        """Get user's playlists."""
        if not self._client:
            return []
        try:
            loop = asyncio.get_event_loop()
            playlists = await loop.run_in_executor(
                None, self._client.get_library_playlists,
            )
            return [
                {
                    "id": p.get("playlistId", ""),
                    "title": p.get("title", ""),
                    "count": p.get("count", 0),
                }
                for p in playlists
            ]
        except Exception as exc:
            logger.error("Failed to get playlists: %s", exc)
            return []

    async def get_playlist_items(self, playlist_id: str) -> list[MediaItem]:
        """Get tracks in a playlist."""
        if not self._client:
            return []
        try:
            loop = asyncio.get_event_loop()
            playlist = await loop.run_in_executor(
                None, lambda: self._client.get_playlist(playlist_id),
            )
            tracks = playlist.get("tracks", [])
            return self._parse_results(tracks, "songs")
        except Exception as exc:
            logger.error("Failed to get playlist items: %s", exc)
            return []

    # ── Recommendations ──────────────────────────────────────────

    async def get_recommendations(self) -> list[MediaItem]:
        """Get personalised recommendations from YouTube Music."""
        if not self._client:
            return []

        cache_key = "recommendations"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            loop = asyncio.get_event_loop()
            home = await loop.run_in_executor(None, self._client.get_home)
            items: list[MediaItem] = []
            for section in home[:3]:
                for r in section.get("contents", [])[:5]:
                    vid = r.get("videoId", "")
                    if not vid:
                        continue
                    title = r.get("title", "")
                    artists = r.get("artists", [])
                    artist = ", ".join(
                        a.get("name", "") for a in artists
                    ) if artists else ""
                    items.append(MediaItem(
                        id=vid,
                        title=title,
                        artist=artist,
                        provider=self.provider_id,
                        media_type="song",
                    ))
            self._cache_set(cache_key, items, _SEARCH_CACHE_TTL)
            return items
        except Exception as exc:
            logger.error("Recommendations failed: %s", exc)
            return []

    # ── Health ───────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if ytmusicapi can connect."""
        if not self._client:
            return False
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: self._client.search("test", filter="songs", limit=1),
            )
            return True
        except Exception:
            return False
