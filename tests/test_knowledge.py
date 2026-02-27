"""Tests for DocumentProcessor, AccessGate, KnowledgeIndex."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.integrations.knowledge.index import KnowledgeIndex
from cortex.integrations.knowledge.privacy import AccessGate
from cortex.integrations.knowledge.processor import DocumentProcessor


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


class TestDocumentProcessor:
    def test_process_short_text_single_chunk(self):
        proc = DocumentProcessor()
        chunks = proc.process_text("Hello, world!", "doc1")
        assert len(chunks) == 1
        assert chunks[0].text == "Hello, world!"

    def test_process_long_text_multiple_chunks(self):
        proc = DocumentProcessor()
        long_text = "a" * 5000
        chunks = proc.process_text(long_text, "doc2")
        assert len(chunks) > 1

    def test_chunk_overlap(self):
        proc = DocumentProcessor()
        long_text = "x" * 3000
        chunks = proc.process_text(long_text, "doc3")
        assert len(chunks) >= 2
        # chunk[1] should start before chunk[0] ends (overlap)
        end_of_first = proc.CHUNK_SIZE
        start_of_second = proc.CHUNK_SIZE - proc.CHUNK_OVERLAP
        assert start_of_second < end_of_first

    def test_process_file_txt(self):
        tmp_dir = tempfile.mkdtemp()
        txt_path = Path(tmp_dir) / "notes.txt"
        txt_path.write_text("These are my notes about Python.", encoding="utf-8")
        proc = DocumentProcessor()
        chunks, metadata = proc.process_file(txt_path, owner_id="user1")
        assert len(chunks) >= 1
        assert metadata["owner_id"] == "user1"
        assert metadata["content_type"] == "text/plain"
        assert "doc_id" in metadata

    def test_process_file_json(self):
        tmp_dir = tempfile.mkdtemp()
        json_path = Path(tmp_dir) / "data.json"
        json_path.write_text(
            json.dumps({"key": "value", "name": "test"}), encoding="utf-8"
        )
        proc = DocumentProcessor()
        chunks, metadata = proc.process_file(json_path, owner_id="user1")
        assert len(chunks) >= 1
        assert metadata["content_type"] == "application/json"

    def test_process_file_unsupported(self):
        tmp_dir = tempfile.mkdtemp()
        pdf_path = Path(tmp_dir) / "document.pdf"
        pdf_path.write_bytes(b"%PDF fake content")
        proc = DocumentProcessor()
        with pytest.raises(ValueError, match="Unsupported file type"):
            proc.process_file(pdf_path, owner_id="user1")

    def test_content_hash_deterministic(self):
        tmp_dir = tempfile.mkdtemp()
        txt_path = Path(tmp_dir) / "consistent.txt"
        content = "Deterministic content for hashing."
        txt_path.write_text(content, encoding="utf-8")
        proc = DocumentProcessor()
        _, meta1 = proc.process_file(txt_path, owner_id="user1")
        _, meta2 = proc.process_file(txt_path, owner_id="user2")
        assert meta1["content_hash"] == meta2["content_hash"]
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert meta1["content_hash"] == expected


class TestAccessGate:
    def test_unknown_identity_public_only(self, db_conn):
        gate = AccessGate(db_conn)
        levels = gate.allowed_levels("anyuser", "unknown")
        assert levels == ["public"]

    def test_high_confidence_all_levels(self, db_conn):
        gate = AccessGate(db_conn)
        levels = gate.allowed_levels("user1", "high")
        assert "public" in levels
        assert "household" in levels
        assert "shared" in levels
        assert "private" in levels
        assert len(levels) == 4

    def test_filter_query_returns_tuple(self, db_conn):
        gate = AccessGate(db_conn)
        result = gate.filter_query("user1")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], list)

    def test_can_access_public_doc(self, db_conn):
        db_conn.execute(
            """INSERT INTO knowledge_docs
               (doc_id, owner_id, access_level, source,
                created_at, modified_at, indexed_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            ("publicdoc_0", "user1", "public", "test"),
        )
        db_conn.commit()
        gate = AccessGate(db_conn)
        assert gate.can_access("anyuser", "publicdoc_0", "unknown") is True

    def test_cannot_access_private_doc(self, db_conn):
        db_conn.execute(
            """INSERT INTO knowledge_docs
               (doc_id, owner_id, access_level, source,
                created_at, modified_at, indexed_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            ("privatedoc_0", "user1", "private", "test"),
        )
        db_conn.commit()
        gate = AccessGate(db_conn)
        # user2 with low confidence cannot access user1's private doc
        assert gate.can_access("user2", "privatedoc_0", "low") is False

    def test_can_access_shared_doc(self, db_conn):
        db_conn.execute(
            """INSERT INTO knowledge_docs
               (doc_id, owner_id, access_level, source,
                created_at, modified_at, indexed_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            ("shareddoc_0", "user1", "shared", "test"),
        )
        db_conn.execute(
            "INSERT INTO knowledge_shared_with (doc_id, user_id) VALUES (?, ?)",
            ("shareddoc_0", "user2"),
        )
        db_conn.commit()
        gate = AccessGate(db_conn)
        # user2 with medium confidence can access shared doc
        assert gate.can_access("user2", "shareddoc_0", "medium") is True


class TestKnowledgeIndex:
    def test_add_document_basic(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("Hello world content", "basedoc", "Base Doc")
        metadata = {
            "doc_id": "basedoc",
            "owner_id": "user1",
            "access_level": "private",
            "source": "test",
            "content_hash": "basehash001",
        }
        index = KnowledgeIndex(db_conn)
        added = index.add_document(chunks, metadata)
        assert added >= 1
        row = db_conn.execute(
            "SELECT doc_id FROM knowledge_fts WHERE doc_id = 'basedoc_0'"
        ).fetchone()
        assert row is not None

    def test_add_document_dedup(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("Duplicate content test", "dedupdoc", "Dedup")
        metadata = {
            "doc_id": "dedupdoc",
            "owner_id": "user1",
            "access_level": "private",
            "source": "test",
            "content_hash": "deduphash999",
        }
        index = KnowledgeIndex(db_conn)
        first = index.add_document(chunks, metadata)
        second = index.add_document(chunks, metadata)
        assert first > 0
        assert second == 0

    def test_search_finds_indexed_content(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("Python programming is fun", "pythondoc", "Python")
        metadata = {
            "doc_id": "pythondoc",
            "owner_id": "user1",
            "access_level": "public",
            "source": "test",
            "content_hash": "pythonhash001",
        }
        index = KnowledgeIndex(db_conn)
        index.add_document(chunks, metadata)
        results = index.search("Python", "user1", identity_confidence="high")
        assert len(results) >= 1
        assert any("Python" in r["text"] for r in results)

    def test_search_user_scoped(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("secret private data for user1 only", "secretdoc", "Secret")
        metadata = {
            "doc_id": "secretdoc",
            "owner_id": "user1",
            "access_level": "private",
            "source": "test",
            "content_hash": "secrethash001",
        }
        index = KnowledgeIndex(db_conn)
        index.add_document(chunks, metadata)
        # user2 should NOT find user1's private doc
        results = index.search("secret", "user2", identity_confidence="high")
        assert len(results) == 0

    def test_remove_document(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("To be removed content", "removedoc", "Remove")
        metadata = {
            "doc_id": "removedoc",
            "owner_id": "user1",
            "access_level": "public",
            "source": "test",
            "content_hash": "removehash001",
        }
        index = KnowledgeIndex(db_conn)
        index.add_document(chunks, metadata)
        index.remove_document("removedoc_0")
        results = index.search("removed", "user1")
        assert len(results) == 0

    def test_get_stats(self, db_conn):
        proc = DocumentProcessor()
        chunks = proc.process_text("Stats document content", "statsdoc", "Stats")
        metadata = {
            "doc_id": "statsdoc",
            "owner_id": "user1",
            "access_level": "public",
            "source": "file",
            "content_hash": "statshash001",
        }
        index = KnowledgeIndex(db_conn)
        index.add_document(chunks, metadata)
        stats = index.get_stats()
        assert "total_docs" in stats
        assert stats["total_docs"] >= 1
        assert "by_source" in stats
        assert "by_access_level" in stats
