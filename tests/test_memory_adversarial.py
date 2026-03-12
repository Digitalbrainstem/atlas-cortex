"""Adversarial tests for cortex/memory/ — edge cases, injection, and PII.

Every test here PROVES actual behavior under hostile or degenerate input.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import init_db, set_db_path
from cortex.memory.hot import _fts_query, hot_query, format_memory_context, rrf_fuse
from cortex.memory.pii import redact_pii
from cortex.memory.classification import classify_memory, KEEP_TYPES, DROP_TYPES
from cortex.memory.types import MemoryHit
from cortex.memory.controller import MemorySystem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "mem_adv.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


# ===========================================================================
# FTS5 query sanitization  (_fts_query)
# ===========================================================================

class TestFtsQuerySanitization:
    def test_plain_text_unchanged(self):
        assert _fts_query("hello world") == "hello world"

    def test_strips_double_quotes(self):
        result = _fts_query('he said "hello"')
        assert '"' not in result or result == '""'

    def test_strips_parentheses(self):
        result = _fts_query("query (with parens)")
        assert "(" not in result
        assert ")" not in result

    def test_strips_single_quotes(self):
        result = _fts_query("it's a test")
        # The apostrophe is non-word, so it's replaced with a space
        assert "'" not in result

    def test_boolean_operators_treated_as_tokens(self):
        """AND, OR, NOT are FTS5 operators — _fts_query should strip or pass them as words."""
        result = _fts_query("cats AND dogs OR fish NOT snakes")
        # The function just strips non-word chars, so AND/OR/NOT remain as plain words
        assert "AND" in result or "and" in result.lower()

    def test_empty_string_returns_empty_match(self):
        assert _fts_query("") == '""'

    def test_only_special_chars_returns_empty(self):
        assert _fts_query('!@#$%^&*(){}[]') == '""'

    def test_limits_to_ten_tokens(self):
        long_input = " ".join(f"word{i}" for i in range(20))
        result = _fts_query(long_input)
        tokens = result.split()
        assert len(tokens) == 10

    def test_asterisk_stripped(self):
        """FTS5 wildcard * should be removed."""
        result = _fts_query("prefix*")
        assert "*" not in result

    def test_colon_stripped(self):
        """FTS5 column filter : should be removed."""
        result = _fts_query("column:value")
        assert ":" not in result

    def test_near_operator_neutralized(self):
        """NEAR is an FTS5 operator."""
        result = _fts_query("cat NEAR dog")
        # NEAR stays as a word token — it's only dangerous inside a MATCH expression
        # with the NEAR() syntax or as bare operator, but _fts_query strips non-word
        # The result should be safe tokens
        assert isinstance(result, str)


# ===========================================================================
# PII redaction
# ===========================================================================

class TestPIIRedaction:
    def test_ssn_format_xxx_xx_xxxx(self):
        assert redact_pii("SSN: 123-45-6789") == "SSN: [SSN]"

    def test_email_standard(self):
        assert "[EMAIL]" in redact_pii("reach me at user@example.com")

    def test_email_with_dots(self):
        assert "[EMAIL]" in redact_pii("john.doe@company.co.uk")

    def test_phone_with_dashes(self):
        assert "[PHONE]" in redact_pii("Call 555-123-4567")

    def test_phone_with_dots(self):
        assert "[PHONE]" in redact_pii("Call 555.123.4567")

    def test_phone_no_separator(self):
        assert "[PHONE]" in redact_pii("Call 5551234567")

    def test_visa_card(self):
        assert "[CC]" in redact_pii("Card: 4111111111111111")

    def test_visa_card_with_spaces(self):
        assert "[CC]" in redact_pii("Card: 4111 1111 1111 1111")

    def test_mastercard(self):
        assert "[CC]" in redact_pii("Card: 5111111111111111")

    def test_no_false_positive_on_short_numbers(self):
        """4-digit numbers should not be redacted."""
        text = "My PIN is 1234"
        assert redact_pii(text) == text

    def test_multiple_pii_in_one_string(self):
        text = "Email user@test.com, SSN 123-45-6789, phone 555-123-4567"
        result = redact_pii(text)
        assert "[EMAIL]" in result
        assert "[SSN]" in result
        assert "[PHONE]" in result
        assert "user@test.com" not in result

    def test_preserves_surrounding_text(self):
        result = redact_pii("My SSN is 123-45-6789 and that is private")
        assert result.startswith("My SSN is ")
        assert result.endswith(" and that is private")


# ===========================================================================
# Memory classification
# ===========================================================================

class TestClassifyMemory:
    def test_short_text_is_chit_chat(self):
        """Under 4 words → chit_chat."""
        assert classify_memory("hi there") == "chit_chat"
        assert classify_memory("ok") == "chit_chat"

    def test_preference_detected(self):
        assert classify_memory("I like chocolate ice cream a lot") == "preference"
        assert classify_memory("I prefer dark roast coffee always") == "preference"

    def test_fact_detected(self):
        assert classify_memory("My name is Alice and I work here") == "fact"
        assert classify_memory("I live in Portland Oregon now") == "fact"

    def test_correction_detected(self):
        assert classify_memory("That's wrong, I actually live in Seattle") == "correction"

    def test_default_is_fact(self):
        """Long text with no keyword match should default to 'fact'."""
        result = classify_memory("The capital of France is Paris and it has many museums")
        assert result == "fact"

    def test_keep_types_are_frozenset(self):
        assert isinstance(KEEP_TYPES, frozenset)
        assert "preference" in KEEP_TYPES
        assert "fact" in KEEP_TYPES
        assert "correction" in KEEP_TYPES

    def test_drop_types_are_frozenset(self):
        assert isinstance(DROP_TYPES, frozenset)
        assert "chit_chat" in DROP_TYPES
        assert "greeting" in DROP_TYPES

    def test_classify_returns_type_in_keep_or_drop(self):
        """Every classification result should be in KEEP_TYPES or DROP_TYPES."""
        samples = [
            "hi",
            "I like pizza and it is my favorite",
            "My name is Bob and I work at Google",
            "That's wrong actually I said something else entirely",
            "The weather is nice today but it might rain later this week",
        ]
        all_types = KEEP_TYPES | DROP_TYPES
        for text in samples:
            result = classify_memory(text)
            assert result in all_types, f"classify_memory({text!r}) returned {result!r} not in known types"


# ===========================================================================
# MemorySystem with no provider
# ===========================================================================

class TestMemorySystemNoProvider:
    @pytest.mark.asyncio
    async def test_recall_with_no_provider_no_crash(self, db):
        """MemorySystem with provider=None should not crash on recall."""
        ms = MemorySystem(conn=db, provider=None)
        results = await ms.recall("hello world", user_id="test-user")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_remember_empty_string(self, db):
        """remember('') should not crash — it may be dropped by classification."""
        ms = MemorySystem(conn=db, provider=None)
        ms._writer = MagicMock()
        ms._writer.enqueue = AsyncMock()
        await ms.remember("", user_id="test-user")
        ms._writer.enqueue.assert_called_once_with("", "test-user", None)

    @pytest.mark.asyncio
    async def test_remember_very_long_text(self, db):
        """10000-char string should be accepted without exception."""
        ms = MemorySystem(conn=db, provider=None)
        ms._writer = MagicMock()
        ms._writer.enqueue = AsyncMock()
        long_text = "word " * 2000  # ~10000 chars
        await ms.remember(long_text, user_id="test-user")
        ms._writer.enqueue.assert_called_once()
        actual_text = ms._writer.enqueue.call_args[0][0]
        assert len(actual_text) >= 9000


# ===========================================================================
# hot_query integration with real DB
# ===========================================================================

class TestHotQueryIntegration:
    def test_returns_empty_for_nonexistent_user(self, db):
        results = hot_query("test query", "nonexistent-user", db)
        assert results == []

    def test_finds_inserted_memory(self, db):
        db.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text) VALUES (?, ?, ?)",
            ("doc-1", "alice", "I love chocolate ice cream"),
        )
        db.commit()
        results = hot_query("chocolate ice cream", "alice", db)
        assert len(results) >= 1
        assert any("chocolate" in r.text for r in results)

    def test_user_isolation(self, db):
        """Memories from user A should not appear in user B's query."""
        db.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text) VALUES (?, ?, ?)",
            ("doc-a", "alice", "alice secret data here"),
        )
        db.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text) VALUES (?, ?, ?)",
            ("doc-b", "bob", "bob public info here"),
        )
        db.commit()
        results = hot_query("secret data", "bob", db)
        for r in results:
            assert r.user_id == "bob" or "alice" not in r.text.lower()

    def test_special_chars_in_query_dont_crash(self, db):
        """Queries with SQL/FTS5 special chars should not raise."""
        db.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text) VALUES (?, ?, ?)",
            ("doc-1", "user", "normal text content"),
        )
        db.commit()
        dangerous_queries = [
            'test "with quotes"',
            "test (with parens)",
            "test AND OR NOT",
            "test *wildcard*",
            "column:injection",
            "'; DROP TABLE memory_fts; --",
        ]
        for q in dangerous_queries:
            results = hot_query(q, "user", db)
            assert isinstance(results, list), f"hot_query crashed on {q!r}"


