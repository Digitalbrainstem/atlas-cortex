"""Tests for the self-recovery startup system."""

from __future__ import annotations

import json
import os
import sqlite3
import tarfile

import pytest

from cortex.system.recovery import RecoveryResult, SelfRecovery


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_good_db(path: str) -> None:
    """Create a valid test DB with schema."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    for tbl in ("interactions", "admin_users", "system_settings",
                "emotional_profiles", "speaker_profiles"):
        conn.execute(f"CREATE TABLE IF NOT EXISTS [{tbl}] (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO interactions VALUES (1, 'test_interaction')")
    conn.execute("INSERT INTO admin_users VALUES (1, 'admin')")
    conn.commit()
    conn.close()


def _make_snapshot_archive(snap_dir: str, db_path: str, config_path: str | None = None) -> str:
    """Create a valid snapshot archive in snap_dir."""
    import shutil
    from datetime import datetime, timezone

    snap_dir_p = os.path.join(snap_dir)
    os.makedirs(snap_dir_p, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(snap_dir_p, f"snapshot_{ts}.tar.gz")
    staging = os.path.join(snap_dir_p, ".staging_test")
    os.makedirs(staging, exist_ok=True)

    try:
        shutil.copy2(db_path, os.path.join(staging, "cortex.db"))
        if config_path and os.path.exists(config_path):
            shutil.copy2(config_path, os.path.join(staging, "cortex.env"))
        state = {"timestamp": datetime.now(timezone.utc).isoformat(), "active_services": []}
        with open(os.path.join(staging, "system_state.json"), "w") as f:
            json.dump(state, f)

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in os.listdir(staging):
                tar.add(os.path.join(staging, item), arcname=item)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return archive_path


@pytest.fixture()
def recovery_env(tmp_path):
    """Set up temp environment for recovery tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    snap_dir = data_dir / "snapshots"
    snap_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    db_path = data_dir / "cortex.db"
    config_path = config_dir / "cortex.env"

    return {
        "db_path": db_path,
        "config_path": config_path,
        "snap_dir": snap_dir,
        "data_dir": data_dir,
        "tmp_path": tmp_path,
    }


def _make_recovery(env: dict) -> SelfRecovery:
    return SelfRecovery(
        db_path=env["db_path"],
        config_path=env["config_path"],
        snapshot_dir=env["snap_dir"],
    )


# ──────────────────────────────────────────────────────────────────
# Missing DB
# ──────────────────────────────────────────────────────────────────


class TestMissingDB:
    def test_fresh_start_no_db_no_snapshots(self, recovery_env):
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert result.recovered is True
        assert result.issue == "missing_database"
        assert "fresh" in result.details.lower()

    def test_restore_from_snapshot_when_db_missing(self, recovery_env):
        # Create a good DB, snapshot it, then delete DB
        _make_good_db(str(recovery_env["db_path"]))
        _make_snapshot_archive(
            str(recovery_env["snap_dir"]),
            str(recovery_env["db_path"]),
        )
        os.unlink(recovery_env["db_path"])

        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert result.recovered is True
        assert result.issue == "missing_database"
        assert "restored" in result.details.lower()
        assert recovery_env["db_path"].exists()


# ──────────────────────────────────────────────────────────────────
# Integrity check
# ──────────────────────────────────────────────────────────────────


