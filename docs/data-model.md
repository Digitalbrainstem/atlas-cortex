# Atlas Cortex — Data Model (Refactored)

## Design Principles

1. **Normalize JSON columns** into proper relational tables when the data is queried, filtered, or joined
2. **Keep JSON only** for truly opaque blobs (config payloads, diagnostic data)
3. **SQLite strengths**: FTS5 for text search, JSON1 for the remaining blobs, WAL mode for concurrent reads
4. **ChromaDB** handles all vector embeddings (memory + knowledge) — SQLite handles everything else

## Database Files

| File | Purpose | Backup Priority |
|------|---------|----------------|
| `/data/cortex.db` | All structured data (tables below) | **Critical** |
| `/data/cortex_chroma/` | ChromaDB persistent storage (memory + knowledge vectors) | **Critical** |
| `/data/backups/` | Automated backup directory | N/A (is the backup) |

---

## Schema

### ha_devices

Device registry synced from Home Assistant nightly.

```sql
CREATE TABLE ha_devices (
    entity_id TEXT PRIMARY KEY,         -- "light.living_room"
    friendly_name TEXT NOT NULL,        -- "Living Room Lights"
    domain TEXT NOT NULL,               -- light, switch, sensor, climate, lock, cover, fan, media_player
    area_id TEXT,                       -- HA area: "kitchen", "office" (nullable)
    state TEXT,                         -- last known state: "on", "off", "72.5", "locked"
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_devices_domain ON ha_devices(domain);
CREATE INDEX idx_devices_area ON ha_devices(area_id);
```

### device_aliases

Normalized from `ha_devices.aliases` — queried on every Layer 2 command to match user speech to entities.

```sql
CREATE TABLE device_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,            -- FK → ha_devices
    alias TEXT NOT NULL,                -- "living room", "main lights", "front room"
    source TEXT DEFAULT 'nightly',      -- 'nightly' | 'user' | 'seed'
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id) ON DELETE CASCADE
);

CREATE INDEX idx_aliases_entity ON device_aliases(entity_id);
CREATE INDEX idx_aliases_alias ON device_aliases(alias);
```

### device_capabilities

Normalized from `ha_devices.capabilities` — queried to determine valid actions per device.

```sql
CREATE TABLE device_capabilities (
    entity_id TEXT NOT NULL,
    capability TEXT NOT NULL,           -- "turn_on", "turn_off", "set_brightness", "set_color", "set_temperature"
    PRIMARY KEY (entity_id, capability),
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id) ON DELETE CASCADE
);
```

### command_patterns

Regex patterns for matching natural language to HA actions. Grows automatically over time.

```sql
CREATE TABLE command_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,              -- regex: "turn (on|off) (?:the )?(.+)"
    intent TEXT NOT NULL,               -- toggle, get_state, set_brightness, set_temp, lock, unlock
    entity_domain TEXT,                 -- which HA domain this targets
    entity_match_group INTEGER,         -- which regex capture group contains the entity name
    value_match_group INTEGER,          -- which capture group contains the value (nullable)
    response_template TEXT,             -- "Done — {entity} is now {state}"
    source TEXT NOT NULL DEFAULT 'seed',
    confidence REAL DEFAULT 1.0,
    hit_count INTEGER DEFAULT 0,
    last_hit TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_patterns_domain ON command_patterns(entity_domain);
CREATE INDEX idx_patterns_source ON command_patterns(source);
```

### interactions

Core interaction log. JSON removed for entities_used (normalized to junction table).