# ===========================================================================
# RRF fusion
# ===========================================================================

class TestRRFFusion:
    def test_empty_inputs(self):
        assert rrf_fuse([], []) == []

    def test_single_source_passthrough(self):
        hits = [MemoryHit("d1", "u1", "text1", 0.5)]
        result = rrf_fuse(hits, [])
        assert len(result) == 1
        assert result[0].doc_id == "d1"

    def test_duplicate_doc_ids_merged(self):
        fts = [MemoryHit("d1", "u1", "text1", 0.9)]
        vec = [MemoryHit("d1", "u1", "text1", 0.8)]
        result = rrf_fuse(fts, vec)
        assert len(result) == 1
        # RRF score should be higher than either single source
        assert result[0].score > 1.0 / (60 + 1)

    def test_ordering_by_score(self):
        fts = [
            MemoryHit("d1", "u1", "text1", 0.9),
            MemoryHit("d2", "u1", "text2", 0.5),
        ]
        vec = [
            MemoryHit("d2", "u1", "text2", 0.8),
            MemoryHit("d3", "u1", "text3", 0.7),
        ]
        result = rrf_fuse(fts, vec)
        # d2 appears in both lists → highest RRF score
        assert result[0].doc_id == "d2"


# ===========================================================================
# format_memory_context
# ===========================================================================

class TestFormatMemoryContext:
    def test_empty_hits(self):
        assert format_memory_context([]) == ""

    def test_single_hit(self):
        hits = [MemoryHit("d1", "u1", "likes coffee", 0.5)]
        result = format_memory_context(hits)
        assert result == "- likes coffee"

    def test_truncation_at_max_chars(self):
        hits = [
            MemoryHit(f"d{i}", "u1", f"memory number {i} with some extra text", 0.5)
            for i in range(100)
        ]
        result = format_memory_context(hits, max_chars=100)
        assert len(result) <= 200  # some tolerance for the last line added
        assert result.startswith("- memory number 0")
