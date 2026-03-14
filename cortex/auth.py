"""Admin authentication for Atlas Cortex.

JWT-based auth with bcrypt password hashing.  On first run the default admin
account is seeded (username: ``admin``, password: ``atlas-admin``).  Change it
immediately via the admin panel.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = int(os.environ.get("CORTEX_JWT_EXPIRY", "86400"))  # 24h

_bearer = HTTPBearer(auto_error=False)

# Lazy-initialised JWT secret — see _get_or_create_secret()
_jwt_secret: str | None = None


def _get_or_create_secret() -> str:
    """Return the JWT signing secret.

    Resolution order:
    1. ``CORTEX_JWT_SECRET`` env var (preferred for multi-instance deploys).
    2. Persisted secret in the ``system_settings`` DB table.
    3. Auto-generate a random 64-byte hex secret, store it in DB, and warn.
    """
    global _jwt_secret
    if _jwt_secret is not None:
        return _jwt_secret

    from_env = os.environ.get("CORTEX_JWT_SECRET")
    if from_env:
        _jwt_secret = from_env
        return _jwt_secret

    # Try to read / write the DB — import here to avoid circular imports
    from cortex.db import get_db

    try:
        conn = get_db()
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'jwt_secret'",
        ).fetchone()
        if row is not None:
            _jwt_secret = row["value"]
            log.warning(
                "Using auto-generated JWT secret from database. "
                "Set CORTEX_JWT_SECRET env var for multi-instance deployments."
            )
            return _jwt_secret

        # First run — generate and persist
        generated = secrets.token_hex(64)
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('jwt_secret', ?)",
            (generated,),
        )
        conn.commit()
        _jwt_secret = generated
        log.warning(
            "Generated and stored a random JWT secret. "
            "Set CORTEX_JWT_SECRET env var for multi-instance deployments."
        )
        return _jwt_secret
    except Exception:
        # DB not ready yet (e.g. during early import) — fall back to a
        # per-process ephemeral secret.  This path should be rare.
        _jwt_secret = secrets.token_hex(64)
        log.warning(
            "Database unavailable — using ephemeral JWT secret. "
            "Tokens will not survive a restart."
        )
        return _jwt_secret


# Public alias so tests & other modules can read the live secret
def get_jwt_secret() -> str:
    """Return the active JWT secret (lazy-initialised)."""
    return _get_or_create_secret()


# Backward-compatible module-level alias (use get_jwt_secret() in new code)
JWT_SECRET = None  # type: ignore[assignment] — lazy; use get_jwt_secret()


# ── Password helpers ──────────────────────────────────────────────

def _prepare_password(password: str) -> bytes:
    """Encode *password* to bytes, pre-hashing with SHA-256 if > 72 bytes."""
    pw_bytes = password.encode()
    if len(pw_bytes) > 72:
        pw_bytes = hashlib.sha256(pw_bytes).hexdigest().encode()
    return pw_bytes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prepare_password(password), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────

def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Enforce required claims
    if "sub" not in payload or "username" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required claims",
        )
    return payload


# ── FastAPI dependency ────────────────────────────────────────────

async def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Dependency that ensures the caller has a valid admin JWT."""
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return decode_token(creds.credentials)


# ── Database helpers ──────────────────────────────────────────────

def seed_admin(conn: sqlite3.Connection) -> None:
    """Create the default admin user if no admin exists yet."""
    row = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()
    if row[0] == 0:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("admin", hash_password("atlas-admin")),
        )
        conn.commit()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """Validate credentials and return user dict, or *None* on failure."""
    row = conn.execute(
        "SELECT id, username, password_hash, is_active FROM admin_users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    # Touch last_login
    conn.execute(
        "UPDATE admin_users SET last_login = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), row["id"]),
    )
    conn.commit()
    return {"id": row["id"], "username": row["username"]}
