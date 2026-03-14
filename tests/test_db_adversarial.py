"""Adversarial tests for cortex/db.py — proving the schema and DB layer hold.

These tests verify schema completeness, idempotency, WAL mode, foreign-key
enforcement, migration helpers, and edge-cases that are easy to break during
schema evolution.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    """Fresh database per test."""
    path = tmp_path / "db_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def conn(db_path):
    """Raw connection with row_factory + FK enforcement."""
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _table_names(c: sqlite3.Connection) -> set[str]:
    """Return all user-created table names (excluding internal sqlite_ tables)."""
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _column_names(c: sqlite3.Connection, table: str) -> list[str]:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


# ── init_db idempotency ──────────────────────────────────────────


class TestInitDbIdempotency:
    def test_init_creates_tables(self, db_path):
        c = sqlite3.connect(str(db_path))
        tables = _table_names(c)
        assert len(tables) > 20, f"Expected 20+ tables, got {len(tables)}"
        c.close()

    def test_init_twice_no_crash(self, tmp_path):
        """Calling init_db twice on the same DB must not raise."""
        path = tmp_path / "double.db"
        set_db_path(path)
        init_db(path)
        init_db(path)  # second call — must be safe

    def test_init_twice_same_table_count(self, tmp_path):
        path = tmp_path / "count.db"
        set_db_path(path)
        init_db(path)
        c = sqlite3.connect(str(path))
        count1 = len(_table_names(c))
        c.close()
        init_db(path)
        c = sqlite3.connect(str(path))
        count2 = len(_table_names(c))
        c.close()
        assert count1 == count2, "init_db re-run changed table count"


# ── get_db properties ─────────────────────────────────────────────


class TestGetDb:
    def test_returns_connection(self, db_path):
        c = get_db()
        assert isinstance(c, sqlite3.Connection)

    def test_wal_mode(self, db_path):
        c = get_db()
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", f"Expected WAL mode, got {mode}"

    def test_foreign_keys_enabled(self, db_path):
        c = get_db()
        fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1, "Foreign keys should be ON"

    def test_row_factory_is_row(self, db_path):
        c = get_db()
        assert c.row_factory is sqlite3.Row

    def test_same_connection_same_thread(self, db_path):
        """get_db() must return the same object on repeated calls in one thread."""
        c1 = get_db()
        c2 = get_db()
        assert c1 is c2

    def test_different_connection_different_thread(self, db_path):
        """get_db() must return separate connections for separate threads."""
        results = {}

        def _worker(name):
            results[name] = id(get_db())

        t1 = threading.Thread(target=_worker, args=("a",))
        t2 = threading.Thread(target=_worker, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert results["a"] != results["b"], "Threads must get distinct connections"


# ── set_db_path ───────────────────────────────────────────────────


class TestSetDbPath:
    def test_changes_path(self, tmp_path):
        p1 = tmp_path / "a.db"
        p2 = tmp_path / "b.db"
        set_db_path(p1)
        init_db(p1)
        set_db_path(p2)
        init_db(p2)
        # Both files should exist
        assert p1.exists()
        assert p2.exists()

    def test_resets_thread_local(self, tmp_path):
        """After set_db_path, the next get_db() must open the NEW path."""
        p1 = tmp_path / "old.db"
        p2 = tmp_path / "new.db"
        set_db_path(p1)
        init_db(p1)
        c1 = get_db()
        set_db_path(p2)
        init_db(p2)
        c2 = get_db()
        assert c1 is not c2, "set_db_path must invalidate cached connection"


# ── Table existence ───────────────────────────────────────────────

# The schema is large; test every table the codebase relies on.

EXPECTED_TABLES = [
    "ha_devices",
    "device_aliases",
    "device_capabilities",
    "command_patterns",
    "interactions",
    "interaction_entities",
    "speaker_profiles",
    "satellite_rooms",
    "presence_sensors",
    "room_context_log",
    "emotional_profiles",
    "filler_phrases",
    "user_topics",
    "user_activity_hours",
    "user_profiles",
    "parental_controls",
    "parental_allowed_devices",
    "parental_restricted_actions",
    "memory_metrics",
    "memory_fts",
    "knowledge_docs",
    "knowledge_shared_with",
    "knowledge_fts",
    "list_registry",
    "list_aliases",
    "list_permissions",
    "list_items",
    "learned_patterns",
    "evolution_log",
    "mistake_log",
    "mistake_tags",
    "backup_log",
    "hardware_profile",
    "model_config",
    "context_checkpoints",
    "context_metrics",
    "discovered_services",
    "service_config",
    "plugin_registry",
    "guardrail_events",
    "jailbreak_patterns",
    "jailbreak_exemplars",
    "file_checksums",
    "audit_log",
    "tts_voices",
    "system_settings",
    "hardware_gpu",
    "admin_users",
    "satellites",
    "satellite_audio_sessions",
    "avatar_skins",
    "avatar_assignments",
]


class TestTableExistence:
    @pytest.mark.parametrize("table", EXPECTED_TABLES)
    def test_table_exists(self, conn, table):
        tables = _table_names(conn)
        assert table in tables, f"Table '{table}' missing from schema"

    def test_no_unexpected_tables(self, conn):
        """Warn if unknown tables appear (not a hard failure — just visibility)."""
        actual = _table_names(conn)
        expected = set(EXPECTED_TABLES)
        extra = actual - expected
        # FTS shadow tables (e.g. memory_fts_content) are expected
        extra = {t for t in extra if not any(t.startswith(f"{fts}_") for fts in ("memory_fts", "knowledge_fts"))}
        # Allow but document extra tables
        if extra:
            pytest.skip(f"Extra tables found (not necessarily wrong): {extra}")

    def test_notification_log_table(self, conn):
        """notification_log must exist — used by cortex.notifications.channels."""
        tables = _table_names(conn)
        assert "notification_log" in tables


# ── Column spot-checks ────────────────────────────────────────────


class TestColumnSpotChecks:
    """Verify key tables have the columns that code depends on."""

    def test_admin_users_columns(self, conn):
        cols = _column_names(conn, "admin_users")
        for expected in ("id", "username", "password_hash", "is_active", "last_login"):
            assert expected in cols, f"admin_users missing column '{expected}'"

    def test_interactions_columns(self, conn):
        cols = _column_names(conn, "interactions")
        for expected in (
            "id", "user_id", "speaker_id", "message", "matched_layer",
            "intent", "sentiment", "response", "response_time_ms",
            "llm_model", "confidence_score", "created_at",
        ):
            assert expected in cols, f"interactions missing column '{expected}'"

    def test_user_profiles_columns(self, conn):
        cols = _column_names(conn, "user_profiles")
        for expected in (
            "user_id", "display_name", "age_group", "preferred_tone",
            "preferred_voice", "is_parent", "onboarding_complete",
        ):
            assert expected in cols, f"user_profiles missing column '{expected}'"

    def test_command_patterns_columns(self, conn):
        cols = _column_names(conn, "command_patterns")
        for expected in ("id", "pattern", "intent", "entity_domain", "confidence", "hit_count"):
            assert expected in cols, f"command_patterns missing column '{expected}'"

    def test_guardrail_events_columns(self, conn):
        cols = _column_names(conn, "guardrail_events")
        for expected in ("id", "user_id", "direction", "category", "severity", "action_taken", "content_tier"):
            assert expected in cols, f"guardrail_events missing column '{expected}'"

    def test_satellites_columns(self, conn):
        cols = _column_names(conn, "satellites")
        for expected in (
            "id", "display_name", "hostname", "room", "status", "mode",
            "wake_word", "volume", "mic_gain", "vad_sensitivity",
            "tts_voice", "vad_enabled", "led_brightness", "is_active",
        ):
            assert expected in cols, f"satellites missing column '{expected}'"


# ── Foreign-key enforcement ───────────────────────────────────────


class TestForeignKeys:
    def test_fk_pragma_is_on(self, conn):
        val = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert val == 1

    def test_device_alias_requires_device(self, conn):
        """Inserting an alias for a non-existent device must fail."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO device_aliases (entity_id, alias) VALUES (?, ?)",
                ("fake.entity", "my alias"),
            )

    def test_cascade_delete_device_aliases(self, conn):
        """Deleting a device should cascade-delete its aliases."""
        conn.execute(
            "INSERT INTO ha_devices (entity_id, friendly_name, domain) VALUES (?, ?, ?)",
            ("light.test", "Test Light", "light"),
        )
        conn.execute(
            "INSERT INTO device_aliases (entity_id, alias) VALUES (?, ?)",
            ("light.test", "bedroom lamp"),
        )
        conn.commit()
        conn.execute("DELETE FROM ha_devices WHERE entity_id = 'light.test'")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM device_aliases WHERE entity_id = 'light.test'"
        ).fetchone()[0]
        assert count == 0, "Cascade delete should remove aliases"

    def test_interaction_entity_fk(self, conn):
        """interaction_entities references both interactions and ha_devices."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO interaction_entities (interaction_id, entity_id) VALUES (?, ?)",
                (99999, "nonexistent.device"),
            )

    def test_guardrail_direction_check(self, conn):
        """guardrail_events.direction has a CHECK constraint (input/output)."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO guardrail_events (direction, category, severity, action_taken) "
                "VALUES (?, ?, ?, ?)",
                ("sideways", "test", "low", "blocked"),
            )

    def test_satellite_mode_check(self, conn):
        """satellites.mode is constrained to 'dedicated' or 'shared'."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO satellites (id, display_name, mode) VALUES (?, ?, ?)",
                ("sat-bad", "Bad Sat", "invalid_mode"),
            )

    def test_satellite_status_check(self, conn):
        """satellites.status is constrained to a known set of values."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO satellites (id, display_name, status) VALUES (?, ?, ?)",
                ("sat-bad2", "Bad Sat 2", "exploding"),
            )


