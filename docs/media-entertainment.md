# Atlas Cortex — Media & Entertainment (Part 8)

Multi-source, multi-room media control with support for local files, streaming services, and Home Assistant media players.

## Overview

Atlas provides a unified voice interface for controlling media playback across your home, aggregating content from local libraries, streaming services, and HA-integrated media players.

## Supported Sources

| Source | Type | Integration Method |
|--------|------|-------------------|
| **Local Files** | Music, podcasts, audiobooks on server | Direct file access / Jellyfin / Plex / Navidrome |
| **YouTube Music** | Streaming | `ytmusicapi` (unofficial API) or HA integration |
| **Spotify** | Streaming | Spotify Connect API via HA / `spotipy` |
| **Apple Music** | Streaming | HA integration (limited) |
| **Tidal** | Streaming | HA integration |
| **TuneIn / iHeartRadio** | Internet radio | HA media player / direct streams |
| **Podcasts** | RSS feeds | Built-in RSS parser + local caching |
| **Audiobooks** | Local / Audible | Audiobookshelf integration or local files |

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Atlas Cortex                         │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │              Media Controller                     │  │
│  │                                                   │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ │  │
│  │  │ Local   │ │ Spotify │ │ YouTube │ │ Radio  │ │  │
│  │  │ Library │ │ Connect │ │ Music   │ │ Streams│ │  │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └───┬────┘ │  │
│  │       └───────────┼──────────┼───────────┘      │  │
│  │                   ▼                              │  │
│  │          ┌──────────────────┐                    │  │
│  │          │  Playback Router │                    │  │
│  │          │  • Room target   │                    │  │
│  │          │  • Multi-room    │                    │  │
│  │          │  • Queue mgmt   │                    │  │
│  │          └────────┬─────────┘                    │  │
│  └───────────────────┼──────────────────────────────┘  │
│                      │                                  │
└──────────────────────┼──────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         │             │             │
   ┌─────▼──────┐ ┌───▼────────┐ ┌──▼──────────┐
   │ Satellite  │ │ HA Media   │ │ Chromecast  │
   │ Speaker    │ │ Player     │ │ / AirPlay   │
   └────────────┘ └────────────┘ └─────────────┘
```

## Natural Language Interface

```
User: "Play jazz in the living room"
Atlas: "Playing jazz from your library on the living room speaker."

User: "Play Bohemian Rhapsody"
Atlas: "I found it on Spotify and in your local library. 
  Which would you prefer?" / *plays from preferred source*

User: "Play some chill music everywhere"
Atlas: *starts multi-room playback of chill playlist*

User: "What's playing?"
Atlas: "Take Five by Dave Brubeck, from your local jazz collection. 
  2 minutes and 15 seconds remaining."

User: "Skip this song"
Atlas: "Skipped. Now playing: So What by Miles Davis."

User: "Play my morning playlist"
Atlas: *loads user's "morning" playlist — learned from patterns*

User: "Play the latest episode of my podcast"
Atlas: "Playing episode 247 of The Daily on the kitchen speaker."

User: "Read my audiobook"
Atlas: "Resuming 'Project Hail Mary' — chapter 12, 3 hours remaining."
```

## Media Provider Interface

```python
class MediaProvider(ABC):
    """Abstract media source."""

    @abstractmethod
    async def search(self, query: str, media_type: str = "music") -> list[MediaItem]:
        """Search for tracks, albums, artists, playlists, podcasts."""

    @abstractmethod
    async def get_stream_url(self, item_id: str) -> str:
        """Get playable stream URL."""

    @abstractmethod
    async def health(self) -> bool:
        """Check if source is available."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider display name."""

    @property
    @abstractmethod
    def supported_types(self) -> list[str]:
        """['music', 'podcast', 'audiobook', 'radio']"""
