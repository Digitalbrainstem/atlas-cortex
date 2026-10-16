"""P12 isolated upgrade, recovery, configuration and lifecycle acceptance."""
from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from cortex.backup import create_backup, restore_backup
from cortex.backup.storage import data_lock
from cortex.db import _create_schema
from cortex.orchestrator.turn_store import TurnStore
from cortex.pipeline.turns import TurnIdentity
from cortex.system.recovery import SelfRecovery


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "cortex.env").write_text("CORTEX_TURN_MAX_ACTIVE=1\n")
    monkeypatch.setenv("CORTEX_DATA_DIR", str(data))
    monkeypatch.setenv("CORTEX_CONFIG_DIR", str(config))
    conn = sqlite3.connect(data / "cortex.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    _create_schema(conn)
    conn.execute("CREATE TABLE household_fixture (value TEXT)")
    conn.execute("INSERT INTO household_fixture VALUES ('before')")
    conn.commit()
    yield data, config, conn
    conn.close()


def test_wal_backup_isolated_restore_preserves_turns_and_policy(deployment, tmp_path):
    data, config, conn = deployment
    store = TurnStore(conn)
    for number in range(100):
        store.accept(TurnIdentity(str(number), "household", "anonymous"),
                     f"Retained question {number}", input_validated=True)
    assert Path(str(data / "cortex.db") + "-wal").stat().st_size > 0
    archive = create_backup()
    assert archive.stat().st_mode & 0o777 == 0o600
    target = tmp_path / "isolated"
    restore_backup(archive, data_dir=target)
    with sqlite3.connect(target / "cortex.db") as restored:
        restored.row_factory = sqlite3.Row
        recovered = TurnStore(restored)
        assert recovered.status()["counts"] == {"accepted": 100}
        assert recovered.recover(startup=True) == 100
        assert recovered.status()["counts"] == {"recovery_pending": 100}
        assert not recovered.pending_ids()
    assert (target / "cortex.env").read_text() == (config / "cortex.env").read_text()
    manifest = json.loads((target / "restored-manifest.json").read_text())
    assert manifest["format"] == 1
    assert manifest["turn_policy"]["max_active"] == 1
    assert manifest["turn_policy"]["prefetch"] == 8
    assert store.status()["counts"] == {"accepted": 100}


def test_upgrade_repeat_and_restore_matching_pre_ledger_database(deployment):
    data, _, conn = deployment
    for table in ("pending_turns", "pending_turn_sequences", "pending_turn_usage"):
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    previous = create_backup()
    _create_schema(conn)
    conn.commit()
    store = TurnStore(conn)
    store.accept(TurnIdentity("new", "upgrade", "anonymous"), "New question", input_validated=True)
    _create_schema(conn)
    conn.commit()
    assert store.get("new", "anonymous").transcript == "New question"
    assert conn.execute("SELECT value FROM household_fixture").fetchone()[0] == "before"
    conn.close()
    restore_backup(previous)
    with sqlite3.connect(data / "cortex.db") as rolled_back:
        assert not rolled_back.execute("SELECT name FROM sqlite_master WHERE name='pending_turns'").fetchall()
        assert rolled_back.execute("SELECT value FROM household_fixture").fetchone()[0] == "before"
        _create_schema(rolled_back)
        rolled_back.commit()
        assert rolled_back.execute("SELECT COUNT(*) FROM pending_turns").fetchone()[0] == 0
    assert len(list((data / "backups").glob("cortex_pre_restore_*.tar.gz"))) == 1


def test_restore_refuses_active_deployment_without_mutation(deployment):
    data, _, conn = deployment
    archive = create_backup()
    with data_lock(data):
        with pytest.raises(RuntimeError, match="in use"):
            restore_backup(archive)
    assert conn.execute("SELECT value FROM household_fixture").fetchone()[0] == "before"
    assert not list((data / "backups").glob("cortex_pre_restore_*.tar.gz"))


@pytest.mark.parametrize("kind", ["traversal", "link", "corrupt", "future_format"])
def test_invalid_restore_does_not_touch_destination(deployment, tmp_path, kind):
    data, _, conn = deployment
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("../escaped" if kind == "traversal" else "cortex.db")
        payload = b"not a database"
        if kind == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = str(data / "cortex.db")
        elif kind == "future_format":
            from cortex.backup.storage import copy_database
            copy_database(data / "cortex.db", tmp_path / "snapshot.db")
            tar.add(tmp_path / "snapshot.db", arcname="cortex.db")
            member.name = "manifest.json"
            payload = b'{"format": 999}'
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        restore_backup(archive)
    assert conn.execute("SELECT value FROM household_fixture").fetchone()[0] == "before"
    assert not (tmp_path / "escaped").exists()
    assert not list((data / "backups").glob("cortex_pre_restore_*.tar.gz"))


def test_missing_database_with_invalid_snapshot_fails_closed(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "snapshot_bad.tar.gz").write_bytes(b"invalid")
    recovery = SelfRecovery(db_path=tmp_path / "cortex.db", snapshot_dir=snapshots)
    with pytest.raises(tarfile.ReadError):
        recovery.run_startup_checks()
    assert not (tmp_path / "cortex.db").exists()


def test_same_second_backups_are_distinct(deployment):
    first, second = create_backup(), create_backup()
    assert first != second and first.exists() and second.exists()


async def test_server_owns_data_directory_for_lifetime(deployment, monkeypatch):
    from cortex import server
    data, _, _ = deployment
    @asynccontextmanager
    async def services(app):
        yield
    monkeypatch.setattr(server, "_services_lifespan", services)
    async with server.lifespan(server.app):
        with pytest.raises(RuntimeError, match="in use"):
            with data_lock(data):
                pass
    with data_lock(data):
        pass
    assert server.app.state.ready is False


async def test_liveness_readiness_and_degraded_llm(deployment, monkeypatch):
    from cortex import server
    _, _, conn = deployment
    provider = SimpleNamespace(health=AsyncMock(return_value=True))
    monkeypatch.setattr(server, "_get_provider", lambda: provider)
    monkeypatch.setattr(server.app.state, "ready", True, raising=False)
    monkeypatch.setattr(server.app.state, "turn_store", TurnStore(conn), raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                base_url="http://test") as client:
        assert (await client.get("/live")).json() == {"status": "alive"}
        provider.health.assert_not_awaited()
        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["optional"]["tts"] == "not_checked"
        provider.health.return_value = False
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/live")).status_code == 200
        provider.health.side_effect = TimeoutError("unavailable")
        assert (await client.get("/ready")).status_code == 503


async def test_admin_restore_is_explicitly_offline_only():
    from fastapi import HTTPException
    from cortex.admin.system import restore_snapshot
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(snapshotter=object())))
    with pytest.raises(HTTPException) as error:
        await restore_snapshot(request, {"path": "snapshot.tar.gz"}, {})
    assert error.value.status_code == 409
