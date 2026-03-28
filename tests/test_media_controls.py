"""Tests for media control REST endpoints and WebSocket state."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_media_controller():
    """Reset the module-level _ctrl singleton between tests."""
    from cortex.admin.media import _ctrl

    _ctrl._states.clear()
    _ctrl._queues.clear()
    _ctrl._router = None
    yield
    _ctrl._states.clear()
    _ctrl._queues.clear()
    _ctrl._router = None


@pytest.fixture(autouse=True)
def _silence_broadcast():
    """Suppress WebSocket broadcasts in all tests by default."""
    with patch(
        "cortex.avatar.broadcast.broadcast_to_room",
        new_callable=AsyncMock,
    ):
        yield


@pytest.fixture()
def client():
    """FastAPI TestClient with admin auth bypassed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cortex.admin.helpers import require_admin
    from cortex.admin.media import router

    app = FastAPI()
    app.include_router(router)

    async def _no_auth():
        return {"id": 1, "username": "admin"}

    app.dependency_overrides[require_admin] = _no_auth

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def _mock_router():
    """Return a mock PlaybackRouter with async stubs."""
    r = AsyncMock()
    r.resolve_target = AsyncMock(return_value=None)
    r.play = AsyncMock(return_value=True)
    r.pause = AsyncMock(return_value=True)
    r.stop = AsyncMock(return_value=True)
    r.set_volume = AsyncMock(return_value=True)
    return r


def _make_item(title="Test Song", artist="Test Artist", provider="local"):
    from cortex.media.base import MediaItem

    return MediaItem(
        id="test-1",
        title=title,
        artist=artist,
        album="Test Album",
        genre="pop",
        duration_seconds=210,
        provider=provider,
        stream_url="http://example.com/stream.mp3",
        metadata={"album_art_url": "http://example.com/art.jpg"},
    )


def _patch_providers(**overrides):
    """Patch all media provider classes at their source modules.

    Pass keyword args like ``local=[item1]`` to set search results.
    Providers not mentioned return ``[]``.
    """
    modules = {
        "local": "cortex.media.local_library.LocalLibraryProvider",
        "youtube_music": "cortex.media.youtube_music.YouTubeMusicProvider",
        "plex": "cortex.media.plex.PlexProvider",
        "audiobookshelf": "cortex.media.audiobookshelf.AudiobookshelfProvider",
        "podcasts": "cortex.media.podcasts.PodcastProvider",
    }
    patches = {}
    for key, path in modules.items():
        mock_cls = MagicMock()
        inst = mock_cls.return_value
        inst.search = AsyncMock(return_value=overrides.get(key, []))
        inst.health = AsyncMock(return_value=bool(overrides.get(key)))
        inst.get_stream_url = AsyncMock(return_value=None)
        patches[key] = patch(path, mock_cls)
    return patches


# ── POST /media/play ────────────────────────────────────────────

