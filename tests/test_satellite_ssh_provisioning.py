"""Tests for SSH key provisioning and password rotation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    yield


@pytest.fixture()
def _seed_satellite():
    """Insert a test satellite into the DB."""
    db = get_db()
    db.execute(
        "INSERT INTO satellites (id, display_name, ip_address, mode, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sat-kitchen", "Kitchen", "192.168.1.50", "dedicated", "online"),
    )
    db.commit()


# ── Schema ────────────────────────────────────────────────────────


def test_satellites_table_has_ssh_password_column():
    """The satellites table should have an ssh_password column."""
    db = get_db()
    cols = [row[1] for row in db.execute("PRAGMA table_info(satellites)").fetchall()]
    assert "ssh_password" in cols


def test_satellites_table_has_ssh_key_installed_column():
    """The satellites table should have an ssh_key_installed column."""
    db = get_db()
    cols = [row[1] for row in db.execute("PRAGMA table_info(satellites)").fetchall()]
    assert "ssh_key_installed" in cols


# ── provision_ssh_key ─────────────────────────────────────────────


@pytest.mark.usefixtures("_seed_satellite")
async def test_provision_ssh_key_sends_exec_script():
    """provision_ssh_key should send an EXEC_SCRIPT command."""
    from cortex.satellite.provisioning import provision_ssh_key

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        result = await provision_ssh_key("sat-kitchen", "ssh-ed25519 AAAA test@host")

    assert result is True
    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[0][0] == "sat-kitchen"
    assert call_args[0][1] == "EXEC_SCRIPT"
    payload = call_args[0][2]
    assert "authorized_keys" in payload["script"]
    assert "ssh-ed25519 AAAA test@host" in payload["script"]
    assert "chpasswd" in payload["script"]


@pytest.mark.usefixtures("_seed_satellite")
async def test_provision_ssh_key_updates_db():
    """provision_ssh_key should store password and set ssh_key_installed."""
    from cortex.satellite.provisioning import provision_ssh_key

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        await provision_ssh_key("sat-kitchen", "ssh-ed25519 AAAA test@host")

    db = get_db()
    row = db.execute(
        "SELECT ssh_password, ssh_key_installed FROM satellites WHERE id = ?",
        ("sat-kitchen",),
    ).fetchone()
    assert row is not None
    assert row[0] is not None and len(row[0]) > 20  # Random token
    assert row[1] == 1  # True


@pytest.mark.usefixtures("_seed_satellite")
async def test_provision_ssh_key_deduplicates_authorized_keys():
    """The script should use sort -u to avoid duplicate key entries."""
    from cortex.satellite.provisioning import provision_ssh_key

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        await provision_ssh_key("sat-kitchen", "ssh-ed25519 AAAA test@host")

    script = mock_send.call_args[0][2]["script"]
    assert "sort -u" in script


@pytest.mark.usefixtures("_seed_satellite")
async def test_provision_ssh_key_escapes_single_quotes():
    """Single quotes in the key should be escaped."""
    from cortex.satellite.provisioning import provision_ssh_key

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    key_with_quote = "ssh-ed25519 AAAA comment='test'"
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        await provision_ssh_key("sat-kitchen", key_with_quote)

    script = mock_send.call_args[0][2]["script"]
    # The key should be escaped, not contain raw unescaped quotes
    assert "comment='\\''test'\\''" in script


# ── rotate_password ───────────────────────────────────────────────


@pytest.mark.usefixtures("_seed_satellite")
async def test_rotate_password_sends_chpasswd():
    """rotate_password should send a chpasswd command."""
    from cortex.satellite.provisioning import rotate_password

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        new_pw = await rotate_password("sat-kitchen")

    assert isinstance(new_pw, str)
    assert len(new_pw) > 20

    call_args = mock_send.call_args
    assert call_args[0][1] == "EXEC_SCRIPT"
    assert "chpasswd" in call_args[0][2]["script"]
    assert new_pw in call_args[0][2]["script"]


@pytest.mark.usefixtures("_seed_satellite")
async def test_rotate_password_updates_db():
    """rotate_password should store the new password in the DB."""
    from cortex.satellite.provisioning import rotate_password

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        new_pw = await rotate_password("sat-kitchen")

    db = get_db()
    row = db.execute(
        "SELECT ssh_password FROM satellites WHERE id = ?",
        ("sat-kitchen",),
    ).fetchone()
    assert row[0] == new_pw


@pytest.mark.usefixtures("_seed_satellite")
async def test_rotate_password_generates_unique_passwords():
    """Two rotations should produce different passwords."""
    from cortex.satellite.provisioning import rotate_password

    mock_send = AsyncMock(return_value={"id": 1, "status": "sent"})
    with patch("cortex.satellite.websocket.send_remote_command", mock_send):
        pw1 = await rotate_password("sat-kitchen")
        pw2 = await rotate_password("sat-kitchen")

    assert pw1 != pw2


# ── Admin API endpoints ──────────────────────────────────────────


@pytest.mark.usefixtures("_seed_satellite")
async def test_ssh_info_endpoint_returns_data():
    """GET /admin/satellites/{id}/ssh-info should return SSH details."""
    from cortex.admin.satellites import get_ssh_info

    # Mock the Depends(require_admin)
    result = await get_ssh_info("sat-kitchen", admin={})
    assert result["ssh_username"] == "atlas"
    assert result["ip_address"] == "192.168.1.50"
    assert result["ssh_key_installed"] is False
    assert result["ssh_command"] == "ssh atlas@192.168.1.50"


async def test_ssh_info_endpoint_404_for_missing_satellite():
    """GET /admin/satellites/{id}/ssh-info should 404 for unknown satellite."""
    from fastapi import HTTPException

    from cortex.admin.satellites import get_ssh_info

    with pytest.raises(HTTPException) as exc_info:
        await get_ssh_info("sat-nonexistent", admin={})
    assert exc_info.value.status_code == 404
