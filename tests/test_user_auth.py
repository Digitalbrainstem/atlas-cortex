"""Tests for per-user chat authentication."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from cortex.auth_user import (
    UserAuthConfig,
    UserAuthManager,
    get_user_auth,
    reset_user_auth,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the UserAuthManager singleton between tests."""
    reset_user_auth()
    yield
    reset_user_auth()


@pytest.fixture()
def mgr():
    """Return a fresh UserAuthManager with sample users."""
    m = UserAuthManager()
    m._users["alice"] = UserAuthConfig(
        user_id="alice",
        display_name="Alice",
        auth_method="none",
        content_tier="adult",
    )
    m._users["bob"] = UserAuthConfig(
        user_id="bob",
        display_name="Bob",
        auth_method="pin",
        content_tier="teen",
    )
    # Give bob a hashed PIN ("1234")
    m.set_pin("bob", "1234")
    m._users["charlie"] = UserAuthConfig(
        user_id="charlie",
        display_name="Charlie",
        auth_method="password",
        content_tier="child",
    )
    m.set_password("charlie", "secret123")
    return m


@pytest.fixture()
def mem_db():
    """Return an in-memory SQLite connection with the user_auth table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE user_auth (
            user_id                  TEXT PRIMARY KEY,
            display_name             TEXT NOT NULL,
            auth_method              TEXT DEFAULT 'none',
            pin_hash                 TEXT DEFAULT '',
            password_hash            TEXT DEFAULT '',
            passkey_credential_id    TEXT DEFAULT '',
            passkey_public_key       TEXT DEFAULT '',
            content_tier             TEXT DEFAULT 'adult',
            require_auth_on_new_device INTEGER DEFAULT 1,
            trusted_devices          TEXT DEFAULT '[]',
            avatar_url               TEXT DEFAULT '',
            created_at               TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


# ── UserAuthManager: list / get ───────────────────────────────────


class TestListUsers:
    def test_list_users_returns_safe_data(self, mgr):
        result = mgr.list_users()
        assert len(result) == 3
        names = {u["display_name"] for u in result}
        assert names == {"Alice", "Bob", "Charlie"}
        # No secrets exposed
        for u in result:
            assert "pin_hash" not in u
            assert "password_hash" not in u

    def test_list_users_requires_auth_flag(self, mgr):
        result = {u["user_id"]: u for u in mgr.list_users()}
        assert result["alice"]["requires_auth"] is False
        assert result["bob"]["requires_auth"] is True
        assert result["charlie"]["requires_auth"] is True

    def test_get_user(self, mgr):
        assert mgr.get_user("alice") is not None
        assert mgr.get_user("alice").display_name == "Alice"
        assert mgr.get_user("nonexistent") is None

    def test_list_empty(self):
        m = UserAuthManager()
        assert m.list_users() == []


# ── PIN authentication ────────────────────────────────────────────


class TestPinAuth:
    def test_verify_correct_pin(self, mgr):
        assert mgr.verify_pin("bob", "1234") is True

    def test_verify_wrong_pin(self, mgr):
        assert mgr.verify_pin("bob", "0000") is False

    def test_verify_pin_wrong_user(self, mgr):
        assert mgr.verify_pin("alice", "1234") is False

    def test_verify_pin_nonexistent_user(self, mgr):
        assert mgr.verify_pin("nobody", "1234") is False

    def test_set_pin(self, mgr):
        mgr.set_pin("alice", "5678")
        assert mgr.get_user("alice").auth_method == "pin"
        assert mgr.verify_pin("alice", "5678") is True
        assert mgr.verify_pin("alice", "1234") is False

    def test_set_pin_clears_password(self, mgr):
        mgr.set_pin("charlie", "9999")
        assert mgr.get_user("charlie").password_hash == ""
        assert mgr.get_user("charlie").auth_method == "pin"

    def test_set_pin_nonexistent_user(self, mgr):
        assert mgr.set_pin("nobody", "1234") is False


# ── Password authentication ──────────────────────────────────────


class TestPasswordAuth:
    def test_verify_correct_password(self, mgr):
        assert mgr.verify_password("charlie", "secret123") is True

    def test_verify_wrong_password(self, mgr):
        assert mgr.verify_password("charlie", "wrong") is False

    def test_verify_password_wrong_method(self, mgr):
        assert mgr.verify_password("bob", "1234") is False

    def test_set_password(self, mgr):
        mgr.set_password("alice", "newpass")
        assert mgr.get_user("alice").auth_method == "password"
        assert mgr.verify_password("alice", "newpass") is True

    def test_set_password_clears_pin(self, mgr):
        mgr.set_password("bob", "newpass")
        assert mgr.get_user("bob").pin_hash == ""
        assert mgr.get_user("bob").auth_method == "password"

    def test_set_password_nonexistent_user(self, mgr):
        assert mgr.set_password("nobody", "pass") is False


# ── Remove auth ───────────────────────────────────────────────────


class TestRemoveAuth:
    def test_remove_auth(self, mgr):
        mgr.remove_auth("bob")
        assert mgr.get_user("bob").auth_method == "none"
        assert mgr.get_user("bob").pin_hash == ""

    def test_remove_auth_nonexistent(self, mgr):
        assert mgr.remove_auth("nobody") is False


# ── Device trust ──────────────────────────────────────────────────


class TestDeviceTrust:
    def test_trust_and_check_device(self, mgr):
        mgr.trust_device("bob", "device_abc123")
        assert mgr.is_device_trusted("bob", "device_abc123") is True

    def test_untrusted_device(self, mgr):
        assert mgr.is_device_trusted("bob", "device_unknown") is False

    def test_untrust_device(self, mgr):
        mgr.trust_device("bob", "device_abc123")
        mgr.untrust_device("bob", "device_abc123")
        assert mgr.is_device_trusted("bob", "device_abc123") is False

    def test_no_auth_required_on_any_device(self, mgr):
        mgr.get_user("alice").require_auth_on_new_device = False
        assert mgr.is_device_trusted("alice", "any_device") is True

    def test_nonexistent_user_not_trusted(self, mgr):
        assert mgr.is_device_trusted("nobody", "device_x") is False

    def test_trust_device_deduplication(self, mgr):
        mgr.trust_device("alice", "dev1")
        mgr.trust_device("alice", "dev1")
        assert mgr.get_user("alice").trusted_devices.count("dev1") == 1

    def test_trust_device_empty_fingerprint(self, mgr):
        mgr.trust_device("alice", "")
        assert "" not in mgr.get_user("alice").trusted_devices


# ── Session tokens ────────────────────────────────────────────────


class TestSessionTokens:
    def test_generate_and_verify(self, mgr):
        token = mgr.generate_session_token("alice")
        assert isinstance(token, str)
        assert mgr.verify_session_token(token) == "alice"

    def test_invalid_token(self, mgr):
        assert mgr.verify_session_token("garbage") is None

    def test_empty_token(self, mgr):
        assert mgr.verify_session_token("") is None

    def test_wrong_type_token(self, mgr):
        """A token with type != 'chat' should be rejected."""
        import jwt as _jwt
        from cortex.auth import get_jwt_secret
        token = _jwt.encode(
            {"user_id": "alice", "type": "admin", "exp": time.time() + 3600},
            get_jwt_secret(),
            algorithm="HS256",
        )
        assert mgr.verify_session_token(token) is None

    def test_expired_token(self, mgr):
        """An expired token should be rejected."""
        import jwt as _jwt
        from cortex.auth import get_jwt_secret
        token = _jwt.encode(
            {"user_id": "alice", "type": "chat", "exp": time.time() - 100},
            get_jwt_secret(),
            algorithm="HS256",
        )
        assert mgr.verify_session_token(token) is None


# ── Database persistence ──────────────────────────────────────────


class TestDatabasePersistence:
    def test_save_and_load(self, mem_db):
        mgr1 = UserAuthManager()
        user = UserAuthConfig(
            user_id="dave",
            display_name="Dave",
            auth_method="pin",
            content_tier="teen",
            avatar_url="🧑",
        )
        mgr1._users["dave"] = user
        mgr1.set_pin("dave", "4321")
        mgr1.save_to_db(mem_db, mgr1.get_user("dave"))

        # Load into a fresh manager
        mgr2 = UserAuthManager()
        mgr2.load_from_db(mem_db)
        assert "dave" in mgr2._users
        loaded = mgr2.get_user("dave")
        assert loaded.display_name == "Dave"
        assert loaded.auth_method == "pin"
        assert loaded.content_tier == "teen"
        assert loaded.avatar_url == "🧑"
        assert mgr2.verify_pin("dave", "4321") is True

    def test_save_trusted_devices(self, mem_db):
        mgr1 = UserAuthManager()
        user = UserAuthConfig(user_id="eve", display_name="Eve")
        mgr1._users["eve"] = user
        mgr1.trust_device("eve", "dev1")
        mgr1.trust_device("eve", "dev2")
        mgr1.save_to_db(mem_db, user)

        mgr2 = UserAuthManager()
        mgr2.load_from_db(mem_db)
        assert mgr2.is_device_trusted("eve", "dev1") is True
        assert mgr2.is_device_trusted("eve", "dev2") is True

    def test_load_empty_table(self, mem_db):
        mgr = UserAuthManager()
        mgr.load_from_db(mem_db)
        assert mgr.list_users() == []

    def test_load_graceful_on_missing_table(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        mgr = UserAuthManager()
        mgr.load_from_db(conn)  # Should not raise
        assert mgr.list_users() == []


# ── Singleton ─────────────────────────────────────────────────────


class TestSingleton:
    def test_get_user_auth_returns_same_instance(self):
        a = get_user_auth()
        b = get_user_auth()
        assert a is b

    def test_reset_clears_singleton(self):
        a = get_user_auth()
        reset_user_auth()
        b = get_user_auth()
        assert a is not b


# ── Guest mode ────────────────────────────────────────────────────


class TestGuestMode:
    def test_guest_user_no_auth_no_persistence(self):
        """Guest users get tokens but leave no trace in the manager."""
        mgr = UserAuthManager()
        mgr._users["guest"] = UserAuthConfig(
            user_id="guest",
            display_name="Guest",
            auth_method="none",
            content_tier="adult",
        )
        token = mgr.generate_session_token("guest")
        assert mgr.verify_session_token(token) == "guest"
        # Guest has no auth method
        assert mgr.get_user("guest").auth_method == "none"