class TestPlayEndpoint:
    def test_play_returns_item(self, client):
        from cortex.admin.media import _ctrl

        item = _make_item()
        _ctrl._router = _mock_router()
        pp = _patch_providers(local=[item])
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            resp = client.post("/media/play", json={"query": "test song"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["item"]["title"] == "Test Song"
        assert data["item"]["artist"] == "Test Artist"
        assert data["room"] == "default"

    def test_play_no_results(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        pp = _patch_providers()
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            resp = client.post("/media/play", json={"query": "nonexistent"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_play_with_room(self, client):
        from cortex.admin.media import _ctrl

        item = _make_item()
        _ctrl._router = _mock_router()
        pp = _patch_providers(local=[item])
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            resp = client.post(
                "/media/play",
                json={"query": "test", "room": "kitchen"},
            )

        assert resp.json()["room"] == "kitchen"

    def test_play_updates_state(self, client):
        from cortex.admin.media import _ctrl

        item = _make_item()
        _ctrl._router = _mock_router()
        pp = _patch_providers(local=[item])
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            client.post("/media/play", json={"query": "test"})

        state = _ctrl.get_state("default")
        assert state.is_playing is True
        assert state.current_item is not None
        assert state.current_item.title == "Test Song"


# ── POST /media/pause ────────────────────────────────────────────

class TestPauseEndpoint:
    def test_pause_success(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/pause", json={})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["room"] == "default"

    def test_pause_specific_room(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/pause", json={"room": "bedroom"})

        assert resp.json()["room"] == "bedroom"

    def test_pause_sets_not_playing(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        _ctrl.set_state("default", PlaybackState(is_playing=True))
        _ctrl._router = _mock_router()
        client.post("/media/pause", json={})

        assert _ctrl.get_state("default").is_playing is False


# ── POST /media/resume ──────────────────────────────────────────

class TestResumeEndpoint:
    def test_resume_success(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        item = _make_item()
        _ctrl.set_state("default", PlaybackState(
            is_playing=False, current_item=item, target_room="default",
        ))
        _ctrl._router = _mock_router()
        resp = client.post("/media/resume", json={})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_resume_sets_playing(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        client.post("/media/resume", json={})

        assert _ctrl.get_state("default").is_playing is True


# ── POST /media/stop ────────────────────────────────────────────

class TestStopEndpoint:
    def test_stop_resets_state(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        _ctrl.set_state("default", PlaybackState(is_playing=True))
        _ctrl._router = _mock_router()
        client.post("/media/stop", json={})

        state = _ctrl.get_state("default")
        assert state.is_playing is False
        assert state.current_item is None

    def test_stop_specific_room(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/stop", json={"room": "office"})

        assert resp.json()["room"] == "office"


# ── POST /media/next ────────────────────────────────────────────

class TestNextEndpoint:
    def test_next_pops_queue(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        _ctrl.add_to_queue("default", {"title": "Song 2", "artist": "B"})
        _ctrl.add_to_queue("default", {"title": "Song 3", "artist": "C"})

        resp = client.post("/media/next", json={})

        data = resp.json()
        assert data["ok"] is True
        assert data["item"]["title"] == "Song 2"
        assert len(_ctrl.get_queue("default")) == 1

    def test_next_empty_queue(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/next", json={})

        assert resp.json()["message"] == "Queue empty"


# ── POST /media/previous ────────────────────────────────────────

class TestPreviousEndpoint:
    def test_previous_resets_position(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        _ctrl.set_state("default", PlaybackState(position_seconds=120.0))
        _ctrl._router = _mock_router()
        client.post("/media/previous", json={})

        assert _ctrl.get_state("default").position_seconds == 0


# ── POST /media/volume ──────────────────────────────────────────

class TestVolumeEndpoint:
    def test_set_volume(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/volume", json={"level": 75})

        assert resp.status_code == 200
        assert resp.json()["volume"] == 75
        assert _ctrl.get_state("default").volume == 0.75

    def test_volume_clamps(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        client.post("/media/volume", json={"level": 150})

        assert _ctrl.get_state("default").volume == 1.0

    def test_volume_zero(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        client.post("/media/volume", json={"level": 0})

        assert _ctrl.get_state("default").volume == 0.0


# ── POST /media/seek ────────────────────────────────────────────

class TestSeekEndpoint:
    def test_seek_updates_position(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/seek", json={"position_seconds": 90.5})

        assert resp.status_code == 200
        assert resp.json()["position_seconds"] == 90.5
        assert _ctrl.get_state("default").position_seconds == 90.5

    def test_seek_clamps_negative(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        resp = client.post("/media/seek", json={"position_seconds": -10})

        assert resp.json()["position_seconds"] == 0.0


# ── GET /media/now-playing ──────────────────────────────────────

class TestNowPlayingEndpoint:
    def test_nothing_playing(self, client):
        resp = client.get("/media/now-playing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_playing"] is False
        assert data["item"] is None

    def test_with_active_playback(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        item = _make_item()
        _ctrl.set_state("default", PlaybackState(
            is_playing=True,
            current_item=item,
            position_seconds=42.0,
            volume=0.8,
            target_room="default",
        ))

        resp = client.get("/media/now-playing")
        data = resp.json()
        assert data["is_playing"] is True
        assert data["item"]["title"] == "Test Song"
        assert data["position_seconds"] == 42.0
        assert data["volume"] == 0.8

    def test_now_playing_room_filter(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        item = _make_item()
        _ctrl.set_state("kitchen", PlaybackState(
            is_playing=True, current_item=item, target_room="kitchen",
        ))

        resp = client.get("/media/now-playing?room=kitchen")
        assert resp.json()["is_playing"] is True

        resp2 = client.get("/media/now-playing?room=bedroom")
        assert resp2.json()["is_playing"] is False

    def test_now_playing_album_art(self, client):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        item = _make_item()
        _ctrl.set_state("default", PlaybackState(
            is_playing=True, current_item=item,
        ))

        data = client.get("/media/now-playing").json()
        assert data["item"]["album_art_url"] == "http://example.com/art.jpg"


# ── GET /media/search ───────────────────────────────────────────

class TestSearchEndpoint:
    def test_search_empty_query(self, client):
        resp = client.get("/media/search?q=")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_search_returns_results(self, client):
        from cortex.admin.media import _ctrl

        mock_results = [
            {"id": "1", "title": "Song A", "artist": "Artist A",
             "album": "", "duration_seconds": 200, "provider": "local",
             "media_type": "song", "album_art_url": ""},
            {"id": "2", "title": "Song B", "artist": "Artist B",
             "album": "", "duration_seconds": 180, "provider": "youtube_music",
             "media_type": "song", "album_art_url": ""},
        ]

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock, return_value=mock_results,
        ):
            resp = client.get("/media/search?q=test")

        data = resp.json()
        assert data["total"] == 2
        assert data["results"][0]["title"] == "Song A"
        assert data["results"][1]["provider"] == "youtube_music"

    def test_search_with_provider_filter(self, client):
        from cortex.admin.media import _ctrl

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock, return_value=[],
        ) as mock_sp:
            client.get("/media/search?q=test&provider=plex")

        mock_sp.assert_called_once_with("test", "plex")

    def test_search_no_provider_passes_none(self, client):
        from cortex.admin.media import _ctrl

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock, return_value=[],
        ) as mock_sp:
            client.get("/media/search?q=test")

        mock_sp.assert_called_once_with("test", None)


# ── GET /media/queue ────────────────────────────────────────────

class TestQueueEndpoint:
    def test_empty_queue(self, client):
        resp = client.get("/media/queue")
        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    def test_queue_with_items(self, client):
        from cortex.admin.media import _ctrl

        _ctrl.add_to_queue("default", {"title": "A", "artist": "X"})
        _ctrl.add_to_queue("default", {"title": "B", "artist": "Y"})

        resp = client.get("/media/queue")
        assert len(resp.json()["queue"]) == 2

    def test_queue_room_isolation(self, client):
        from cortex.admin.media import _ctrl

        _ctrl.add_to_queue("kitchen", {"title": "A"})

        resp = client.get("/media/queue?room=bedroom")
        assert resp.json()["queue"] == []

    def test_queue_default_room(self, client):
        resp = client.get("/media/queue")
        assert resp.json()["room"] == "default"


# ── POST /media/queue/add ───────────────────────────────────────

class TestQueueAddEndpoint:
    def test_add_to_queue(self, client):
        from cortex.admin.media import _ctrl

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock,
            return_value=[{"id": "1", "title": "Queued", "artist": "Art"}],
        ):
            resp = client.post("/media/queue/add", json={"query": "queue me"})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["item"]["title"] == "Queued"
        assert len(_ctrl.get_queue("default")) == 1

    def test_add_to_queue_no_results(self, client):
        from cortex.admin.media import _ctrl

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock, return_value=[],
        ):
            resp = client.post("/media/queue/add", json={"query": "nothing"})

        assert resp.json()["ok"] is False

    def test_add_to_queue_specific_room(self, client):
        from cortex.admin.media import _ctrl

        with patch.object(
            _ctrl, "search_providers",
            new_callable=AsyncMock,
            return_value=[{"id": "1", "title": "Q", "artist": "A"}],
        ):
            resp = client.post(
                "/media/queue/add",
                json={"query": "test", "room": "kitchen"},
            )

        assert resp.json()["room"] == "kitchen"
        assert len(_ctrl.get_queue("kitchen")) == 1


# ── POST /media/queue/clear ─────────────────────────────────────

class TestQueueClearEndpoint:
    def test_clear_queue(self, client):
        from cortex.admin.media import _ctrl

        _ctrl.add_to_queue("default", {"title": "A"})
        _ctrl.add_to_queue("default", {"title": "B"})
        resp = client.post("/media/queue/clear", json={})

        assert resp.status_code == 200
        assert _ctrl.get_queue("default") == []

    def test_clear_queue_specific_room(self, client):
        from cortex.admin.media import _ctrl

        _ctrl.add_to_queue("kitchen", {"title": "A"})
        _ctrl.add_to_queue("default", {"title": "B"})
        client.post("/media/queue/clear", json={"room": "kitchen"})

        assert _ctrl.get_queue("kitchen") == []
        assert len(_ctrl.get_queue("default")) == 1


# ── MEDIA_STATE WebSocket message format ─────────────────────────

class TestMediaStateBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_media_state_format(self):
        from cortex.avatar.broadcast import broadcast_media_state
        from cortex.media.base import PlaybackState

        sent_messages = []

        async def _mock_broadcast(room, msg):
            sent_messages.append((room, msg))

        item = _make_item()
        state = PlaybackState(
            is_playing=True,
            current_item=item,
            position_seconds=30.0,
            volume=0.7,
            target_room="living_room",
        )
        queue = [{"title": "Next Song", "artist": "Someone"}]

        with patch(
            "cortex.avatar.broadcast.broadcast_to_room", _mock_broadcast,
        ):
            await broadcast_media_state("living_room", state, queue)

        assert len(sent_messages) == 1
        room, msg = sent_messages[0]
        assert room == "living_room"
        assert msg["type"] == "MEDIA_STATE"
        assert msg["is_playing"] is True
        assert msg["item"]["title"] == "Test Song"
        assert msg["item"]["artist"] == "Test Artist"
        assert msg["item"]["album_art_url"] == "http://example.com/art.jpg"
        assert msg["item"]["duration_seconds"] == 210
        assert msg["item"]["provider"] == "local"
        assert msg["position_seconds"] == 30.0
        assert msg["volume"] == 0.7
        assert msg["target_room"] == "living_room"
        assert len(msg["queue"]) == 1
        assert msg["queue"][0]["title"] == "Next Song"

    @pytest.mark.asyncio
    async def test_broadcast_media_state_no_item(self):
        from cortex.avatar.broadcast import broadcast_media_state
        from cortex.media.base import PlaybackState

        sent_messages = []

        async def _mock_broadcast(room, msg):
            sent_messages.append(msg)

        state = PlaybackState(is_playing=False)

        with patch(
            "cortex.avatar.broadcast.broadcast_to_room", _mock_broadcast,
        ):
            await broadcast_media_state("default", state)

        msg = sent_messages[0]
        assert msg["type"] == "MEDIA_STATE"
        assert msg["is_playing"] is False
        assert msg["item"] is None
        assert msg["queue"] == []

    @pytest.mark.asyncio
    async def test_broadcast_media_state_empty_queue(self):
        from cortex.avatar.broadcast import broadcast_media_state
        from cortex.media.base import PlaybackState

        sent_messages = []

        async def _mock_broadcast(room, msg):
            sent_messages.append(msg)

        state = PlaybackState(is_playing=True, current_item=_make_item())

        with patch(
            "cortex.avatar.broadcast.broadcast_to_room", _mock_broadcast,
        ):
            await broadcast_media_state("default", state, [])

        assert sent_messages[0]["queue"] == []

    @pytest.mark.asyncio
    async def test_broadcast_target_room_from_state(self):
        from cortex.avatar.broadcast import broadcast_media_state
        from cortex.media.base import PlaybackState

        sent_messages = []

        async def _mock_broadcast(room, msg):
            sent_messages.append(msg)

        state = PlaybackState(
            is_playing=True,
            current_item=_make_item(),
            target_room="bedroom",
        )

        with patch(
            "cortex.avatar.broadcast.broadcast_to_room", _mock_broadcast,
        ):
            await broadcast_media_state("bedroom", state)

        assert sent_messages[0]["target_room"] == "bedroom"


# ── MediaController unit tests ───────────────────────────────────

class TestMediaController:
    def test_state_isolation_by_room(self):
        from cortex.admin.media import _ctrl
        from cortex.media.base import PlaybackState

        _ctrl.set_state("kitchen", PlaybackState(is_playing=True, target_room="kitchen"))
        _ctrl.set_state("bedroom", PlaybackState(is_playing=False, target_room="bedroom"))

        assert _ctrl.get_state("kitchen").is_playing is True
        assert _ctrl.get_state("bedroom").is_playing is False
        assert _ctrl.get_state("unknown").is_playing is False

    def test_queue_operations(self):
        from cortex.admin.media import _ctrl

        assert _ctrl.get_queue("room1") == []
        _ctrl.add_to_queue("room1", {"title": "A"})
        _ctrl.add_to_queue("room1", {"title": "B"})
        assert len(_ctrl.get_queue("room1")) == 2

        _ctrl.clear_queue("room1")
        assert _ctrl.get_queue("room1") == []

    @pytest.mark.asyncio
    async def test_search_providers_parallel(self):
        from cortex.admin.media import _ctrl

        item = _make_item()

        with patch("cortex.media.local_library.LocalLibraryProvider") as MockLocal, \
             patch("cortex.media.youtube_music.YouTubeMusicProvider") as MockYT, \
             patch("cortex.media.plex.PlexProvider") as MockPlex, \
             patch("cortex.media.audiobookshelf.AudiobookshelfProvider") as MockAB, \
             patch("cortex.media.podcasts.PodcastProvider") as MockPod:
            MockLocal.return_value.search = AsyncMock(return_value=[item])
            MockYT.return_value.search = AsyncMock(return_value=[])
            MockPlex.return_value.search = AsyncMock(return_value=[])
            MockAB.return_value.search = AsyncMock(return_value=[])
            MockPod.return_value.search = AsyncMock(return_value=[])

            results = await _ctrl.search_providers("test")

        assert len(results) == 1
        assert results[0]["title"] == "Test Song"

    @pytest.mark.asyncio
    async def test_search_providers_with_filter(self):
        from cortex.admin.media import _ctrl

        item = _make_item(provider="plex")

        with patch("cortex.media.plex.PlexProvider") as MockPlex:
            MockPlex.return_value.search = AsyncMock(return_value=[item])

            results = await _ctrl.search_providers("test", provider="plex")

        assert len(results) == 1
        assert results[0]["provider"] == "plex"

    @pytest.mark.asyncio
    async def test_search_providers_handles_failure(self):
        from cortex.admin.media import _ctrl

        with patch("cortex.media.local_library.LocalLibraryProvider") as MockLocal, \
             patch("cortex.media.youtube_music.YouTubeMusicProvider") as MockYT, \
             patch("cortex.media.plex.PlexProvider") as MockPlex, \
             patch("cortex.media.audiobookshelf.AudiobookshelfProvider") as MockAB, \
             patch("cortex.media.podcasts.PodcastProvider") as MockPod:
            MockLocal.return_value.search = AsyncMock(side_effect=Exception("fail"))
            MockYT.return_value.search = AsyncMock(return_value=[])
            MockPlex.return_value.search = AsyncMock(return_value=[])
            MockAB.return_value.search = AsyncMock(return_value=[])
            MockPod.return_value.search = AsyncMock(return_value=[])

            results = await _ctrl.search_providers("test")

        assert results == []


# ── Integration: play → state → now-playing round-trip ───────────

class TestPlaybackRoundTrip:
    def test_play_then_now_playing(self, client):
        from cortex.admin.media import _ctrl

        item = _make_item()
        _ctrl._router = _mock_router()
        pp = _patch_providers(local=[item])
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            client.post("/media/play", json={"query": "test"})

        resp = client.get("/media/now-playing")
        data = resp.json()
        assert data["is_playing"] is True
        assert data["item"]["title"] == "Test Song"

    def test_play_pause_resume_stop(self, client):
        from cortex.admin.media import _ctrl

        item = _make_item()
        _ctrl._router = _mock_router()
        pp = _patch_providers(local=[item])
        with pp["local"], pp["youtube_music"], pp["plex"], pp["audiobookshelf"], pp["podcasts"]:
            client.post("/media/play", json={"query": "test"})

        assert _ctrl.get_state("default").is_playing is True

        client.post("/media/pause", json={})
        assert _ctrl.get_state("default").is_playing is False

        client.post("/media/resume", json={})
        assert _ctrl.get_state("default").is_playing is True

        client.post("/media/stop", json={})
        assert _ctrl.get_state("default").is_playing is False
        assert _ctrl.get_state("default").current_item is None

    def test_volume_then_now_playing(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        client.post("/media/volume", json={"level": 42})

        resp = client.get("/media/now-playing")
        assert resp.json()["volume"] == 0.42

    def test_seek_then_now_playing(self, client):
        from cortex.admin.media import _ctrl

        _ctrl._router = _mock_router()
        client.post("/media/seek", json={"position_seconds": 60.0})

        resp = client.get("/media/now-playing")
        assert resp.json()["position_seconds"] == 60.0
