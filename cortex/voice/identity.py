"""Voice identity — speaker recognition and enrollment.

Uses voice embeddings (resemblyzer or generic) to identify household members
by their voice.  Integrates with user profiles for personalization.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class IdentifyResult:
    """Result of a speaker identification attempt."""

    user_id: str | None
    confidence: float
    is_known: bool
    age_estimate: str | None = None


# ---------------------------------------------------------------------------
# Table bootstrap
# ---------------------------------------------------------------------------

_VOICE_ENROLLMENTS_DDL = """
CREATE TABLE IF NOT EXISTS voice_enrollments (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    label       TEXT DEFAULT '',
    embedding   TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_voice_enroll_user ON voice_enrollments(user_id);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the voice_enrollments table if it does not exist."""
    conn.executescript(_VOICE_ENROLLMENTS_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine similarity in [-1, 1]."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Speaker identifier
# ---------------------------------------------------------------------------


class SpeakerIdentifier:
    """Identify speakers by voice embeddings."""

    def __init__(self, conn: sqlite3.Connection, embedding_dim: int = 256):
        self._conn = conn
        self._dim = embedding_dim
        self._enrollments: dict[str, list[list[float]]] = {}
        _ensure_table(conn)
        self._load_enrollments()

    # -- persistence --------------------------------------------------------

    def _load_enrollments(self) -> None:
        """Load enrolled voice embeddings from DB."""
        self._enrollments.clear()
        rows = self._conn.execute(
            "SELECT user_id, embedding FROM voice_enrollments"
        ).fetchall()
        for row in rows:
            user_id = row[0] if isinstance(row, (tuple, list)) else row["user_id"]
            raw = row[1] if isinstance(row, (tuple, list)) else row["embedding"]
            emb = json.loads(raw)
            self._enrollments.setdefault(user_id, []).append(emb)

    # -- public API ---------------------------------------------------------

    async def identify(
        self,
        audio_embedding: list[float],
        threshold: float = 0.75,
    ) -> IdentifyResult:
        """Identify speaker from voice embedding.

        Compares against all enrolled embeddings using cosine similarity.
        Returns :class:`IdentifyResult` with *user_id*, *confidence*, and
        *is_known*.
        """
        query = np.asarray(audio_embedding, dtype=np.float64)
        best_user: str | None = None
        best_score: float = -1.0

        for user_id, embeddings in self._enrollments.items():
            # Average all enrollment embeddings for this user
            centroid = np.mean(embeddings, axis=0)
            score = _cosine_similarity(query, centroid)
            if score > best_score:
                best_score = score
                best_user = user_id

        is_known = best_score >= threshold
        age_group, _ = self.estimate_age_group(audio_embedding)
        return IdentifyResult(
            user_id=best_user if is_known else None,
            confidence=max(best_score, 0.0),
            is_known=is_known,
            age_estimate=age_group,
        )

    async def enroll(
        self,
        user_id: str,
        audio_embedding: list[float],
        label: str = "",
    ) -> bool:
        """Enroll a new voice sample for *user_id*.

        Multiple samples improve accuracy.  Stores to DB + in-memory cache.
        """
        row_id = uuid.uuid4().hex
        emb_json = json.dumps(audio_embedding)
        try:
            self._conn.execute(
                "INSERT INTO voice_enrollments (id, user_id, label, embedding) "
                "VALUES (?, ?, ?, ?)",
                (row_id, user_id, label, emb_json),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to enroll voice sample for %s", user_id)
            return False

        self._enrollments.setdefault(user_id, []).append(audio_embedding)
        return True

    async def unenroll(self, user_id: str) -> bool:
        """Remove all voice enrollments for *user_id*."""
        try:
            self._conn.execute(
                "DELETE FROM voice_enrollments WHERE user_id = ?",
                (user_id,),
            )
            self._conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to unenroll user %s", user_id)
            return False

        self._enrollments.pop(user_id, None)
        return True

    def estimate_age_group(
        self, audio_embedding: list[float]
    ) -> tuple[str, float]:
        """Rough age estimation from voice characteristics.

        Returns ``(age_group, confidence)`` where *age_group* is one of
        ``'child'``, ``'teen'``, ``'adult'``, ``'unknown'``.

        .. note:: This is a placeholder — always returns ``('unknown', 0.0)``.
        """
        return ("unknown", 0.0)
