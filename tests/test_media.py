"""Tests for the media & entertainment module."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path


# ── Helpers ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Give every test a clean in-memory database."""
    db_path = tmp_path / "test_media.db"
    set_db_path(db_path)
    init_db()
    yield
    try:
        conn = get_db()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# MediaProvider ABC
# ══════════════════════════════════════════════════════════════════

class TestMediaProviderInterface:
    def test_cannot_instantiate_abc(self):
        from cortex.media.base import MediaProvider

        with pytest.raises(TypeError):
            MediaProvider()

    def test_media_item_defaults(self):
        from cortex.media.base import MediaItem

        item = MediaItem(id="1", title="Test Song")
        assert item.provider == ""
        assert item.media_type == "song"
        assert item.duration_seconds == 0
        assert item.metadata == {}

    def test_playback_state_defaults(self):
        from cortex.media.base import PlaybackState

        state = PlaybackState()
        assert state.is_playing is False
        assert state.current_item is None
        assert state.volume == 0.5


# ══════════════════════════════════════════════════════════════════
# PlaybackRouter
# ══════════════════════════════════════════════════════════════════

class TestPlaybackRouter:
    @pytest.mark.asyncio
    async def test_resolve_target_satellite_first(self):
        from cortex.media.router import PlaybackRouter

        conn = get_db()
        conn.execute(
            "INSERT INTO satellites (id, display_name, room, status) VALUES (?, ?, ?, ?)",
            ("sat-1", "Living Room Sat", "living_room", "online"),
        )
        conn.commit()

        router = PlaybackRouter()
        target = await router.resolve_target("living_room")
        assert target is not None
        assert target.target_type == "satellite"
        assert target.target_id == "sat-1"

    @pytest.mark.asyncio
    async def test_resolve_target_chromecast_fallback(self):
        from cortex.media.router import PlaybackRouter

        router = PlaybackRouter()
        router._chromecasts = [
            {"name": "Living Room Speaker", "room": "living_room"},
        ]
        target = await router.resolve_target("living_room")
        assert target is not None
        assert target.target_type == "chromecast"

    @pytest.mark.asyncio
    async def test_resolve_target_ha_fallback(self):
        from cortex.media.router import PlaybackRouter

        conn = get_db()
        conn.execute(
            "INSERT INTO ha_devices (entity_id, friendly_name, domain, area_id) "
            "VALUES (?, ?, ?, ?)",
            ("media_player.bedroom", "Bedroom Speaker", "media_player", "bedroom"),
        )
        conn.commit()

        router = PlaybackRouter()
        target = await router.resolve_target("bedroom")
        assert target is not None
        assert target.target_type == "ha_media_player"

    @pytest.mark.asyncio
    async def test_resolve_target_none(self):
        from cortex.media.router import PlaybackRouter

        router = PlaybackRouter()
        target = await router.resolve_target("nonexistent_room")
        assert target is None

    @pytest.mark.asyncio
    async def test_transfer_stubs(self):
        from cortex.media.router import PlaybackRouter

        conn = get_db()
        conn.execute(
            "INSERT INTO satellites (id, display_name, room, status) VALUES (?, ?, ?, ?)",
            ("sat-a", "Kitchen Sat", "kitchen", "online"),
        )
        conn.execute(
            "INSERT INTO satellites (id, display_name, room, status) VALUES (?, ?, ?, ?)",
            ("sat-b", "Bedroom Sat", "bedroom", "online"),
        )
        conn.commit()

        router = PlaybackRouter()
        ok = await router.transfer("kitchen", "bedroom")
        assert ok is True

    @pytest.mark.asyncio
    async def test_play_satellite_stub(self):
        from cortex.media.router import PlaybackTarget, PlaybackRouter

        router = PlaybackRouter()
        target = PlaybackTarget(
            target_type="satellite", target_id="sat-1", room="kitchen",
        )
        ok = await router.play("http://example.com/stream.mp3", target)
        assert ok is True

    @pytest.mark.asyncio
    async def test_stop_pause_volume(self):
        from cortex.media.router import PlaybackTarget, PlaybackRouter

        router = PlaybackRouter()
        target = PlaybackTarget(
            target_type="satellite", target_id="sat-1", room="kitchen",
        )
        assert await router.stop(target) is True
        assert await router.pause(target) is True
        assert await router.set_volume(target, 0.7) is True

    @pytest.mark.asyncio
    async def test_get_all_targets(self):
        from cortex.media.router import PlaybackRouter

        conn = get_db()
        conn.execute(
            "INSERT INTO satellites (id, display_name, room, status) VALUES (?, ?, ?, ?)",
            ("sat-1", "LR Sat", "living_room", "online"),
        )
        conn.commit()

        router = PlaybackRouter()
        router._chromecasts = [{"name": "Kitchen Cast", "room": "kitchen"}]
        targets = await router.get_all_targets()
        assert len(targets) >= 2
        types = {t.target_type for t in targets}
        assert "satellite" in types
        assert "chromecast" in types


