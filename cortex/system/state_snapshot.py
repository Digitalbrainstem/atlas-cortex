"""Periodic state snapshots for Atlas recovery.

Atomically backs up cortex.db (via VACUUM INTO), config, and running
state into timestamped compressed archives.  Optionally syncs to NAS.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SnapshotInfo:
    timestamp: str
    path: str
    size_bytes: int


@dataclass
class SnapshotConfig:
    interval: int = 900
    snapshot_dir: str = ""
    keep_count: int = 24
    nas_enabled: bool = False
    nas_path: str = ""
    nas_interval: int = 3600


class StateSnapshot:
    """Periodic snapshots of Atlas state for recovery."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        config_path: str | Path | None = None,
        state_fn: Any | None = None,
    ) -> None:
        data_dir = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
        self.cfg = SnapshotConfig(
            interval=int(os.environ.get("SNAPSHOT_INTERVAL_SECONDS", "900")),
            snapshot_dir=os.environ.get("SNAPSHOT_DIR", str(data_dir / "snapshots")),
            keep_count=int(os.environ.get("SNAPSHOT_KEEP_COUNT", "24")),
            nas_enabled=os.environ.get("SNAPSHOT_NAS_ENABLED", "").lower() in ("1", "true", "yes"),
            nas_path=os.environ.get("SNAPSHOT_NAS_PATH", ""),
            nas_interval=int(os.environ.get("SNAPSHOT_NAS_INTERVAL", "3600")),
        )
        self._db_path = Path(db_path) if db_path else data_dir / "cortex.db"
        self._config_path = Path(config_path) if config_path else Path(
            os.environ.get("CORTEX_CONFIG_DIR", "./config")
        ) / "cortex.env"
        self._state_fn = state_fn
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_nas_sync: float = 0.0

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(
            "Snapshot scheduler started (interval=%ds, keep=%d, dir=%s)",
            self.cfg.interval,
            self.cfg.keep_count,
            self.cfg.snapshot_dir,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Snapshot scheduler stopped")

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                await self.take_snapshot()
                self.cleanup_old()
                if self.cfg.nas_enabled and (time.time() - self._last_nas_sync) >= self.cfg.nas_interval:
                    await self.sync_to_nas()
            except Exception:
                logger.exception("Snapshot cycle failed")
            await asyncio.sleep(self.cfg.interval)

    # -- public API ---------------------------------------------------------

    async def take_snapshot(self) -> SnapshotInfo:
        """Create a snapshot archive. Safe to call manually."""
        snap_dir = Path(self.cfg.snapshot_dir)
        snap_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_path = snap_dir / f"snapshot_{ts}.tar.gz"
        staging_dir = snap_dir / f".staging_{ts}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Atomic DB backup via VACUUM INTO
            await self._backup_db(staging_dir / "cortex.db")

            # 2. Config file
            if self._config_path.exists():
                shutil.copy2(self._config_path, staging_dir / "cortex.env")

            # 3. System state JSON
            state = await self._collect_state()
            (staging_dir / "system_state.json").write_text(
                json.dumps(state, indent=2, default=str), encoding="utf-8",
            )

            # 4. Create compressed archive
            with tarfile.open(archive_path, "w:gz") as tar:
                for item in staging_dir.iterdir():
                    tar.add(item, arcname=item.name)

            size = archive_path.stat().st_size
            logger.info("Snapshot created: %s (%d bytes)", archive_path.name, size)
            return SnapshotInfo(timestamp=ts, path=str(archive_path), size_bytes=size)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def list_snapshots(self) -> list[SnapshotInfo]:
        """List available snapshots, newest first."""
        snap_dir = Path(self.cfg.snapshot_dir)
        if not snap_dir.exists():
            return []
        results: list[SnapshotInfo] = []
        for p in sorted(snap_dir.glob("snapshot_*.tar.gz"), reverse=True):
            ts = p.name.replace("snapshot_", "").replace(".tar.gz", "")
            results.append(SnapshotInfo(timestamp=ts, path=str(p), size_bytes=p.stat().st_size))
        return results

    async def restore_from_snapshot(self, path: str | Path) -> dict[str, Any]:
        """Restore DB and config from a snapshot archive."""
        archive = Path(path)
        if not archive.exists():
            raise FileNotFoundError(f"Snapshot not found: {path}")

        restored: list[str] = []
        extract_dir = archive.parent / ".restore_tmp"
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(extract_dir)

            db_backup = extract_dir / "cortex.db"
            if db_backup.exists():
                target = self._db_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db_backup, target)
                restored.append("cortex.db")

            config_backup = extract_dir / "cortex.env"
            if config_backup.exists():
                target = self._config_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(config_backup, target)
                restored.append("cortex.env")

            state_file = extract_dir / "system_state.json"
            state = {}
            if state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))

            logger.info("Restored from snapshot %s: %s", archive.name, restored)
            return {"restored": restored, "state": state}
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    def cleanup_old(self) -> int:
        """Remove snapshots beyond keep count. Returns number removed."""
        snapshots = self.list_snapshots()
        removed = 0
        for snap in snapshots[self.cfg.keep_count:]:
            try:
                Path(snap.path).unlink()
                removed += 1
            except OSError:
                logger.warning("Failed to remove old snapshot: %s", snap.path)
        if removed:
            logger.info("Cleaned up %d old snapshots", removed)
        return removed

    async def sync_to_nas(self) -> bool:
        """Rsync latest snapshot to NAS path."""
        if not self.cfg.nas_path:
            return False
        snapshots = self.list_snapshots()
        if not snapshots:
            return False
        latest = snapshots[0]
        try:
            proc = await asyncio.create_subprocess_exec(
                "rsync", "-az", "--timeout=60", latest.path, self.cfg.nas_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                self._last_nas_sync = time.time()
                logger.info("Synced snapshot to NAS: %s", self.cfg.nas_path)
                return True
            logger.warning("NAS sync failed (rc=%d): %s", proc.returncode, stderr.decode())
        except Exception:
            logger.exception("NAS sync error")
        return False

    # -- internals ----------------------------------------------------------

    async def _backup_db(self, dest: Path) -> None:
        """Atomic DB backup using VACUUM INTO (WAL-safe)."""
        if not self._db_path.exists():
            logger.debug("DB file not found at %s — skipping backup", self._db_path)
            return
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(f"VACUUM INTO '{dest}'")
        finally:
            conn.close()

    async def _collect_state(self) -> dict[str, Any]:
        """Gather current system state for the snapshot."""
        state: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_services": [],
            "pending_tasks": [],
            "resource_levels": {},
        }
        # Gather active services from scheduler
        try:
            from cortex.scheduler import _background_services
            state["active_services"] = [svc.name for svc in _background_services]
        except Exception:
            pass

        # Gather resource levels if monitor is running
        if self._state_fn:
            try:
                extra = self._state_fn()
                state.update(extra)
            except Exception:
                pass

        return state

    def get_latest_snapshot_age(self) -> float | None:
        """Return age in seconds of the latest snapshot, or None."""
        snapshots = self.list_snapshots()
        if not snapshots:
            return None
        try:
            ts = snapshots[0].timestamp
            dt = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            return None
