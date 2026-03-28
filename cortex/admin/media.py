"""Admin API endpoints for media & entertainment.

# Module ownership: Media admin endpoints — providers, history, podcasts, library,
# playback control, search, queue management.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Shared media controller (module-level singleton) ─────────────

class _MediaController:
    """Thin wrapper around PlaybackRouter + provider search with room-keyed state."""

    def __init__(self) -> None:
        from cortex.media.base import PlaybackState
        self._states: dict[str, PlaybackState] = {}
        self._queues: dict[str, list[dict[str, Any]]] = {}
        self._router: Any | None = None

    def _get_router(self) -> Any:
        if self._router is None:
            from cortex.media.router import PlaybackRouter
            self._router = PlaybackRouter()
        return self._router

    def get_state(self, room: str) -> Any:
        from cortex.media.base import PlaybackState
        return self._states.get(room, PlaybackState())

    def set_state(self, room: str, state: Any) -> None:
        self._states[room] = state

    def get_queue(self, room: str) -> list[dict[str, Any]]:
        return self._queues.get(room, [])

    def add_to_queue(self, room: str, item: dict[str, Any]) -> None:
        self._queues.setdefault(room, []).append(item)

    def clear_queue(self, room: str) -> None:
        self._queues[room] = []

    async def broadcast_state(self, room: str) -> None:
        """Push MEDIA_STATE to avatar displays for the given room."""
        try:
            from cortex.avatar.broadcast import broadcast_media_state
            state = self.get_state(room)
            queue = self.get_queue(room)
            await broadcast_media_state(room, state, queue)
        except Exception:
            logger.debug("broadcast_state failed for room=%s", room, exc_info=True)

    async def search_providers(
        self, query: str, provider: str | None = None,
    ) -> list[dict[str, Any]]:
        from cortex.media.audiobookshelf import AudiobookshelfProvider
        from cortex.media.local_library import LocalLibraryProvider
        from cortex.media.plex import PlexProvider
        from cortex.media.podcasts import PodcastProvider
        from cortex.media.youtube_music import YouTubeMusicProvider

        providers_map = {
            "local": LocalLibraryProvider,
            "youtube_music": YouTubeMusicProvider,
            "plex": PlexProvider,
            "audiobookshelf": AudiobookshelfProvider,
            "podcasts": PodcastProvider,
        }

        if provider and provider in providers_map:
            classes = [providers_map[provider]]
        else:
            classes = list(providers_map.values())

        async def _search(cls: type) -> list[dict[str, Any]]:
            try:
                inst = cls()
                items = await inst.search(query)
                return [
                    {
                        "id": it.id,
                        "title": it.title,
                        "artist": it.artist,
                        "album": it.album,
                        "duration_seconds": it.duration_seconds,
                        "provider": it.provider,
                        "media_type": it.media_type,
                        "album_art_url": it.metadata.get("album_art_url", ""),
                    }
                    for it in items
                ]
            except Exception:
                return []

        results_nested = await asyncio.gather(*[_search(c) for c in classes])
        return [item for sublist in results_nested for item in sublist]


_ctrl = _MediaController()


# ── Playback control endpoints ───────────────────────────────────


class PlayRequest(BaseModel):
    query: str
    provider: str | None = None
    room: str = ""


@router.post("/media/play")
async def play_media(req: PlayRequest, _: dict = Depends(require_admin)):
    """Search and start playback."""
    from cortex.media.base import MediaItem, PlaybackState
    from cortex.media.local_library import LocalLibraryProvider
    from cortex.media.youtube_music import YouTubeMusicProvider

    room = req.room or "default"
    results: list[MediaItem] = []

    if not req.provider or req.provider == "local":
        try:
            results.extend(await LocalLibraryProvider().search(req.query))
        except Exception:
            pass

    if not results and (not req.provider or req.provider == "youtube_music"):
        try:
            yt = YouTubeMusicProvider()
            if await yt.health():
                results.extend(await yt.search(req.query))
        except Exception:
            pass

    if not results:
        return {"ok": False, "error": f"No results for \"{req.query}\""}

    item = results[0]
    pb_router = _ctrl._get_router()
    target = await pb_router.resolve_target(room)
    if target:
        stream_url = item.stream_url
        if not stream_url:
            try:
                if item.provider == "youtube_music":
                    stream_url = await YouTubeMusicProvider().get_stream_url(item.id) or ""
                elif item.provider == "local":
                    stream_url = await LocalLibraryProvider().get_stream_url(item.id) or ""
            except Exception:
                stream_url = ""
        if stream_url:
            await pb_router.play(stream_url, target)

    state = PlaybackState(
        is_playing=True,
        current_item=item,
        position_seconds=0,
        target_room=room,
    )
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)

    return {
        "ok": True,
        "item": {
            "title": item.title,
            "artist": item.artist,
            "album": item.album,
            "provider": item.provider,
            "duration_seconds": item.duration_seconds,
            "album_art_url": item.metadata.get("album_art_url", ""),
        },
        "room": room,
    }


class RoomRequest(BaseModel):
    room: str = ""


@router.post("/media/pause")
async def pause_media(req: RoomRequest, _: dict = Depends(require_admin)):
    """Pause playback in a room."""
    room = req.room or "default"
    pb_router = _ctrl._get_router()
    target = await pb_router.resolve_target(room)
    if target:
        await pb_router.pause(target)
    state = _ctrl.get_state(room)
    state.is_playing = False
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "room": room}


@router.post("/media/resume")
async def resume_media(req: RoomRequest, _: dict = Depends(require_admin)):
    """Resume playback in a room."""
    room = req.room or "default"
    state = _ctrl.get_state(room)
    if state.current_item:
        pb_router = _ctrl._get_router()
        target = await pb_router.resolve_target(room)
        if target and state.current_item.stream_url:
            await pb_router.play(state.current_item.stream_url, target)
    state.is_playing = True
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "room": room}


@router.post("/media/stop")
async def stop_media(req: RoomRequest, _: dict = Depends(require_admin)):
    """Stop playback in a room."""
    from cortex.media.base import PlaybackState

    room = req.room or "default"
    pb_router = _ctrl._get_router()
    target = await pb_router.resolve_target(room)
    if target:
        await pb_router.stop(target)
    _ctrl.set_state(room, PlaybackState())
    await _ctrl.broadcast_state(room)
    return {"ok": True, "room": room}


@router.post("/media/next")
async def next_track(req: RoomRequest, _: dict = Depends(require_admin)):
    """Skip to the next track in the queue."""
    from cortex.media.base import PlaybackState

    room = req.room or "default"
    queue = _ctrl.get_queue(room)
    if queue:
        next_item = queue.pop(0)
        _ctrl._queues[room] = queue
        state = PlaybackState(
            is_playing=True,
            position_seconds=0,
            target_room=room,
        )
        _ctrl.set_state(room, state)
        await _ctrl.broadcast_state(room)
        return {"ok": True, "item": next_item, "room": room}
    return {"ok": True, "room": room, "message": "Queue empty"}


@router.post("/media/previous")
async def previous_track(req: RoomRequest, _: dict = Depends(require_admin)):
    """Go to the previous track."""
    room = req.room or "default"
    state = _ctrl.get_state(room)
    state.position_seconds = 0
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "room": room}


class VolumeRequest(BaseModel):
    level: int
    room: str = ""


@router.post("/media/volume")
async def set_volume(req: VolumeRequest, _: dict = Depends(require_admin)):
    """Set volume (0–100) for a room."""
    room = req.room or "default"
    vol = max(0, min(100, req.level)) / 100.0
    pb_router = _ctrl._get_router()
    target = await pb_router.resolve_target(room)
    if target:
        await pb_router.set_volume(target, vol)
    state = _ctrl.get_state(room)
    state.volume = vol
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "volume": req.level, "room": room}


class SatelliteVolumeRequest(BaseModel):
    level: int
    satellite_id: str


@router.post("/media/satellite-volume")
async def set_satellite_volume(
    req: SatelliteVolumeRequest,
    _: dict = Depends(require_admin),
):
    """Set a satellite's LOCAL system volume (ALSA/PipeWire) via the satellite agent."""
    vol = max(0, min(100, req.level)) / 100.0
    try:
        from cortex.satellite.websocket import send_command
        sent = await send_command(req.satellite_id, "volume", {"level": vol})
        if not sent:
            return {"ok": False, "error": f"Satellite {req.satellite_id} not connected"}
    except Exception as exc:
        logger.warning("satellite-volume error: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "volume": req.level, "satellite_id": req.satellite_id}


# ── All rooms state ──────────────────────────────────────────────


@router.get("/media/all-rooms")
async def all_rooms_state(_: dict = Depends(require_admin)):
    """Return playback state for ALL rooms that have active media."""
    rooms: list[dict[str, Any]] = []
    for room_key, state in _ctrl._states.items():
        item_dict = None
        if state.current_item:
            it = state.current_item
            item_dict = {
                "title": it.title,
                "artist": it.artist,
                "album": it.album,
                "album_art_url": it.metadata.get("album_art_url", ""),
                "duration_seconds": it.duration_seconds,
                "provider": it.provider,
            }
        rooms.append({
            "room": room_key,
            "is_playing": state.is_playing,
            "item": item_dict,
            "position_seconds": state.position_seconds,
            "volume": state.volume,
            "queue": _ctrl.get_queue(room_key),
        })
    return {"rooms": rooms}


class SeekRequest(BaseModel):
    position_seconds: float
    room: str = ""


@router.post("/media/seek")
async def seek_media(req: SeekRequest, _: dict = Depends(require_admin)):
    """Seek to a position in the current track."""
    room = req.room or "default"
    state = _ctrl.get_state(room)
    state.position_seconds = max(0.0, req.position_seconds)
    _ctrl.set_state(room, state)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "position_seconds": state.position_seconds, "room": room}


