"""Abstract base class for media providers.

# Module ownership: Media provider interface — search, stream, playlists
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class MediaItem:
    """A single media item from any provider."""

    id: str
    title: str
    artist: str = ""
    album: str = ""
    genre: str = ""
    duration_seconds: float = 0
    provider: str = ""
    media_type: str = "song"  # song, album, playlist, audiobook, podcast
    stream_url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class PlaybackState:
    """Current playback state on a target device."""

    is_playing: bool = False
    current_item: MediaItem | None = None
    position_seconds: float = 0
    volume: float = 0.5
    target_room: str = ""


class MediaProvider(abc.ABC):
    """Abstract base class for all media providers.

    Concrete implementations: YouTubeMusicProvider, LocalLibraryProvider,
    PlexProvider, AudiobookshelfProvider, PodcastProvider.
    """

    provider_id: str = ""
    display_name: str = ""
    supports_streaming: bool = True

    @abc.abstractmethod
    async def search(self, query: str, media_type: str = "") -> list[MediaItem]:
        """Search this provider's catalogue."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_stream_url(self, item_id: str) -> str | None:
        """Return a streamable URL for the given item."""
        raise NotImplementedError

    @abc.abstractmethod
    async def health(self) -> bool:
        """Return True if this provider's backend is reachable."""
        raise NotImplementedError

    async def get_playlists(self) -> list[dict]:
        """Return user playlists (override if supported)."""
        return []

    async def get_playlist_items(self, playlist_id: str) -> list[MediaItem]:
        """Return items in a playlist (override if supported)."""
        return []
