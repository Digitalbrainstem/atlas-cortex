"""Adversarial tests for cortex/auth.py — proving edge-cases break or hold.

Every test here exists because the behaviour is *not* obvious from reading the
source.  If a test fails, it means the code has a real bug or a security gap
that needs addressing.
"""

from __future__ import annotations

import json
import base64
import sqlite3
import time

import bcrypt
import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from cortex.auth import (
    JWT_ALGORITHM,
    JWT_EXPIRY_SECONDS,
    JWT_SECRET,
    authenticate,
    create_token,
    decode_token,
    hash_password,
    require_admin,
    seed_admin,
    verify_password,
)
from cortex.db import init_db, set_db_path


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Fresh database per test — completely isolated."""
    path = tmp_path / "auth_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    """Raw connection with row_factory + FK enforcement."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Default-secret security ───────────────────────────────────────


class TestDefaultSecretWarning:
    """JWT_SECRET falls back to a hard-coded string when the env var is unset.
    These tests verify the *mechanism* works but document the risk."""

    def test_default_secret_is_not_empty(self):
        assert JWT_SECRET, "JWT_SECRET must never be empty"

    def test_default_secret_is_well_known(self):
        """If this passes, anyone who reads the source can forge tokens."""
        assert JWT_SECRET == "atlas-cortex-change-me", (
            "Default secret changed — update documentation & rotation guide"
        )

    def test_token_forged_with_default_secret(self):
        """Prove an attacker who knows the default secret can forge tokens."""
        forged = jwt.encode(
            {"sub": "999", "username": "evil", "exp": int(time.time()) + 3600},
            "atlas-cortex-change-me",
            algorithm="HS256",
        )
        payload = decode_token(forged)
        assert payload["username"] == "evil", (
            "Forged token accepted — default secret is a security risk"
        )


# ── JWT round-trip & edge-cases ───────────────────────────────────


class TestJWTRoundTrip:
    def test_basic_roundtrip(self):
        token = create_token(42, "bob")
        p = decode_token(token)
        assert p["sub"] == "42"
        assert p["username"] == "bob"
        assert "iat" in p
        assert "exp" in p

    def test_sub_is_string(self):
        """create_token casts user_id to str — verify consumers can rely on that."""
        token = create_token(1, "admin")
        p = decode_token(token)
        assert isinstance(p["sub"], str), "sub claim must be a string"

    def test_expiry_is_in_future(self):
        token = create_token(1, "admin")
        p = decode_token(token)
        assert p["exp"] > time.time()

    def test_expiry_matches_config(self):
        before = int(time.time())
        token = create_token(1, "admin")
        p = decode_token(token)
        expected = before + JWT_EXPIRY_SECONDS
        assert abs(p["exp"] - expected) < 2, "Expiry drift > 1 s"


