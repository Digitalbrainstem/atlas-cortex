"""Model registry for self-evolution.

Tracks base models, LoRA adapters, and candidates through their
lifecycle: registration → evaluation → promotion → retirement.
"""

# Module ownership: Self-evolution model registry

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from cortex.db import get_db

log = logging.getLogger(__name__)


class ModelRegistry:
    """CRUD operations for the model_registry table."""

    def register_model(
        self,
        model_name: str,
        model_type: str = "base",
        source: str = "",
        metadata: dict | None = None,
    ) -> int:
        """Register a new model. Returns the row id."""
        conn = get_db()
        meta_json = json.dumps(metadata or {})
        cur = conn.execute(
            "INSERT INTO model_registry (model_name, model_type, source, metadata) "
            "VALUES (?, ?, ?, ?)",
            (model_name, model_type, source, meta_json),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_models(
        self, status: str = "", model_type: str = ""
    ) -> list[dict]:
        """List models, optionally filtered by status and/or type."""
        conn = get_db()
        clauses: list[str] = []
        params: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if model_type:
            clauses.append("model_type = ?")
            params.append(model_type)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM model_registry{where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active_model(self) -> dict | None:
        """Return the currently active model, if any."""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM model_registry WHERE status = 'active' LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def promote_model(self, model_id: int) -> bool:
        """Promote a candidate to active. Retires the current active model."""
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()

        # Retire current active model(s)
        conn.execute(
            "UPDATE model_registry SET status = 'retired' WHERE status = 'active'"
        )
        # Promote target
        cur = conn.execute(
            "UPDATE model_registry SET status = 'active', promoted_at = ? WHERE id = ?",
            (now, model_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def retire_model(self, model_id: int) -> bool:
        """Retire a model."""
        conn = get_db()
        cur = conn.execute(
            "UPDATE model_registry SET status = 'retired' WHERE id = ?",
            (model_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    def update_scores(
        self,
        model_id: int,
        eval_score: float = 0,
        safety_score: float = 0,
        personality_score: float = 0,
    ) -> None:
        """Update evaluation scores for a model."""
        conn = get_db()
        conn.execute(
            "UPDATE model_registry "
            "SET eval_score = ?, safety_score = ?, personality_score = ? "
            "WHERE id = ?",
            (eval_score, safety_score, personality_score, model_id),
        )
        conn.commit()
