"""Grounding and anti-hallucination system for Atlas Cortex.

Handles:
  - Confidence scoring
  - High-risk claim detection
  - Mistake logging
  - Confidence-aware response adjustment

See docs/grounding.md for full design.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# High-risk patterns that lower default confidence
# ──────────────────────────────────────────────────────────────────

_HIGH_RISK_PATTERNS = [
    re.compile(r"v\d+\.\d+"),                         # version numbers
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),             # dates
    re.compile(r"https?://\S+"),                        # URLs
    re.compile(r"\b(port|ports?)\s+\d{2,5}\b", re.IGNORECASE),  # port numbers
    re.compile(r"\b\d+\s*(MB|GB|TB|ms|Hz|KB)\b", re.IGNORECASE),  # technical numbers
]


def assess_confidence(
    response: str,
    layer: str = "llm",
    memory_hits: int = 0,
    mistake_penalty: float = 0.0,
) -> float:
    """Score the confidence of a response (0.0–1.0).

    Args:
        response:       Generated response text.
        layer:          Which layer produced it (``"instant"``, ``"tool"``, ``"llm"``).
        memory_hits:    Number of memory hits that corroborated the answer.
        mistake_penalty: Per-topic penalty from prior mistakes (0.0–0.3).

    Returns a confidence float from 0.0 (none) to 1.0 (certain).
    """
    if layer == "instant":
        return 1.0
    if layer == "tool":
        return 0.9

    # Base LLM confidence
    base = 0.5

    # Boost from memory corroboration
    if memory_hits >= 3:
        base += 0.2
    elif memory_hits >= 1:
        base += 0.1

    # Penalise high-risk patterns
    risk_hits = sum(1 for p in _HIGH_RISK_PATTERNS if p.search(response))
    if risk_hits >= 3:
        base -= 0.3
    elif risk_hits >= 1:
        base -= 0.15

    # Apply mistake history penalty
    base -= mistake_penalty

    return max(0.05, min(1.0, base))


def log_mistake(
    conn: Any,
    interaction_id: int | None,
    user_id: str,
    claim_text: str,
    correction_text: str,
    detection_method: str,
    confidence_at_time: float = 0.0,
    mistake_category: str = "factual",
    tags: list[str] | None = None,
) -> None:
    """Record a mistake in the mistake_log and mistake_tags tables."""
    if conn is None:
        return
    try:
        cur = conn.execute(
            """
            INSERT INTO mistake_log
              (interaction_id, user_id, claim_text, correction_text,
               detection_method, mistake_category, confidence_at_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (interaction_id, user_id, claim_text, correction_text,
             detection_method, mistake_category, confidence_at_time),
        )
        mistake_id = cur.lastrowid
        for tag in (tags or []):
            conn.execute(
                "INSERT OR IGNORE INTO mistake_tags (mistake_id, tag) VALUES (?, ?)",
                (mistake_id, tag),
            )
        conn.commit()
    except Exception as exc:
        logger.debug("Mistake logging failed: %s", exc)


def get_topic_mistake_penalty(conn: Any, topics: list[str], days: int = 30) -> float:
    """Return confidence penalty based on prior mistakes in the given topics."""
    if conn is None or not topics:
        return 0.0
    try:
        placeholders = ",".join("?" * len(topics))
        row = conn.execute(
            f"""
            SELECT COUNT(*) as cnt FROM mistake_tags mt
            JOIN mistake_log ml ON mt.mistake_id = ml.id
            WHERE mt.tag IN ({placeholders})
            AND ml.created_at > datetime('now', ? || ' days')
            """,
            (*topics, f"-{int(days)}"),
        ).fetchone()
        count = row["cnt"] if row else 0
        return min(count * 0.1, 0.3)
    except Exception:
        return 0.0
