"""Pre-cached kid-friendly joke bank with rotation and TTS caching.

Jokes are served via Layer 1 (instant answers) with pre-generated TTS audio
so there is zero latency — no LLM call, no TTS synthesis wait.

Usage::

    from cortex.jokes import get_random_joke, init_joke_bank
    init_joke_bank()                # idempotent — seeds DB + pre-generates TTS
    joke = get_random_joke()        # returns a Joke with .setup and .punchline
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)

# ── Joke data class ──────────────────────────────────────────────

@dataclass
class Joke:
    id: int
    setup: str
    punchline: str
    category: str


# ── Kid-friendly joke bank ───────────────────────────────────────

_JOKES: list[dict[str, str]] = [
    {"setup": "Why don't scientists trust atoms?", "punchline": "Because they make up everything!", "category": "science"},
    {"setup": "What do you call a bear with no teeth?", "punchline": "A gummy bear!", "category": "animals"},
    {"setup": "Why did the bicycle fall over?", "punchline": "Because it was two-tired!", "category": "general"},
    {"setup": "What do you call a sleeping dinosaur?", "punchline": "A dino-snore!", "category": "animals"},
    {"setup": "Why can't you give Elsa a balloon?", "punchline": "Because she will let it go!", "category": "movies"},
    {"setup": "What do you call cheese that isn't yours?", "punchline": "Nacho cheese!", "category": "food"},
    {"setup": "Why did the teddy bear say no to dessert?", "punchline": "Because she was already stuffed!", "category": "food"},
    {"setup": "What do you call a fish without eyes?", "punchline": "A fsh!", "category": "animals"},
    {"setup": "Why did the math book look sad?", "punchline": "Because it had too many problems!", "category": "school"},
    {"setup": "What do you call a dog that does magic tricks?", "punchline": "A Labracadabrador!", "category": "animals"},
    {"setup": "Why did the cookie go to the doctor?", "punchline": "Because it was feeling crummy!", "category": "food"},
    {"setup": "What did the ocean say to the beach?", "punchline": "Nothing, it just waved!", "category": "nature"},
    {"setup": "Why do bananas have to put on sunscreen?", "punchline": "Because they might peel!", "category": "food"},
    {"setup": "What do you call a train that sneezes?", "punchline": "Achoo-choo train!", "category": "general"},
    {"setup": "Why are ghosts bad liars?", "punchline": "Because you can see right through them!", "category": "spooky"},
    {"setup": "What do you call a fake noodle?", "punchline": "An impasta!", "category": "food"},
    {"setup": "Why did the student eat his homework?", "punchline": "Because the teacher told him it was a piece of cake!", "category": "school"},
    {"setup": "What do you call a cow with no legs?", "punchline": "Ground beef!", "category": "animals"},
    {"setup": "Why don't eggs tell jokes?", "punchline": "Because they'd crack each other up!", "category": "food"},
    {"setup": "What did one wall say to the other wall?", "punchline": "I'll meet you at the corner!", "category": "general"},
    {"setup": "Why did the scarecrow win an award?", "punchline": "Because he was outstanding in his field!", "category": "general"},
    {"setup": "What do you call a snowman with a six-pack?", "punchline": "An abdominal snowman!", "category": "nature"},
    {"setup": "Why do cows wear bells?", "punchline": "Because their horns don't work!", "category": "animals"},
    {"setup": "What did the left eye say to the right eye?", "punchline": "Between you and me, something smells!", "category": "general"},
    {"setup": "Why did the golfer bring two pairs of pants?", "punchline": "In case he got a hole in one!", "category": "sports"},
]


# ── DB schema ────────────────────────────────────────────────────

_JOKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS joke_bank (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    setup       TEXT NOT NULL,
    punchline   TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    hash        TEXT UNIQUE NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS joke_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    joke_id     INTEGER NOT NULL REFERENCES joke_bank(id),
    told_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    room        TEXT DEFAULT 'default',
    user_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_joke_usage_joke ON joke_usage(joke_id);
CREATE INDEX IF NOT EXISTS idx_joke_usage_told ON joke_usage(told_at);

CREATE TABLE IF NOT EXISTS tts_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text_hash   TEXT NOT NULL,
    voice_id    TEXT NOT NULL,
    audio_path  TEXT NOT NULL,
    sample_rate INTEGER DEFAULT 24000,
    size_bytes  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(text_hash, voice_id)
);
"""


def _joke_hash(setup: str, punchline: str) -> str:
    return hashlib.sha256(f"{setup}||{punchline}".encode()).hexdigest()[:16]


def init_joke_bank() -> None:
    """Create joke tables and seed the joke bank (idempotent)."""
    init_db()
    conn = get_db()
    for stmt in _JOKE_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    # Seed jokes
    for joke in _JOKES:
        h = _joke_hash(joke["setup"], joke["punchline"])
        try:
            conn.execute(
                "INSERT OR IGNORE INTO joke_bank (setup, punchline, category, hash) VALUES (?, ?, ?, ?)",
                (joke["setup"], joke["punchline"], joke["category"], h),
            )
        except Exception:
            pass
    conn.commit()
    logger.info("Joke bank initialized with %d jokes", len(_JOKES))


# ── Joke selection with rotation ─────────────────────────────────