# ── State & search ───────────────────────────────────────────────


@router.get("/media/now-playing")
async def now_playing(
    _: dict = Depends(require_admin),
    room: str = Query(""),
):
    """Return current playback state for a room."""
    target_room = room or "default"
    state = _ctrl.get_state(target_room)
    item_dict = None
    if state.current_item:
        it = state.current_item
        item_dict = {
            "title": it.title,
            "artist": it.artist,
            "album": it.album,
            "album_art_url": it.metadata.get("album_art_url", ""),
            "duration_seconds": it.duration_seconds,
            "provider": it.provider,
        }
    return {
        "is_playing": state.is_playing,
        "item": item_dict,
        "position_seconds": state.position_seconds,
        "volume": state.volume,
        "room": target_room,
    }


@router.get("/media/search")
async def search_media(
    _: dict = Depends(require_admin),
    q: str = Query(""),
    provider: str = Query(""),
):
    """Search across media providers."""
    if not q:
        return {"results": [], "total": 0}
    results = await _ctrl.search_providers(q, provider or None)
    return {"results": results, "total": len(results)}


# ── Queue ────────────────────────────────────────────────────────


@router.get("/media/queue")
async def get_queue(
    _: dict = Depends(require_admin),
    room: str = Query(""),
):
    """Return the playback queue for a room."""
    target_room = room or "default"
    return {"queue": _ctrl.get_queue(target_room), "room": target_room}