```sql
CREATE TABLE interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,                       -- Open WebUI user ID
    speaker_id TEXT,                    -- voice embedding match ID (null for typed)
    message TEXT NOT NULL,
    matched_layer TEXT NOT NULL,        -- 'instant' | 'tool' | 'llm'
    intent TEXT,
    sentiment TEXT,                     -- 'positive' | 'negative' | 'neutral' | 'question' | 'command'
    sentiment_score REAL,              -- VADER compound score (-1.0 to 1.0)
    response TEXT,
    response_time_ms INTEGER,
    llm_model TEXT,                     -- which model was used (null for Layer 1/2)
    llm_tool_calls TEXT,               -- JSON blob (opaque diagnostic, rarely queried into)
    filler_used TEXT,
    confidence_score REAL,             -- response confidence at delivery time
    pattern_id INTEGER,
    resolved_area TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (pattern_id) REFERENCES command_patterns(id)
);

CREATE INDEX idx_interactions_layer ON interactions(matched_layer);
CREATE INDEX idx_interactions_user ON interactions(user_id);
CREATE INDEX idx_interactions_created ON interactions(created_at);
CREATE INDEX idx_interactions_fallthrough ON interactions(matched_layer, created_at)
    WHERE matched_layer = 'llm';
```

**Note:** `llm_tool_calls` stays as JSON — it's opaque diagnostic data stored whole and only scanned with LIKE for fallthrough analysis. Not worth normalizing.

### interaction_entities

Normalized from `interactions.entities_used` — enables fast queries for device command stats and fallthrough analysis.

```sql
CREATE TABLE interaction_entities (
    interaction_id INTEGER NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (interaction_id, entity_id),
    FOREIGN KEY (interaction_id) REFERENCES interactions(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id)
);

CREATE INDEX idx_ie_entity ON interaction_entities(entity_id);
```

```sql
-- HA command interception rate (replaces JSON LIKE query)
SELECT
    ROUND(100.0 * SUM(CASE WHEN i.matched_layer = 'tool' THEN 1 ELSE 0 END) /
    COUNT(DISTINCT i.id), 1) as intercept_pct
FROM interactions i
JOIN interaction_entities ie ON i.id = ie.interaction_id;

-- Most-controlled devices
SELECT entity_id, COUNT(*) as uses
FROM interaction_entities
GROUP BY entity_id ORDER BY uses DESC LIMIT 10;
```

### speaker_profiles

Voice embeddings for speaker identification.

```sql
CREATE TABLE speaker_profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    display_name TEXT NOT NULL,
    embedding BLOB NOT NULL,            -- 256-dim float32 vector (1024 bytes)
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sample_count INTEGER DEFAULT 1,
    last_verified TIMESTAMP,
    confidence_threshold REAL DEFAULT 0.85
);

CREATE INDEX idx_speakers_user ON speaker_profiles(user_id);
```

### emotional_profiles

Per-user personality state. JSON columns normalized out to `filler_phrases`, `user_topics`, `user_activity_hours`.

```sql
CREATE TABLE emotional_profiles (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    rapport_score REAL DEFAULT 0.5,
    preferred_tone TEXT DEFAULT 'neutral',
    communication_style TEXT,           -- free text: "concise, technical, uses humor"
    humor_style TEXT,                   -- "dry sarcasm", "puns", "none detected"
    relationship_notes TEXT,            -- LLM-generated summary (free text, stored whole)
    interaction_count INTEGER DEFAULT 0,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    last_interaction TIMESTAMP,
    last_evolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### filler_phrases

Normalized from `emotional_profiles.filler_pool` — queried on every response for rotation and recency.

```sql
CREATE TABLE filler_phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,              -- per-user personalized phrases
    sentiment TEXT NOT NULL,            -- 'greeting' | 'question' | 'frustrated' | 'excited' | 'late_night' | 'follow_up'
    phrase TEXT NOT NULL,               -- "Hey!", "Good question — ", "Hmm, "
    source TEXT DEFAULT 'default',      -- 'default' | 'nightly_evolved' | 'user_style_match'
    use_count INTEGER DEFAULT 0,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);