# ══════════════════════════════════════════════════════════════════
# YouTubeMusicProvider
# ══════════════════════════════════════════════════════════════════

class TestYouTubeMusicProvider:
    @pytest.mark.asyncio
    async def test_health_without_client(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        assert await p.health() is False

    @pytest.mark.asyncio
    async def test_search_without_client(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        results = await p.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_mock_client(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        mock_client = MagicMock()
        mock_client.search.return_value = [
            {
                "videoId": "abc123",
                "title": "Test Song",
                "artists": [{"name": "Test Artist"}],
                "album": {"name": "Test Album"},
                "duration_seconds": 240,
            },
        ]
        p._client = mock_client

        results = await p.search("test")
        assert len(results) == 1
        assert results[0].title == "Test Song"
        assert results[0].artist == "Test Artist"
        assert results[0].id == "abc123"

    @pytest.mark.asyncio
    async def test_search_cache(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        mock_client = MagicMock()
        mock_client.search.return_value = [
            {"videoId": "x", "title": "Cached", "artists": [], "album": None},
        ]
        p._client = mock_client

        await p.search("cache test")
        await p.search("cache test")  # should hit cache
        assert mock_client.search.call_count == 1

    @pytest.mark.asyncio
    async def test_get_stream_url_no_ytdlp(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        # Without yt-dlp installed in test env, should return None gracefully
        url = await p.get_stream_url("fake_id")
        # Either None (no yt-dlp) or a URL if installed
        assert url is None or isinstance(url, str)

    @pytest.mark.asyncio
    async def test_get_playlists_without_client(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        assert await p.get_playlists() == []

    @pytest.mark.asyncio
    async def test_recommendations_without_client(self):
        from cortex.media.youtube_music import YouTubeMusicProvider

        p = YouTubeMusicProvider()
        assert await p.get_recommendations() == []


# ══════════════════════════════════════════════════════════════════
# LocalLibraryProvider
# ══════════════════════════════════════════════════════════════════

class TestLocalLibraryProvider:
    @pytest.mark.asyncio
    async def test_scan_empty_directory(self, tmp_path):
        from cortex.media.local_library import LocalLibraryProvider

        p = LocalLibraryProvider()
        count = await p.scan_directory(str(tmp_path))
        assert count == 0

    @pytest.mark.asyncio
    async def test_scan_with_audio_files(self, tmp_path):
        from cortex.media.local_library import LocalLibraryProvider

        # Create fake audio files
        (tmp_path / "song1.mp3").write_bytes(b"fake mp3 data")
        (tmp_path / "song2.flac").write_bytes(b"fake flac data")
        (tmp_path / "readme.txt").write_text("not audio")

        p = LocalLibraryProvider()
        count = await p.scan_directory(str(tmp_path))
        assert count == 2

    @pytest.mark.asyncio
    async def test_search_local(self, tmp_path):
        from cortex.media.local_library import LocalLibraryProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO media_library (provider, media_type, title, artist, file_path) "
            "VALUES ('local', 'song', 'Bohemian Rhapsody', 'Queen', '/music/bohemian.mp3')",
        )
        conn.commit()

        p = LocalLibraryProvider()
        results = await p.search("Bohemian")
        assert len(results) == 1
        assert results[0].title == "Bohemian Rhapsody"
        assert results[0].artist == "Queen"

    @pytest.mark.asyncio
    async def test_get_stream_url_local(self):
        from cortex.media.local_library import LocalLibraryProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO media_library (provider, media_type, title, file_path) "
            "VALUES ('local', 'song', 'Test', '/music/test.mp3')",
        )
        conn.commit()

        row = conn.execute("SELECT id FROM media_library WHERE title = 'Test'").fetchone()
        item_id = str(row[0])

        p = LocalLibraryProvider()
        url = await p.get_stream_url(item_id)
        assert url == "/music/test.mp3"

    @pytest.mark.asyncio
    async def test_health_always_true(self):
        from cortex.media.local_library import LocalLibraryProvider

        p = LocalLibraryProvider()
        assert await p.health() is True

    @pytest.mark.asyncio
    async def test_scan_nonexistent_directory(self):
        from cortex.media.local_library import LocalLibraryProvider

        p = LocalLibraryProvider()
        count = await p.scan_directory("/nonexistent/path/12345")
        assert count == 0


# ══════════════════════════════════════════════════════════════════
# PlexProvider
# ══════════════════════════════════════════════════════════════════

class TestPlexProvider:
    @pytest.mark.asyncio
    async def test_health_without_server(self):
        from cortex.media.plex import PlexProvider

        p = PlexProvider()
        assert await p.health() is False

    @pytest.mark.asyncio
    async def test_search_without_server(self):
        from cortex.media.plex import PlexProvider

        p = PlexProvider()
        assert await p.search("test") == []

    @pytest.mark.asyncio
    async def test_search_with_mock_server(self):
        from cortex.media.plex import PlexProvider

        p = PlexProvider()
        mock_item = MagicMock()
        mock_item.title = "Plex Track"
        mock_item.type = "track"
        mock_item.duration = 300000  # ms
        mock_item.ratingKey = 42
        mock_item.artist.return_value = MagicMock(title="Plex Artist")
        mock_item.album.return_value = MagicMock(title="Plex Album")

        mock_server = MagicMock()
        mock_server.search.return_value = [mock_item]
        p._server = mock_server

        results = await p.search("plex test")
        assert len(results) == 1
        assert results[0].title == "Plex Track"
        assert results[0].id == "42"

    @pytest.mark.asyncio
    async def test_playlists_without_server(self):
        from cortex.media.plex import PlexProvider

        p = PlexProvider()
        assert await p.get_playlists() == []


# ══════════════════════════════════════════════════════════════════
# AudiobookshelfProvider
# ══════════════════════════════════════════════════════════════════

class TestAudiobookshelfProvider:
    @pytest.mark.asyncio
    async def test_health_without_config(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        assert await p.health() is False

    @pytest.mark.asyncio
    async def test_search_without_config(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        assert await p.search("test") == []

    @pytest.mark.asyncio
    async def test_get_stream_url(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        p._base_url = "http://localhost:13378"
        url = await p.get_stream_url("book-123")
        assert url == "http://localhost:13378/api/items/book-123/play"

    @pytest.mark.asyncio
    async def test_get_stream_url_no_config(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        assert await p.get_stream_url("x") is None

    @pytest.mark.asyncio
    async def test_progress_no_config(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        assert await p.get_progress("x") == {}
        assert await p.report_progress("x", 100) is False

    @pytest.mark.asyncio
    async def test_chapters_no_config(self):
        from cortex.media.audiobookshelf import AudiobookshelfProvider

        p = AudiobookshelfProvider()
        assert await p.get_chapters("x") == []


# ══════════════════════════════════════════════════════════════════
# PodcastProvider
# ══════════════════════════════════════════════════════════════════

_SAMPLE_RSS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
      <channel>
        <title>Test Podcast</title>
        <description>A test podcast feed</description>
        <item>
          <title>Episode 1</title>
          <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" />
          <description>First episode</description>
          <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
          <itunes:duration>1:30:00</itunes:duration>
        </item>
        <item>
          <title>Episode 2</title>
          <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg" />
          <itunes:duration>45:30</itunes:duration>
        </item>
      </channel>
    </rss>
""")


class TestPodcastProvider:
    @pytest.mark.asyncio
    async def test_health_always_true(self):
        from cortex.media.podcasts import PodcastProvider

        p = PodcastProvider()
        assert await p.health() is True

    @pytest.mark.asyncio
    async def test_parse_duration(self):
        from cortex.media.podcasts import PodcastProvider

        assert PodcastProvider._parse_duration("1:30:00") == 5400
        assert PodcastProvider._parse_duration("45:30") == 2730
        assert PodcastProvider._parse_duration("3600") == 3600
        assert PodcastProvider._parse_duration("") == 0
        assert PodcastProvider._parse_duration("invalid") == 0

    @pytest.mark.asyncio
    async def test_subscribe_and_list(self):
        from cortex.media.podcasts import PodcastProvider
        from unittest.mock import AsyncMock

        p = PodcastProvider()

        mock_response = MagicMock()
        mock_response.text = _SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = client_instance

            sub_id = await p.subscribe("https://example.com/feed.xml")
            assert sub_id > 0

            subs = await p.list_subscriptions()
            assert len(subs) == 1
            assert subs[0]["title"] == "Test Podcast"

    @pytest.mark.asyncio
    async def test_episodes_from_rss(self):
        from cortex.media.podcasts import PodcastProvider

        p = PodcastProvider()

        mock_response = MagicMock()
        mock_response.text = _SAMPLE_RSS
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            client_instance = AsyncMock()
            client_instance.get = AsyncMock(return_value=mock_response)
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = client_instance

            sub_id = await p.subscribe("https://example.com/rss.xml")
            episodes = await p.get_episodes(sub_id)
            assert len(episodes) == 2
            assert episodes[0].title == "Episode 1"

    @pytest.mark.asyncio
    async def test_progress_tracking(self):
        from cortex.media.podcasts import PodcastProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO podcast_subscriptions (id, title, feed_url) VALUES (1, 'Test', 'http://x')",
        )
        conn.execute(
            "INSERT INTO podcast_episodes (id, subscription_id, title, audio_url, duration_seconds) "
            "VALUES (10, 1, 'Ep', 'http://x/ep.mp3', 3600)",
        )
        conn.commit()

        p = PodcastProvider()
        progress = await p.get_progress("10")
        assert progress == 0.0

        await p.report_progress("10", 1800.0)
        progress = await p.get_progress("10")
        assert progress == 1800.0

    @pytest.mark.asyncio
    async def test_search_episodes(self):
        from cortex.media.podcasts import PodcastProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO podcast_subscriptions (id, title, feed_url) VALUES (1, 'Tech Talk', 'http://x')",
        )
        conn.execute(
            "INSERT INTO podcast_episodes (id, subscription_id, title, audio_url) "
            "VALUES (1, 1, 'Python Tips', 'http://x/ep1.mp3')",
        )
        conn.commit()

        p = PodcastProvider()
        results = await p.search("Python")
        assert len(results) == 1
        assert results[0].title == "Python Tips"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        from cortex.media.podcasts import PodcastProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO podcast_subscriptions (id, title, feed_url) VALUES (1, 'Test', 'http://x')",
        )
        conn.commit()

        p = PodcastProvider()
        ok = await p.unsubscribe(1)
        assert ok is True
        subs = await p.list_subscriptions()
        assert len(subs) == 0

    @pytest.mark.asyncio
    async def test_get_stream_url(self):
        from cortex.media.podcasts import PodcastProvider

        conn = get_db()
        conn.execute(
            "INSERT INTO podcast_subscriptions (id, title, feed_url) VALUES (1, 'Test', 'http://x')",
        )
        conn.execute(
            "INSERT INTO podcast_episodes (id, subscription_id, title, audio_url) "
            "VALUES (5, 1, 'Ep', 'http://example.com/ep5.mp3')",
        )
        conn.commit()

        p = PodcastProvider()
        url = await p.get_stream_url("5")
        assert url == "http://example.com/ep5.mp3"


# ══════════════════════════════════════════════════════════════════
# PreferenceEngine
# ══════════════════════════════════════════════════════════════════

class TestPreferenceEngine:
    def test_record_play_and_get_preferences(self):
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        engine.record_play("alice", "rock")
        engine.record_play("alice", "rock")
        engine.record_play("alice", "jazz")

        prefs = engine.get_preferred_genres("alice")
        assert "rock" in prefs
        assert "jazz" in prefs
        # Rock played twice → higher affinity
        assert prefs.index("rock") < prefs.index("jazz")

    def test_empty_preferences(self):
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        assert engine.get_preferred_genres("nobody") == []

    def test_time_preference_returns_genre(self):
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        genre = engine.get_time_preference("alice")
        assert isinstance(genre, str)
        assert len(genre) > 0

    def test_suggest_query_returns_string(self):
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        query = engine.suggest_query("alice")
        assert isinstance(query, str)
        assert len(query) > 0

    def test_record_play_empty_genre_ignored(self):
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        engine.record_play("alice", "")  # should not raise
        assert engine.get_preferred_genres("alice") == []


# ══════════════════════════════════════════════════════════════════
# MediaPlugin
# ══════════════════════════════════════════════════════════════════

class TestMediaPlugin:
    @pytest.mark.asyncio
    async def test_match_play(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("play some jazz music", {})
        assert m.matched is True
        assert m.intent == "play"

    @pytest.mark.asyncio
    async def test_match_pause(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("pause the music", {})
        assert m.matched is True
        assert m.intent == "pause"

    @pytest.mark.asyncio
    async def test_match_stop(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("stop playing", {})
        assert m.matched is True
        assert m.intent == "stop"

    @pytest.mark.asyncio
    async def test_match_skip(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("skip this song", {})
        assert m.matched is True
        assert m.intent == "next"

    @pytest.mark.asyncio
    async def test_match_volume(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("volume up", {})
        assert m.matched is True
        assert m.intent == "volume_up"

        m = await p.match("volume 80", {})
        assert m.matched is True
        assert m.intent == "volume_set"

    @pytest.mark.asyncio
    async def test_match_whats_playing(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("what's playing", {})
        assert m.matched is True
        assert m.intent == "query"

    @pytest.mark.asyncio
    async def test_match_podcast(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("any new podcasts", {})
        assert m.matched is True
        assert m.intent == "podcast_check"

    @pytest.mark.asyncio
    async def test_match_audiobook(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("continue my audiobook", {})
        assert m.matched is True
        assert m.intent == "audiobook_resume"

    @pytest.mark.asyncio
    async def test_match_no_match(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("what time is it", {})
        assert m.matched is False

    @pytest.mark.asyncio
    async def test_match_smart_play(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("play something", {})
        assert m.matched is True
        assert m.intent == "play_smart"

    @pytest.mark.asyncio
    async def test_match_listen_to(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("listen to Beethoven", {})
        assert m.matched is True
        assert m.intent == "play"

    @pytest.mark.asyncio
    async def test_match_transfer(self):
        from cortex.plugins.media import MediaPlugin

        p = MediaPlugin()
        await p.setup({})

        m = await p.match("move this to the bedroom", {})
        assert m.matched is True
        assert m.intent == "transfer"

    @pytest.mark.asyncio
    async def test_handle_play_no_results(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="play", metadata={"query": "xyznonexistent123"})
        result = await p.handle("play xyznonexistent123", match, {"user_id": "alice", "room": ""})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_pause(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="pause")
        result = await p.handle("pause", match, {"room": ""})
        assert result.success is True
        assert "pause" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_stop(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="stop")
        result = await p.handle("stop", match, {"room": ""})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_handle_query_nothing_playing(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="query")
        result = await p.handle("what's playing", match, {})
        assert result.success is True
        assert "nothing" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_volume(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="volume_set", metadata={"level": 75})
        result = await p.handle("volume 75", match, {"room": ""})
        assert result.success is True
        assert "75%" in result.response

    @pytest.mark.asyncio
    async def test_handle_play_with_local_results(self):
        from cortex.plugins.media import MediaPlugin
        from cortex.plugins.base import CommandMatch

        # Seed local library
        conn = get_db()
        conn.execute(
            "INSERT INTO media_library (provider, media_type, title, artist, file_path) "
            "VALUES ('local', 'song', 'Test Track', 'Test Artist', '/music/test.mp3')",
        )
        conn.commit()

        p = MediaPlugin()
        await p.setup({})

        match = CommandMatch(matched=True, intent="play", metadata={"query": "Test Track"})
        result = await p.handle("play Test Track", match, {"user_id": "alice", "room": ""})
        assert result.success is True
        assert "Test Track" in result.response


# ══════════════════════════════════════════════════════════════════
# Admin API
# ══════════════════════════════════════════════════════════════════

class TestAdminMediaAPI:
    @pytest.mark.asyncio
    async def test_providers_endpoint(self):
        """Test that the providers endpoint returns the expected structure."""
        from cortex.admin.media import list_providers

        # Call directly (bypass FastAPI dep injection)
        result = await list_providers(_={})
        assert "providers" in result
        assert len(result["providers"]) == 5
        ids = {p["id"] for p in result["providers"]}
        assert "youtube_music" in ids
        assert "local" in ids
        assert "plex" in ids
        assert "audiobookshelf" in ids
        assert "podcasts" in ids

    @pytest.mark.asyncio
    async def test_history_endpoint(self):
        from cortex.admin.media import playback_history

        result = await playback_history(_={})
        assert "history" in result
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_now_playing_endpoint(self):
        from cortex.admin.media import now_playing

        result = await now_playing(_={})
        assert result["is_playing"] is False

    @pytest.mark.asyncio
    async def test_preferences_endpoint(self):
        from cortex.admin.media import user_preferences
        from cortex.media.preferences import PreferenceEngine

        engine = PreferenceEngine()
        engine.record_play("testuser", "jazz")

        result = await user_preferences("testuser", _={})
        assert result["user_id"] == "testuser"
        assert len(result["preferences"]) == 1

    @pytest.mark.asyncio
    async def test_podcasts_endpoint(self):
        from cortex.admin.media import list_podcasts

        result = await list_podcasts(_={})
        assert "subscriptions" in result
