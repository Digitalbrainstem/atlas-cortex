"""Avatar skin resolution from database.

OWNERSHIP: This module owns skin lookup and assignment logic.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_skin(room: str, user_id: str | None = None) -> dict[str, Any]:
    """Resolve the avatar skin for a room/user.

    Priority: user-specific assignment → default skin.
    """
    try:
        from cortex.db import get_db, init_db
        init_db()
        conn = get_db()
        if user_id:
            row = conn.execute(
                "SELECT s.id, s.name, s.path FROM avatar_assignments a "
                "JOIN avatar_skins s ON a.skin_id = s.id "
                "WHERE a.user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                return {"id": row[0], "name": row[1], "path": row[2]}
        row = conn.execute(
            "SELECT id, name, path FROM avatar_skins WHERE is_default = TRUE"
        ).fetchone()
        if row:
            return {"id": row[0], "name": row[1], "path": row[2]}
    except Exception:
        logger.exception("Failed to resolve avatar skin")
    return {"id": "default", "name": "Atlas Default", "path": "cortex/avatar/skins/default.svg"}