CREATE INDEX idx_fillers_user_sentiment ON filler_phrases(user_id, sentiment);
CREATE INDEX idx_fillers_last_used ON filler_phrases(last_used);
```

```sql
-- Select filler: random from pool, excluding last 2 used
SELECT phrase FROM filler_phrases
WHERE user_id = ? AND sentiment = ?
AND id NOT IN (
    SELECT id FROM filler_phrases
    WHERE user_id = ?
    ORDER BY last_used DESC LIMIT 2
)
ORDER BY RANDOM() LIMIT 1;
```

### user_topics

Normalized from `emotional_profiles.common_topics` — queried for topic-based model selection and confidence adjustment.

```sql
CREATE TABLE user_topics (
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,                -- "docker", "networking", "home automation"
    mention_count INTEGER DEFAULT 1,
    last_mentioned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, topic),
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);
```

### user_activity_hours

Normalized from `emotional_profiles.peak_hours` — queried for time-of-day filler selection.

```sql
CREATE TABLE user_activity_hours (
    user_id TEXT NOT NULL,
    hour INTEGER NOT NULL,             -- 0-23
    interaction_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, hour),
    FOREIGN KEY (user_id) REFERENCES emotional_profiles(user_id) ON DELETE CASCADE
);

-- Peak hours query
SELECT hour, interaction_count FROM user_activity_hours
WHERE user_id = ? ORDER BY interaction_count DESC LIMIT 3;
```

### user_profiles

Structured user data for age-awareness and profile management.

```sql
CREATE TABLE user_profiles (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    age INTEGER,
    age_group TEXT DEFAULT 'unknown',
    age_confidence REAL DEFAULT 0.0,
    vocabulary_level TEXT DEFAULT 'moderate',
    preferred_tone TEXT DEFAULT 'neutral',
    communication_style TEXT DEFAULT 'moderate',
    humor_style TEXT,
    is_parent BOOLEAN DEFAULT FALSE,
    parent_user_id TEXT,
    onboarding_complete BOOLEAN DEFAULT FALSE,
    profile_version INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_user_id) REFERENCES user_profiles(user_id)
);

