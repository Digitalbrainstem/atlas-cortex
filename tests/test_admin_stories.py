"""Tests for stories admin API endpoints."""

from __future__ import annotations

import sqlite3

import pytest

from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import init_db, set_db_path


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def client(db_path):
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cortex.admin_api import router
    from cortex.auth import seed_admin as _seed

    test_app = FastAPI()
    test_app.include_router(router)

    def get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _seed(conn)
        return conn

    with patch("cortex.admin.helpers._db", get_test_db):
        yield TestClient(test_app)


@pytest.fixture()
def auth_header(db):
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


# ── Helpers ───────────────────────────────────────────────────────

def _insert_story(db_path, title="Test Story", genre="adventure", age="child", interactive=0):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO stories (title, genre, target_age_group, total_chapters, "
        "is_interactive, parent_approved, created_at) "
        "VALUES (?, ?, ?, 5, ?, 0, datetime('now'))",
        (title, genre, age, interactive),
    )
    conn.commit()
    story_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return story_id


def _insert_chapter(db_path, story_id, chapter_num=1, title="Ch 1"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO story_chapters (story_id, chapter_number, title, content, created_at) "
        "VALUES (?, ?, ?, 'Once upon a time...', datetime('now'))",
        (story_id, chapter_num, title),
    )
    conn.commit()
    conn.close()


def _insert_progress(db_path, story_id, user_id="user1", chapter=1, completed=0):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO story_progress (user_id, story_id, current_chapter, "
        "started_at, last_listened, completed) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'), ?)",
        (user_id, story_id, chapter, completed),
    )
    conn.commit()
    conn.close()


def _insert_character(db_path, story_id, name="Hero", voice_id="af_bella"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO story_characters (story_id, name, voice_id, voice_style, created_at) "
        "VALUES (?, ?, ?, 'warm', datetime('now'))",
        (story_id, name, voice_id),
    )
    conn.commit()
    char_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return char_id


# ── Tests ─────────────────────────────────────────────────────────

class TestStoryList:
    def test_list_empty(self, client, auth_header):
        resp = client.get("/admin/stories", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stories"] == []
        assert data["total"] == 0

    def test_list_stories(self, client, auth_header, db_path):
        _insert_story(db_path, "Adventure Time")
        _insert_story(db_path, "Mystery Manor", genre="mystery")
        resp = client.get("/admin/stories", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_filter_by_genre(self, client, auth_header, db_path):
        _insert_story(db_path, "Adventure Time", genre="adventure")
        _insert_story(db_path, "Mystery Manor", genre="mystery")
        resp = client.get("/admin/stories?genre=mystery", headers=auth_header)
        assert resp.status_code == 200
        stories = resp.json()["stories"]
        assert len(stories) == 1
        assert stories[0]["genre"] == "mystery"


class TestStoryDetail:
    def test_get_story(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Adventure Time")
        _insert_chapter(db_path, sid, 1, "The Beginning")
        _insert_character(db_path, sid, "Hero", "af_bella")
        resp = client.get(f"/admin/stories/{sid}", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Adventure Time"
        assert len(data["chapters"]) == 1
        assert len(data["characters"]) == 1

    def test_get_story_not_found(self, client, auth_header):
        resp = client.get("/admin/stories/9999", headers=auth_header)
        assert resp.status_code == 404


class TestStoryCreate:
    def test_create_story(self, client, auth_header):
        resp = client.post(
            "/admin/stories",
            json={"title": "New Story", "genre": "fantasy", "age_group": "teen",
                  "total_chapters": 10, "interactive": True},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data

    def test_created_story_in_list(self, client, auth_header):
        client.post(
            "/admin/stories",
            json={"title": "My Story"},
            headers=auth_header,
        )
        resp = client.get("/admin/stories", headers=auth_header)
        titles = [s["title"] for s in resp.json()["stories"]]
        assert "My Story" in titles


class TestStoryDelete:
    def test_delete_story(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "To Delete")
        resp = client.delete(f"/admin/stories/{sid}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify gone
        resp2 = client.get(f"/admin/stories/{sid}", headers=auth_header)
        assert resp2.status_code == 404

    def test_delete_not_found(self, client, auth_header):
        resp = client.delete("/admin/stories/9999", headers=auth_header)
        assert resp.status_code == 404

    def test_delete_cascades(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Cascade Test")
        _insert_chapter(db_path, sid)
        _insert_character(db_path, sid)
        _insert_progress(db_path, sid)
        resp = client.delete(f"/admin/stories/{sid}", headers=auth_header)
        assert resp.status_code == 200


class TestStoryApproval:
    def test_approve_story(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Pending Approval")
        resp = client.post(f"/admin/stories/{sid}/approve", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify approved
        resp2 = client.get(f"/admin/stories/{sid}", headers=auth_header)
        assert resp2.json()["parent_approved"] == 1

    def test_approve_not_found(self, client, auth_header):
        resp = client.post("/admin/stories/9999/approve", headers=auth_header)
        assert resp.status_code == 404


class TestStoryProgress:
    def test_progress_empty(self, client, auth_header):
        resp = client.get("/admin/stories/progress", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["progress"] == []

    def test_progress_with_data(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Progress Story")
        _insert_progress(db_path, sid, "user1", 3)
        _insert_progress(db_path, sid, "user2", 1, completed=1)
        resp = client.get("/admin/stories/progress", headers=auth_header)
        assert resp.status_code == 200
        progress = resp.json()["progress"]
        assert len(progress) == 2


class TestCharacterVoices:
    def test_list_characters(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Voice Story")
        _insert_character(db_path, sid, "Hero", "af_bella")
        _insert_character(db_path, sid, "Villain", "am_echo")
        resp = client.get(f"/admin/stories/characters/{sid}", headers=auth_header)
        assert resp.status_code == 200
        chars = resp.json()["characters"]
        assert len(chars) == 2

    def test_list_characters_not_found(self, client, auth_header):
        resp = client.get("/admin/stories/characters/9999", headers=auth_header)
        assert resp.status_code == 404

    def test_assign_character_voice(self, client, auth_header, db_path):
        sid = _insert_story(db_path, "Voice Story")
        resp = client.post(
            f"/admin/stories/characters/{sid}",
            json={"name": "Narrator", "voice_id": "af_nicole", "voice_style": "warm"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data

    def test_assign_voice_story_not_found(self, client, auth_header):
        resp = client.post(
            "/admin/stories/characters/9999",
            json={"name": "Nobody", "voice_id": "af_bella"},
            headers=auth_header,
        )
        assert resp.status_code == 404
