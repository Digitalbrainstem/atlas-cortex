"""Tests for CAG (Cache-Augmented Generation) knowledge injection system."""
from __future__ import annotations

import sqlite3

import pytest

from cortex.memory.cag import (
    CAGEngine,
    KnowledgeBank,
    CAGRecall,
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
def cag(db):
    """CAGEngine instance with in-memory DB."""
    return CAGEngine(db_conn=db, mode="ollama")


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


async def test_index_document(cag, db):
    bank_id = await cag.index_document(
        title="Test Doc",
        text="NeoCardiol is a heart failure medication with 73% efficacy.",
        tags=["medical", "cardiology"],
    )
    assert bank_id.startswith("cag_")

    row = db.execute(
        "SELECT * FROM kv_cache_banks WHERE bank_id = ?", (bank_id,)
    ).fetchone()
    assert row is not None
    assert row["title"] == "Test Doc"
    assert "73% efficacy" in row["text"]


async def test_index_duplicate_replaces(cag, db):
    text = "Same content twice"
    id1 = await cag.index_document(title="First", text=text)
    id2 = await cag.index_document(title="Second", text=text)
    assert id1 == id2  # Same content hash

    row = db.execute(
        "SELECT title FROM kv_cache_banks WHERE bank_id = ?", (id1,)
    ).fetchone()
    assert row["title"] == "Second"


async def test_index_different_content(cag, db):
    id1 = await cag.index_document(title="Doc A", text="Alpha content")
    id2 = await cag.index_document(title="Doc B", text="Beta content")
    assert id1 != id2


# ── Knowledge Retrieval ──────────────────────────────────────────


async def test_recall_basic(cag, db):
    await cag.index_document(
        title="Heart Med",
        text="NeoCardiol treats heart failure with 73% efficacy rate.",
    )

    results = await cag.recall("heart failure treatment")
    assert len(results) >= 1
    assert "NeoCardiol" in results[0].text


async def test_recall_no_results(cag):
    results = await cag.recall("quantum computing")
    assert results == []


async def test_recall_empty_query(cag):
    results = await cag.recall("")
    assert results == []


async def test_recall_multiple_docs(cag):
    await cag.index_document(title="Heart Med", text="NeoCardiol treats heart failure.")
    await cag.index_document(title="Brain Med", text="NeuroCalm treats anxiety disorders.")
    await cag.index_document(title="Lung Med", text="PulmoFlex treats asthma.")

    results = await cag.recall("heart failure")
    assert len(results) >= 1
    assert any("NeoCardiol" in r.text for r in results)


async def test_recall_with_tags(cag):
    await cag.index_document(
        title="Heart Med", text="NeoCardiol heart med.", tags=["cardiology"],
    )
    await cag.index_document(
        title="Brain Med", text="NeuroCalm brain med.", tags=["neurology"],
    )

    results = await cag.recall("med", tags=["cardiology"])
    # Tag filter narrows results
    assert all("cardiology" in str(r.tags) for r in results)


# ── Context Formatting ───────────────────────────────────────────


async def test_format_context_empty(cag):
    ctx = await cag.format_context("anything")
    assert ctx == ""


async def test_format_context_with_docs(cag):
    await cag.index_document(title="Med Guide", text="Important medical info here.")

    ctx = await cag.format_context("medical info")
    assert "[Med Guide]" in ctx
    assert "Important medical info" in ctx


# ── Bank Management ──────────────────────────────────────────────


async def test_list_banks_empty(cag):
    banks = await cag.list_banks()
    assert banks == []


async def test_list_banks_after_index(cag):
    await cag.index_document(title="Doc 1", text="First document")
    await cag.index_document(title="Doc 2", text="Second document")
    banks = await cag.list_banks()
    assert len(banks) == 2


async def test_delete_bank(cag):
    bank_id = await cag.index_document(title="ToDelete", text="Remove me")
    assert await cag.delete_bank(bank_id)
    banks = await cag.list_banks()
    assert len(banks) == 0


async def test_delete_nonexistent(cag):
    assert not await cag.delete_bank("fake_id")


# ── Stats ────────────────────────────────────────────────────────


async def test_stats(cag):
    stats = await cag.get_stats()
    assert stats["mode"] == "ollama"
    assert stats["banks"] == 0

    await cag.index_document(title="Test", text="Some text")
    stats = await cag.get_stats()
    assert stats["banks"] == 1


async def test_stats_no_db():
    p = CAGEngine(mode="ollama")
    stats = await p.get_stats()
    assert stats["mode"] == "ollama"
    assert stats["banks"] == 0


# ── Mode Detection ───────────────────────────────────────────────


def test_default_mode():
    p = CAGEngine(mode="ollama")
    assert p.mode == "ollama"


def test_transformers_mode():
    p = CAGEngine(mode="transformers")
    assert p.mode == "transformers"


# ── Table Creation ───────────────────────────────────────────────


def test_ensure_tables_idempotent(db):
    _ensure_tables(db)
    _ensure_tables(db)
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND (name LIKE 'kv_%' OR name LIKE 'cag_%')"
    ).fetchall()
    assert len(tables) >= 2


# ── Singleton ────────────────────────────────────────────────────


def test_singleton():
    from cortex.memory.cag import get_cag_engine, set_cag_engine

    assert get_cag_engine() is None
    p = CAGEngine(mode="ollama")
    set_cag_engine(p)
    assert get_cag_engine() is p
    set_cag_engine(None)  # type: ignore[arg-type]


# ── KnowledgeBank / CAGRecall dataclasses ─────────────────────


def test_knowledge_bank_defaults():
    kb = KnowledgeBank(bank_id="x", title="t", source_hash="h", text="txt")
    assert kb.tags == []
    assert kb.token_count == 0
    assert kb.cache_path is None


def test_cag_recall_defaults():
    pr = CAGRecall(bank_id="x", title="t", text="txt", score=0.5)
    assert pr.tags == []
    assert pr.cache_available is False
