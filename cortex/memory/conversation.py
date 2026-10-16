"""Scoped memory hooks for all conversation transports."""
from __future__ import annotations

import logging
import sqlite3

from cortex.memory.controller import get_memory_system

logger = logging.getLogger(__name__)


async def recall_context(message: str, user_id: str) -> str:
    memory = get_memory_system()
    if memory is None or user_id in ("anonymous", "guest", "default", "web_user"):
        return ""
    try:
        hits = await memory.recall(message, user_id=user_id, top_k=5)
        return "\n".join(hit.text for hit in hits)[:8000]
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.warning("Conversation memory recall failed for scoped user", exc_info=True)
        return ""


async def remember_response(message: str, response: str, user_id: str) -> None:
    memory = get_memory_system()
    if memory is None or user_id in ("anonymous", "guest", "default", "web_user"):
        return
    try:
        await memory.remember(f"User asked: {message}\nAtlas replied: {response[:500]}", user_id=user_id)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.warning("Conversation memory write could not be queued", exc_info=True)
