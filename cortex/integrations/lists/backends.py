"""List backend abstraction — pluggable storage for Atlas Cortex lists (Phase I6.1)."""

from __future__ import annotations

import abc
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class ListItem:
    id: str
    content: str
    done: bool = False
    added_by: str = ""
    added_at: str = ""


class ListBackend(abc.ABC):
    @abc.abstractmethod
    async def get_items(self, list_id: str) -> list[ListItem]:
        raise NotImplementedError

    @abc.abstractmethod
    async def add_item(self, list_id: str, content: str, user_id: str = "") -> ListItem:
        raise NotImplementedError

    @abc.abstractmethod
    async def remove_item(self, list_id: str, item_id: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def mark_done(self, list_id: str, item_id: str, done: bool = True) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def clear_done(self, list_id: str) -> int:
        raise NotImplementedError


class SQLiteListBackend(ListBackend):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def get_items(self, list_id: str) -> list[ListItem]:
        rows = self._conn.execute(
            "SELECT id, content, done, added_by, added_at FROM list_items "
            "WHERE list_id = ? ORDER BY added_at ASC",
            (list_id,),
        ).fetchall()
        return [
            ListItem(
                id=r["id"],
                content=r["content"],
                done=bool(r["done"]),
                added_by=r["added_by"] or "",
                added_at=str(r["added_at"] or ""),
            )
            for r in rows
        ]

    async def add_item(self, list_id: str, content: str, user_id: str = "") -> ListItem:
        item_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO list_items (id, list_id, content, done, added_by, added_at) "
            "VALUES (?, ?, ?, FALSE, ?, ?)",
            (item_id, list_id, content, user_id, now),
        )
        self._conn.commit()
        return ListItem(id=item_id, content=content, done=False, added_by=user_id, added_at=now)

    async def remove_item(self, list_id: str, item_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM list_items WHERE list_id = ? AND id = ?",
            (list_id, item_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    async def mark_done(self, list_id: str, item_id: str, done: bool = True) -> bool:
        cur = self._conn.execute(
            "UPDATE list_items SET done = ? WHERE list_id = ? AND id = ?",
            (done, list_id, item_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    async def clear_done(self, list_id: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM list_items WHERE list_id = ? AND done = TRUE",
            (list_id,),
        )
        self._conn.commit()
        return cur.rowcount
