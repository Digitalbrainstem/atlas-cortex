"""Access gate for knowledge queries — enforces user-scoped privacy (Phase I5.4).

Access levels (least → most restrictive):
  public     — anyone can read
  household  — any identified user
  shared     — only users in knowledge_shared_with
  private    — owner only
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ACCESS_LEVELS = ["public", "household", "shared", "private"]

IDENTITY_LEVELS: dict[str, list[str]] = {
    "unknown": ["public"],
    "low":     ["public", "household"],
    "medium":  ["public", "household", "shared"],
    "high":    ["public", "household", "shared", "private"],
}


class AccessGate:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def allowed_levels(self, user_id: str, identity_confidence: str = "high") -> list[str]:
        # Fail closed: unknown/invalid values default to the most restrictive tier
        return IDENTITY_LEVELS.get(identity_confidence, IDENTITY_LEVELS["unknown"])

    def filter_query(
        self,
        user_id: str,
        identity_confidence: str = "high",
    ) -> tuple[str, list]:
        levels = self.allowed_levels(user_id, identity_confidence)
        clauses: list[str] = []
        params: list[Any] = []

        if "public" in levels:
            clauses.append("access_level = 'public'")
        if "household" in levels:
            clauses.append("access_level = 'household'")
        if "shared" in levels:
            clauses.append(
                "(access_level = 'shared' AND doc_id IN "
                "(SELECT doc_id FROM knowledge_shared_with WHERE user_id = ?))"
            )
            params.append(user_id)
        if "private" in levels:
            clauses.append("(access_level = 'private' AND owner_id = ?)")
            params.append(user_id)

        where = "(" + " OR ".join(clauses) + ")" if clauses else "1=0"
        return where, params

    def can_access(
        self,
        user_id: str,
        doc_id: str,
        identity_confidence: str = "high",
    ) -> bool:
        where, params = self.filter_query(user_id, identity_confidence)
        sql = f"SELECT 1 FROM knowledge_docs WHERE doc_id = ? AND {where} LIMIT 1"
        row = self._conn.execute(sql, [doc_id] + params).fetchone()
        return row is not None
