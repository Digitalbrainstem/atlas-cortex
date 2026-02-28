"""Admin authentication for Atlas Cortex.

JWT-based auth with bcrypt password hashing.  On first run the default admin
account is seeded (username: ``admin``, password: ``atlas-admin``).  Change it
immediately via the admin panel.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Configuration ─────────────────────────────────────────────────
JWT_SECRET = os.environ.get("CORTEX_JWT_SECRET", "atlas-cortex-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = int(os.environ.get("CORTEX_JWT_EXPIRY", "86400"))  # 24h

_bearer = HTTPBearer(auto_error=False)


# ── Password helpers ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────

def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


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
