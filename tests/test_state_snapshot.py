"""Tests for the state snapshot system."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from cortex.system.state_snapshot import SnapshotInfo, StateSnapshot


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_test_db(path: str) -> None:
    """Create a minimal SQLite DB for snapshot tests."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO test_data VALUES (1, 'hello')")
    conn.execute("INSERT INTO test_data VALUES (2, 'world')")
    conn.commit()
    conn.close()


def _make_config(path: str) -> None:
    """Create a minimal config file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("TTS_PROVIDER=kokoro\nLLM_PROVIDER=transformers\n")


@pytest.fixture()
def snap_env(tmp_path):
    """Set up a temp environment with DB and config for snapshot tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snap_dir = data_dir / "snapshots"
    snap_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    db_path = data_dir / "cortex.db"
    config_path = config_dir / "cortex.env"

    _make_test_db(str(db_path))
    _make_config(str(config_path))

    return {
        "db_path": db_path,
        "config_path": config_path,
        "snap_dir": snap_dir,
        "data_dir": data_dir,
        "tmp_path": tmp_path,
    }


def _make_snapshot(env: dict, **overrides) -> StateSnapshot:
    """Create a StateSnapshot wired to the test environment."""
    ss = StateSnapshot(
        db_path=env["db_path"],
        config_path=env["config_path"],
    )
    ss.cfg.snapshot_dir = str(env["snap_dir"])
    ss.cfg.keep_count = overrides.get("keep_count", 24)
    return ss


# ──────────────────────────────────────────────────────────────────
# Snapshot creation
# ──────────────────────────────────────────────────────────────────


class TestSnapshotCreation:
    @pytest.mark.asyncio
    async def test_take_snapshot_creates_archive(self, snap_env):
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()
        assert os.path.exists(info.path)
        assert info.size_bytes > 0
        assert info.timestamp  # non-empty

    @pytest.mark.asyncio
    async def test_snapshot_contains_db(self, snap_env):
        import tarfile

        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        with tarfile.open(info.path, "r:gz") as tar:
            names = tar.getnames()
        assert "cortex.db" in names

    @pytest.mark.asyncio
    async def test_snapshot_contains_config(self, snap_env):
        import tarfile

        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        with tarfile.open(info.path, "r:gz") as tar:
            names = tar.getnames()
        assert "cortex.env" in names

    @pytest.mark.asyncio
    async def test_snapshot_contains_state_json(self, snap_env):
        import tarfile

        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        with tarfile.open(info.path, "r:gz") as tar:
            names = tar.getnames()
            assert "system_state.json" in names
            f = tar.extractfile("system_state.json")
            assert f is not None
            state = json.loads(f.read())
            assert "timestamp" in state

    @pytest.mark.asyncio
    async def test_snapshot_db_is_valid(self, snap_env):
        import tarfile

        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        with tarfile.open(info.path, "r:gz") as tar:
            tar.extractall(snap_env["tmp_path"] / "verify")

        db_copy = snap_env["tmp_path"] / "verify" / "cortex.db"
        conn = sqlite3.connect(str(db_copy))
        rows = conn.execute("SELECT * FROM test_data ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][1] == "hello"

    @pytest.mark.asyncio
    async def test_snapshot_no_db_still_works(self, snap_env):
        os.unlink(snap_env["db_path"])
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()
        assert os.path.exists(info.path)

    @pytest.mark.asyncio
    async def test_snapshot_no_config_still_works(self, snap_env):
        os.unlink(snap_env["config_path"])
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()
        assert os.path.exists(info.path)

    @pytest.mark.asyncio
    async def test_state_fn_called(self, snap_env):
        called = False

        def state_fn():
            nonlocal called
            called = True
            return {"resource_levels": {"ram_pct": 0.55}}

        ss = _make_snapshot(snap_env)
        ss._state_fn = state_fn
        await ss.take_snapshot()
        assert called


# ──────────────────────────────────────────────────────────────────
# Listing & cleanup
# ──────────────────────────────────────────────────────────────────


class TestSnapshotListing:
    @pytest.mark.asyncio
    async def test_list_empty(self, snap_env):
        ss = _make_snapshot(snap_env)
        assert ss.list_snapshots() == []

    @pytest.mark.asyncio
    async def test_list_after_create(self, snap_env):
        ss = _make_snapshot(snap_env)
        await ss.take_snapshot()
        snaps = ss.list_snapshots()
        assert len(snaps) == 1
        assert isinstance(snaps[0], SnapshotInfo)

    @pytest.mark.asyncio
    async def test_list_ordered_newest_first(self, snap_env):
        import asyncio

        ss = _make_snapshot(snap_env)
        await ss.take_snapshot()
        await asyncio.sleep(1.1)  # ensure different timestamp
        await ss.take_snapshot()
        snaps = ss.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0].timestamp >= snaps[1].timestamp


class TestSnapshotCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_old(self, snap_env):
        import asyncio

        ss = _make_snapshot(snap_env, keep_count=2)
        for _ in range(4):
            await ss.take_snapshot()
            await asyncio.sleep(1.1)
        removed = ss.cleanup_old()
        assert removed == 2
        assert len(ss.list_snapshots()) == 2

    @pytest.mark.asyncio
    async def test_cleanup_nothing_to_remove(self, snap_env):
        ss = _make_snapshot(snap_env, keep_count=10)
        await ss.take_snapshot()
        removed = ss.cleanup_old()
        assert removed == 0


# ──────────────────────────────────────────────────────────────────
# Restore
# ──────────────────────────────────────────────────────────────────


class TestSnapshotRestore:
    @pytest.mark.asyncio
    async def test_restore_db_and_config(self, snap_env):
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        # Delete originals
        os.unlink(snap_env["db_path"])
        os.unlink(snap_env["config_path"])
        assert not snap_env["db_path"].exists()

        result = await ss.restore_from_snapshot(info.path)
        assert "cortex.db" in result["restored"]
        assert "cortex.env" in result["restored"]
        assert snap_env["db_path"].exists()
        assert snap_env["config_path"].exists()

    @pytest.mark.asyncio
    async def test_restore_verifies_db_content(self, snap_env):
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()

        # Modify original DB
        conn = sqlite3.connect(str(snap_env["db_path"]))
        conn.execute("DELETE FROM test_data")
        conn.commit()
        conn.close()

        # Restore
        await ss.restore_from_snapshot(info.path)

        conn = sqlite3.connect(str(snap_env["db_path"]))
        rows = conn.execute("SELECT COUNT(*) FROM test_data").fetchone()[0]
        conn.close()
        assert rows == 2  # original data restored

    @pytest.mark.asyncio
    async def test_restore_nonexistent_raises(self, snap_env):
        ss = _make_snapshot(snap_env)
        with pytest.raises(FileNotFoundError):
            await ss.restore_from_snapshot("/no/such/snapshot.tar.gz")

    @pytest.mark.asyncio
    async def test_restore_returns_state(self, snap_env):
        ss = _make_snapshot(snap_env)
        info = await ss.take_snapshot()
        result = await ss.restore_from_snapshot(info.path)
        assert "state" in result
        assert "timestamp" in result["state"]


# ──────────────────────────────────────────────────────────────────
# Backup age
# ──────────────────────────────────────────────────────────────────


class TestSnapshotAge:
    @pytest.mark.asyncio
    async def test_latest_snapshot_age_none_when_empty(self, snap_env):
        ss = _make_snapshot(snap_env)
        assert ss.get_latest_snapshot_age() is None

    @pytest.mark.asyncio
    async def test_latest_snapshot_age_recent(self, snap_env):
        ss = _make_snapshot(snap_env)
        await ss.take_snapshot()
        age = ss.get_latest_snapshot_age()
        assert age is not None
        assert age < 10  # just created, should be very recent
