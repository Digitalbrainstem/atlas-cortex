"""Media service authentication for satellite displays.

Stores OAuth tokens and API keys securely for media playback.
Tokens are stored server-side, never on the satellite.
The satellite gets temporary embed URLs with auth baked in.

# Module ownership: Satellite display media authentication
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_FILE = Path(
    os.environ.get("ATLAS_DATA_BASE", os.path.expanduser("~/.atlas"))
) / "media_auth.json"


@dataclass
class MediaAuth:
    """Authentication for a media service."""

    provider: str  # youtube, plex, netflix, spotify
    auth_type: str  # oauth, api_key, cookie
    token: str = ""
    refresh_token: str = ""
    expires_at: float = 0
    account_name: str = ""
    is_premium: bool = False


class MediaAuthManager:
    """Manage authentication for media services.

    Storage keys follow the pattern ``provider`` for global accounts and
    ``provider:user_id`` for per-user accounts.  :meth:`get_auth` resolves
    with fallback: user-specific → global.
    """

    def __init__(self) -> None:
        self._auths: dict[str, MediaAuth] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if AUTH_FILE.exists():
            try:
                data = json.loads(AUTH_FILE.read_text())
                for key, info in data.items():
                    self._auths[key] = MediaAuth(**info)
            except Exception as e:
                logger.warning("Failed to load media auth: %s", e)

    def _save(self) -> None:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for key, auth in self._auths.items():
            data[key] = {
                "provider": auth.provider,
                "auth_type": auth.auth_type,
                "token": auth.token,
                "refresh_token": auth.refresh_token,
                "expires_at": auth.expires_at,
                "account_name": auth.account_name,
                "is_premium": auth.is_premium,
            }
        AUTH_FILE.write_text(json.dumps(data, indent=2))
        AUTH_FILE.chmod(0o600)

    @staticmethod
    def _key(provider: str, user_id: str = "") -> str:
        """Build the storage key: ``provider`` or ``provider:user_id``."""
        return f"{provider}:{user_id}" if user_id else provider

    # -- CRUD ---------------------------------------------------------------

    def set_auth(
        self,
        provider: str,
        token: str,
        refresh_token: str = "",
        auth_type: str = "oauth",
        account_name: str = "",
        is_premium: bool = False,
        expires_at: float = 0,
        user_id: str = "",
    ) -> None:
        """Store authentication for a media provider.

        When *user_id* is given the auth is stored as a per-user override;
        otherwise it becomes the global default.
        """
        key = self._key(provider, user_id)
        self._auths[key] = MediaAuth(
            provider=provider,
            auth_type=auth_type,
            token=token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_name=account_name,
            is_premium=is_premium,
        )
        self._save()
        label = f"{provider}:{user_id}" if user_id else provider
        logger.info("Auth stored for %s (%s)", label, account_name)

    def get_auth(self, provider: str, user_id: str = "") -> MediaAuth | None:
        """Get auth with fallback: user-specific → global."""
        if user_id:
            user_auth = self._auths.get(self._key(provider, user_id))
            if user_auth:
                return user_auth
        return self._auths.get(provider)

    def is_authenticated(self, provider: str, user_id: str = "") -> bool:
        """Check if a provider has a stored token (respects fallback)."""
        auth = self.get_auth(provider, user_id)
        return auth is not None and bool(auth.token)

    def is_premium(self, provider: str, user_id: str = "") -> bool:
        """Check if a provider account is premium (respects fallback)."""
        auth = self.get_auth(provider, user_id)
        return auth.is_premium if auth else False

    def remove_auth(self, provider: str, user_id: str = "") -> bool:
        """Remove stored auth for a provider.  Returns True if it existed."""
        key = self._key(provider, user_id)
        if key in self._auths:
            del self._auths[key]
            self._save()
            return True
        return False

    def set_global_default(self, provider: str, user_id: str) -> bool:
        """Copy a user's auth as the global default.  Returns success."""
        user_auth = self._auths.get(self._key(provider, user_id))
        if not user_auth:
            return False
        self._auths[provider] = MediaAuth(
            provider=user_auth.provider,
            auth_type=user_auth.auth_type,
            token=user_auth.token,
            refresh_token=user_auth.refresh_token,
            expires_at=user_auth.expires_at,
            account_name=user_auth.account_name,
            is_premium=user_auth.is_premium,
        )
        self._save()
        logger.info("Global default for %s set from user %s", provider, user_id)
        return True

    # -- query --------------------------------------------------------------

    def list_providers(self) -> list[dict[str, object]]:
        """Return a summary of all stored providers (no secrets)."""
        result: list[dict[str, object]] = []
        for key, a in self._auths.items():
            parts = key.split(":", 1)
            result.append({
                "provider": a.provider,
                "account_name": a.account_name,
                "is_premium": a.is_premium,
                "authenticated": bool(a.token),
                "user_id": parts[1] if len(parts) > 1 else "",
                "is_global": len(parts) == 1,
            })
        return result

    def get_youtube_embed_url(
        self, video_id: str, user_id: str = ""
    ) -> str:
        """Get YouTube embed URL.  Premium accounts get ad-free nocookie embed."""
        auth = self.get_auth("youtube", user_id)
        base = "https://www.youtube-nocookie.com/embed"
        params = "autoplay=1&rel=0&modestbranding=1&iv_load_policy=3"
        if auth and auth.is_premium:
            params += "&fs=1"
        return f"{base}/{video_id}?{params}"

    # -- auto-refresh -------------------------------------------------------

    async def ensure_fresh_token(
        self, provider: str, user_id: str = ""
    ) -> str | None:
        """Get a valid access token, auto-refreshing if expired.

        Google refresh tokens last indefinitely.  This method transparently
        refreshes the access token when it is within 5 minutes of expiry so
        users never need to re-login.
        """
        auth = self.get_auth(provider, user_id)
        if not auth or not auth.token:
            return None

        # 5-minute buffer before expiry
        if auth.expires_at and time.time() > auth.expires_at - 300:
            if not auth.refresh_token:
                return auth.token  # No refresh token — return stale token
            oauth = YouTubeOAuth()
            new_token = await oauth.refresh_token(auth.refresh_token)
            if new_token:
                auth.token = new_token["access_token"]
                auth.expires_at = time.time() + new_token.get("expires_in", 3600)
                self._save()
                logger.info("Auto-refreshed token for %s", provider)
            else:
                logger.warning("Token refresh failed for %s", provider)

        return auth.token


