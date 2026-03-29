"""Tests for Memory Palace knowledge injection system."""
from __future__ import annotations

import sqlite3

import pytest

from cortex.memory.palace import (
    MemoryPalace,
    KnowledgeBank,
    PalaceRecall,
    _ensure_tables,
    _fts_sanitize,
)


@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


@pytest.fixture
def palace(db):
    """MemoryPalace instance with in-memory DB."""
    return MemoryPalace(db_conn=db, mode="ollama")


# ── FTS Sanitization ────────────────────────────────────────────


def test_fts_sanitize_basic():
    assert _fts_sanitize("hello world") == "hello OR world"


def test_fts_sanitize_special_chars():
    result = _fts_sanitize("what is NeoCardiol's dosing?")
    assert "NeoCardiol" in result
    assert "dosing" in result
    assert "'" not in result
    assert "?" not in result


def test_fts_sanitize_short_tokens_dropped():
    result = _fts_sanitize("a I am ok")
    assert "am" in result
    assert "ok" in result


def test_fts_sanitize_empty():
    assert _fts_sanitize("") == ""
    assert _fts_sanitize("? !") == ""


# ── Document Indexing ────────────────────────────────────────────


async def test_index_document(palace, db):
    bank_id = await palace.index_document(
        title="Test Doc",
        text="NeoCardiol is a heart failure medication with 73% efficacy.",
        tags=["medical", "cardiology"],
    )
    assert bank_id.startswith("palace_")

    row = db.execute(
        "SELECT * FROM kv_cache_banks WHERE bank_id = ?", (bank_id,)
    ).fetchone()
    assert row is not None
    assert row["title"] == "Test Doc"
    assert "73% efficacy" in row["text"]


async def test_index_duplicate_replaces(palace, db):
    text = "Same content twice"
    id1 = await palace.index_document(title="First", text=text)
    id2 = await palace.index_document(title="Second", text=text)
    assert id1 == id2  # Same content hash

    row = db.execute(
        "SELECT title FROM kv_cache_banks WHERE bank_id = ?", (id1,)
    ).fetchone()
    assert row["title"] == "Second"


async def test_index_different_content(palace, db):
    id1 = await palace.index_document(title="Doc A", text="Alpha content")
    id2 = await palace.index_document(title="Doc B", text="Beta content")
    assert id1 != id2


# ── Knowledge Retrieval ──────────────────────────────────────────


async def test_recall_basic(palace, db):
    await palace.index_document(
        title="Heart Med",
        text="NeoCardiol treats heart failure with 73% efficacy rate.",
    )

    results = await palace.recall("heart failure treatment")
    assert len(results) >= 1
    assert "NeoCardiol" in results[0].text


async def test_recall_no_results(palace):
    results = await palace.recall("quantum computing")
    assert results == []


async def test_recall_empty_query(palace):
    results = await palace.recall("")
    assert results == []


async def test_recall_multiple_docs(palace):
    await palace.index_document(title="Heart Med", text="NeoCardiol treats heart failure.")
    await palace.index_document(title="Brain Med", text="NeuroCalm treats anxiety disorders.")
    await palace.index_document(title="Lung Med", text="PulmoFlex treats asthma.")

    results = await palace.recall("heart failure")
    assert len(results) >= 1
    assert any("NeoCardiol" in r.text for r in results)


async def test_recall_with_tags(palace):
    await palace.index_document(
        title="Heart Med", text="NeoCardiol heart med.", tags=["cardiology"],
    )
    await palace.index_document(
        title="Brain Med", text="NeuroCalm brain med.", tags=["neurology"],
    )

    results = await palace.recall("med", tags=["cardiology"])
    # Tag filter narrows results
    assert all("cardiology" in str(r.tags) for r in results)


# ── Context Formatting ───────────────────────────────────────────


async def test_format_context_empty(palace):
    ctx = await palace.format_context("anything")
    assert ctx == ""


async def test_format_context_with_docs(palace):
    await palace.index_document(title="Med Guide", text="Important medical info here.")

    ctx = await palace.format_context("medical info")
    assert "[Med Guide]" in ctx
    assert "Important medical info" in ctx


# ── Bank Management ──────────────────────────────────────────────


async def test_list_banks_empty(palace):
    banks = await palace.list_banks()
    assert banks == []


async def test_list_banks_after_index(palace):
    await palace.index_document(title="Doc 1", text="First document")
    await palace.index_document(title="Doc 2", text="Second document")
    banks = await palace.list_banks()
    assert len(banks) == 2


async def test_delete_bank(palace):
    bank_id = await palace.index_document(title="ToDelete", text="Remove me")
    assert await palace.delete_bank(bank_id)
    banks = await palace.list_banks()
    assert len(banks) == 0


async def test_delete_nonexistent(palace):
    assert not await palace.delete_bank("fake_id")


# ── Stats ────────────────────────────────────────────────────────


async def test_stats(palace):
    stats = await palace.get_stats()
    assert stats["mode"] == "ollama"
    assert stats["banks"] == 0

    await palace.index_document(title="Test", text="Some text")
    stats = await palace.get_stats()
    assert stats["banks"] == 1


async def test_stats_no_db():
    p = MemoryPalace(mode="ollama")
    stats = await p.get_stats()
    assert stats["mode"] == "ollama"
    assert stats["banks"] == 0


# ── Mode Detection ───────────────────────────────────────────────


def test_default_mode():
    p = MemoryPalace(mode="ollama")
    assert p.mode == "ollama"


def test_transformers_mode():
    p = MemoryPalace(mode="transformers")
    assert p.mode == "transformers"


# ── Table Creation ───────────────────────────────────────────────


def test_ensure_tables_idempotent(db):
    _ensure_tables(db)
    _ensure_tables(db)
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND (name LIKE 'kv_%' OR name LIKE 'palace_%')"
    ).fetchall()
    assert len(tables) >= 2


# ── Singleton ────────────────────────────────────────────────────


def test_singleton():
    from cortex.memory.palace import get_palace, set_palace

    assert get_palace() is None
    p = MemoryPalace(mode="ollama")
    set_palace(p)
    assert get_palace() is p
    set_palace(None)  # type: ignore[arg-type]


# ── KnowledgeBank / PalaceRecall dataclasses ─────────────────────


def test_knowledge_bank_defaults():
    kb = KnowledgeBank(bank_id="x", title="t", source_hash="h", text="txt")
    assert kb.tags == []
    assert kb.token_count == 0
    assert kb.cache_path is None


def test_palace_recall_defaults():
    pr = PalaceRecall(bank_id="x", title="t", text="txt", score=0.5)
    assert pr.tags == []
    assert pr.cache_available is False