class TestExpiredToken:
    def test_expired_token_rejected(self):
        """Manually craft a token that expired 10 s ago."""
        payload = {
            "sub": "1",
            "username": "admin",
            "iat": int(time.time()) - 20,
            "exp": int(time.time()) - 10,
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()


class TestTamperedToken:
    def test_modified_payload_rejected(self):
        """Change the payload (e.g. escalate user_id) but keep the signature."""
        token = create_token(1, "admin")
        parts = token.split(".")
        assert len(parts) == 3

        # Decode payload, change sub, re-encode — but keep old signature
        raw = base64.urlsafe_b64decode(parts[1] + "==")
        data = json.loads(raw)
        data["sub"] = "9999"
        new_payload = (
            base64.urlsafe_b64encode(json.dumps(data).encode())
            .rstrip(b"=")
            .decode()
        )
        tampered = f"{parts[0]}.{new_payload}.{parts[2]}"
        with pytest.raises(HTTPException) as exc:
            decode_token(tampered)
        assert exc.value.status_code == 401

    def test_wrong_algorithm_rejected(self):
        """Token signed with HS384 must be rejected by HS256-only decoder."""
        payload = {
            "sub": "1",
            "username": "admin",
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS384")
        with pytest.raises(HTTPException):
            decode_token(token)

    def test_none_algorithm_rejected(self):
        """The classic 'alg: none' attack must not bypass verification."""
        payload = {
            "sub": "1",
            "username": "admin",
            "exp": int(time.time()) + 3600,
        }
        # Build an unsigned token by hand
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()
        unsigned_token = f"{header}.{body}."
        with pytest.raises(HTTPException):
            decode_token(unsigned_token)


class TestMissingClaims:
    def test_token_without_sub(self):
        """Token that lacks 'sub' — decode_token doesn't validate claims,
        so this documents current behaviour (no error, just missing key)."""
        payload = {"username": "admin", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        p = decode_token(token)
        assert "sub" not in p, "Token without sub should decode (no claim enforcement)"

    def test_token_without_username(self):
        payload = {"sub": "1", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        p = decode_token(token)
        assert "username" not in p

    def test_completely_empty_payload(self):
        """Only exp is needed to avoid ExpiredSignatureError."""
        payload = {"exp": int(time.time()) + 3600}
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        p = decode_token(token)
        assert isinstance(p, dict)


class TestGarbageTokens:
    def test_empty_string(self):
        with pytest.raises(HTTPException):
            decode_token("")

    def test_random_bytes(self):
        with pytest.raises(HTTPException):
            decode_token("not-a-jwt-at-all")

    def test_three_dots_no_content(self):
        with pytest.raises(HTTPException):
            decode_token("..")

    def test_unicode_garbage(self):
        with pytest.raises(HTTPException):
            decode_token("日本語.トークン.テスト")


# ── Password hashing ─────────────────────────────────────────────


class TestPasswordHashing:
    def test_roundtrip(self):
        h = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", h)

    def test_wrong_password(self):
        h = hash_password("right")
        assert not verify_password("wrong", h)

    def test_hash_is_bcrypt(self):
        h = hash_password("test")
        assert h.startswith("$2"), f"Expected bcrypt hash, got: {h[:10]}"

    def test_same_password_different_hashes(self):
        """Salt must differ between calls."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2, "Two hashes of the same password must differ (unique salt)"

    def test_empty_password(self):
        """Empty password should hash and verify without error.
        Whether the app *allows* empty passwords is a policy decision,
        but the primitives must not crash."""
        h = hash_password("")
        assert verify_password("", h)
        assert not verify_password("notempty", h)

    def test_very_long_password_crashes(self):
        """BUG: bcrypt raises ValueError on passwords > 72 bytes.
        verify_password does not handle this, so a long password causes an
        unhandled crash instead of returning False.  hash_password silently
        truncates (via gensalt), but verify_password does not."""
        base = "A" * 72
        long_pw = base + "EXTRA"
        h = hash_password(base)
        with pytest.raises(ValueError, match="password cannot be longer than 72 bytes"):
            verify_password(long_pw, h)

    def test_unicode_password(self):
        h = hash_password("pässwörd-日本語")
        assert verify_password("pässwörd-日本語", h)
        assert not verify_password("password", h)


# ── seed_admin ────────────────────────────────────────────────────


class TestSeedAdmin:
    def test_creates_default_admin(self, db):
        seed_admin(db)
        row = db.execute("SELECT username FROM admin_users").fetchone()
        assert row is not None
        assert row["username"] == "admin"

    def test_idempotent(self, db):
        """Calling seed_admin twice must not crash or create duplicates."""
        seed_admin(db)
        seed_admin(db)
        count = db.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        assert count == 1, f"Expected 1 admin user, got {count}"

    def test_does_not_overwrite_existing_admin(self, db):
        """If an admin exists (even with a different username), seed_admin
        should leave the table alone."""
        db.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("custom-admin", hash_password("custom-pass")),
        )
        db.commit()
        seed_admin(db)
        count = db.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
        assert count == 1, "seed_admin should skip when any admin user exists"
        row = db.execute("SELECT username FROM admin_users").fetchone()
        assert row["username"] == "custom-admin"

    def test_default_password_works(self, db):
        """Out-of-the-box: admin / atlas-admin must authenticate."""
        seed_admin(db)
        result = authenticate(db, "admin", "atlas-admin")
        assert result is not None
        assert result["username"] == "admin"


# ── authenticate() ────────────────────────────────────────────────


class TestAuthenticate:
    def test_correct_credentials(self, db):
        seed_admin(db)
        result = authenticate(db, "admin", "atlas-admin")
        assert result is not None
        assert result["id"] is not None
        assert result["username"] == "admin"

    def test_wrong_password_returns_none(self, db):
        seed_admin(db)
        assert authenticate(db, "admin", "wrong-password") is None

    def test_nonexistent_user_returns_none(self, db):
        seed_admin(db)
        assert authenticate(db, "ghost", "any-password") is None

    def test_inactive_user_returns_none(self, db):
        """Deactivated accounts must be locked out."""
        seed_admin(db)
        db.execute("UPDATE admin_users SET is_active = FALSE WHERE username = 'admin'")
        db.commit()
        assert authenticate(db, "admin", "atlas-admin") is None

    def test_updates_last_login(self, db):
        seed_admin(db)
        authenticate(db, "admin", "atlas-admin")
        row = db.execute(
            "SELECT last_login FROM admin_users WHERE username = 'admin'"
        ).fetchone()
        assert row["last_login"] is not None, "last_login should be set after authenticate()"

    def test_failed_login_does_not_update_last_login(self, db):
        seed_admin(db)
        authenticate(db, "admin", "wrong")
        row = db.execute(
            "SELECT last_login FROM admin_users WHERE username = 'admin'"
        ).fetchone()
        assert row["last_login"] is None, "Failed login should not touch last_login"

    def test_sql_injection_in_username(self, db):
        """Parameterized queries must prevent injection."""
        seed_admin(db)
        result = authenticate(db, "' OR 1=1 --", "anything")
        assert result is None

    def test_sql_injection_in_password(self, db):
        seed_admin(db)
        result = authenticate(db, "admin", "' OR 1=1 --")
        assert result is None


# ── require_admin() FastAPI dependency ────────────────────────────


class TestRequireAdmin:
    async def test_valid_token(self):
        token = create_token(1, "admin")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        payload = await require_admin(creds)
        assert payload["username"] == "admin"

    async def test_missing_credentials(self):
        with pytest.raises(HTTPException) as exc:
            await require_admin(None)
        assert exc.value.status_code == 401
        assert "not authenticated" in exc.value.detail.lower()

    async def test_invalid_token(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="garbage")
        with pytest.raises(HTTPException) as exc:
            await require_admin(creds)
        assert exc.value.status_code == 401

    async def test_expired_token(self):
        payload = {
            "sub": "1",
            "username": "admin",
            "iat": int(time.time()) - 20,
            "exp": int(time.time()) - 10,
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            await require_admin(creds)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    async def test_token_with_different_secret(self):
        """Token signed with a secret the server doesn't know."""
        token = jwt.encode(
            {"sub": "1", "username": "admin", "exp": int(time.time()) + 3600},
            "totally-different-secret",
            algorithm="HS256",
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc:
            await require_admin(creds)
        assert exc.value.status_code == 401