class QueueAddRequest(BaseModel):
    query: str
    provider: str | None = None
    room: str = ""


@router.post("/media/queue/add")
async def add_to_queue(req: QueueAddRequest, _: dict = Depends(require_admin)):
    """Search for a track and add it to the queue."""
    room = req.room or "default"
    results = await _ctrl.search_providers(req.query, req.provider)
    if not results:
        return {"ok": False, "error": f"No results for \"{req.query}\""}
    item = results[0]
    _ctrl.add_to_queue(room, item)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "item": item, "room": room}


class QueueClearRequest(BaseModel):
    room: str = ""


@router.post("/media/queue/clear")
async def clear_queue(req: QueueClearRequest, _: dict = Depends(require_admin)):
    """Clear the playback queue for a room."""
    room = req.room or "default"
    _ctrl.clear_queue(room)
    await _ctrl.broadcast_state(room)
    return {"ok": True, "room": room}


# ── Provider overview ────────────────────────────────────────────

@router.get("/media/providers")
async def list_providers(_: dict = Depends(require_admin)):
    """List configured media providers with health status."""
    from cortex.media.audiobookshelf import AudiobookshelfProvider
    from cortex.media.local_library import LocalLibraryProvider
    from cortex.media.plex import PlexProvider
    from cortex.media.podcasts import PodcastProvider
    from cortex.media.youtube_music import YouTubeMusicProvider

    providers_info = []
    for cls in (
        YouTubeMusicProvider,
        LocalLibraryProvider,
        PlexProvider,
        AudiobookshelfProvider,
        PodcastProvider,
    ):
        p = cls()
        try:
            healthy = await p.health()
        except Exception:
            healthy = False
        providers_info.append({
            "id": p.provider_id,
            "name": p.display_name,
            "healthy": healthy,
            "streaming": p.supports_streaming,
        })
    return {"providers": providers_info}


