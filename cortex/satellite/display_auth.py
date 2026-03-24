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
    """Manage authentication for media services."""

    def __init__(self) -> None:
        self._auths: dict[str, MediaAuth] = {}
        self._load()

    def _load(self) -> None:
        if AUTH_FILE.exists():
            try:
                data = json.loads(AUTH_FILE.read_text())
                for provider, info in data.items():
                    self._auths[provider] = MediaAuth(**info)
            except Exception as e:
                logger.warning("Failed to load media auth: %s", e)

    def _save(self) -> None:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for provider, auth in self._auths.items():
            data[provider] = {
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

    def set_auth(
        self,
        provider: str,
        token: str,
        refresh_token: str = "",
        auth_type: str = "oauth",
        account_name: str = "",
        is_premium: bool = False,
        expires_at: float = 0,
    ) -> None:
        """Store authentication for a media provider."""
        self._auths[provider] = MediaAuth(
            provider=provider,
            auth_type=auth_type,
            token=token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_name=account_name,
            is_premium=is_premium,
        )
        self._save()
        logger.info("Auth stored for %s (%s)", provider, account_name)

    def get_auth(self, provider: str) -> MediaAuth | None:
        """Return stored auth for *provider*, or ``None``."""
        return self._auths.get(provider)

    def is_authenticated(self, provider: str) -> bool:
        """Check if a provider has a stored token."""
        auth = self._auths.get(provider)
        return auth is not None and bool(auth.token)

    def is_premium(self, provider: str) -> bool:
        """Check if a provider account is premium."""
        auth = self._auths.get(provider)
        return auth.is_premium if auth else False

    def remove_auth(self, provider: str) -> bool:
        """Remove stored auth for a provider. Returns True if it existed."""
        if provider in self._auths:
            del self._auths[provider]
            self._save()
            return True
        return False

    def list_providers(self) -> list[dict[str, object]]:
        """Return a summary of all stored providers (no secrets)."""
        return [
            {
                "provider": a.provider,
                "account_name": a.account_name,
                "is_premium": a.is_premium,
                "authenticated": bool(a.token),
            }
            for a in self._auths.values()
        ]

    def get_youtube_embed_url(self, video_id: str) -> str:
        """Get YouTube embed URL.  Premium accounts get ad-free nocookie embed."""
        auth = self._auths.get("youtube")
        base = "https://www.youtube-nocookie.com/embed"
        params = "autoplay=1&rel=0&modestbranding=1&iv_load_policy=3"
        if auth and auth.is_premium:
            params += "&fs=1"
        return f"{base}/{video_id}?{params}"


# Singleton
_manager: MediaAuthManager | None = None


def get_media_auth() -> MediaAuthManager:
    """Return the singleton :class:`MediaAuthManager`."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = MediaAuthManager()
    return _manager
