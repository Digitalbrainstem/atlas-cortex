"""Tests for voice backup commands."""

import sqlite3
import pytest

from cortex.backup.voice_commands import match_backup_intent, handle_backup_command
from cortex.db import init_db, set_db_path


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    c = sqlite3.connect(str(path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


class TestMatchIntent:
    def test_backup_now(self):
        assert match_backup_intent("backup now") == "create"

    def test_back_up_please(self):
        assert match_backup_intent("can you back up now please") == "create"

    def test_backup_immediately(self):
        assert match_backup_intent("run a backup immediately") == "create"

    def test_last_backup(self):
        assert match_backup_intent("when was the last backup") == "last"

    def test_latest_backup(self):
        assert match_backup_intent("what time was the latest backup") == "last"

    def test_list_backups(self):
        assert match_backup_intent("list all backups") == "list"

    def test_show_backups(self):
        assert match_backup_intent("show me the backups") == "list"

    def test_how_many_backups(self):
        assert match_backup_intent("how many backups do I have") == "list"

    def test_no_match(self):
        assert match_backup_intent("turn off the lights") is None

    def test_no_match_unrelated(self):
        assert match_backup_intent("what time is it") is None


class TestHandleBackupLast:
    @pytest.mark.asyncio
    async def test_no_backups(self, conn):
        r = await handle_backup_command("last", conn=conn)
        assert "No backups" in r

    @pytest.mark.asyncio
    async def test_with_backup(self, conn):
        conn.execute(
            "INSERT INTO backup_log (archive_path, backup_type, size_bytes, success) "
            "VALUES (?, ?, ?, ?)",
            ("/data/backups/test.tar.gz", "manual", 1024, True),
        )
        conn.commit()
        r = await handle_backup_command("last", conn=conn)
        assert "test.tar.gz" in r
        assert "successful" in r

    @pytest.mark.asyncio
    async def test_no_conn(self):
        r = await handle_backup_command("last", conn=None)
        assert "can't check" in r


class TestHandleBackupList:
    @pytest.mark.asyncio
    async def test_empty(self, conn):
        r = await handle_backup_command("list", conn=conn)
        assert "No backups" in r

    @pytest.mark.asyncio
    async def test_with_entries(self, conn):
        for i in range(3):
            conn.execute(
                "INSERT INTO backup_log (archive_path, backup_type, size_bytes, success) "
                "VALUES (?, ?, ?, ?)",
                (f"/data/backups/backup_{i}.tar.gz", "daily", 1024 * 1024, True),
            )
        conn.commit()
        r = await handle_backup_command("list", conn=conn)
        assert "Last 3" in r
        assert "backup_0.tar.gz" in r

    @pytest.mark.asyncio
    async def test_unknown_intent(self):
        r = await handle_backup_command("unknown")
        assert "not sure" in r