# ── Playback history ────────────────────────────────────────────

@router.get("/media/history")
async def playback_history(
    _: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user_id: str | None = None,
):
    """Recent playback history."""
    # Resolve Query defaults when called directly (outside FastAPI)
    if not isinstance(page, int):
        page = 1
    if not isinstance(per_page, int):
        per_page = 50
    conn = _h._db()
    where, params = [], []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)

    where_sql = " AND ".join(where) if where else "1=1"
    total = conn.execute(
        f"SELECT COUNT(*) FROM media_playback_history WHERE {where_sql}",
        params,
    ).fetchone()[0]

    offset = (page - 1) * per_page
    cur = conn.execute(
        f"SELECT * FROM media_playback_history WHERE {where_sql} "
        "ORDER BY played_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    return {
        "history": _h._rows(cur),
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── User preferences ────────────────────────────────────────────

@router.get("/media/preferences/{user_id}")
async def user_preferences(user_id: str, _: dict = Depends(require_admin)):
    """Get genre preferences for a user."""
    conn = _h._db()
    cur = conn.execute(
        "SELECT genre, affinity, play_count, last_played "
        "FROM media_preferences WHERE user_id = ? "
        "ORDER BY affinity DESC",
        (user_id,),
    )
    return {"user_id": user_id, "preferences": _h._rows(cur)}


# ── Playback targets ────────────────────────────────────────────

@router.get("/media/targets")
async def playback_targets(_: dict = Depends(require_admin)):
    """List available playback targets."""
    from cortex.media.router import PlaybackRouter

    router_inst = PlaybackRouter()
    targets = await router_inst.get_all_targets()
    return {
        "targets": [
            {
                "type": t.target_type,
                "id": t.target_id,
                "room": t.room,
            }
            for t in targets
        ],
    }


# ── Podcast subscriptions ───────────────────────────────────────

@router.get("/media/podcasts")
async def list_podcasts(_: dict = Depends(require_admin)):
    """List podcast subscriptions."""
    from cortex.media.podcasts import PodcastProvider

    provider = PodcastProvider()
    subs = await provider.list_subscriptions()
    return {"subscriptions": subs}


class PodcastSubscribeRequest(BaseModel):
    feed_url: str


@router.post("/media/podcasts/subscribe")
async def subscribe_podcast(
    req: PodcastSubscribeRequest,
    _: dict = Depends(require_admin),
):
    """Subscribe to a podcast feed."""
    from cortex.media.podcasts import PodcastProvider

    provider = PodcastProvider()
    sub_id = await provider.subscribe(req.feed_url)
    if sub_id:
        return {"ok": True, "subscription_id": sub_id}
    return {"ok": False, "error": "Failed to subscribe"}


# ── Library scan ─────────────────────────────────────────────────

class LibraryScanRequest(BaseModel):
    path: str


@router.post("/media/library/scan")
async def scan_library(
    req: LibraryScanRequest,
    _: dict = Depends(require_admin),
):
    """Trigger a local library scan."""
    from cortex.media.local_library import LocalLibraryProvider

    provider = LocalLibraryProvider()
    count = await provider.scan_directory(req.path)
    return {"ok": True, "files_indexed": count}


# ── Media auth for satellite display ─────────────────────────────


@router.get("/media/auth")
async def list_media_auth(_: dict = Depends(require_admin)):
    """List media service authentication (no secrets)."""
    from cortex.satellite.display_auth import get_media_auth

    return {"providers": get_media_auth().list_providers()}


class MediaAuthRequest(BaseModel):
    token: str = ""
    refresh_token: str = ""
    auth_type: str = "oauth"
    account_name: str = ""
    is_premium: bool = False
    user_id: str = ""


@router.post("/media/auth/{provider}")
async def set_media_auth(
    provider: str,
    req: MediaAuthRequest,
    _: dict = Depends(require_admin),
):
    """Store authentication for a media provider."""
    from cortex.satellite.display_auth import get_media_auth

    mgr = get_media_auth()
    mgr.set_auth(
        provider=provider,
        token=req.token,
        refresh_token=req.refresh_token,
        auth_type=req.auth_type,
        account_name=req.account_name,
        is_premium=req.is_premium,
        user_id=req.user_id,
    )
    return {"ok": True}


@router.delete("/media/auth/{provider}")
async def remove_media_auth(
    provider: str,
    user_id: str = "",
    _: dict = Depends(require_admin),
):
    """Remove stored auth for a media provider."""
    from cortex.satellite.display_auth import get_media_auth

    return {"ok": get_media_auth().remove_auth(provider, user_id=user_id)}


class SetGlobalDefaultRequest(BaseModel):
    user_id: str


@router.post("/media/auth/{provider}/set-global")
async def set_global_default(
    provider: str,
    req: SetGlobalDefaultRequest,
    _: dict = Depends(require_admin),
):
    """Copy a user's auth as the global default for a provider."""
    from cortex.satellite.display_auth import get_media_auth

    ok = get_media_auth().set_global_default(provider, req.user_id)
    if ok:
        return {"ok": True}
    return {"ok": False, "error": f"No auth found for {provider}:{req.user_id}"}


# ── YouTube OAuth device flow ────────────────────────────────────


class YouTubeStartRequest(BaseModel):
    user_id: str = ""


@router.post("/media/auth/youtube/start")
async def start_youtube_auth(
    req: YouTubeStartRequest | None = None,
    _: dict = Depends(require_admin),
):
    """Start YouTube OAuth device flow.  Returns code for user to enter."""
    from cortex.satellite.display_auth import YouTubeOAuth

    oauth = YouTubeOAuth()
    flow = await oauth.start_device_flow()
    user_id = req.user_id if req else ""
    return {
        "user_code": flow["user_code"],
        "verification_url": flow["verification_url"],
        "message": f"Go to {flow['verification_url']} and enter code: {flow['user_code']}",
        "device_code": flow["device_code"],
        "expires_in": flow["expires_in"],
        "user_id": user_id,
    }


class YouTubeCompleteRequest(BaseModel):
    device_code: str
    timeout: int = 120
    user_id: str = ""
    set_global: bool = False
    account_name: str = ""


@router.post("/media/auth/youtube/complete")
async def complete_youtube_auth(
    req: YouTubeCompleteRequest,
    _: dict = Depends(require_admin),
):
    """Poll for YouTube OAuth completion.  Call after user enters the code."""
    import time as _time

    from cortex.satellite.display_auth import YouTubeOAuth, get_media_auth

    oauth = YouTubeOAuth()
    token = await oauth.poll_for_token(req.device_code, timeout=req.timeout)
    if token:
        mgr = get_media_auth()
        account_name = req.account_name or (
            f"{req.user_id}'s YouTube" if req.user_id else "YouTube Premium"
        )
        mgr.set_auth(
            provider="youtube",
            token=token["access_token"],
            refresh_token=token.get("refresh_token", ""),
            auth_type="oauth",
            account_name=account_name,
            is_premium=True,
            expires_at=_time.time() + token.get("expires_in", 3600),
            user_id=req.user_id,
        )
        if req.set_global and req.user_id:
            mgr.set_global_default("youtube", req.user_id)
        return {"ok": True, "message": "YouTube account linked!"}

    return {"ok": False, "message": "Authorization timed out or was denied"}
