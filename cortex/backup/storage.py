"""Shared SQLite-safe snapshots and offline restore primitives."""
# Module ownership: Backup and restore
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator


@contextmanager
def data_lock(directory: Path) -> Iterator[None]:
    """One server or offline restorer may own a data directory at a time."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".cortex.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Data directory is in use; stop Cortex before restoring") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def validate_database(path: Path) -> None:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Backup database failed SQLite integrity validation")


def copy_database(source: Path, target: Path) -> None:
    """SQLite backup includes committed WAL pages and does not replace live inodes."""
    validate_database(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            busy, _, _ = dst.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                raise RuntimeError("Database checkpoint is busy; stop all database users")
    validate_database(target)
    target.chmod(0o600)


def config_path(data: Path) -> Path:
    configured = Path(os.environ.get("CORTEX_CONFIG_DIR", "./config")) / "cortex.env"
    if configured.exists() or "CORTEX_CONFIG_DIR" in os.environ:
        return configured
    for path in (data / "cortex.env", data.parent / "cortex.env"):
        if path.exists():
            return path
    return configured


def write_manifest(directory: Path) -> None:
    from cortex.orchestrator.turn_store import TurnStorePolicy
    (directory / "manifest.json").write_text(json.dumps({
        "format": 1,
        "turn_policy": asdict(TurnStorePolicy.from_env()),
        "build_revision": os.environ.get("CORTEX_BUILD_REVISION", "unknown"),
        "restore_policy": "offline; restore matching code/config, then confirm recovered work",
        "external_files_consistency": "Stop ChromaDB and asset writers for a consistent file snapshot",
    }, indent=2), encoding="utf-8")


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def write_archive(staging: Path, destination: Path) -> None:
    temporary = destination.parent / f".{uuid.uuid4().hex}.partial"
    def regular_only(member: tarfile.TarInfo) -> tarfile.TarInfo:
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"Cannot back up link or special file: {member.name}")
        return member
    try:
        with temporary.open("xb") as output:
            temporary.chmod(0o600)
            with tarfile.open(fileobj=output, mode="w:gz") as tar:
                for item in staging.iterdir():
                    tar.add(item, arcname=item.name, filter=regular_only)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def unpack_archive(archive: Path, parent: Path) -> Iterator[Path]:
    """Validate every member before writing; never follow archive links."""
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=parent) as name:
        staging = Path(name)
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            seen: set[str] = set()
            for member in members:
                destination = staging / member.name
                if (not destination.resolve().is_relative_to(staging.resolve())
                        or member.name in seen or not (member.isfile() or member.isdir())):
                    raise ValueError(f"Unsupported or unsafe backup member: {member.name}")
                seen.add(member.name)
            for member in members:
                destination = staging / member.name
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        raise ValueError(f"Unreadable backup member: {member.name}")
                    with source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(0o600)
        database = staging / "cortex.db"
        if not database.is_file():
            raise ValueError("Backup has no cortex.db")
        validate_database(database)
        manifest = staging / "manifest.json"
        if manifest.exists() and json.loads(manifest.read_text())["format"] != 1:
            raise ValueError("Unsupported backup format")
        yield staging
