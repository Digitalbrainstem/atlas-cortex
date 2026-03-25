"""Per-user authentication for the chat interface.

NOT admin auth — this is for family members using Atlas.
Supports: no auth, PIN, password, WebAuthn passkey/fingerprint.

Module ownership: User chat authentication
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import bcrypt
import jwt

from cortex.auth import get_jwt_secret, _prepare_password

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
CHAT_TOKEN_EXPIRY = int(os.environ.get("CORTEX_CHAT_TOKEN_EXPIRY", "86400"))


@dataclass
class UserAuthConfig:
    """Authentication configuration for a chat user."""

    user_id: str
    display_name: str
    auth_method: str = "none"  # none, pin, password, passkey
    pin_hash: str = ""
    password_hash: str = ""
    passkey_credential_id: str = ""
    passkey_public_key: str = ""
    content_tier: str = "adult"  # child, teen, adult
    require_auth_on_new_device: bool = True
    trusted_devices: list[str] = field(default_factory=list)
    avatar_url: str = ""
    created_at: float = field(default_factory=time.time)


class UserAuthManager:
    """Manage user authentication for the chat interface."""

    def __init__(self) -> None:
        self._users: dict[str, UserAuthConfig] = {}

    def load_from_db(self, conn: Any) -> None:
        """Load user auth configs from the database."""
        try:
            rows = conn.execute("SELECT * FROM user_auth").fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM user_auth LIMIT 0").description or []]
            for row in rows:
                data = dict(zip(cols, row)) if not isinstance(row, dict) else dict(row)
                self._users[data["user_id"]] = UserAuthConfig(
                    user_id=data["user_id"],
                    display_name=data["display_name"],
                    auth_method=data.get("auth_method", "none"),
                    pin_hash=data.get("pin_hash", "") or "",
                    password_hash=data.get("password_hash", "") or "",
                    passkey_credential_id=data.get("passkey_credential_id", "") or "",
                    passkey_public_key=data.get("passkey_public_key", "") or "",
                    content_tier=data.get("content_tier", "adult") or "adult",
                    require_auth_on_new_device=bool(data.get("require_auth_on_new_device", 1)),
                    trusted_devices=json.loads(data.get("trusted_devices", "[]") or "[]"),
                    avatar_url=data.get("avatar_url", "") or "",
                )
        except Exception as e:
            logger.debug("Failed to load user auth: %s", e)

    def save_to_db(self, conn: Any, user: UserAuthConfig) -> None:
        """Save a user auth config to the database."""
        conn.execute(
            """INSERT OR REPLACE INTO user_auth
            (user_id, display_name, auth_method, pin_hash, password_hash,
             passkey_credential_id, passkey_public_key, content_tier,
             require_auth_on_new_device, trusted_devices, avatar_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.user_id,
                user.display_name,
                user.auth_method,
                user.pin_hash,
                user.password_hash,
                user.passkey_credential_id,
                user.passkey_public_key,
                user.content_tier,
                int(user.require_auth_on_new_device),
                json.dumps(user.trusted_devices),
                user.avatar_url,
            ),
        )
        conn.commit()
        self._users[user.user_id] = user

    def get_user(self, user_id: str) -> UserAuthConfig | None:
        """Return a user config by ID, or None."""
        return self._users.get(user_id)

    def list_users(self) -> list[dict]:
        """List users for the profile picker (no secrets exposed)."""
        return [
            {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "auth_method": u.auth_method,
                "content_tier": u.content_tier,
                "avatar_url": u.avatar_url,
                "requires_auth": u.auth_method != "none",
            }
            for u in self._users.values()
        ]

    def verify_pin(self, user_id: str, pin: str) -> bool:
        """Verify a user's PIN."""
        user = self._users.get(user_id)
        if not user or user.auth_method != "pin":
            return False
        return bcrypt.checkpw(
            _prepare_password(pin), user.pin_hash.encode()
        )

    def verify_password(self, user_id: str, password: str) -> bool:
        """Verify a user's password."""
        user = self._users.get(user_id)
        if not user or user.auth_method != "password":
            return False
        return bcrypt.checkpw(
            _prepare_password(password), user.password_hash.encode()
        )

    def set_pin(self, user_id: str, pin: str) -> bool:
        """Set a PIN for a user. Returns True on success."""
        user = self._users.get(user_id)
        if not user:
            return False
        user.auth_method = "pin"
        user.pin_hash = bcrypt.hashpw(
            _prepare_password(pin), bcrypt.gensalt()
        ).decode()
        user.password_hash = ""
        return True

    def set_password(self, user_id: str, password: str) -> bool:
        """Set a password for a user. Returns True on success."""
        user = self._users.get(user_id)
        if not user:
            return False
        user.auth_method = "password"
        user.password_hash = bcrypt.hashpw(
            _prepare_password(password), bcrypt.gensalt()
        ).decode()
        user.pin_hash = ""
        return True

    def remove_auth(self, user_id: str) -> bool:
        """Remove auth requirement for a user. Returns True on success."""
        user = self._users.get(user_id)
        if not user:
            return False
        user.auth_method = "none"
        user.pin_hash = ""
        user.password_hash = ""
        return True

    def is_device_trusted(self, user_id: str, device_fingerprint: str) -> bool:
        """Check if a device is trusted for this user (skip auth)."""
        user = self._users.get(user_id)
        if not user:
            return False
        if not user.require_auth_on_new_device:
            return True
        return device_fingerprint in user.trusted_devices

    def trust_device(self, user_id: str, device_fingerprint: str) -> None:
        """Mark a device as trusted for this user."""
        user = self._users.get(user_id)
        if user and device_fingerprint and device_fingerprint not in user.trusted_devices:
            user.trusted_devices.append(device_fingerprint)

    def untrust_device(self, user_id: str, device_fingerprint: str) -> None:
        """Remove device trust."""
        user = self._users.get(user_id)
        if user and device_fingerprint in user.trusted_devices:
            user.trusted_devices.remove(device_fingerprint)

    def generate_session_token(self, user_id: str) -> str:
        """Generate a short-lived session token for a verified user."""
        return jwt.encode(
            {
                "user_id": user_id,
                "exp": int(time.time()) + CHAT_TOKEN_EXPIRY,
                "iat": int(time.time()),
                "type": "chat",
            },
            get_jwt_secret(),
            algorithm=JWT_ALGORITHM,
        )

    def verify_session_token(self, token: str) -> str | None:
        """Verify a chat session token. Returns user_id or None."""
        try:
            payload = jwt.decode(
                token, get_jwt_secret(), algorithms=[JWT_ALGORITHM]
            )
            if payload.get("type") != "chat":
                return None
            return payload.get("user_id")
        except Exception:
            return None


# Singleton
_manager: UserAuthManager | None = None


def get_user_auth() -> UserAuthManager:
    """Return the global UserAuthManager singleton."""
    global _manager
    if _manager is None:
        _manager = UserAuthManager()
    return _manager


def reset_user_auth() -> None:
    """Reset the singleton (for tests)."""
    global _manager
    _manager = None