CREATE INDEX idx_profiles_age_group ON user_profiles(age_group);
```

### parental_controls

```sql
CREATE TABLE parental_controls (
    child_user_id TEXT PRIMARY KEY,
    parent_user_id TEXT NOT NULL,
    content_filter_level TEXT DEFAULT 'strict',
    allowed_hours_start TEXT DEFAULT '07:00',
    allowed_hours_end TEXT DEFAULT '21:00',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (child_user_id) REFERENCES user_profiles(user_id),
    FOREIGN KEY (parent_user_id) REFERENCES user_profiles(user_id)
);
```

### parental_allowed_devices

Normalized from `parental_controls.allowed_devices` — which HA entities a child can control.

```sql
CREATE TABLE parental_allowed_devices (
    child_user_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (child_user_id, entity_id),
    FOREIGN KEY (child_user_id) REFERENCES parental_controls(child_user_id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES ha_devices(entity_id)
);
```

### parental_restricted_actions

Normalized from `parental_controls.require_parent_for` — actions that require parent confirmation.

```sql
CREATE TABLE parental_restricted_actions (
    child_user_id TEXT NOT NULL,
    action TEXT NOT NULL,               -- "lock", "climate", "alarm", "media"
    PRIMARY KEY (child_user_id, action),
    FOREIGN KEY (child_user_id) REFERENCES parental_controls(child_user_id) ON DELETE CASCADE
);
```

### satellite_rooms

Maps voice satellites to physical rooms. `mic_position` normalized to columns.

```sql
CREATE TABLE satellite_rooms (
    satellite_id TEXT PRIMARY KEY,
    area_id TEXT NOT NULL,
    area_name TEXT NOT NULL,
    floor TEXT,
    mic_x REAL,                        -- meters, for triangulation (nullable)
    mic_y REAL,
    mic_z REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### presence_sensors

```sql
CREATE TABLE presence_sensors (
    entity_id TEXT PRIMARY KEY,
    area_id TEXT NOT NULL,
    sensor_type TEXT NOT NULL,
    priority INTEGER DEFAULT 1,
    indicates_presence_when TEXT DEFAULT 'on',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_presence_area ON presence_sensors(area_id);
```

### room_context_log

Audit trail. `presence_signals` stays as JSON — it's diagnostic data, not queried into.

```sql
CREATE TABLE room_context_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id INTEGER,
    resolved_area TEXT,
    confidence REAL,
    satellite_id TEXT,
    satellite_area TEXT,
    presence_signals TEXT,              -- JSON (opaque diagnostic blob)
    speaker_id TEXT,
    resolution_method TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id)
);

CREATE INDEX idx_room_context_interaction ON room_context_log(interaction_id);
```

### learned_patterns

```sql
CREATE TABLE learned_patterns (
    interaction_id INTEGER PRIMARY KEY,
    pattern_id INTEGER NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id),
    FOREIGN KEY (pattern_id) REFERENCES command_patterns(id)
);
```

### evolution_log

```sql
CREATE TABLE evolution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    devices_discovered INTEGER DEFAULT 0,
    devices_removed INTEGER DEFAULT 0,
    patterns_generated INTEGER DEFAULT 0,
    patterns_learned INTEGER DEFAULT 0,
    patterns_pruned INTEGER DEFAULT 0,
    profiles_evolved INTEGER DEFAULT 0,
    mistakes_reviewed INTEGER DEFAULT 0,
    intercept_rate REAL,
    total_interactions_today INTEGER,
    notes TEXT
);
```

### mistake_log

`topic_tags` normalized to `mistake_tags` table.

```sql
CREATE TABLE mistake_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id INTEGER,
    user_id TEXT,
    claim_text TEXT NOT NULL,
    correction_text TEXT,
    detection_method TEXT NOT NULL,
    mistake_category TEXT,
    confidence_at_time REAL,
    root_cause TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (interaction_id) REFERENCES interactions(id)
);

CREATE INDEX idx_mistakes_category ON mistake_log(mistake_category);
CREATE INDEX idx_mistakes_unresolved ON mistake_log(resolved) WHERE resolved = FALSE;
```

### mistake_tags

Normalized from `mistake_log.topic_tags` — queried for per-topic confidence adjustment.

```sql
CREATE TABLE mistake_tags (
    mistake_id INTEGER NOT NULL,
    tag TEXT NOT NULL,                  -- "docker", "python", "networking"
    PRIMARY KEY (mistake_id, tag),
    FOREIGN KEY (mistake_id) REFERENCES mistake_log(id) ON DELETE CASCADE
);

CREATE INDEX idx_mistake_tags_tag ON mistake_tags(tag);
```

```sql
-- Per-topic mistake count (for confidence penalty)
SELECT tag, COUNT(*) as mistakes
FROM mistake_tags mt
JOIN mistake_log ml ON mt.mistake_id = ml.id
WHERE ml.created_at > datetime('now', '-30 days')
GROUP BY tag ORDER BY mistakes DESC;
```

### guardrail_events

Safety guardrail trigger log. See [safety-guardrails.md](safety-guardrails.md).

```sql
CREATE TABLE guardrail_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,
    direction TEXT NOT NULL,         -- 'input' | 'output'
    category TEXT NOT NULL,          -- 'self_harm' | 'explicit' | 'injection' | 'pii' | ...
    severity TEXT NOT NULL,          -- 'pass' | 'warn' | 'soft_block' | 'hard_block'
    trigger_text TEXT,               -- the text that triggered (redacted if PII)
    action_taken TEXT,               -- 'passed' | 'warned' | 'replaced' | 'blocked'
    content_tier TEXT,               -- tier at time of event
    FOREIGN KEY (user_id) REFERENCES user_profiles(user_id)
);

CREATE INDEX idx_guardrail_severity ON guardrail_events(severity);
CREATE INDEX idx_guardrail_category ON guardrail_events(category);
CREATE INDEX idx_guardrail_user ON guardrail_events(user_id);
```

### jailbreak_patterns

Learned regex patterns from blocked jailbreak attempts. See [safety-guardrails.md](safety-guardrails.md).

```sql
CREATE TABLE jailbreak_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    source_event_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_matched_at TIMESTAMP,
    match_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    reviewed BOOLEAN DEFAULT FALSE,
    notes TEXT,
    FOREIGN KEY (source_event_id) REFERENCES guardrail_events(id)
);

CREATE INDEX idx_jb_active ON jailbreak_patterns(active);
```

### jailbreak_exemplars

Semantic exemplar embeddings for detecting novel jailbreak phrasing.

```sql
CREATE TABLE jailbreak_exemplars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    embedding BLOB,
    source_event_id INTEGER,
    cluster_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_event_id) REFERENCES guardrail_events(id)
);

CREATE INDEX idx_jb_cluster ON jailbreak_exemplars(cluster_id);
```

### tts_voices

Voice registry for TTS providers. See [voice-engine.md](voice-engine.md).

```sql
CREATE TABLE tts_voices (
    id TEXT PRIMARY KEY,               -- 'orpheus_tara', 'orpheus_leo', 'piper_amy'
    provider TEXT NOT NULL,            -- 'orpheus' | 'parler' | 'piper'
    display_name TEXT NOT NULL,        -- 'Tara', 'Leo', 'Amy'
    gender TEXT,                       -- 'female' | 'male' | 'neutral'
    language TEXT DEFAULT 'en',
    accent TEXT,                       -- 'american' | 'british' | 'australian'
    style TEXT,                        -- 'warm' | 'professional' | 'casual' | 'energetic'
    supports_emotion BOOLEAN DEFAULT TRUE,
    sample_audio_path TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    metadata TEXT                      -- provider-specific config (JSON)
);
```

### list_registry

`backend_config` stays as JSON (opaque, varies per backend). Permissions normalized to `list_permissions`. Aliases normalized to `list_aliases`.

```sql
CREATE TABLE list_registry (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    backend TEXT NOT NULL,
    backend_config TEXT NOT NULL,       -- JSON (opaque config blob, varies per backend)
    owner_id TEXT NOT NULL,
    access_level TEXT DEFAULT 'private',
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP
);

CREATE INDEX idx_lists_owner ON list_registry(owner_id);
CREATE INDEX idx_lists_category ON list_registry(category);
```

### list_aliases

Normalized from `list_registry.aliases`.

```sql
CREATE TABLE list_aliases (
    list_id TEXT NOT NULL,
    alias TEXT NOT NULL,                -- "groceries", "shopping list", "the list"
    PRIMARY KEY (list_id, alias),
    FOREIGN KEY (list_id) REFERENCES list_registry(id) ON DELETE CASCADE
);

CREATE INDEX idx_list_aliases_alias ON list_aliases(alias);
```

### list_permissions

Normalized from `list_registry.shared_with/can_add/can_view/can_remove`.

```sql
CREATE TABLE list_permissions (
    list_id TEXT NOT NULL,
    user_id TEXT NOT NULL,              -- "*" = anyone (including unknown)
    can_add BOOLEAN DEFAULT FALSE,
    can_view BOOLEAN DEFAULT FALSE,
    can_remove BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (list_id, user_id),
    FOREIGN KEY (list_id) REFERENCES list_registry(id) ON DELETE CASCADE
);

CREATE INDEX idx_list_perms_user ON list_permissions(user_id);
```

```sql
-- Can this user add to this list?
SELECT 1 FROM list_permissions
WHERE list_id = ? AND (user_id = ? OR user_id = '*') AND can_add = TRUE
LIMIT 1;
```

### knowledge_docs

Metadata for indexed documents (vectors live in ChromaDB).

```sql
CREATE TABLE knowledge_docs (
    doc_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'private',
    source TEXT NOT NULL,
    source_path TEXT,
    content_type TEXT,
    title TEXT,
    chunk_index INTEGER DEFAULT 0,
    total_chunks INTEGER DEFAULT 1,
    content_hash TEXT,
    created_at TIMESTAMP,
    modified_at TIMESTAMP,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_knowledge_owner ON knowledge_docs(owner_id);
CREATE INDEX idx_knowledge_access ON knowledge_docs(access_level);
CREATE INDEX idx_knowledge_source ON knowledge_docs(source);
```

### knowledge_shared_with

Normalized sharing permissions for knowledge documents.

```sql
CREATE TABLE knowledge_shared_with (
    doc_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY (doc_id, user_id),
    FOREIGN KEY (doc_id) REFERENCES knowledge_docs(doc_id) ON DELETE CASCADE
);
```

### memory_metrics

```sql
CREATE TABLE memory_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    operation TEXT,
    latency_ms REAL,
    hit_count INTEGER,
    user_id TEXT,
    success BOOLEAN DEFAULT TRUE,
    notes TEXT
);

CREATE INDEX idx_metrics_op ON memory_metrics(operation, ts);
```

### FTS5 Virtual Tables

```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
    doc_id, user_id, text, type, tags,
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    doc_id, owner_id, access_level, source, title, text, tags,
    tokenize='porter unicode61'
);
```

---

## What Stays as JSON (and why)

| Column | Table | Reason |
|--------|-------|--------|
| `llm_tool_calls` | interactions | Opaque diagnostic blob, varies per interaction, only scanned with LIKE |
| `backend_config` | list_registry | Completely different structure per backend type |
| `presence_signals` | room_context_log | Diagnostic snapshot, never filtered/joined on |
| `relationship_notes` | emotional_profiles | Free-form LLM-generated text, stored/retrieved whole |
| `notes` | evolution_log | Free-form summary text |

---

### discovered_services

Services found during network scan (Part 2: Integration Layer).

```sql
CREATE TABLE discovered_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_type TEXT NOT NULL,         -- 'home_assistant' | 'nextcloud' | 'mqtt' | 'caldav' | 'imap' | 'nas_smb' | 'nas_nfs'
    name TEXT,                          -- friendly name: "Home Assistant", "Family Nextcloud"
    url TEXT NOT NULL,                  -- base URL or host:port
    discovery_method TEXT,              -- 'mdns' | 'manual' | 'probe'
    is_configured BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT FALSE,
    last_health_check TIMESTAMP,
    health_status TEXT DEFAULT 'unknown', -- 'healthy' | 'degraded' | 'unreachable' | 'unknown'
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### service_config

Credentials and settings for configured services.

```sql
CREATE TABLE service_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES discovered_services(id),
    config_key TEXT NOT NULL,           -- 'api_token' | 'username' | 'password' | 'mount_path' | 'port'
    config_value TEXT NOT NULL,         -- encrypted for sensitive values
    is_sensitive BOOLEAN DEFAULT FALSE, -- TRUE for tokens, passwords
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(service_id, config_key)
);
```

### plugin_registry

Active plugins and their state.

```sql
CREATE TABLE plugin_registry (
    id TEXT PRIMARY KEY,               -- 'ha_commands' | 'nextcloud_files' | 'caldav_calendar' | 'list_ha_todo'
    service_id INTEGER REFERENCES discovered_services(id),
    plugin_type TEXT NOT NULL,          -- 'action' | 'knowledge' | 'list_backend'
    display_name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    pattern_count INTEGER DEFAULT 0,    -- how many Layer 2 patterns registered
    last_health_check TIMESTAMP,
    health_status TEXT DEFAULT 'unknown',
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### backup_log

Tracks all backup operations. See [backup-restore.md](backup-restore.md).

```sql
CREATE TABLE backup_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_path TEXT NOT NULL,
    backup_type TEXT NOT NULL,          -- 'daily' | 'weekly' | 'monthly' | 'manual'
    size_bytes INTEGER,
    db_row_count INTEGER,
    chroma_doc_count INTEGER,
    duration_ms INTEGER,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### hardware_profile

Detected hardware capabilities. See [context-management.md](context-management.md).

```sql
CREATE TABLE hardware_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gpu_mode TEXT,               -- 'multi' | 'single' | 'cpu_only'
    gpu_count INTEGER DEFAULT 0,
    cpu_model TEXT,
    cpu_cores INTEGER,
    ram_mb INTEGER,
    disk_free_gb REAL,
    os_name TEXT,
    limits_json TEXT,
    assignments_json TEXT,       -- GPU role assignments
    is_current BOOLEAN DEFAULT TRUE
);

CREATE UNIQUE INDEX idx_hw_current ON hardware_profile(is_current) WHERE is_current = TRUE;
```

### hardware_gpu

One row per detected GPU. Links to `hardware_profile`.

```sql
CREATE TABLE hardware_gpu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES hardware_profile(id),
    gpu_index INTEGER,           -- 0, 1, 2...
    vendor TEXT,                  -- 'amd' | 'nvidia' | 'intel' | 'apple'
    name TEXT,
    vram_mb INTEGER,
    is_igpu BOOLEAN DEFAULT FALSE,
    compute_api TEXT,             -- 'rocm' | 'cuda' | 'oneapi' | 'metal'
    driver_version TEXT,
    assigned_role TEXT,           -- 'llm' | 'voice' | 'stt' | null
    env_json TEXT                 -- isolation env vars
);

