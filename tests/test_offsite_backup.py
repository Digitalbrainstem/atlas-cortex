"""Tests for offsite backup — mock subprocess calls for rsync."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from cortex.backup.offsite import OffsiteBackup, OffsiteBackupError

SAMPLE_RSYNC_STATS = """\
Number of files: 10
Number of files transferred: 3
Number of regular files transferred: 3
Total file size: 1,234,567
Total transferred file size: 500,000
Literal data: 500,000
Matched data: 0
File list size: 200
"""

SAMPLE_RSYNC_LIST = """\
-rw-r--r--      1,234 2025/01/01 12:00:00 cortex_daily_20250101T120000Z.tar.gz
-rw-r--r--      5,678 2025/01/02 12:00:00 cortex_daily_20250102T120000Z.tar.gz
-rw-r--r--     10,000 2025/01/07 12:00:00 cortex_weekly_20250107T120000Z.tar.gz
"""


def _make_process(returncode=0, stdout=b"", stderr=b""):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


@pytest.fixture
def backup_dir(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    # Create some sample backup files
    (d / "cortex_daily_20250101T120000Z.tar.gz").write_bytes(b"fake" * 100)
    (d / "cortex_daily_20250102T120000Z.tar.gz").write_bytes(b"fake" * 200)
    return str(d)


class TestOffsiteBackupInit:
    def test_valid_methods(self):
        OffsiteBackup(remote_path="/mnt/nas/backups", method="rsync")
        OffsiteBackup(remote_path="/mnt/nas/backups", method="smb", smb_share="//nas/share")

    def test_invalid_method(self):
        with pytest.raises(ValueError, match="Unsupported method"):
            OffsiteBackup(remote_path="/mnt/nas/backups", method="ftp")


class TestRsyncSync:
    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_sync_success(self, mock_exec, backup_dir):
        mock_exec.return_value = _make_process(
            stdout=SAMPLE_RSYNC_STATS.encode("utf-8")
        )

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        result = await backup.sync(local_backup_dir=backup_dir)

        assert result["files_synced"] == 3
        assert result["bytes_transferred"] == 500000
        assert "duration_ms" in result
        assert result["duration_ms"] >= 0

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_sync_with_ssh_key(self, mock_exec, backup_dir):
        mock_exec.return_value = _make_process(
            stdout=SAMPLE_RSYNC_STATS.encode("utf-8")
        )

        backup = OffsiteBackup(
            remote_path="user@nas:/backups",
            method="rsync",
            ssh_key="/home/user/.ssh/id_rsa",
        )
        await backup.sync(local_backup_dir=backup_dir)

        # Check that ssh key was included in the command
        call_args = mock_exec.call_args[0]
        assert "-e" in call_args
        assert any("id_rsa" in str(a) for a in call_args)

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_sync_failure(self, mock_exec, backup_dir):
        mock_exec.return_value = _make_process(
            returncode=1,
            stderr=b"rsync: connection refused",
        )

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        with pytest.raises(OffsiteBackupError, match="failed"):
            await backup.sync(local_backup_dir=backup_dir)

    async def test_sync_missing_dir(self):
        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        with pytest.raises(OffsiteBackupError, match="does not exist"):
            await backup.sync(local_backup_dir="/nonexistent/path")


class TestRsyncListRemote:
    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_list_remote_backups(self, mock_exec):
        mock_exec.return_value = _make_process(
            stdout=SAMPLE_RSYNC_LIST.encode("utf-8")
        )

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        result = await backup.list_remote_backups()

        assert len(result) == 3
        names = {r["name"] for r in result}
        assert "cortex_daily_20250101T120000Z.tar.gz" in names
        assert "cortex_weekly_20250107T120000Z.tar.gz" in names

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_list_remote_empty(self, mock_exec):
        mock_exec.return_value = _make_process(stdout=b"")

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        result = await backup.list_remote_backups()
        assert result == []


class TestRetentionPolicy:
    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_apply_retention_keeps_recent(self, mock_exec):
        # First call: list, subsequent calls: delete
        list_output = (
            "-rw-r--r-- 1,000 2025/01/01 12:00:00 cortex_daily_20250101.tar.gz\n"
            "-rw-r--r-- 1,000 2025/01/02 12:00:00 cortex_daily_20250102.tar.gz\n"
            "-rw-r--r-- 1,000 2025/01/03 12:00:00 cortex_daily_20250103.tar.gz\n"
        )
        mock_exec.return_value = _make_process(stdout=list_output.encode("utf-8"))

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        result = await backup.apply_retention(daily=2, weekly=0, monthly=0)

        # 3 daily backups, keep 2 → delete 1
        assert result["kept"] == 2
        assert result["deleted"] == 1

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_apply_retention_empty(self, mock_exec):
        mock_exec.return_value = _make_process(stdout=b"")

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        result = await backup.apply_retention()
        assert result["kept"] == 0
        assert result["deleted"] == 0


class TestHealth:
    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_health_ok(self, mock_exec):
        mock_exec.return_value = _make_process(stdout=b"some listing")

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        assert await backup.health() is True

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_health_failure(self, mock_exec):
        mock_exec.return_value = _make_process(
            returncode=1, stderr=b"connection refused"
        )

        backup = OffsiteBackup(remote_path="user@nas:/backups", method="rsync")
        assert await backup.health() is False


class TestSMBBackup:
    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_smb_sync(self, mock_exec, backup_dir):
        mock_exec.return_value = _make_process(stdout=b"putting file ok")

        backup = OffsiteBackup(
            remote_path="/backups",
            method="smb",
            smb_share="//nas/share",
            smb_user="user",
        )
        result = await backup.sync(local_backup_dir=backup_dir)
        assert result["files_synced"] == 2
        assert result["bytes_transferred"] > 0

    async def test_smb_no_share_raises(self):
        backup = OffsiteBackup(
            remote_path="/backups",
            method="smb",
            smb_share=None,
        )
        with pytest.raises(OffsiteBackupError, match="smb_share is required"):
            await backup.sync(local_backup_dir=".")

    @patch("cortex.backup.offsite.asyncio.create_subprocess_exec")
    async def test_smb_health(self, mock_exec):
        mock_exec.return_value = _make_process(stdout=b"listing ok")

        backup = OffsiteBackup(
            remote_path="/backups",
            method="smb",
            smb_share="//nas/share",
        )
        assert await backup.health() is True
