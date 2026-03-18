# Module ownership: Story progress tracking and library management
from __future__ import annotations

import json
import logging

from cortex.db import get_db, init_db

log = logging.getLogger(__name__)


class StoryLibrary:
    """Track user progress, favourites, and recommendations."""

    async def save_progress(
        self,
        user_id: str,
        story_id: int,
        chapter: int,
        choice_index: int = -1,
    ) -> None:
        """Save or update a user's progress in a story."""

        init_db()
        conn = get_db()
        row = conn.execute(
            "SELECT id, choices_made FROM story_progress "
            "WHERE user_id = ? AND story_id = ?",
            (user_id, story_id),
        ).fetchone()

        if row is None:
            choices = [choice_index] if choice_index >= 0 else []
            conn.execute(
                "INSERT INTO story_progress "
                "(user_id, story_id, current_chapter, choices_made, last_listened) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (user_id, story_id, chapter, json.dumps(choices)),
            )
        else:
            choices: list[int] = json.loads(row["choices_made"])  # type: ignore[no-redef]
            if choice_index >= 0:
                choices.append(choice_index)
            conn.execute(
                "UPDATE story_progress SET current_chapter = ?, choices_made = ?, "
                "last_listened = CURRENT_TIMESTAMP WHERE id = ?",
                (chapter, json.dumps(choices), row["id"]),
            )
        conn.commit()
        log.info("Saved progress for user %s on story %d (ch %d)", user_id, story_id, chapter)

    async def get_progress(self, user_id: str, story_id: int) -> dict | None:
        """Return progress dict or None."""

        init_db()
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM story_progress WHERE user_id = ? AND story_id = ?",
            (user_id, story_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["choices_made"] = json.loads(result["choices_made"])
        return result

    async def mark_complete(self, user_id: str, story_id: int) -> None:
        """Mark a story as completed for a user."""

        init_db()
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM story_progress WHERE user_id = ? AND story_id = ?",
            (user_id, story_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO story_progress "
                "(user_id, story_id, completed, last_listened) "
                "VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
                (user_id, story_id),
            )
        else:
            conn.execute(
                "UPDATE story_progress SET completed = 1, "
                "last_listened = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
        conn.commit()
        log.info("Marked story %d complete for user %s", story_id, user_id)

    async def get_favorites(self, user_id: str) -> list[dict]:
        """Return completed stories for a user (favorites = completed stories)."""

        init_db()
        conn = get_db()
        rows = conn.execute(
            "SELECT s.*, sp.completed, sp.last_listened "
            "FROM story_progress sp "
            "JOIN stories s ON s.id = sp.story_id "
            "WHERE sp.user_id = ? AND sp.completed = 1 "
            "ORDER BY sp.last_listened DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def list_in_progress(self, user_id: str) -> list[dict]:
        """Return stories the user has started but not finished."""

        init_db()
        conn = get_db()
        rows = conn.execute(
            "SELECT s.*, sp.current_chapter, sp.last_listened "
            "FROM story_progress sp "
            "JOIN stories s ON s.id = sp.story_id "
            "WHERE sp.user_id = ? AND sp.completed = 0 "
            "ORDER BY sp.last_listened DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def get_recommendations(
        self, user_id: str, age_group: str
    ) -> list[dict]:
        """Recommend stories the user has not started, filtered by age group."""

        init_db()
        conn = get_db()
        rows = conn.execute(
            "SELECT s.* FROM stories s "
            "WHERE s.target_age_group = ? "
            "AND s.parent_approved = 1 "
            "AND s.id NOT IN ("
            "  SELECT story_id FROM story_progress WHERE user_id = ?"
            ") "
            "ORDER BY s.created_at DESC",
            (age_group, user_id),
        ).fetchall()
        return [dict(r) for r in rows]