CREATE INDEX idx_hw_gpu_profile ON hardware_gpu(profile_id);
```

### model_config

Active model assignments per role.

```sql
CREATE TABLE model_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL UNIQUE,          -- 'fast' | 'standard' | 'thinking' | 'embedding'
    model_name TEXT NOT NULL,
    context_default INTEGER,
    context_max INTEGER,
    temperature REAL DEFAULT 0.7,
    auto_selected BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### context_checkpoints

Compressed conversation history segments.

```sql
CREATE TABLE context_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    checkpoint_number INTEGER NOT NULL,
    summary TEXT NOT NULL,
    summary_tokens INTEGER,
    turn_range_start INTEGER,
    turn_range_end INTEGER,
    original_token_count INTEGER,
    topics TEXT,
    decisions_made TEXT,
    entities_mentioned TEXT,
    unresolved_questions TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(conversation_id, checkpoint_number)
);

CREATE INDEX idx_ctx_ckpt_conv ON context_checkpoints(conversation_id);
```

### context_metrics

Per-request context utilization tracking.

```sql
CREATE TABLE context_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id INTEGER REFERENCES interaction_log(id),
    context_budget INTEGER,
    system_tokens INTEGER,
    memory_tokens INTEGER,
    checkpoint_tokens INTEGER,
    active_message_tokens INTEGER,
    generation_reserve INTEGER,
    thinking_tokens_used INTEGER,
    compaction_triggered BOOLEAN DEFAULT FALSE,
    checkpoint_created BOOLEAN DEFAULT FALSE,
    gpu_vram_used_mb INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Entity-Relationship Diagram

```
                    device_aliases ──┐
                device_capabilities ──┤
                                      ▼
