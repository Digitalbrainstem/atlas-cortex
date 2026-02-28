"""Offsite backup — sync local backups to NAS or remote server (Phase I7.1)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OffsiteBackupError(Exception):
    """Base error for offsite backup operations."""


class OffsiteBackup:
    """Sync local backups to a NAS or remote server."""

    def __init__(
        self,
        remote_path: str,
        method: str = "rsync",
        ssh_key: str | None = None,
        smb_share: str | None = None,
        smb_user: str | None = None,
        smb_password: str | None = None,
    ) -> None:
        if method not in ("rsync", "smb"):
            raise ValueError(f"Unsupported method: {method}. Use 'rsync' or 'smb'.")
        self.remote_path = remote_path
        self.method = method
        self.ssh_key = ssh_key
        self.smb_share = smb_share
        self.smb_user = smb_user
        self.smb_password = smb_password

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def sync(self, local_backup_dir: str = "./data/backups") -> dict:
        """Sync local backups to remote.

        Returns: ``{files_synced, bytes_transferred, duration_ms}``
        """
        local_path = Path(local_backup_dir)
        if not local_path.exists():
            raise OffsiteBackupError(f"Local backup directory does not exist: {local_path}")

        start = time.monotonic()

        if self.method == "rsync":
            result = await self._rsync_sync(local_path)
        else:
            result = await self._smb_sync(local_path)

        result["duration_ms"] = int((time.monotonic() - start) * 1000)
        return result

    async def list_remote_backups(self) -> list[dict]:
        """List backups on the remote server."""
        if self.method == "rsync":
            return await self._rsync_list()
        else:
            return await self._smb_list()

    async def apply_retention(
        self, daily: int = 7, weekly: int = 4, monthly: int = 12
    ) -> dict:
        """Apply retention policy on remote — delete old backups.

        Keeps the most recent *daily* daily backups, *weekly* weekly backups,
        and *monthly* monthly backups.

        Returns: ``{kept, deleted}``
        """
        backups = await self.list_remote_backups()
        if not backups:
            return {"kept": 0, "deleted": 0}

        to_keep: set[str] = set()
        to_delete: list[str] = []

        # Categorize by backup type
        by_type: dict[str, list[dict]] = {"daily": [], "weekly": [], "monthly": [], "other": []}
        for b in backups:
            name = b.get("name", "")
            if "_daily_" in name:
                by_type["daily"].append(b)
            elif "_weekly_" in name:
                by_type["weekly"].append(b)
            elif "_monthly_" in name:
                by_type["monthly"].append(b)
            else:
                by_type["other"].append(b)

        # Sort each category by name (which includes timestamp) descending
        limits = {"daily": daily, "weekly": weekly, "monthly": monthly, "other": daily}
        for btype, items in by_type.items():
            items.sort(key=lambda x: x.get("name", ""), reverse=True)
            limit = limits.get(btype, daily)
            for i, item in enumerate(items):
                if i < limit:
                    to_keep.add(item["name"])
                else:
                    to_delete.append(item["name"])

        # Delete excess backups
        deleted = 0
        for name in to_delete:
            try:
                if self.method == "rsync":
                    await self._rsync_delete(name)
                else:
                    await self._smb_delete(name)
                deleted += 1
            except OffsiteBackupError as exc:
                logger.warning("Failed to delete remote backup %s: %s", name, exc)

        return {"kept": len(to_keep), "deleted": deleted}

    async def health(self) -> bool:
        """Test connectivity to the remote."""
        try:
            if self.method == "rsync":
                return await self._rsync_health()
            else:
                return await self._smb_health()
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Rsync helpers
    # ------------------------------------------------------------------ #

    async def _rsync_sync(self, local_path: Path) -> dict:
        """Run rsync to sync local backups to remote."""
        cmd = ["rsync", "-avz", "--stats"]
        if self.ssh_key:
            cmd.extend(["-e", f"ssh -i {self.ssh_key} -o StrictHostKeyChecking=no"])
        cmd.extend([f"{local_path}/", self.remote_path])

        stdout, stderr = await self._run_subprocess(cmd)

        files_synced = 0
        bytes_transferred = 0
        for line in stdout.splitlines():
            m = re.search(r"Number of regular files transferred:\s*(\d+)", line)
            if m:
                files_synced = int(m.group(1))
            m = re.search(r"Total transferred file size:\s*([\d,]+)", line)
            if m:
                bytes_transferred = int(m.group(1).replace(",", ""))

        return {"files_synced": files_synced, "bytes_transferred": bytes_transferred}

    async def _rsync_list(self) -> list[dict]:
        """List files on the rsync remote."""
        cmd = ["rsync", "--list-only"]
        if self.ssh_key:
            cmd.extend(["-e", f"ssh -i {self.ssh_key} -o StrictHostKeyChecking=no"])
        cmd.append(self.remote_path)

        stdout, _ = await self._run_subprocess(cmd)

        results: list[dict] = []
        for line in stdout.splitlines():
            # rsync --list-only format: permissions size date time name
            parts = line.split(None, 4)
            if len(parts) >= 5 and parts[4].endswith(".tar.gz"):
                results.append({
                    "name": parts[4].strip(),
                    "size": int(parts[1].replace(",", "")) if parts[1].replace(",", "").isdigit() else 0,
                })
        return results

    async def _rsync_delete(self, name: str) -> None:
        """Delete a single file on the rsync remote."""
        # Use ssh to remove the file if remote_path has host:path format
        if ":" in self.remote_path:
            host, path = self.remote_path.rsplit(":", 1)
            cmd = ["ssh"]
            if self.ssh_key:
                cmd.extend(["-i", self.ssh_key, "-o", "StrictHostKeyChecking=no"])
            cmd.extend([host, "rm", "-f", f"{path}/{name}"])
        else:
            cmd = ["rm", "-f", f"{self.remote_path}/{name}"]

        await self._run_subprocess(cmd)

    async def _rsync_health(self) -> bool:
        """Check connectivity via rsync --list-only."""
        try:
            await self._rsync_list()
            return True
        except OffsiteBackupError:
            return False

    # ------------------------------------------------------------------ #
    # SMB helpers
    # ------------------------------------------------------------------ #

    async def _smb_sync(self, local_path: Path) -> dict:
        """Sync via smbclient."""
        if not self.smb_share:
            raise OffsiteBackupError("smb_share is required for SMB method")

        files = list(local_path.glob("*.tar.gz"))
        files_synced = 0
        bytes_transferred = 0

        for f in files:
            cmd = self._smb_cmd(f'put "{f}" "{self.remote_path}/{f.name}"')
            try:
                await self._run_subprocess(cmd)
                files_synced += 1
                bytes_transferred += f.stat().st_size
            except OffsiteBackupError as exc:
                logger.warning("SMB upload failed for %s: %s", f.name, exc)

        return {"files_synced": files_synced, "bytes_transferred": bytes_transferred}

    async def _smb_list(self) -> list[dict]:
        """List files on the SMB share."""
        if not self.smb_share:
            raise OffsiteBackupError("smb_share is required for SMB method")

        cmd = self._smb_cmd(f'ls "{self.remote_path}/*"')
        stdout, _ = await self._run_subprocess(cmd)

        results: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if line.endswith(".tar.gz") or ".tar.gz" in line:
                parts = line.split()
                if parts:
                    name = parts[0]
                    size = 0
                    for p in parts[1:]:
                        if p.isdigit():
                            size = int(p)
                            break
                    results.append({"name": name, "size": size})
        return results

    async def _smb_delete(self, name: str) -> None:
        """Delete a file on the SMB share."""
        cmd = self._smb_cmd(f'del "{self.remote_path}/{name}"')
        await self._run_subprocess(cmd)

    async def _smb_health(self) -> bool:
        """Check SMB connectivity."""
        try:
            await self._smb_list()
            return True
        except OffsiteBackupError:
            return False

    def _smb_cmd(self, command: str) -> list[str]:
        """Build an smbclient command."""
        cmd = ["smbclient", self.smb_share or ""]
        if self.smb_user:
            cmd.extend(["-U", self.smb_user])
        if self.smb_password:
            cmd.extend(["-N"])  # use -N with password passed via env
        cmd.extend(["-c", command])
        return cmd

    # ------------------------------------------------------------------ #
    # Subprocess helper
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _run_subprocess(cmd: list[str]) -> tuple[str, str]:
        """Run a command and return (stdout, stderr). Raises on non-zero exit."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise OffsiteBackupError(
                f"Command {cmd[0]} failed (rc={proc.returncode}): {stderr[:500]}"
            )

        return stdout, stderr
