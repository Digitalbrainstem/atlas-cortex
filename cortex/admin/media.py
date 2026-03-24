"""Admin API endpoints for media & entertainment.

# Module ownership: Media admin endpoints — providers, history, podcasts, library
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from cortex.admin import helpers as _h
from cortex.admin.helpers import require_admin

router = APIRouter()


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


# ── Now playing ──────────────────────────────────────────────────

@router.get("/media/now-playing")
async def now_playing(_: dict = Depends(require_admin)):
    """Return current playback state (if any)."""
    return {
        "is_playing": False,
        "item": None,
        "position": 0,
        "volume": 0.5,
        "room": "",
    }


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
    )
    return {"ok": True}


@router.delete("/media/auth/{provider}")
async def remove_media_auth(provider: str, _: dict = Depends(require_admin)):
    """Remove stored auth for a media provider."""
    from cortex.satellite.display_auth import get_media_auth

    return {"ok": get_media_auth().remove_auth(provider)}


# ── YouTube OAuth device flow ────────────────────────────────────


@router.post("/media/auth/youtube/start")
async def start_youtube_auth(_: dict = Depends(require_admin)):
    """Start YouTube OAuth device flow.  Returns code for user to enter."""
    from cortex.satellite.display_auth import YouTubeOAuth

    oauth = YouTubeOAuth()
    flow = await oauth.start_device_flow()
    return {
        "user_code": flow["user_code"],
        "verification_url": flow["verification_url"],
        "message": f"Go to {flow['verification_url']} and enter code: {flow['user_code']}",
        "device_code": flow["device_code"],
        "expires_in": flow["expires_in"],
    }


class YouTubeCompleteRequest(BaseModel):
    device_code: str
    timeout: int = 120


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
        mgr.set_auth(
            provider="youtube",
            token=token["access_token"],
            refresh_token=token.get("refresh_token", ""),
            auth_type="oauth",
            account_name="YouTube Premium",
            is_premium=True,
            expires_at=_time.time() + token.get("expires_in", 3600),
        )
        return {"ok": True, "message": "YouTube account linked!"}

    return {"ok": False, "message": "Authorization timed out or was denied"}
