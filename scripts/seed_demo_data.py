"""Seed the database with demo data for admin panel screenshots."""

import sqlite3
import os
import struct
from pathlib import Path
from datetime import datetime, timezone, timedelta

from cortex.db import init_db, get_db, set_db_path
from cortex.auth import seed_admin

DATA_DIR = Path(os.environ.get("CORTEX_DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

set_db_path(DATA_DIR / "cortex.db")
init_db()
conn = get_db()
seed_admin(conn)

now = datetime.now(timezone.utc)

# ── Users ─────────────────────────────────────────────────────────
users = [
    ("derek", "Derek", 34, "adult", 0.95, "advanced", "casual", "detailed", "dry", False, None, True),
    ("sarah", "Sarah", 32, "adult", 0.95, "moderate", "friendly", "moderate", "playful", True, None, True),
    ("emma", "Emma", 8, "child", 0.95, "simple", "neutral", "brief", None, False, "sarah", True),
    ("logan", "Logan", 14, "teen", 0.95, "moderate", "casual", "moderate", "memes", False, "sarah", True),
    ("guest", "Guest", None, "unknown", 0.0, "moderate", "neutral", "moderate", None, False, None, False),
]
for u in users:
    conn.execute(
        """INSERT OR IGNORE INTO user_profiles
        (user_id, display_name, age, age_group, age_confidence,
         vocabulary_level, preferred_tone, communication_style, humor_style,
         is_parent, parent_user_id, onboarding_complete)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", u)

# ── Emotional Profiles ────────────────────────────────────────────
profiles = [
    ("derek", "Derek", 0.82, "casual", "concise, technical", "dry sarcasm", 487, 312, 41),
    ("sarah", "Sarah", 0.71, "friendly", "detailed, warm", "playful", 203, 165, 12),
    ("emma", "Emma", 0.45, "neutral", "simple, curious", None, 89, 78, 3),
    ("logan", "Logan", 0.38, "casual", "brief, teen slang", "memes", 56, 29, 8),
]
for p in profiles:
    conn.execute(
        """INSERT OR IGNORE INTO emotional_profiles
        (user_id, display_name, rapport_score, preferred_tone,
         communication_style, humor_style, interaction_count,
         positive_count, negative_count, last_interaction)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (*p, now.isoformat()))

# ── Topics ────────────────────────────────────────────────────────
topics = [
    ("derek", "docker", 45), ("derek", "networking", 32), ("derek", "home automation", 28),
    ("derek", "python", 22), ("derek", "3d printing", 15),
    ("sarah", "recipes", 18), ("sarah", "garden", 14), ("sarah", "schedules", 12),
    ("emma", "dinosaurs", 25), ("emma", "drawing", 18), ("emma", "math homework", 10),
    ("logan", "gaming", 20), ("logan", "music", 12),
]
for t in topics:
    conn.execute(
        "INSERT OR IGNORE INTO user_topics (user_id, topic, mention_count) VALUES (?,?,?)", t)

# ── Interactions ──────────────────────────────────────────────────
interactions = [
    ("derek", "Turn off the living room lights", "tool", "command", 0.8, "Done — living room lights off.", 85),
    ("derek", "What time is it?", "instant", "question", 0.0, "It's 10:32 PM.", 2),
    ("derek", "Explain how Docker networking works", "llm", "question", 0.3, "Docker uses bridge networks by default...", 2100),
    ("sarah", "Add milk to the grocery list", "tool", "command", 0.2, "Added milk to your grocery list.", 120),
    ("sarah", "Good morning Atlas", "instant", "greeting", 0.6, "Good morning, Sarah! It's a beautiful day.", 3),
    ("emma", "What are dinosaurs?", "llm", "question", 0.4, "Dinosaurs were amazing creatures...", 1800),
    ("logan", "Play some music", "tool", "command", 0.1, "Playing your playlist on the living room speaker.", 95),
    ("derek", "Set the thermostat to 72", "tool", "command", 0.5, "Done — thermostat set to 72°F.", 110),
    ("derek", "Good night", "tool", "command", 0.8, "Good night, Derek. Running bedtime routine.", 150),
    ("sarah", "What's on my calendar tomorrow?", "llm", "question", 0.2, "You have a dentist appointment at 2pm.", 2400),
]
for i, (uid, msg, layer, sent, score, resp, ms) in enumerate(interactions):
    ts = (now - timedelta(hours=len(interactions) - i)).isoformat()
    conn.execute(
        """INSERT INTO interactions
        (user_id, message, matched_layer, sentiment, sentiment_score, response, response_time_ms, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (uid, msg, layer, sent, score, resp, ms, ts))

# ── Devices ───────────────────────────────────────────────────────
devices = [
    ("light.living_room", "Living Room Lights", "light", "living_room", "Living Room", "on"),
    ("light.bedroom", "Bedroom Lights", "light", "bedroom", "Bedroom", "off"),
    ("light.kitchen", "Kitchen Lights", "light", "kitchen", "Kitchen", "on"),
    ("switch.garage_door", "Garage Door", "switch", "garage", "Garage", "off"),
    ("climate.main", "Main Thermostat", "climate", "living_room", "Living Room", "heat"),
    ("lock.front_door", "Front Door Lock", "lock", "entrance", "Entrance", "locked"),
    ("media_player.living_room", "Living Room Speaker", "media_player", "living_room", "Living Room", "idle"),
    ("sensor.outdoor_temp", "Outdoor Temperature", "sensor", "outdoor", "Outdoor", "68"),
    ("fan.bedroom", "Bedroom Fan", "fan", "bedroom", "Bedroom", "off"),
    ("cover.garage", "Garage Cover", "cover", "garage", "Garage", "closed"),
]
for d in devices:
    conn.execute(
        "INSERT OR IGNORE INTO ha_devices (entity_id, friendly_name, domain, area_id, area_name, state) VALUES (?,?,?,?,?,?)", d)

# ── Command Patterns ──────────────────────────────────────────────
patterns = [
    (r"turn (on|off) (?:the )?(.+)", "toggle", "light", 2, 1, "seed", 1.0, 45),
    (r"set (?:the )?(.+) to (\d+)", "set_value", "climate", 1, 2, "seed", 0.9, 23),
    (r"(lock|unlock) (?:the )?(.+)", "lock", "lock", 2, 1, "seed", 0.95, 12),
    (r"(open|close) (?:the )?(.+)", "cover", "cover", 2, 1, "learned", 0.85, 8),
    (r"play (.+) (?:on|in) (?:the )?(.+)", "media_play", "media_player", 2, None, "learned", 0.7, 5),
    (r"dim (?:the )?(.+) to (\d+)%?", "set_brightness", "light", 1, 2, "nightly", 0.8, 15),
]
for p in patterns:
    conn.execute(
        """INSERT INTO command_patterns
        (pattern, intent, entity_domain, entity_match_group, value_match_group,
         source, confidence, hit_count) VALUES (?,?,?,?,?,?,?,?)""", p)

# ── Safety Events ─────────────────────────────────────────────────
events = [
    ("logan", "input", "profanity", "WARN", "Watch your language", "filtered", "teen"),
    ("guest", "input", "injection", "HARD_BLOCK", "Ignore all previous...", "blocked", "unknown"),
    ("emma", "output", "explicit_content", "SOFT_BLOCK", "Response contained...", "rewritten", "child"),
    (None, "input", "pii_detected", "WARN", "SSN pattern detected", "redacted", "adult"),
    ("logan", "input", "jailbreak_attempt", "HARD_BLOCK", "DAN prompt detected", "blocked", "teen"),
]
for idx, e in enumerate(events):
    ts = (now - timedelta(hours=len(events) - idx, minutes=30)).isoformat()
    conn.execute(
        """INSERT INTO guardrail_events
        (user_id, direction, category, severity, trigger_text, action_taken, content_tier, created_at)
        VALUES (?,?,?,?,?,?,?,?)""", (*e, ts))

# ── Parental Controls ─────────────────────────────────────────────
conn.execute(
    "INSERT OR IGNORE INTO parental_controls (child_user_id, parent_user_id, content_filter_level, allowed_hours_start, allowed_hours_end) VALUES (?,?,?,?,?)",
    ("emma", "sarah", "strict", "07:00", "20:00"))
conn.execute(
    "INSERT OR IGNORE INTO parental_controls (child_user_id, parent_user_id, content_filter_level, allowed_hours_start, allowed_hours_end) VALUES (?,?,?,?,?)",
    ("logan", "sarah", "moderate", "06:00", "22:00"))

# ── Speaker Profiles ──────────────────────────────────────────────
for uid, name, count in [("derek", "Derek", 5), ("sarah", "Sarah", 3), ("emma", "Emma", 2)]:
    embedding = struct.pack(f"{256}f", *([0.1] * 256))
    conn.execute(
        "INSERT OR IGNORE INTO speaker_profiles (id, user_id, display_name, embedding, sample_count) VALUES (?,?,?,?,?)",
        (f"spk-{uid}", uid, name, embedding, count))

# ── Jailbreak Patterns ────────────────────────────────────────────
jp = [
    ("ignore.*previous.*instructions", "seed", 3),
    ("you are now.*DAN", "learned", 1),
    ("pretend you.*no.*restrictions", "seed", 2),
    ("act as.*unfiltered", "learned", 0),
]
for p in jp:
    conn.execute("INSERT OR IGNORE INTO jailbreak_patterns (pattern, source, hit_count) VALUES (?,?,?)", p)

conn.commit()
print(f"Demo data seeded: {DATA_DIR / 'cortex.db'}")
