"""Fallthrough analyzer — finds LLM-fallthrough interactions and proposes new patterns (Phase I4.2)."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Heuristic phrase patterns for common HA commands
_HA_PHRASE_RULES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"\bturn\s+(on|off)\b", re.IGNORECASE), "toggle", 0.8),
    (re.compile(r"\b(switch|flip)\s+(on|off)\b", re.IGNORECASE), "toggle", 0.75),
    (re.compile(r"\b(lock|unlock)\b", re.IGNORECASE), "lock", 0.8),
    (re.compile(r"\b(open|close)\b.+\b(door|blind|curtain|shade|garage)\b", re.IGNORECASE), "cover", 0.8),
    (re.compile(r"\bset\b.+\bto\s+\d+\s*(%|degrees?|°)\b", re.IGNORECASE), "set_value", 0.75),
    (re.compile(r"\bdim\b|\bbrighten\b", re.IGNORECASE), "set_brightness", 0.7),
    (re.compile(r"\b(pause|play|stop|skip|next)\b.+\b(music|media|song)\b", re.IGNORECASE), "media_control", 0.7),
    (re.compile(r"\b(activate|trigger|run)\b.+\b(scene|automation|routine)\b", re.IGNORECASE), "activate_scene", 0.7),
    (re.compile(r"\b(warmer|cooler|hotter|colder)\b", re.IGNORECASE), "adjust_temperature", 0.65),
]


def _build_candidate_pattern(message: str, intent: str) -> str | None:
    """Build a simple regex pattern from a message string."""
    # Lowercase and strip punctuation
    cleaned = re.sub(r"[^\w\s]", "", message.lower()).strip()
    if not cleaned:
        return None
    # Escape the cleaned message to make a literal-ish pattern
    # Replace specific tokens with generic capture groups
    pattern = re.sub(r"\b\d+\b", r"(\\d+)", cleaned)
    pattern = f"(?i){re.escape(pattern)}"
    # Un-escape the capture groups we inserted
    pattern = pattern.replace(r"\(\\d\+\)", r"(\d+)")
    return pattern


class FallthroughAnalyzer:
    """Analyze LLM-fallthrough interactions and propose new patterns."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get_fallthroughs(self, since_hours: int = 24, limit: int = 100) -> list[dict]:
        """Return recent interactions that fell through to the LLM layer."""
        rows = self._conn.execute(
            """SELECT id, message, response, created_at
               FROM interactions
               WHERE matched_layer = 'llm'
                 AND created_at > datetime('now', ?)
               ORDER BY created_at DESC
               LIMIT ?""",
            (f"-{since_hours} hours", limit),
        ).fetchall()
        return [
            {"id": r[0], "message": r[1], "response": r[2], "created_at": r[3]}
            for r in rows
        ]

    def extract_candidate_patterns(self, interactions: list[dict]) -> list[dict]:
        """Heuristically extract candidate patterns from fallthrough messages."""
        candidates: list[dict] = []
        for interaction in interactions:
            message: str = interaction.get("message") or ""
            if not message:
                continue
            for rule_re, intent, confidence in _HA_PHRASE_RULES:
                if rule_re.search(message):
                    pattern = _build_candidate_pattern(message, intent)
                    if pattern:
                        candidates.append({
                            "pattern": pattern,
                            "intent": intent,
                            "confidence": confidence,
                            "source_interaction_id": interaction["id"],
                        })
                    break  # one candidate per interaction
        logger.debug(
            "extract_candidate_patterns: %d candidates from %d interactions",
            len(candidates),
            len(interactions),
        )
        return candidates

    def save_learned_patterns(self, candidates: list[dict]) -> int:
        """Insert candidate patterns that do not already exist, link learned_patterns."""
        inserted = 0
        for candidate in candidates:
            pattern: str = candidate["pattern"]
            intent: str = candidate["intent"]
            confidence: float = candidate["confidence"]
            source_id: int = candidate.get("source_interaction_id", 0)

            # Skip duplicates
            existing = self._conn.execute(
                "SELECT id FROM command_patterns WHERE pattern = ?", (pattern,)
            ).fetchone()
            if existing:
                continue

            cursor = self._conn.execute(
                """INSERT INTO command_patterns
                   (pattern, intent, source, confidence)
                   VALUES (?, ?, 'learned', ?)""",
                (pattern, intent, confidence),
            )
            pattern_id = cursor.lastrowid

            # Link to learned_patterns if source interaction is known
            if source_id:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO learned_patterns
                           (interaction_id, pattern_id)
                           VALUES (?, ?)""",
                        (source_id, pattern_id),
                    )
                except Exception as exc:
                    logger.warning("Could not insert learned_patterns row: %s", exc)

            inserted += 1

        self._conn.commit()
        logger.info("save_learned_patterns: inserted %d new patterns", inserted)
        return inserted

    def run(self, since_hours: int = 24) -> dict:
        """Full pipeline: analyze → propose → save."""
        fallthroughs = self.get_fallthroughs(since_hours=since_hours)
        candidates = self.extract_candidate_patterns(fallthroughs)
        saved = self.save_learned_patterns(candidates)
        return {
            "fallthroughs_analyzed": len(fallthroughs),
            "patterns_proposed": len(candidates),
            "patterns_saved": saved,
        }