def get_random_joke(room: str = "default", user_id: str | None = None) -> Joke | None:
    """Pick a joke that hasn't been told recently.

    Rotation rules:
    - Prefer jokes not told in the last 7 days
    - If all jokes told recently, pick the least recently told
    - Never repeat the last joke told
    """
    try:
        conn = get_db()

        # Get last joke told (to avoid immediate repeat)
        last = conn.execute(
            "SELECT joke_id FROM joke_usage ORDER BY told_at DESC LIMIT 1"
        ).fetchone()
        last_id = last[0] if last else -1

        # Prefer jokes not told in 7 days
        fresh = conn.execute(
            "SELECT j.id, j.setup, j.punchline, j.category FROM joke_bank j "
            "WHERE j.active = TRUE AND j.id != ? AND j.id NOT IN ("
            "  SELECT joke_id FROM joke_usage WHERE told_at > datetime('now', '-7 days')"
            ") ORDER BY RANDOM() LIMIT 1",
            (last_id,),
        ).fetchone()

        if not fresh:
            # All told recently — pick least recently told
            fresh = conn.execute(
                "SELECT j.id, j.setup, j.punchline, j.category FROM joke_bank j "
                "LEFT JOIN joke_usage u ON j.id = u.joke_id "
                "WHERE j.active = TRUE AND j.id != ? "
                "ORDER BY u.told_at ASC NULLS FIRST LIMIT 1",
                (last_id,),
            ).fetchone()

        if not fresh:
            return None

        joke = Joke(id=fresh[0], setup=fresh[1], punchline=fresh[2], category=fresh[3])

        # Record usage
        conn.execute(
            "INSERT INTO joke_usage (joke_id, room, user_id) VALUES (?, ?, ?)",
            (joke.id, room, user_id),
        )
        conn.commit()

        return joke
    except Exception:
        logger.exception("Failed to get random joke")
        return None


# ── TTS pre-caching ──────────────────────────────────────────────

def _cache_dir() -> Path:
    data_dir = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
    cache = data_dir / "tts_cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_cached_audio(text: str, voice_id: str = "default") -> tuple[bytes, int] | None:
    """Return cached (pcm_bytes, sample_rate) for text+voice, or None."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT audio_path, sample_rate FROM tts_cache WHERE text_hash = ? AND voice_id = ?",
            (_text_hash(text), voice_id),
        ).fetchone()
        if row and Path(row[0]).exists():
            return Path(row[0]).read_bytes(), row[1]
    except Exception:
        logger.debug("TTS cache miss for %r", text[:40])
    return None


async def cache_tts(text: str, voice_id: str = "default", speed: float = 1.0) -> tuple[bytes, int] | None:
    """Synthesize and cache TTS for text. Returns (pcm_bytes, sample_rate)."""
    try:
        from cortex.voice.providers import get_tts_provider
        tts = get_tts_provider()
        sample_rate = getattr(tts, "sample_rate", 24000)

        audio_chunks = []
        async for chunk in tts.synthesize(text, speed=speed, stream=True):
            audio_chunks.append(chunk)
        pcm = b"".join(audio_chunks)

        if not pcm:
            return None

        # Save to file
        h = _text_hash(text)
        path = _cache_dir() / f"{voice_id}_{h}.pcm"
        path.write_bytes(pcm)

        # Record in DB
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO tts_cache (text_hash, voice_id, audio_path, sample_rate, size_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (h, voice_id, str(path), sample_rate, len(pcm)),
        )
        conn.commit()
        logger.info("Cached TTS: %r → %s (%d bytes)", text[:40], path.name, len(pcm))
        return pcm, sample_rate
    except Exception:
        logger.exception("Failed to cache TTS for %r", text[:40])
        return None


async def pre_generate_joke_audio(voice_id: str = "default") -> int:
    """Pre-generate TTS audio for all active jokes. Returns count generated."""
    init_joke_bank()
    conn = get_db()
    jokes = conn.execute(
        "SELECT id, setup, punchline FROM joke_bank WHERE active = TRUE"
    ).fetchall()

    count = 0
    for joke in jokes:
        setup, punchline = joke[1], joke[2]
        # Cache setup and punchline separately (they're sent as separate TTS sentences)
        for text in [setup, punchline]:
            cached = get_cached_audio(text, voice_id)
            if not cached:
                result = await cache_tts(text, voice_id)
                if result:
                    count += 1
    logger.info("Pre-generated TTS for %d joke segments (voice=%s)", count, voice_id)
    return count


async def stream_cached_joke_to_avatar(room: str, joke: Joke) -> bool:
    """Stream pre-cached joke audio to avatar, falling back to live TTS.

    Returns True if joke was streamed successfully.
    """
    import uuid
    from cortex.avatar.websocket import (
        broadcast_tts_chunk,
        broadcast_tts_end,
        broadcast_tts_start,
        should_play_on_avatar,
    )

    if not should_play_on_avatar(room):
        return False

    voice_id = "default"  # TODO: resolve from room/user

    for text in [joke.setup, joke.punchline]:
        cached = get_cached_audio(text, voice_id)
        if cached:
            pcm, sample_rate = cached
        else:
            # Fall back to live synthesis + cache
            result = await cache_tts(text, voice_id)
            if result:
                pcm, sample_rate = result
            else:
                return False

        sid = uuid.uuid4().hex[:12]
        await broadcast_tts_start(room, sid, sample_rate, text)

        # Stream in chunks
        chunk_size = 4096
        for i in range(0, len(pcm), chunk_size):
            chunk = pcm[i : i + chunk_size]
            await broadcast_tts_chunk(room, sid, base64.b64encode(chunk).decode("ascii"))

        await broadcast_tts_end(room, sid)

    return True
