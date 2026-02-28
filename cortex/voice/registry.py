"""Voice registry — tts_voices database schema and seed data (C11.4)."""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

CREATE_TTS_VOICES = """
CREATE TABLE IF NOT EXISTS tts_voices (
    id                  TEXT PRIMARY KEY,
    provider            TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    gender              TEXT,
    language            TEXT DEFAULT 'en',
    accent              TEXT,
    style               TEXT,
    supports_emotion    INTEGER DEFAULT 1,
    sample_audio_path   TEXT,
    is_default          INTEGER DEFAULT 0,
    metadata            TEXT
);
"""

# ---------------------------------------------------------------------------
# Seed data (Orpheus built-in voices + Piper fallback voices)
# ---------------------------------------------------------------------------

SEED_VOICES: list[dict] = [
    # Orpheus voices
    {"id": "orpheus_tara",  "provider": "orpheus", "display_name": "Tara",  "gender": "female", "style": "warm",         "supports_emotion": 1, "is_default": 1},
    {"id": "orpheus_leah",  "provider": "orpheus", "display_name": "Leah",  "gender": "female", "style": "energetic",    "supports_emotion": 1},
    {"id": "orpheus_jess",  "provider": "orpheus", "display_name": "Jess",  "gender": "female", "style": "casual",       "supports_emotion": 1},
    {"id": "orpheus_leo",   "provider": "orpheus", "display_name": "Leo",   "gender": "male",   "style": "professional", "supports_emotion": 1},
    {"id": "orpheus_dan",   "provider": "orpheus", "display_name": "Dan",   "gender": "male",   "style": "casual",       "supports_emotion": 1},
    {"id": "orpheus_mia",   "provider": "orpheus", "display_name": "Mia",   "gender": "female", "style": "gentle",       "supports_emotion": 1},
    {"id": "orpheus_zac",   "provider": "orpheus", "display_name": "Zac",   "gender": "male",   "style": "energetic",    "supports_emotion": 1},
    {"id": "orpheus_anna",  "provider": "orpheus", "display_name": "Anna",  "gender": "female", "style": "professional", "supports_emotion": 1},
    # Piper fallback voices
    {"id": "piper_amy",     "provider": "piper",   "display_name": "Amy",   "gender": "female", "style": "neutral",      "supports_emotion": 0},
    {"id": "piper_jenny",   "provider": "piper",   "display_name": "Jenny", "gender": "female", "style": "neutral",      "supports_emotion": 0},
    {"id": "piper_ryan",    "provider": "piper",   "display_name": "Ryan",  "gender": "male",   "style": "neutral",      "supports_emotion": 0},
]


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def init_voice_registry(conn: sqlite3.Connection) -> None:
    """Create the tts_voices table and insert seed rows if absent."""
    conn.execute(CREATE_TTS_VOICES)
    _seed_voices(conn)
    conn.commit()


def _seed_voices(conn: sqlite3.Connection) -> None:
    for v in SEED_VOICES:
        conn.execute(
            """
            INSERT OR IGNORE INTO tts_voices
                (id, provider, display_name, gender, language, style,
                 supports_emotion, is_default)
            VALUES
                (:id, :provider, :display_name, :gender, 'en', :style,
                 :supports_emotion, :is_default)
            """,
            {
                "id": v["id"],
                "provider": v["provider"],
                "display_name": v["display_name"],
                "gender": v.get("gender"),
                "style": v.get("style"),
                "supports_emotion": v.get("supports_emotion", 1),
                "is_default": v.get("is_default", 0),
            },
        )


def get_default_voice(conn: sqlite3.Connection, provider: str | None = None) -> dict | None:
    """Return the default voice row, optionally filtered by provider."""
    sql = "SELECT * FROM tts_voices WHERE is_default = 1"
    params: list = []
    if provider:
        sql += " AND provider = ?"
        params.append(provider)
    sql += " LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if row is None and provider:
        # Fallback: first voice for that provider
        row = conn.execute(
            "SELECT * FROM tts_voices WHERE provider = ? LIMIT 1", [provider]
        ).fetchone()
    return dict(row) if row else None


def list_voices(conn: sqlite3.Connection, provider: str | None = None) -> list[dict]:
    """Return all voices, optionally filtered by provider."""
    if provider:
        rows = conn.execute(
            "SELECT * FROM tts_voices WHERE provider = ? ORDER BY display_name",
            [provider],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tts_voices ORDER BY provider, display_name"
        ).fetchall()
    return [dict(r) for r in rows]
