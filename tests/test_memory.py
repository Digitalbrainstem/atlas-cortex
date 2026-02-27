"""Tests for the memory system (HOT/COLD paths)."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path
from cortex.memory import (
    MemoryWriter,
    hot_query,
    redact_pii,
    _classify_memory,
    format_memory_context,
)


@pytest.fixture
def db_conn():
    """In-memory SQLite DB with full schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


class TestPIIRedaction:
    def test_email_redacted(self):
        assert "[EMAIL]" in redact_pii("Contact me at user@example.com please")

    def test_phone_redacted(self):
        assert "[PHONE]" in redact_pii("Call me at 555-867-5309")

    def test_ssn_redacted(self):
        assert "[SSN]" in redact_pii("My SSN is 123-45-6789")

    def test_no_pii_unchanged(self):
        text = "The sky is blue and grass is green."
        assert redact_pii(text) == text

    def test_multiple_pii_types(self):
        text = "Email: foo@bar.com, Phone: 555-123-4567"
        cleaned = redact_pii(text)
        assert "[EMAIL]" in cleaned
        assert "[PHONE]" in cleaned


class TestMemoryClassifier:
    def test_short_text_is_chit_chat(self):
        assert _classify_memory("ok") == "chit_chat"

    def test_preference(self):
        assert _classify_memory("I love dark mode themes") == "preference"

    def test_personal_fact(self):
        assert _classify_memory("my name is Derek and I work in IT") == "fact"

    def test_correction(self):
        assert _classify_memory("actually that's wrong, the correct answer is X") == "correction"


class TestHOTQuery:
    def test_empty_db_returns_empty_list(self, db_conn):
        results = hot_query("docker container", "alice", db_conn)
        assert results == []

    def test_finds_inserted_memory(self, db_conn):
        # Insert a memory manually
        db_conn.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text, type, tags) VALUES (?, ?, ?, ?, ?)",
            ("doc1", "alice", "I prefer dark mode in all my editors", "preference", "editors ui"),
        )
        db_conn.commit()

        results = hot_query("dark mode preference", "alice", db_conn)
        assert len(results) >= 1
        assert any("dark mode" in r.text for r in results)

    def test_user_scoped(self, db_conn):
        db_conn.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text, type, tags) VALUES (?, ?, ?, ?, ?)",
            ("doc2", "bob", "Bob likes cats", "preference", "pets"),
        )
        db_conn.commit()

        # Query as alice — should not find bob's memory
        results = hot_query("cats", "alice", db_conn)
        assert all(r.user_id == "alice" for r in results)


class TestFormatMemoryContext:
    def test_empty_hits_returns_empty_string(self):
        assert format_memory_context([]) == ""

    def test_formats_hits(self):
        from cortex.memory import MemoryHit
        hits = [
            MemoryHit("id1", "u1", "User prefers dark mode", 0.9),
            MemoryHit("id2", "u1", "User has a cat named Luna", 0.8),
        ]
        ctx = format_memory_context(hits)
        assert "dark mode" in ctx
        assert "Luna" in ctx

    def test_respects_max_chars(self):
        from cortex.memory import MemoryHit
        hits = [MemoryHit(f"id{i}", "u1", "x" * 200, 1.0) for i in range(10)]
        ctx = format_memory_context(hits, max_chars=300)
        assert len(ctx) <= 310  # a bit of slack for bullet formatting


class TestMemoryWriter:
    @pytest.mark.asyncio
    async def test_enqueue_and_write(self, db_conn):
        writer = MemoryWriter(db_conn)
        writer.start()
        await writer.enqueue("I love Python programming", "alice")
        # Give the worker time to process
        await asyncio.sleep(0.1)
        await writer.stop()

        results = hot_query("Python programming", "alice", db_conn)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_drops_chit_chat(self, db_conn):
        writer = MemoryWriter(db_conn)
        writer.start()
        await writer.enqueue("ok", "alice")  # too short = chit_chat
        await asyncio.sleep(0.1)
        await writer.stop()

        # Nothing written because it was classified as chit_chat
        results = hot_query("ok", "alice", db_conn)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_pii_redacted_in_write(self, db_conn):
        writer = MemoryWriter(db_conn)
        writer.start()
        await writer.enqueue("my email is secret@example.com for newsletters", "alice")
        await asyncio.sleep(0.1)
        await writer.stop()

        results = hot_query("email newsletters", "alice", db_conn)
        if results:
            # PII should be redacted in stored text
            assert "secret@example.com" not in results[0].text
            assert "[EMAIL]" in results[0].text
