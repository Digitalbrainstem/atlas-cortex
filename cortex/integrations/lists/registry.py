"""List registry — resolves list names, manages permissions (Phase I6.1)."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from cortex.integrations.lists.backends import ListBackend, SQLiteListBackend
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

_ADD_PATTERNS = re.compile(
    r"add (.+?) to (?:my )?(\w[\w\s]*?) list",
    re.IGNORECASE,
)
_GET_PATTERNS = re.compile(
    r"(?:what(?:'s| is) on|show me) (?:my )?(\w[\w\s]*?) list",
    re.IGNORECASE,
)
_REMOVE_PATTERNS = re.compile(
    r"remove (.+?) from (?:my )?(\w[\w\s]*?) list",
    re.IGNORECASE,
)


class ListRegistry:
    def __init__(self, conn: Any, default_backend: ListBackend | None = None) -> None:
        self._conn = conn
        self.backend: ListBackend = default_backend or SQLiteListBackend(conn)

    def create_list(
        self,
        display_name: str,
        owner_id: str,
        backend: str = "sqlite",
        access_level: str = "private",
        category: str | None = None,
    ) -> str:
        list_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO list_registry (id, display_name, backend, backend_config, "
            "owner_id, access_level, category) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (list_id, display_name, backend, "{}", owner_id, access_level, category),
        )
        self._conn.execute(
            "INSERT INTO list_permissions (list_id, user_id, can_add, can_view, can_remove) "
            "VALUES (?, ?, TRUE, TRUE, TRUE)",
            (list_id, owner_id),
        )
        self._conn.commit()
        return list_id

    def resolve(self, name: str, user_id: str) -> dict | None:
        name_lower = name.lower().strip()

        # Try display_name exact (case-insensitive)
        row = self._conn.execute(
            "SELECT * FROM list_registry WHERE lower(display_name) = ?",
            (name_lower,),
        ).fetchone()

        # Try alias
        if row is None:
            alias_row = self._conn.execute(
                "SELECT list_id FROM list_aliases WHERE lower(alias) = ?",
                (name_lower,),
            ).fetchone()
            if alias_row:
                row = self._conn.execute(
                    "SELECT * FROM list_registry WHERE id = ?",
                    (alias_row["list_id"],),
                ).fetchone()

        # Try category
        if row is None:
            row = self._conn.execute(
                "SELECT * FROM list_registry WHERE lower(category) = ?",
                (name_lower,),
            ).fetchone()

        if row is None:
            return None

        if not self.check_permission(row["id"], user_id, "view"):
            return None

        return dict(row)

    def add_alias(self, list_id: str, alias: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO list_aliases (list_id, alias) VALUES (?, ?)",
            (list_id, alias),
        )
        self._conn.commit()

    def grant_permission(
        self,
        list_id: str,
        user_id: str,
        can_add: bool = True,
        can_view: bool = True,
        can_remove: bool = False,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO list_permissions "
            "(list_id, user_id, can_add, can_view, can_remove) VALUES (?, ?, ?, ?, ?)",
            (list_id, user_id, can_add, can_view, can_remove),
        )
        self._conn.commit()

    def check_permission(self, list_id: str, user_id: str, action: str) -> bool:
        row = self._conn.execute(
            "SELECT owner_id FROM list_registry WHERE id = ?", (list_id,)
        ).fetchone()
        if row and row["owner_id"] == user_id:
            return True

        perm = self._conn.execute(
            "SELECT can_view, can_add, can_remove FROM list_permissions "
            "WHERE list_id = ? AND user_id = ?",
            (list_id, user_id),
        ).fetchone()
        if perm is None:
            return False
        if action == "view":
            return bool(perm["can_view"])
        if action == "add":
            return bool(perm["can_add"])
        if action == "remove":
            return bool(perm["can_remove"])
        return False

    def list_all(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT DISTINCT lr.* FROM list_registry lr "
            "LEFT JOIN list_permissions lp ON lr.id = lp.list_id "
            "WHERE lr.owner_id = ? OR (lp.user_id = ? AND lp.can_view = TRUE)",
            (user_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]


class ListPlugin(CortexPlugin):
    plugin_id = "lists"
    display_name = "Smart Lists"
    plugin_type = "list_backend"

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._registry = ListRegistry(conn)
        self._backend = self._registry.backend

    async def setup(self, config: dict) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(self, message: str, context: dict) -> CommandMatch:
        m = _ADD_PATTERNS.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="add_item",
                confidence=0.9,
                metadata={"item": m.group(1).strip(), "list_name": m.group(2).strip()},
            )
        m = _REMOVE_PATTERNS.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="remove_item",
                confidence=0.9,
                metadata={"item": m.group(1).strip(), "list_name": m.group(2).strip()},
            )
        m = _GET_PATTERNS.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="get_list",
                confidence=0.9,
                metadata={"list_name": m.group(1).strip()},
            )
        return CommandMatch(matched=False)

    async def handle(self, message: str, match: CommandMatch, context: dict) -> CommandResult:
        user_id = context.get("user_id", "unknown")
        intent = match.intent
        meta = match.metadata

        if intent == "add_item":
            list_name = meta.get("list_name", "")
            item_text = meta.get("item", "")
            lst = self._registry.resolve(list_name, user_id)
            if lst is None:
                list_id = self._registry.create_list(list_name, owner_id=user_id)
            else:
                list_id = lst["id"]
                if not self._registry.check_permission(list_id, user_id, "add"):
                    return CommandResult(
                        success=False,
                        response=f"You don't have permission to add items to the {list_name} list.",
                    )
            await self._backend.add_item(list_id, item_text, user_id)
            return CommandResult(
                success=True,
                response=f"Added {item_text} to your {list_name} list.",
            )

        if intent == "get_list":
            list_name = meta.get("list_name", "")
            lst = self._registry.resolve(list_name, user_id)
            if lst is None:
                return CommandResult(
                    success=True,
                    response=f"You don't have a {list_name} list yet.",
                )
            items = await self._backend.get_items(lst["id"])
            if not items:
                return CommandResult(
                    success=True,
                    response=f"Your {list_name} list is empty.",
                )
            lines = "\n".join(
                f"{'✓' if item.done else '•'} {item.content}" for item in items
            )
            return CommandResult(
                success=True,
                response=f"Here's your {list_name} list:\n{lines}",
            )

        if intent == "remove_item":
            list_name = meta.get("list_name", "")
            item_text = meta.get("item", "").lower()
            lst = self._registry.resolve(list_name, user_id)
            if lst is None:
                return CommandResult(success=False, response=f"No {list_name} list found.")
            if not self._registry.check_permission(lst["id"], user_id, "remove"):
                return CommandResult(
                    success=False,
                    response=f"You don't have permission to remove items from the {list_name} list.",
                )
            items = await self._backend.get_items(lst["id"])
            target = next((i for i in items if item_text in i.content.lower()), None)
            if target is None:
                return CommandResult(
                    success=False,
                    response=f"Couldn't find that item in your {list_name} list.",
                )
            await self._backend.remove_item(lst["id"], target.id)
            return CommandResult(
                success=True,
                response=f"Removed {target.content} from your {list_name} list.",
            )

        return CommandResult(success=False, response="I'm not sure what you want to do with the list.")