speaker_profiles ─── user_id ──▶ ha_devices
        │                            ▲
        │ user_id                    │ entity_id
        ▼                           │
emotional_profiles              interaction_entities
        │                           │
        ├── filler_phrases          │
        ├── user_topics             │
        └── user_activity_hours     │
                                    │
user_profiles ──────────────▶ interactions ◀── command_patterns
        │                         │    │              ▲
        └── parental_controls     │    │              │
              ├── _allowed_devices│    │      learned_patterns
              └── _restricted_acts│    │
                                  │    ├── room_context_log
                                  │    └── mistake_log
                                  │              └── mistake_tags
                                  │
satellite_rooms                   presence_sensors

list_registry ──┬── list_aliases
                └── list_permissions

knowledge_docs ──── knowledge_shared_with

evolution_log (standalone)
memory_metrics (standalone)
```

---

## Schema Statistics

| Category | Tables | Purpose |
|----------|--------|---------|
| Devices & Patterns | 4 | ha_devices, device_aliases, device_capabilities, command_patterns |
| Interactions | 3 | interactions, interaction_entities, learned_patterns |
| Voice & Spatial | 4 | speaker_profiles, satellite_rooms, presence_sensors, room_context_log |
| User & Personality | 7 | emotional_profiles, filler_phrases, user_topics, user_activity_hours, user_profiles, parental_controls (+2 child tables) |
| Lists | 3 | list_registry, list_aliases, list_permissions |
| Knowledge | 3 | knowledge_docs, knowledge_shared_with, knowledge_fts |
| Memory | 2 | memory_fts, memory_metrics |
| Learning | 3 | mistake_log, mistake_tags, evolution_log |
| Safety | 3 | guardrail_events, jailbreak_patterns, jailbreak_exemplars |
| Backup | 1 | backup_log |
| Context & Hardware | 5 | hardware_profile, hardware_gpu, model_config, context_checkpoints, context_metrics |
| Discovery & Plugins | 3 | discovered_services, service_config, plugin_registry |
| Voice | 1 | tts_voices |
| **Total** | **~42 tables** | (including FTS5 virtual tables and junction tables) |
