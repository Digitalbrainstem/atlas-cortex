"""Database initialisation for Atlas Cortex.

Creates (or migrates) the SQLite database with the full schema defined in
docs/data-model.md.  The database path is taken from the ``CORTEX_DATA_DIR``
environment variable (default: ``./data``).

Usage::

    from cortex.db import get_db, init_db
    init_db()                  # idempotent — safe to call multiple times
    conn = get_db()            # returns a thread-safe connection
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_DB_PATH: Path | None = None
_LOCAL = threading.local()


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        data_dir = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = data_dir / "cortex.db"
    return _DB_PATH


def set_db_path(path: str | Path) -> None:
    """Override the database path (useful for tests)."""
    global _DB_PATH, _LOCAL
    _DB_PATH = Path(path)
    _LOCAL = threading.local()


def get_db() -> sqlite3.Connection:
    """Return a per-thread SQLite connection (WAL mode, FK enabled)."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _LOCAL.conn = conn
    return conn


def init_db(path: str | Path | None = None) -> None:
    """Create all tables (idempotent — safe to run multiple times)."""
    if path:
        set_db_path(path)
    conn = get_db()
    _create_schema(conn)
    conn.commit()


_SCHEMA_SQL = """
-- ───────── Devices & Patterns ─────────

CREATE TABLE IF NOT EXISTS ha_devices (
    entity_id      TEXT PRIMARY KEY,
    friendly_name  TEXT NOT NULL,
    domain         TEXT NOT NULL,
    area_id        TEXT,
    state          TEXT,
    discovered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_devices_domain ON ha_devices(domain);
CREATE INDEX IF NOT EXISTS idx_devices_area   ON ha_devices(area_id);

CREATE TABLE IF NOT EXISTS device_aliases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  TEXT NOT NULL,
    alias      TEXT NOT NULL,
    source     TEXT DEFAULT 'nightly',
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON device_aliases(entity_id);
CREATE INDEX IF NOT EXISTS idx_aliases_alias  ON device_aliases(alias);

CREATE TABLE IF NOT EXISTS device_capabilities (
    entity_id  TEXT NOT NULL,
    capability TEXT NOT NULL,
    PRIMARY KEY (entity_id, capability),
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS command_patterns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern              TEXT NOT NULL,
    intent               TEXT NOT NULL,
    entity_domain        TEXT,
    entity_match_group   INTEGER,
    value_match_group    INTEGER,
    response_template    TEXT,
    source               TEXT NOT NULL DEFAULT 'seed',
    confidence           REAL DEFAULT 1.0,
    hit_count            INTEGER DEFAULT 0,
    last_hit             TIMESTAMP,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patterns_domain ON command_patterns(entity_domain);
CREATE INDEX IF NOT EXISTS idx_patterns_source ON command_patterns(source);

-- ───────── Interactions ─────────

CREATE TABLE IF NOT EXISTS interactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT,
    speaker_id       TEXT,
    message          TEXT NOT NULL,
    matched_layer    TEXT NOT NULL,
    intent           TEXT,
    sentiment        TEXT,
    sentiment_score  REAL,
    response         TEXT,
    response_time_ms INTEGER,
    llm_model        TEXT,
    llm_tool_calls   TEXT,
    filler_used      TEXT,
    confidence_score REAL,
    pattern_id       INTEGER,
    resolved_area    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pattern_id) REFERENCES command_patterns(id)
);
CREATE INDEX IF NOT EXISTS idx_interactions_layer   ON interactions(matched_layer);
CREATE INDEX IF NOT EXISTS idx_interactions_user    ON interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at);
CREATE INDEX IF NOT EXISTS idx_interactions_fallthrough ON interactions(matched_layer, created_at)
    WHERE matched_layer = 'llm';

CREATE TABLE IF NOT EXISTS interaction_entities (
    interaction_id INTEGER NOT NULL,
    entity_id      TEXT NOT NULL,
    PRIMARY KEY (interaction_id, entity_id),
    FOREIGN KEY (interaction_id) REFERENCES interactions(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id)
);
CREATE INDEX IF NOT EXISTS idx_ie_entity ON interaction_entities(entity_id);

-- ───────── Voice & Spatial ─────────

CREATE TABLE IF NOT EXISTS speaker_profiles (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT,
    display_name         TEXT NOT NULL,
    embedding            BLOB NOT NULL,
    enrolled_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sample_count         INTEGER DEFAULT 1,
    last_verified        TIMESTAMP,
    confidence_threshold REAL DEFAULT 0.85
);
CREATE INDEX IF NOT EXISTS idx_speakers_user ON speaker_profiles(user_id);

CREATE TABLE IF NOT EXISTS satellite_rooms (
    satellite_id TEXT PRIMARY KEY,
    area_id      TEXT NOT NULL,
    area_name    TEXT NOT NULL,
    floor        TEXT,
    mic_x        REAL,
    mic_y        REAL,
    mic_z        REAL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS presence_sensors (
    entity_id               TEXT PRIMARY KEY,
    area_id                 TEXT NOT NULL,
    sensor_type             TEXT NOT NULL,
    priority                INTEGER DEFAULT 1,
    indicates_presence_when TEXT DEFAULT 'on',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_presence_area ON presence_sensors(area_id);

CREATE TABLE IF NOT EXISTS room_context_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id    INTEGER,
    resolved_area     TEXT,
    confidence        REAL,
    satellite_id      TEXT,
    satellite_area    TEXT,
    presence_signals  TEXT,
    speaker_id        TEXT,
    resolution_method TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id)
);
CREATE INDEX IF NOT EXISTS idx_room_context_interaction ON room_context_log(interaction_id);

-- ───────── User & Personality ─────────

CREATE TABLE IF NOT EXISTS emotional_profiles (
    user_id              TEXT PRIMARY KEY,
    display_name         TEXT,
    rapport_score        REAL DEFAULT 0.5,
    preferred_tone       TEXT DEFAULT 'neutral',
    communication_style  TEXT,
    humor_style          TEXT,
    relationship_notes   TEXT,
    interaction_count    INTEGER DEFAULT 0,
    positive_count       INTEGER DEFAULT 0,
    negative_count       INTEGER DEFAULT 0,
    last_interaction     TIMESTAMP,
    last_evolved_at      TIMESTAMP,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS filler_phrases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    sentiment  TEXT NOT NULL,
    phrase     TEXT NOT NULL,
    source     TEXT DEFAULT 'default',
    use_count  INTEGER DEFAULT 0,
    last_used  TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fillers_user_sentiment ON filler_phrases(user_id, sentiment);
CREATE INDEX IF NOT EXISTS idx_fillers_last_used      ON filler_phrases(last_used);

CREATE TABLE IF NOT EXISTS user_topics (
    user_id        TEXT NOT NULL,
    topic          TEXT NOT NULL,
    mention_count  INTEGER DEFAULT 1,
    last_mentioned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, topic),
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_activity_hours (
    user_id           TEXT NOT NULL,
    hour              INTEGER NOT NULL,
    interaction_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, hour),
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id             TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    age                 INTEGER,
    age_group           TEXT DEFAULT 'unknown',
    age_confidence      REAL DEFAULT 0.0,
    vocabulary_level    TEXT DEFAULT 'moderate',
    preferred_tone      TEXT DEFAULT 'neutral',
    communication_style TEXT DEFAULT 'moderate',
    humor_style         TEXT,
    is_parent           BOOLEAN DEFAULT FALSE,
    parent_user_id      TEXT,
    onboarding_complete BOOLEAN DEFAULT FALSE,
    profile_version     INTEGER DEFAULT 1,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_user_id) REFERENCES user_profiles(user_id)
);
CREATE INDEX IF NOT EXISTS idx_profiles_age_group ON user_profiles(age_group);

CREATE TABLE IF NOT EXISTS parental_controls (
    child_user_id        TEXT PRIMARY KEY,
    parent_user_id       TEXT NOT NULL,
    content_filter_level TEXT DEFAULT 'strict',
    allowed_hours_start  TEXT DEFAULT '07:00',
    allowed_hours_end    TEXT DEFAULT '21:00',
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (child_user_id)  REFERENCES user_profiles(user_id),
    FOREIGN KEY (parent_user_id) REFERENCES user_profiles(user_id)
);

CREATE TABLE IF NOT EXISTS parental_allowed_devices (
    child_user_id TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    PRIMARY KEY (child_user_id, entity_id),
    FOREIGN KEY (child_user_id) REFERENCES parental_controls(child_user_id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id)     REFERENCES ha_devices(entity_id)
);

CREATE TABLE IF NOT EXISTS parental_restricted_actions (
    child_user_id TEXT NOT NULL,
    action        TEXT NOT NULL,
    PRIMARY KEY (child_user_id, action),
    FOREIGN KEY (child_user_id) REFERENCES parental_controls(child_user_id) ON DELETE CASCADE
);

-- ───────── Memory ─────────

CREATE TABLE IF NOT EXISTS memory_metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    operation  TEXT,
    latency_ms REAL,
    hit_count  INTEGER,
    user_id    TEXT,
    success    BOOLEAN DEFAULT TRUE,
    notes      TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_op ON memory_metrics(operation, ts);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    doc_id, user_id, text, type, tags,
    tokenize='porter unicode61'
);

-- ───────── Knowledge ─────────

CREATE TABLE IF NOT EXISTS knowledge_docs (
    doc_id        TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    access_level  TEXT NOT NULL DEFAULT 'private',
    source        TEXT NOT NULL,
    source_path   TEXT,
    content_type  TEXT,
    title         TEXT,
    chunk_index   INTEGER DEFAULT 0,
    total_chunks  INTEGER DEFAULT 1,
    content_hash  TEXT,
    created_at    TIMESTAMP,
    modified_at   TIMESTAMP,
    indexed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner  ON knowledge_docs(owner_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_access ON knowledge_docs(access_level);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_docs(source);

CREATE TABLE IF NOT EXISTS knowledge_shared_with (
    doc_id  TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (doc_id, user_id),
    FOREIGN KEY (doc_id) REFERENCES knowledge_docs(doc_id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    doc_id, owner_id, access_level, source, title, text, tags,
    tokenize='porter unicode61'
);

-- ───────── Lists ─────────

CREATE TABLE IF NOT EXISTS list_registry (
    id             TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    backend        TEXT NOT NULL,
    backend_config TEXT NOT NULL,
    owner_id       TEXT NOT NULL,
    access_level   TEXT DEFAULT 'private',
    category       TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lists_owner    ON list_registry(owner_id);
CREATE INDEX IF NOT EXISTS idx_lists_category ON list_registry(category);

CREATE TABLE IF NOT EXISTS list_aliases (
    list_id TEXT NOT NULL,
    alias   TEXT NOT NULL,
    PRIMARY KEY (list_id, alias),
    FOREIGN KEY (list_id) REFERENCES list_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_list_aliases_alias ON list_aliases(alias);

CREATE TABLE IF NOT EXISTS list_permissions (
    list_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    can_add    BOOLEAN DEFAULT FALSE,
    can_view   BOOLEAN DEFAULT FALSE,
    can_remove BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (list_id, user_id),
    FOREIGN KEY (list_id) REFERENCES list_registry(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_list_perms_user ON list_permissions(user_id);

-- ───────── Learning ─────────

CREATE TABLE IF NOT EXISTS learned_patterns (
    interaction_id INTEGER PRIMARY KEY,
    pattern_id     INTEGER NOT NULL,
    processed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id),
    FOREIGN KEY (pattern_id)     REFERENCES command_patterns(id)
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    devices_discovered      INTEGER DEFAULT 0,
    devices_removed         INTEGER DEFAULT 0,
    patterns_generated      INTEGER DEFAULT 0,
    patterns_learned        INTEGER DEFAULT 0,
    patterns_pruned         INTEGER DEFAULT 0,
    profiles_evolved        INTEGER DEFAULT 0,
    mistakes_reviewed       INTEGER DEFAULT 0,
    intercept_rate          REAL,
    total_interactions_today INTEGER,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS mistake_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id     INTEGER,
    user_id            TEXT,
    claim_text         TEXT NOT NULL,
    correction_text    TEXT,
    detection_method   TEXT NOT NULL,
    mistake_category   TEXT,
    confidence_at_time REAL,
    root_cause         TEXT,
    resolved           BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id)
);
CREATE INDEX IF NOT EXISTS idx_mistakes_category   ON mistake_log(mistake_category);
CREATE INDEX IF NOT EXISTS idx_mistakes_unresolved ON mistake_log(resolved) WHERE resolved = FALSE;

CREATE TABLE IF NOT EXISTS mistake_tags (
    mistake_id INTEGER NOT NULL,
    tag        TEXT NOT NULL,
    PRIMARY KEY (mistake_id, tag),
    FOREIGN KEY (mistake_id) REFERENCES mistake_log(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mistake_tags_tag ON mistake_tags(tag);

-- ───────── Backup ─────────

CREATE TABLE IF NOT EXISTS backup_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_path  TEXT NOT NULL,
    backup_type   TEXT NOT NULL,
    size_bytes    INTEGER,
    db_row_count  INTEGER,
    chroma_doc_count INTEGER,
    duration_ms   INTEGER,
    success       BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ───────── Hardware & Context ─────────

CREATE TABLE IF NOT EXISTS hardware_profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gpu_vendor  TEXT,
    gpu_name    TEXT,
    vram_mb     INTEGER,
    is_igpu     BOOLEAN DEFAULT FALSE,
    cpu_model   TEXT,
    cpu_cores   INTEGER,
    ram_mb      INTEGER,
    disk_free_gb REAL,
    os_name     TEXT,
    limits_json TEXT,
    is_current  BOOLEAN DEFAULT TRUE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hw_current ON hardware_profile(is_current)
    WHERE is_current = TRUE;

CREATE TABLE IF NOT EXISTS model_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role            TEXT NOT NULL UNIQUE,
    model_name      TEXT NOT NULL,
    context_default INTEGER,
    context_max     INTEGER,
    temperature     REAL DEFAULT 0.7,
    auto_selected   BOOLEAN DEFAULT TRUE,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_checkpoints (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id      TEXT NOT NULL,
    user_id              TEXT NOT NULL,
    checkpoint_number    INTEGER NOT NULL,
    summary              TEXT NOT NULL,
    summary_tokens       INTEGER,
    turn_range_start     INTEGER,
    turn_range_end       INTEGER,
    original_token_count INTEGER,
    topics               TEXT,
    decisions_made       TEXT,
    entities_mentioned   TEXT,
    unresolved_questions TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, checkpoint_number)
);
CREATE INDEX IF NOT EXISTS idx_ctx_ckpt_conv ON context_checkpoints(conversation_id);

CREATE TABLE IF NOT EXISTS context_metrics (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id        INTEGER,
    context_budget        INTEGER,
    system_tokens         INTEGER,
    memory_tokens         INTEGER,
    checkpoint_tokens     INTEGER,
    active_message_tokens INTEGER,
    generation_reserve    INTEGER,
    thinking_tokens_used  INTEGER,
    compaction_triggered  BOOLEAN DEFAULT FALSE,
    checkpoint_created    BOOLEAN DEFAULT FALSE,
    gpu_vram_used_mb      INTEGER,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ───────── Discovery & Plugins ─────────

CREATE TABLE IF NOT EXISTS discovered_services (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    service_type     TEXT NOT NULL,
    name             TEXT,
    url              TEXT NOT NULL,
    discovery_method TEXT,
    is_configured    BOOLEAN DEFAULT FALSE,
    is_active        BOOLEAN DEFAULT FALSE,
    last_health_check TIMESTAMP,
    health_status    TEXT DEFAULT 'unknown',
    discovered_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(service_type, url)
);

CREATE TABLE IF NOT EXISTS service_config (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES discovered_services(id),
    config_key   TEXT NOT NULL,
    config_value TEXT NOT NULL,
    is_sensitive BOOLEAN DEFAULT FALSE,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(service_id, config_key)
);

CREATE TABLE IF NOT EXISTS plugin_registry (
    id               TEXT PRIMARY KEY,
    service_id       INTEGER REFERENCES discovered_services(id),
    plugin_type      TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    is_active        BOOLEAN DEFAULT TRUE,
    pattern_count    INTEGER DEFAULT 0,
    last_health_check TIMESTAMP,
    health_status    TEXT DEFAULT 'unknown',
    activated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _create_schema(conn: sqlite3.Connection) -> None:
    """Execute all CREATE TABLE / INDEX statements in one transaction."""
    conn.executescript(_SCHEMA_SQL)
