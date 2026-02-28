"""WebDAV/Nextcloud connector — sync files for knowledge indexing (Phase I5.2)."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cortex.integrations.knowledge.index import KnowledgeIndex
from cortex.integrations.knowledge.processor import DocumentProcessor

logger = logging.getLogger(__name__)

# WebDAV XML namespace
DAV_NS = "DAV:"


def _tag(ns: str, local: str) -> str:
    """Build a Clark-notation tag: {namespace}local."""
    return f"{{{ns}}}{local}"


class WebDAVError(Exception):
    """Base error for WebDAV operations."""


class WebDAVConnector:
    """Sync files from a WebDAV server for knowledge indexing."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".xml", ".html", ".htm"}

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        remote_path: str = "/",
        local_cache_dir: str = "./data/webdav_cache",
    ) -> None:
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.remote_path = remote_path
        self.local_cache_dir = Path(local_cache_dir)
        self.local_cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                auth=(self.username, self.password),
                timeout=30.0,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def list_files(self, path: str = "/") -> list[dict]:
        """List files at the given path.

        Returns a list of dicts with keys:
        ``name``, ``path``, ``size``, ``modified``, ``content_type``.
        """
        full_url = f"{self.url}{path}"
        client = await self._get_client()

        propfind_body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop>"
            "<d:displayname/>"
            "<d:getcontentlength/>"
            "<d:getlastmodified/>"
            "<d:getcontenttype/>"
            "<d:resourcetype/>"
            "</d:prop>"
            "</d:propfind>"
        )

        try:
            response = await client.request(
                "PROPFIND",
                full_url,
                content=propfind_body.encode("utf-8"),
                headers={
                    "Content-Type": "application/xml",
                    "Depth": "1",
                },
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise WebDAVError(f"Cannot reach WebDAV at {full_url}: {exc}") from exc

        if response.status_code not in (200, 207):
            raise WebDAVError(
                f"PROPFIND failed with status {response.status_code}: {response.text[:200]}"
            )

        return self._parse_propfind(response.text, path)

    async def download_file(self, remote_path: str) -> bytes:
        """Download a file's content."""
        full_url = f"{self.url}{remote_path}"
        client = await self._get_client()

        try:
            response = await client.get(full_url)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise WebDAVError(f"Cannot download {full_url}: {exc}") from exc

        if response.status_code != 200:
            raise WebDAVError(
                f"GET {remote_path} failed with status {response.status_code}"
            )

        return response.content

    async def sync(
        self, conn: sqlite3.Connection, owner_id: str = "system"
    ) -> dict:
        """Sync changed files into the knowledge index.

        Compares remote modified dates with indexed_at timestamps.
        Only re-indexes files that changed.

        Returns: ``{files_checked, files_updated, files_new, files_deleted}``
        """
        stats = {"files_checked": 0, "files_updated": 0, "files_new": 0, "files_deleted": 0}

        try:
            remote_files = await self.list_files(self.remote_path)
        except WebDAVError as exc:
            logger.error("WebDAV sync failed to list files: %s", exc)
            return stats

        index = KnowledgeIndex(conn)
        processor = DocumentProcessor()

        # Track remote paths to detect deletions
        remote_paths: set[str] = set()

        for file_info in remote_files:
            ext = Path(file_info["name"]).suffix.lower()
            if ext not in self.SUPPORTED_EXTENSIONS:
                continue

            stats["files_checked"] += 1
            remote_paths.add(file_info["path"])

            # Check if already indexed and up-to-date
            existing = conn.execute(
                "SELECT indexed_at FROM knowledge_docs WHERE source = 'webdav' "
                "AND source_path = ? AND chunk_index = 0",
                (file_info["path"],),
            ).fetchone()

            if existing and file_info.get("modified"):
                try:
                    indexed_at = datetime.fromisoformat(
                        existing["indexed_at"].replace("Z", "+00:00")
                    )
                    remote_mod = datetime.fromisoformat(
                        file_info["modified"].replace("Z", "+00:00")
                    )
                    if remote_mod <= indexed_at:
                        continue
                except (ValueError, TypeError):
                    pass

            # Download and index
            try:
                content = await self.download_file(file_info["path"])
            except WebDAVError as exc:
                logger.warning("Failed to download %s: %s", file_info["path"], exc)
                continue

            # Cache locally
            cache_path = self.local_cache_dir / file_info["name"]
            cache_path.write_bytes(content)

            try:
                chunks, metadata = processor.process_file(cache_path, owner_id=owner_id)
            except (ValueError, Exception) as exc:
                logger.warning("Failed to process %s: %s", file_info["name"], exc)
                continue

            metadata["source"] = "webdav"
            metadata["source_path"] = file_info["path"]

            # Remove old version if updating
            if existing:
                doc_prefix = f"webdav_{file_info['path']}"
                for row in conn.execute(
                    "SELECT doc_id FROM knowledge_docs WHERE source = 'webdav' AND source_path = ?",
                    (file_info["path"],),
                ).fetchall():
                    index.remove_document(row["doc_id"])
                stats["files_updated"] += 1
            else:
                stats["files_new"] += 1

            # Force a new content hash so dedup doesn't skip
            metadata["content_hash"] = hashlib.sha256(content).hexdigest()
            index.add_document(chunks, metadata)

        # Detect deletions
        existing_paths = conn.execute(
            "SELECT DISTINCT source_path FROM knowledge_docs WHERE source = 'webdav'"
        ).fetchall()
        for row in existing_paths:
            if row["source_path"] not in remote_paths:
                for doc_row in conn.execute(
                    "SELECT doc_id FROM knowledge_docs WHERE source = 'webdav' AND source_path = ?",
                    (row["source_path"],),
                ).fetchall():
                    index.remove_document(doc_row["doc_id"])
                stats["files_deleted"] += 1

        return stats

    async def health(self) -> bool:
        """Test connectivity to the WebDAV server."""
        try:
            await self.list_files(self.remote_path)
            return True
        except (WebDAVError, Exception):
            return False

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _parse_propfind(self, xml_text: str, base_path: str) -> list[dict]:
        """Parse a PROPFIND multistatus XML response into a list of file dicts."""
        results: list[dict] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse PROPFIND XML: %s", exc)
            return results

        for response_el in root.findall(_tag(DAV_NS, "response")):
            href_el = response_el.find(_tag(DAV_NS, "href"))
            if href_el is None or href_el.text is None:
                continue

            href = href_el.text

            # Skip the collection itself
            propstat = response_el.find(_tag(DAV_NS, "propstat"))
            if propstat is None:
                continue

            prop = propstat.find(_tag(DAV_NS, "prop"))
            if prop is None:
                continue

            # Skip directories (resource type = collection)
            resource_type = prop.find(_tag(DAV_NS, "resourcetype"))
            if resource_type is not None and resource_type.find(_tag(DAV_NS, "collection")) is not None:
                continue

            name_el = prop.find(_tag(DAV_NS, "displayname"))
            size_el = prop.find(_tag(DAV_NS, "getcontentlength"))
            modified_el = prop.find(_tag(DAV_NS, "getlastmodified"))
            ctype_el = prop.find(_tag(DAV_NS, "getcontenttype"))

            name = name_el.text if name_el is not None and name_el.text else href.rsplit("/", 1)[-1]
            size = int(size_el.text) if size_el is not None and size_el.text else 0
            modified = modified_el.text if modified_el is not None else None
            content_type = ctype_el.text if ctype_el is not None else "application/octet-stream"

            # Normalise modified to ISO format if it's an HTTP-date
            if modified:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(modified)
                    modified = dt.isoformat()
                except (ValueError, TypeError):
                    pass

            results.append(
                {
                    "name": name,
                    "path": href,
                    "size": size,
                    "modified": modified,
                    "content_type": content_type,
                }
            )

        return results