```

### Provider Implementations

| Provider | Class | Notes |
|----------|-------|-------|
| `LocalLibraryProvider` | Scans music directories, reads ID3 tags | Supports FLAC, MP3, OGG, WAV, M4A |
| `JellyfinProvider` | REST API integration | Also handles audiobooks/podcasts |
| `SpotifyProvider` | Spotify Connect + `spotipy` | Requires Premium for full playback |
| `YTMusicProvider` | `ytmusicapi` unofficial API | No auth needed for search |
| `RadioProvider` | Direct stream URLs | TuneIn, iHeartRadio, custom stations |
| `PodcastProvider` | RSS feed parser | Auto-download, resume position |
| `HAMediaProvider` | HA REST API `media_player.*` | Wraps any HA-integrated player |

## Playback Controller

```python
class PlaybackController:
    """Manages playback state across rooms and devices."""

    async def play(self, item: MediaItem, target: PlaybackTarget):
        """Start playback on target device(s)."""

    async def pause(self, target: PlaybackTarget | None = None):
        """Pause current playback."""

    async def skip(self, target: PlaybackTarget | None = None):
        """Skip to next track."""

    async def set_volume(self, level: float, target: PlaybackTarget | None = None):
        """Set volume (0.0 - 1.0)."""

    async def transfer(self, from_target: PlaybackTarget, to_target: PlaybackTarget):
        """Move playback from one room to another."""

    async def group(self, targets: list[PlaybackTarget]):
        """Create multi-room group for synchronized playback."""

    async def now_playing(self, target: PlaybackTarget | None = None) -> NowPlaying:
        """Get current playback state."""
```

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS media_providers (
    id            TEXT PRIMARY KEY,
    provider_type TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    config        TEXT,                     -- JSON: credentials, library paths, etc.
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_library (
    id            TEXT PRIMARY KEY,
    provider_id   TEXT REFERENCES media_providers(id),
    media_type    TEXT NOT NULL,            -- "track", "album", "artist", "playlist", "podcast", "audiobook"
    title         TEXT NOT NULL,
    artist        TEXT,
    album         TEXT,
    duration_sec  INTEGER,
    genre         TEXT,
    cover_url     TEXT,
    stream_url    TEXT,
    metadata      TEXT,                     -- JSON: additional metadata
    last_indexed  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playback_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    media_id      TEXT REFERENCES media_library(id),
    target_device TEXT,
    played_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_sec  INTEGER,
    completed     BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_playlists (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    is_smart      BOOLEAN DEFAULT FALSE,   -- auto-generated from patterns
    media_ids     TEXT,                     -- JSON array of media IDs
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playback_state (
    target_device TEXT PRIMARY KEY,
    media_id      TEXT REFERENCES media_library(id),
    position_sec  INTEGER DEFAULT 0,
    volume        REAL DEFAULT 0.5,
    is_playing    BOOLEAN DEFAULT FALSE,
    queue         TEXT,                     -- JSON: upcoming track IDs
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Smart Playlists

Atlas learns music preferences and auto-generates contextual playlists:
- **Morning energy** — upbeat tracks played during morning routines
- **Focus mode** — instrumental/ambient played during work hours
- **Cooking** — tracks commonly played in the kitchen
- **Bedtime** — calm tracks played in evening
- **User-trained** — "I like this" / "Skip" signals shape preferences

## Multi-Room Audio

| Feature | Implementation |
|---------|---------------|
| **Synchronized play** | HA media group or Snapcast for network sync |
| **Room transfer** | "Move the music to the bedroom" — pause + resume on new target |
| **Follow me** | Auto-transfer playback to room user moves to (via presence) |
| **Independent zones** | Different music in different rooms simultaneously |
| **Volume per room** | Independent volume control per satellite/speaker |

## Source Priority

When a song is found on multiple sources:
1. **Local library** (highest quality, no network dependency)
2. **User's preferred service** (configurable)
3. **First available streaming service**
4. **YouTube Music** (fallback — widest catalog)

## Implementation Tasks

| Task | Description |
|------|-------------|
| P8.1 | Media provider interface — abstract source with search/stream/health |
| P8.2 | Local library provider — file scanning, ID3 tags, search index |
| P8.3 | Spotify provider — Spotify Connect API, playlist sync |
| P8.4 | YouTube Music provider — `ytmusicapi` search and streaming |
| P8.5 | HA media provider — wrap HA media_player entities |
| P8.6 | Podcast provider — RSS parsing, auto-download, resume tracking |
| P8.7 | Playback controller — play/pause/skip/volume/transfer |
| P8.8 | Multi-room sync — HA groups, Snapcast, or satellite sync |
| P8.9 | Smart playlists — learn preferences, contextual auto-generation |
| P8.10 | Pipeline integration — Layer 2 plugin for media voice commands |
| P8.11 | Source priority — multi-source resolution, user preference learning |
