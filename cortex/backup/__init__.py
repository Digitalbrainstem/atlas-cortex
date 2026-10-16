"""Backup and restore CLI for Atlas Cortex.

Usage::

    python -m cortex.backup create
    python -m cortex.backup list
    python -m cortex.backup restore --latest daily
    python -m cortex.backup restore path/to/backup.tar.gz

See docs/backup-restore.md for full design.
"""

# Module ownership: Backup and restore

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from cortex.backup.storage import (
    config_path, copy_database, copy_file, data_lock, unpack_archive,
    write_archive, write_manifest,
)

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return Path(os.environ.get("CORTEX_DATA_DIR", "./data"))


def _backup_dir() -> Path:
    d = _data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(backup_type: str = "manual", *, data_dir: Path | None = None) -> Path:
    """Create a compressed backup snapshot.

    Returns the path to the created archive.
    """
    if backup_type not in {"manual", "daily", "weekly", "monthly", "pre_restore"}:
        raise ValueError(f"Unsupported backup type: {backup_type}")
    data = data_dir if data_dir is not None else _data_dir()
    backup_dir = data / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"cortex_{backup_type}_{ts}_{uuid.uuid4().hex[:8]}.tar.gz"
    archive_path = backup_dir / archive_name

    start = time.monotonic()
    db_path = data / "cortex.db"
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=backup_dir) as name:
        staging = Path(name)
        copy_database(db_path, staging / "cortex.db")
        for directory in ("cortex_chroma", "skins"):
            if (data / directory).exists():
                logger.warning("Backing up %s: stop its writers for file-level consistency", directory)
                shutil.copytree(data / directory, staging / directory, symlinks=True)
        env_path = config_path(data)
        if env_path.is_file():
            copy_file(env_path, staging / "cortex.env")
        write_manifest(staging)
        write_archive(staging, archive_path)

    duration_ms = int((time.monotonic() - start) * 1000)
    size_bytes = archive_path.stat().st_size

    # Log to DB
    if data == _data_dir():
        _log_backup(archive_path, backup_type, size_bytes, duration_ms)

    logger.info(
        "Backup created: %s (%.1f MB, %d ms)",
        archive_path.name,
        size_bytes / 1_048_576,
        duration_ms,
    )
    return archive_path


def restore_backup(
    path: Path | None = None, backup_type: str | None = None, *,
    data_dir: Path | None = None,
) -> None:
    """Restore from a backup archive.

    Args:
        path:        Explicit path to a .tar.gz archive.
        backup_type: If ``path`` is None, restore the latest of this type
                     (``"daily"``, ``"weekly"``, ``"monthly"``, ``"manual"``).
    """
    if path is None:
        path = _find_latest(backup_type or "daily")
    if path is None or not path.exists():
        raise FileNotFoundError(f"No backup found (type={backup_type}, path={path})")

    data = data_dir if data_dir is not None else _data_dir()
    logger.info("Restoring from %s ...", path.name)

    with data_lock(data), unpack_archive(path, data) as staging:
        if (data / "cortex.db").exists():
            logger.info("Creating safety snapshot before restore...")
            create_backup("pre_restore", data_dir=data)
        copy_database(staging / "cortex.db", data / "cortex.db")
        # An explicit target must never overwrite the current deployment's config.
        env_target = data / "cortex.env" if data_dir is not None else config_path(data)
        for source, target in (
            (staging / "cortex.env", env_target),
            (staging / "manifest.json", data / "restored-manifest.json"),
        ):
            if source.exists():
                copy_file(source, target)
        for directory in ("cortex_chroma", "skins"):
            source = staging / directory
            if source.exists():
                target = data / directory
                if target.exists():
                    # Preserve the complete old directory, not a merged stale index.
                    target.rename(data / f"{directory}.pre_restore-{uuid.uuid4().hex[:8]}")
                source.rename(target)

    logger.info("Restore complete.")


def list_backups() -> list[dict]:
    """Return metadata for all backups from the backup_log table."""
    db_path = _data_dir() / "cortex.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM backup_log ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        logger.exception("Cannot read backup history")
        raise
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _find_latest(backup_type: str) -> Path | None:
    backups = sorted(
        _backup_dir().glob(f"cortex_{backup_type}_*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return backups[0] if backups else None


def _log_backup(archive_path: Path, backup_type: str, size_bytes: int, duration_ms: int) -> None:
    db_path = _data_dir() / "cortex.db"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            INSERT INTO backup_log (archive_path, backup_type, size_bytes, duration_ms, success)
            VALUES (?, ?, ?, ?, TRUE)
            """,
            (str(archive_path), backup_type, size_bytes, duration_ms),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Could not log backup: %s", exc)


# ──────────────────────────────────────────────────────────────────
# CLI entry point (python -m cortex.backup)
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m cortex.backup",
        description="Atlas Cortex backup and restore tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    sub.add_parser("create", help="Create a manual backup snapshot")

    # list
    sub.add_parser("list", help="List recent backups")

    # restore
    restore_p = sub.add_parser("restore", help="Restore from a backup")
    restore_p.add_argument("path", nargs="?", help="Path to backup archive")
    restore_p.add_argument("--latest", metavar="TYPE", help="Restore latest of type (daily/weekly/monthly)")
    restore_p.add_argument("--target", type=Path, help="Restore into an isolated data directory")

    args = parser.parse_args()

    if args.command == "create":
        path = create_backup("manual")
        print(f"Backup created: {path}")
    elif args.command == "list":
        backups = list_backups()
        if not backups:
            print("No backups found.")
        for b in backups:
            size_mb = (b.get("size_bytes") or 0) / 1_048_576
            print(f"  [{b['backup_type']:8s}] {b['created_at']}  {size_mb:.1f} MB  {b['archive_path']}")
    elif args.command == "restore":
        p = Path(args.path) if args.path else None
        restore_backup(path=p, backup_type=args.latest, data_dir=args.target)
        print("Restore complete.")
