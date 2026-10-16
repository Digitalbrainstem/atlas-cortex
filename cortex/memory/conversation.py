"""Scoped memory hooks for all conversation transports."""
from __future__ import annotations

import logging
import sqlite3

from cortex.memory.controller import MemorySystem, get_memory_system

logger = logging.getLogger(__name__)


def _personal_memory(user_id: str) -> MemorySystem | None:
    memory = get_memory_system()
    if memory is None or user_id in ("anonymous", "guest", "default", "web_user"):
        return None
    exists = memory._conn.execute(
        "SELECT 1 FROM user_profiles WHERE user_id=? UNION ALL "
        "SELECT 1 FROM user_auth WHERE user_id=? LIMIT 1", (user_id, user_id),
    ).fetchone()
    return memory if exists else None


async def recall_context(message: str, user_id: str) -> str:
    try:
        memory = _personal_memory(user_id)
        if memory is None:
            return ""
        hits = await memory.recall(message, user_id=user_id, top_k=5)
        return "\n".join(hit.text for hit in hits)[:8000]
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.warning("Conversation memory recall failed for scoped user", exc_info=True)
        return ""


async def remember_response(message: str, response: str, user_id: str) -> None:
    try:
        memory = _personal_memory(user_id)
        if memory is None:
            return
        await memory.remember(f"User asked: {message}\nAtlas replied: {response[:500]}", user_id=user_id)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.warning("Conversation memory write could not be queued", exc_info=True)