class TestIntegrityCheck:
    def test_healthy_db_passes(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert result.recovered is False

    def test_good_tables_identified(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        r = _make_recovery(recovery_env)
        tables = r._identify_good_tables()
        assert "interactions" in tables
        assert "admin_users" in tables

    def test_missing_db_for_good_tables(self, recovery_env):
        r = _make_recovery(recovery_env)
        tables = r._identify_good_tables()
        assert tables == []


# ──────────────────────────────────────────────────────────────────
# Schema verification
# ──────────────────────────────────────────────────────────────────


class TestSchemaVerification:
    def test_all_required_tables_present(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        r = _make_recovery(recovery_env)
        result = RecoveryResult()
        r._verify_schema(result)
        assert not result.recovered  # no missing tables

    def test_missing_tables_detected(self, recovery_env):
        conn = sqlite3.connect(str(recovery_env["db_path"]))
        conn.execute("CREATE TABLE interactions (id INTEGER PRIMARY KEY)")
        # Missing: admin_users, system_settings, emotional_profiles, speaker_profiles
        conn.commit()
        conn.close()

        r = _make_recovery(recovery_env)
        result = RecoveryResult()
        r._verify_schema(result)
        assert result.recovered is True
        assert result.issue == "missing_tables"
        assert "admin_users" in result.details


# ──────────────────────────────────────────────────────────────────
# Config check
# ──────────────────────────────────────────────────────────────────


class TestConfigCheck:
    def test_config_exists_no_action(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        with open(recovery_env["config_path"], "w") as f:
            f.write("KEY=VALUE\n")
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert "restored_config_from" not in str(result.steps)

    def test_config_restored_from_snapshot(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        config_path = recovery_env["config_path"]
        with open(config_path, "w") as f:
            f.write("RESTORED=yes\n")

        _make_snapshot_archive(
            str(recovery_env["snap_dir"]),
            str(recovery_env["db_path"]),
            str(config_path),
        )
        os.unlink(config_path)

        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert any("restored_config_from" in s for s in result.steps)
        assert config_path.exists()

    def test_no_config_no_snapshot_ok(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        # Should not error — just logs info
        assert result.recovered is False


# ──────────────────────────────────────────────────────────────────
# Backup age check
# ──────────────────────────────────────────────────────────────────


class TestBackupAge:
    def test_no_snapshots_warns(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert any("no_snapshots_found" in s for s in result.steps)

    def test_recent_snapshot_ok(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        _make_snapshot_archive(
            str(recovery_env["snap_dir"]),
            str(recovery_env["db_path"]),
        )
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        # Recent snapshot — no stale_backup step
        assert not any("stale_backup" in s for s in result.steps)


# ──────────────────────────────────────────────────────────────────
# RecoveryResult dataclass
# ──────────────────────────────────────────────────────────────────


class TestRecoveryResult:
    def test_defaults(self):
        r = RecoveryResult()
        assert r.recovered is False
        assert r.issue == ""
        assert r.details == ""
        assert r.steps == []

    def test_mutable_steps(self):
        r = RecoveryResult()
        r.steps.append("step1")
        assert "step1" in r.steps

        # Ensure independent instances
        r2 = RecoveryResult()
        assert r2.steps == []


# ──────────────────────────────────────────────────────────────────
# Full recovery flow
# ──────────────────────────────────────────────────────────────────


class TestFullRecoveryFlow:
    def test_clean_startup(self, recovery_env):
        _make_good_db(str(recovery_env["db_path"]))
        with open(recovery_env["config_path"], "w") as f:
            f.write("OK=1\n")
        _make_snapshot_archive(
            str(recovery_env["snap_dir"]),
            str(recovery_env["db_path"]),
            str(recovery_env["config_path"]),
        )
        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert result.recovered is False

    def test_full_loss_recovery(self, recovery_env):
        """Simulate total data loss with a snapshot available."""
        # Create good state and snapshot
        _make_good_db(str(recovery_env["db_path"]))
        config_path = recovery_env["config_path"]
        with open(config_path, "w") as f:
            f.write("SAVED=yes\n")

        _make_snapshot_archive(
            str(recovery_env["snap_dir"]),
            str(recovery_env["db_path"]),
            str(config_path),
        )

        # Destroy everything except snapshots
        os.unlink(recovery_env["db_path"])
        os.unlink(config_path)

        r = _make_recovery(recovery_env)
        result = r.run_startup_checks()
        assert result.recovered is True
        assert recovery_env["db_path"].exists()
        assert config_path.exists()
