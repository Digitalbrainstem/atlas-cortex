"""Backup and restore CLI for Atlas Cortex.

Usage::

    python -m cortex.backup create
    python -m cortex.backup list
    python -m cortex.backup restore --latest daily
    python -m cortex.backup restore path/to/backup.tar.gz

See docs/backup-restore.md for full design.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return Path(os.environ.get("CORTEX_DATA_DIR", "./data"))


def _backup_dir() -> Path:
    d = _data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(backup_type: str = "manual") -> Path:
    """Create a compressed backup snapshot.

    Returns the path to the created archive.
    """
    data = _data_dir()
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"cortex_{backup_type}_{ts}.tar.gz"
    archive_path = _backup_dir() / archive_name

    start = time.monotonic()
    db_path = data / "cortex.db"
    chroma_path = data / "cortex_chroma"

    with tarfile.open(archive_path, "w:gz") as tar:
        # SQLite online backup to a temp file
        if db_path.exists():
            tmp_db = data / f"cortex_backup_{ts}.db"
            try:
                src = sqlite3.connect(str(db_path))
                dst = sqlite3.connect(str(tmp_db))
                src.backup(dst)
                dst.close()
                src.close()
                tar.add(tmp_db, arcname="cortex.db")
            finally:
                if tmp_db.exists():
                    tmp_db.unlink()

        # ChromaDB directory
        if chroma_path.exists():
            tar.add(chroma_path, arcname="cortex_chroma")

        # Config
        env_path = data.parent / "cortex.env"
        if env_path.exists():
            tar.add(env_path, arcname="cortex.env")

    duration_ms = int((time.monotonic() - start) * 1000)
    size_bytes = archive_path.stat().st_size

    # Log to DB
    _log_backup(archive_path, backup_type, size_bytes, duration_ms)

    logger.info(
        "Backup created: %s (%.1f MB, %d ms)",
        archive_path.name,
        size_bytes / 1_048_576,
        duration_ms,
    )
    return archive_path


def restore_backup(path: Path | None = None, backup_type: str | None = None) -> None:
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

    # Safety snapshot first
    logger.info("Creating safety snapshot before restore...")
    create_backup("pre_restore")

    data = _data_dir()
    logger.info("Restoring from %s ...", path.name)

    with tarfile.open(path, "r:gz") as tar:
        # Use data filter (Python 3.12+) to prevent path traversal attacks
        try:
            tar.extractall(data, filter="data")  # type: ignore[call-arg]
        except TypeError:
            # Fallback for Python < 3.12: validate member paths manually
            for member in tar.getmembers():
                member_path = (data / member.name).resolve()
                if not str(member_path).startswith(str(data.resolve())):
                    raise ValueError(f"Unsafe path in archive: {member.name}")
            tar.extractall(data)  # noqa: S202

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
    except Exception:
        return []
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
        restore_backup(path=p, backup_type=args.latest)
        print("Restore complete.")