# ── _add_column_if_missing ────────────────────────────────────────


class TestAddColumnIfMissing:
    def test_adds_new_column(self, conn, db_path):
        """Calling init_db already ran the migrations. Add a truly new column."""
        from cortex.db import _add_column_if_missing

        _add_column_if_missing(conn, "ha_devices", "test_col_xyz", "TEXT")
        cols = _column_names(conn, "ha_devices")
        assert "test_col_xyz" in cols

    def test_existing_column_no_crash(self, conn, db_path):
        """Adding a column that already exists must silently succeed."""
        from cortex.db import _add_column_if_missing

        _add_column_if_missing(conn, "ha_devices", "friendly_name", "TEXT NOT NULL")
        # No exception means success

    def test_idempotent(self, conn, db_path):
        from cortex.db import _add_column_if_missing

        _add_column_if_missing(conn, "ha_devices", "new_col_abc", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "ha_devices", "new_col_abc", "INTEGER DEFAULT 0")
        cols = _column_names(conn, "ha_devices")
        assert cols.count("new_col_abc") == 1


# ── WAL concurrent reads ─────────────────────────────────────────


class TestWALConcurrency:
    def test_concurrent_reads(self, db_path):
        """WAL mode should allow multiple readers simultaneously."""
        errors = []

        def _reader():
            try:
                c = sqlite3.connect(str(db_path), check_same_thread=False)
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("SELECT COUNT(*) FROM admin_users").fetchone()
                c.close()
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"Concurrent reads failed: {errors}"

    def test_read_while_writing(self, db_path):
        """A reader should not block on a concurrent writer in WAL mode."""
        barrier = threading.Barrier(2, timeout=5)
        results = {"reader": None, "writer": None}

        def _writer():
            c = sqlite3.connect(str(db_path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                ("wal_test", "hash"),
            )
            barrier.wait()  # hold the write txn open while reader runs
            c.commit()
            c.close()
            results["writer"] = "ok"

        def _reader():
            barrier.wait()  # wait until writer has an open txn
            c = sqlite3.connect(str(db_path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            count = c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
            c.close()
            results["reader"] = count

        tw = threading.Thread(target=_writer)
        tr = threading.Thread(target=_reader)
        tw.start(); tr.start()
        tw.join(timeout=5); tr.join(timeout=5)
        assert results["writer"] == "ok"
        assert results["reader"] is not None, "Reader should complete even with open writer txn"


# ── Default seed data ─────────────────────────────────────────────


class TestDefaultSeeds:
    def test_avatar_skins_seeded(self, conn):
        """_create_schema seeds the default avatar skin."""
        row = conn.execute("SELECT id FROM avatar_skins WHERE id = 'default'").fetchone()
        assert row is not None, "Default avatar skin should be seeded by init_db"

    def test_nick_avatar_seeded(self, conn):
        row = conn.execute("SELECT id FROM avatar_skins WHERE id = 'nick'").fetchone()
        assert row is not None, "Nick avatar skin should be seeded by init_db"


# ── FTS virtual tables ────────────────────────────────────────────


class TestFTSTables:
    def test_memory_fts_exists(self, conn):
        tables = _table_names(conn)
        assert "memory_fts" in tables

    def test_knowledge_fts_exists(self, conn):
        tables = _table_names(conn)
        assert "knowledge_fts" in tables

    def test_memory_fts_insert_and_search(self, conn):
        """FTS table must accept inserts and support MATCH queries."""
        conn.execute(
            "INSERT INTO memory_fts (doc_id, user_id, text, type, tags) VALUES (?, ?, ?, ?, ?)",
            ("doc1", "user1", "the quick brown fox", "fact", "animals"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT doc_id FROM memory_fts WHERE memory_fts MATCH 'fox'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "doc1"

    def test_knowledge_fts_insert_and_search(self, conn):
        conn.execute(
            "INSERT INTO knowledge_fts (doc_id, owner_id, access_level, source, title, text, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("k1", "u1", "private", "manual", "Test", "quantum computing basics", "science"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT doc_id FROM knowledge_fts WHERE knowledge_fts MATCH 'quantum'"
        ).fetchall()
        assert len(rows) == 1


# ── Index existence ───────────────────────────────────────────────


class TestIndexExistence:
    """Spot-check that performance-critical indexes exist."""

    def _index_names(self, conn) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r[0] for r in rows}

    @pytest.mark.parametrize("index_name", [
        "idx_devices_domain",
        "idx_interactions_layer",
        "idx_interactions_created",
        "idx_aliases_entity",
        "idx_patterns_source",
        "idx_audit_type",
        "idx_satellites_status",
    ])
    def test_index_exists(self, conn, index_name):
        indexes = self._index_names(conn)
        assert index_name in indexes, f"Index '{index_name}' missing"