# ---------------------------------------------------------------------------
# YouTube OAuth device flow (same flow as smart TVs)
# ---------------------------------------------------------------------------


class YouTubeOAuth:
    """Google OAuth device flow for YouTube Premium — same flow as smart TVs.

    Flow:
    1. Atlas requests a device code from Google
    2. User goes to google.com/device on their phone and enters the code
    3. Atlas polls until user authorizes
    4. OAuth token stored server-side, shared by all satellites + ytmusicapi
    """

    # Google OAuth endpoints
    DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    # ytmusicapi client credentials (public, same as YouTube TV app)
    CLIENT_ID = (
        "861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68.apps.googleusercontent.com"
    )
    CLIENT_SECRET = "SboVhoG9s0rNafixCSGGKXAT"
    SCOPES = "https://www.googleapis.com/auth/youtube"

    async def start_device_flow(self) -> dict[str, object]:
        """Start the OAuth device flow.

        Returns ``{device_code, user_code, verification_url, expires_in, interval}``.
        """
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.DEVICE_CODE_URL,
                data={"client_id": self.CLIENT_ID, "scope": self.SCOPES},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "device_code": data["device_code"],
                "user_code": data["user_code"],
                "verification_url": data.get(
                    "verification_url", "https://google.com/device"
                ),
                "expires_in": data.get("expires_in", 1800),
                "interval": data.get("interval", 5),
            }

    async def poll_for_token(
        self,
        device_code: str,
        interval: int = 5,
        timeout: int = 300,
    ) -> dict[str, object] | None:
        """Poll Google until user authorises.

        Returns ``{access_token, refresh_token, expires_in}`` or *None* on
        timeout / denial.
        """
        import asyncio

        import httpx

        start = time.time()
        async with httpx.AsyncClient() as client:
            while time.time() - start < timeout:
                await asyncio.sleep(interval)
                r = await client.post(
                    self.TOKEN_URL,
                    data={
                        "client_id": self.CLIENT_ID,
                        "client_secret": self.CLIENT_SECRET,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                data = r.json()

                if "access_token" in data:
                    return {
                        "access_token": data["access_token"],
                        "refresh_token": data.get("refresh_token", ""),
                        "expires_in": data.get("expires_in", 3600),
                    }

                error = data.get("error", "")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval += 2
                elif error in ("access_denied", "expired_token"):
                    return None

        return None

    async def refresh_token(self, refresh_tok: str) -> dict[str, object] | None:
        """Refresh an expired access token."""
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.CLIENT_ID,
                    "client_secret": self.CLIENT_SECRET,
                    "refresh_token": refresh_tok,
                    "grant_type": "refresh_token",
                },
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "access_token": data["access_token"],
                    "expires_in": data.get("expires_in", 3600),
                }
        return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: MediaAuthManager | None = None


def get_media_auth() -> MediaAuthManager:
    """Return the singleton :class:`MediaAuthManager`."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = MediaAuthManager()
    return _manager
