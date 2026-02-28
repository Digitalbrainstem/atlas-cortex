"""Tests for WebDAV connector — mock HTTP responses for PROPFIND/GET."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.knowledge.webdav import WebDAVConnector, WebDAVError

SAMPLE_PROPFIND_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/user/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>user</d:displayname>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/user/notes.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>notes.txt</d:displayname>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getlastmodified>Thu, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>text/plain</d:getcontenttype>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/user/readme.md</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>readme.md</d:displayname>
        <d:getcontentlength>100</d:getcontentlength>
        <d:getlastmodified>Thu, 02 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>text/markdown</d:getcontenttype>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/user/image.png</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>image.png</d:displayname>
        <d:getcontentlength>50000</d:getcontentlength>
        <d:getlastmodified>Thu, 01 Jan 2025 12:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>image/png</d:getcontenttype>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


@pytest.fixture
def db_conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "webdav_cache"
    d.mkdir()
    return str(d)


def _mock_response(status_code=207, text="", content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content
    return resp


class TestWebDAVListFiles:
    async def test_list_files_parses_propfind(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_PROPFIND_XML)
        )
        connector._client = mock_client

        files = await connector.list_files("/")
        # Should have 3 files (skips the collection)
        assert len(files) == 3
        names = {f["name"] for f in files}
        assert "notes.txt" in names
        assert "readme.md" in names
        assert "image.png" in names

    async def test_list_files_returns_metadata(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(207, SAMPLE_PROPFIND_XML)
        )
        connector._client = mock_client

        files = await connector.list_files("/")
        notes = next(f for f in files if f["name"] == "notes.txt")
        assert notes["size"] == 42
        assert notes["content_type"] == "text/plain"
        assert notes["modified"] is not None

    async def test_list_files_error_on_failure(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            return_value=_mock_response(403, "Forbidden")
        )
        connector._client = mock_client

        with pytest.raises(WebDAVError, match="PROPFIND failed"):
            await connector.list_files("/")


class TestWebDAVDownload:
    async def test_download_file(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(
            return_value=_mock_response(200, content=b"Hello, world!")
        )
        connector._client = mock_client

        data = await connector.download_file("/notes.txt")
        assert data == b"Hello, world!"

    async def test_download_file_error(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.get = AsyncMock(return_value=_mock_response(404))
        connector._client = mock_client

        with pytest.raises(WebDAVError, match="failed with status 404"):
            await connector.download_file("/missing.txt")


class TestWebDAVSync:
    async def test_sync_indexes_new_files(self, db_conn, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )

        # Mock list_files and download_file
        connector.list_files = AsyncMock(return_value=[
            {
                "name": "notes.txt",
                "path": "/remote/notes.txt",
                "size": 42,
                "modified": "2025-01-01T12:00:00+00:00",
                "content_type": "text/plain",
            },
        ])
        connector.download_file = AsyncMock(return_value=b"These are my notes about Python.")

        result = await connector.sync(db_conn, owner_id="user1")
        assert result["files_checked"] == 1
        assert result["files_new"] == 1
        assert result["files_updated"] == 0

        # Verify indexed
        row = db_conn.execute(
            "SELECT * FROM knowledge_docs WHERE source = 'webdav'"
        ).fetchone()
        assert row is not None

    async def test_sync_skips_unsupported_extensions(self, db_conn, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        connector.list_files = AsyncMock(return_value=[
            {
                "name": "photo.png",
                "path": "/remote/photo.png",
                "size": 5000,
                "modified": "2025-01-01T12:00:00+00:00",
                "content_type": "image/png",
            },
        ])
        connector.download_file = AsyncMock()

        result = await connector.sync(db_conn, owner_id="user1")
        assert result["files_checked"] == 0
        connector.download_file.assert_not_called()


class TestWebDAVHealth:
    async def test_health_ok(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        connector.list_files = AsyncMock(return_value=[])

        assert await connector.health() is True

    async def test_health_failure(self, cache_dir):
        connector = WebDAVConnector(
            url="https://cloud.example.com",
            username="user",
            password="pass",
            local_cache_dir=cache_dir,
        )
        connector.list_files = AsyncMock(side_effect=WebDAVError("down"))

        assert await connector.health() is False
