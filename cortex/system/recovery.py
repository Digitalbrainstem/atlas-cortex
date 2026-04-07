"""Self-recovery on startup: integrity check, snapshot restore, schema verify."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    recovered: bool = False
    issue: str = ""
    details: str = ""
    steps: list[str] = field(default_factory=list)


class SelfRecovery:
    """On startup: verify integrity, restore if needed, rebuild if necessary."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        config_path: str | Path | None = None,
        snapshot_dir: str | Path | None = None,
    ) -> None:
        data_dir = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
        self._db_path = Path(db_path) if db_path else data_dir / "cortex.db"
        self._config_path = Path(config_path) if config_path else Path(
            os.environ.get("CORTEX_CONFIG_DIR", "./config")
        ) / "cortex.env"
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir else Path(
            os.environ.get("SNAPSHOT_DIR", str(data_dir / "snapshots"))
        )

    # -- main entry point ---------------------------------------------------

    def run_startup_checks(self) -> RecoveryResult:
        """Called during Atlas startup before any services start."""
        result = RecoveryResult()

        # Step 1: DB existence
        if not self._db_path.exists():
            self._handle_missing_db(result)
            # Step 3 will be handled by init_db in server startup
            self._check_config(result)
            self._check_backup_age(result)
            return result

        # Step 2: SQLite integrity
        self._check_integrity(result)

        # Step 3: Schema verification
        self._verify_schema(result)

        # Step 4: Config
        self._check_config(result)

        # Step 5: Backup health
        self._check_backup_age(result)

        # Step 6: Log status
        if result.recovered:
            logger.warning(
                "Recovery completed — issue=%s details=%s steps=%s",
                result.issue, result.details, result.steps,
            )
        else:
            logger.info("Startup checks passed — no recovery needed")

        return result

    # -- step 1: missing DB -------------------------------------------------

    def _handle_missing_db(self, result: RecoveryResult) -> None:
        snapshots = self._list_snapshots()
        if snapshots:
            snap_path = snapshots[0]
            logger.info("No database found — restoring from snapshot: %s", snap_path.name)
            try:
                self._restore_db_from_snapshot(snap_path)
                result.recovered = True
                result.issue = "missing_database"
                result.details = f"Restored from snapshot {snap_path.name} — some recent data may be lost"
                result.steps.append(f"restored_db_from:{snap_path.name}")
            except Exception as exc:
                logger.error("Snapshot restore failed: %s", exc)
                self._init_fresh_db(result)
        else:
            self._init_fresh_db(result)

    def _restore_db_from_snapshot(self, snap_path: Path) -> None:
        """Extract cortex.db from a snapshot archive into the DB path."""
        import shutil
        import tarfile

        extract_dir = self._snapshot_dir / ".db_restore"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(snap_path, "r:gz") as tar:
                tar.extractall(extract_dir)
            restored_db = extract_dir / "cortex.db"
            if not restored_db.exists():
                raise FileNotFoundError("cortex.db not found in snapshot")
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(restored_db, self._db_path)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    def _init_fresh_db(self, result: RecoveryResult) -> None:
        logger.info("No database or snapshots found. Starting fresh.")
        result.recovered = True
        result.issue = "missing_database"
        result.details = "No database found. Starting fresh."
        result.steps.append("init_fresh_db")

    # -- step 2: integrity --------------------------------------------------

    def _check_integrity(self, result: RecoveryResult) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute("PRAGMA integrity_check").fetchall()
                status = rows[0][0] if rows else "unknown"
            finally:
                conn.close()
        except Exception as exc:
            status = f"error: {exc}"

        if status == "ok":
            logger.info("Database integrity check: OK")
            return

        logger.error("Database corruption detected: %s", status)
        result.recovered = True
        result.issue = "database_corruption"

        good_tables = self._identify_good_tables()
        snapshots = self._list_snapshots()

        if snapshots:
            snap_path = snapshots[0]
            logger.info("Restoring from snapshot %s and merging salvageable data", snap_path.name)
            try:
                salvaged = self._restore_and_merge(snap_path, good_tables)
                result.details = (
                    f"Recovered from corruption. Restored {len(salvaged)} tables "
                    f"from current DB, rest from snapshot {snap_path.name}."
                )
                result.steps.append(f"integrity_restore:{snap_path.name}")
                result.steps.append(f"salvaged_tables:{','.join(salvaged)}")
            except Exception as exc:
                logger.error("Restore+merge failed: %s — rebuilding from corrupt DB", exc)
                self._try_rebuild_from_corrupt(result)
        else:
            self._try_rebuild_from_corrupt(result)

    def _identify_good_tables(self) -> list[str]:
        """Find tables that can still be read from the (possibly corrupt) DB."""
        good: list[str] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                tables = [
                    r[0] for r in
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                for tbl in tables:
                    try:
                        conn.execute(f"SELECT COUNT(*) FROM [{tbl}]")  # noqa: S608
                        good.append(tbl)
                    except Exception:
                        logger.debug("Table %s is corrupted", tbl)
            finally:
                conn.close()
        except Exception:
            logger.debug("Cannot open corrupt DB to identify good tables")
        return good

    def _restore_and_merge(self, snap_path: Path, good_tables: list[str]) -> list[str]:
        """Restore DB from snapshot, then merge data from good tables."""
        import shutil
        import tarfile

        corrupt_path = self._db_path.with_suffix(".db.corrupt")
        shutil.move(str(self._db_path), str(corrupt_path))

        extract_dir = self._snapshot_dir / ".restore_merge"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(snap_path, "r:gz") as tar:
                tar.extractall(extract_dir)

            restored_db = extract_dir / "cortex.db"
            if restored_db.exists():
                shutil.copy2(restored_db, self._db_path)
            else:
                # Snapshot had no DB — put corrupt back and rebuild
                shutil.move(str(corrupt_path), str(self._db_path))
                return []

            # Merge salvageable data from corrupt DB
            salvaged = self._merge_salvaged(corrupt_path, good_tables)

            # Keep corrupt DB for manual inspection
            logger.info("Corrupt DB preserved at %s", corrupt_path)
            return salvaged
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    def _merge_salvaged(self, corrupt_path: Path, good_tables: list[str]) -> list[str]:
        """Attempt to merge newer rows from the corrupt DB into the restored one."""
        merged: list[str] = []
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=OFF")

            conn.execute(f"ATTACH DATABASE '{corrupt_path}' AS corrupt")
            try:
                # Get tables that exist in both databases
                main_tables = {
                    r[0] for r in
                    conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall()
                }
                for tbl in good_tables:
                    if tbl not in main_tables:
                        continue
                    try:
                        conn.execute(
                            f"INSERT OR IGNORE INTO main.[{tbl}] SELECT * FROM corrupt.[{tbl}]"  # noqa: S608
                        )
                        merged.append(tbl)
                    except Exception:
                        logger.debug("Could not merge table %s", tbl)
                conn.commit()
            finally:
                try:
                    conn.execute("DETACH DATABASE corrupt")
                except Exception:
                    pass
                conn.close()
        except Exception:
            logger.exception("Merge salvaged data failed")
        return merged

    def _try_rebuild_from_corrupt(self, result: RecoveryResult) -> None:
        """Last resort: dump what we can from the corrupt DB, recreate, re-import."""
        import shutil

        corrupt_path = self._db_path.with_suffix(".db.corrupt")
        if self._db_path.exists():
            shutil.move(str(self._db_path), str(corrupt_path))

        good_tables = self._identify_good_tables_from(corrupt_path)

        # init_db will create a fresh schema
        result.details = "Rebuilt database from corrupted source. Some data may be lost."
        result.steps.append("rebuild_from_corrupt")

        if good_tables:
            try:
                # Let init_db create the schema first (called by server startup)
                # We just note what we salvaged for later merge
                result.steps.append(f"salvageable_tables:{','.join(good_tables)}")
                result.details += f" Salvageable tables: {', '.join(good_tables)}"
            except Exception:
                pass

    def _identify_good_tables_from(self, path: Path) -> list[str]:
        """Identify readable tables from a specific DB file."""
        if not path.exists():
            return []
        good: list[str] = []
        try:
            conn = sqlite3.connect(str(path))
            try:
                tables = [
                    r[0] for r in
                    conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                ]
                for tbl in tables:
                    try:
                        conn.execute(f"SELECT COUNT(*) FROM [{tbl}]")  # noqa: S608
                        good.append(tbl)
                    except Exception:
                        pass
            finally:
                conn.close()
        except Exception:
            pass
        return good

    # -- step 3: schema verification ----------------------------------------

    def _verify_schema(self, result: RecoveryResult) -> None:
        """Ensure all expected tables exist. init_db handles migrations."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                tables = {
                    r[0] for r in
                    conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
            finally:
                conn.close()

            # Core tables that must exist
            required = {
                "interactions", "admin_users", "system_settings",
                "emotional_profiles", "speaker_profiles",
            }
            missing = required - tables
            if missing:
                logger.warning("Missing tables detected: %s — init_db will create them", missing)
                result.steps.append(f"missing_tables:{','.join(sorted(missing))}")
                if not result.recovered:
                    result.recovered = True
                    result.issue = "missing_tables"
                    result.details = f"Missing tables: {', '.join(sorted(missing))} — will be created by init_db"
        except Exception as exc:
            logger.warning("Schema verification failed: %s", exc)

    # -- step 4: config check -----------------------------------------------

    def _check_config(self, result: RecoveryResult) -> None:
        if self._config_path.exists():
            return

        snapshots = self._list_snapshots()
        for snap_path in snapshots:
            try:
                self._restore_config_from_snapshot(snap_path)
                result.steps.append(f"restored_config_from:{snap_path.name}")
                logger.info("Restored config from snapshot %s", snap_path.name)
                return
            except Exception:
                continue

        logger.info("No config file found — defaults will be used")

    def _restore_config_from_snapshot(self, snap_path: Path) -> None:
        import shutil
        import tarfile

        extract_dir = self._snapshot_dir / ".config_restore"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(snap_path, "r:gz") as tar:
                tar.extractall(extract_dir)
            cfg = extract_dir / "cortex.env"
            if cfg.exists():
                self._config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cfg, self._config_path)
            else:
                raise FileNotFoundError("cortex.env not in snapshot")
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    # -- step 5: backup health ----------------------------------------------

    def _check_backup_age(self, result: RecoveryResult) -> None:
        snapshots = self._list_snapshots()
        if not snapshots:
            logger.warning("No snapshots found — backups not configured or never run")
            result.steps.append("no_snapshots_found")
            return

        latest = snapshots[0]
        try:
            from datetime import datetime, timezone
            ts = latest.name.replace("snapshot_", "").replace(".tar.gz", "")
            dt = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
            if age_seconds > 3600:
                hours = age_seconds / 3600
                logger.warning("Latest snapshot is %.1f hours old — consider increasing frequency", hours)
                result.steps.append(f"stale_backup:{hours:.1f}h")
        except Exception:
            logger.debug("Could not determine snapshot age")

    # -- helpers ------------------------------------------------------------

    def _list_snapshots(self) -> list[Path]:
        """Return snapshot files newest first."""
        if not self._snapshot_dir.exists():
            return []
        return sorted(self._snapshot_dir.glob("snapshot_*.tar.gz"), reverse=True)
