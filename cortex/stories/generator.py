# Module ownership: Story outline & chapter prompt generation + DB persistence
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cortex.db import get_db, init_db

log = logging.getLogger(__name__)

VALID_GENRES = ("adventure", "fantasy", "science", "bedtime", "mystery")
VALID_AGE_GROUPS = ("child", "teen", "adult")

# ── Data structures ─────────────────────────────────────────────────


@dataclass
class StoryOutline:
    title: str
    genre: str
    target_age: str
    characters: list[dict] = field(default_factory=list)  # [{name, description, role}]
    chapters: list[dict] = field(default_factory=list)  # [{title, summary, is_branching}]
    moral: str = ""


# ── Prompt builder ──────────────────────────────────────────────────


class StoryPromptBuilder:
    """Build LLM prompts for story generation.  Does *not* call the LLM."""

    _AGE_GUIDANCE = {
        "child": (
            "Use simple language suitable for ages 4-8. "
            "Keep sentences short and vivid. Avoid anything scary or violent."
        ),
        "teen": (
            "Use engaging language for ages 9-14. "
            "Allow mild tension and complex themes but nothing graphic."
        ),
        "adult": (
            "Use sophisticated language. Mature themes are acceptable "
            "but keep content respectful."
        ),
    }

    def build_outline_prompt(
        self,
        genre: str,
        age_group: str,
        theme: str = "",
        characters: list[str] | None = None,
    ) -> str:
        """Return a prompt that asks the LLM to produce a JSON story outline."""

        characters = characters or []
        age_note = self._AGE_GUIDANCE.get(age_group, self._AGE_GUIDANCE["child"])

        parts = [
            "You are a children's story architect.",
            f"Genre: {genre}.",
            f"Target audience: {age_group}. {age_note}",
        ]
        if theme:
            parts.append(f"Central theme or moral: {theme}.")
        if characters:
            parts.append(f"Include these characters: {', '.join(characters)}.")
        parts.append(
            "Produce a JSON object with keys: title (str), genre (str), "
            "target_age (str), characters (list of {{name, description, role}}), "
            "chapters (list of {{title, summary, is_branching (bool)}}), moral (str)."
        )
        return "\n".join(parts)

    def build_chapter_prompt(
        self,
        outline: StoryOutline,
        chapter_num: int,
        previous_summary: str = "",
        choice_made: str = "",
    ) -> str:
        """Build a prompt that generates the full text of one chapter."""

        if chapter_num < 1 or chapter_num > len(outline.chapters):
            raise ValueError(
                f"chapter_num {chapter_num} out of range "
                f"(1-{len(outline.chapters)})"
            )

        chapter_info = outline.chapters[chapter_num - 1]
        age_note = self._AGE_GUIDANCE.get(
            outline.target_age, self._AGE_GUIDANCE["child"]
        )

        parts = [
            "You are a talented storyteller.",
            f"Story: {outline.title} ({outline.genre}, for {outline.target_age}).",
            f"Age guidance: {age_note}",
            f"Chapter {chapter_num}: {chapter_info.get('title', '')}.",
            f"Chapter outline: {chapter_info.get('summary', '')}.",
        ]
        if outline.characters:
            names = ", ".join(c.get("name", "") for c in outline.characters)
            parts.append(f"Characters: {names}.")
        if previous_summary:
            parts.append(f"Previously: {previous_summary}")
        if choice_made:
            parts.append(f"The listener chose: {choice_made}")
        if outline.moral:
            parts.append(f"Story moral: {outline.moral}")
        parts.append(
            "Write the full chapter text with dialogue. "
            "Format character dialogue as: CharacterName: \"dialogue text\""
        )
        return "\n".join(parts)

    def build_branching_prompt(
        self,
        outline: StoryOutline,
        chapter_num: int,
        context: str = "",
    ) -> str:
        """Build a prompt that generates a chapter plus 2-3 listener choices."""

        if chapter_num < 1 or chapter_num > len(outline.chapters):
            raise ValueError(
                f"chapter_num {chapter_num} out of range "
                f"(1-{len(outline.chapters)})"
            )

        chapter_info = outline.chapters[chapter_num - 1]
        age_note = self._AGE_GUIDANCE.get(
            outline.target_age, self._AGE_GUIDANCE["child"]
        )

        parts = [
            "You are an interactive storyteller.",
            f"Story: {outline.title} ({outline.genre}, for {outline.target_age}).",
            f"Age guidance: {age_note}",
            f"Chapter {chapter_num}: {chapter_info.get('title', '')}.",
            f"Chapter outline: {chapter_info.get('summary', '')}.",
        ]
        if context:
            parts.append(f"Context so far: {context}")
        parts.append(
            "Write the chapter text, then end with exactly 2-3 choices for "
            "the listener. Format choices as a JSON array under the key "
            "'choices', e.g. {\"choices\": [\"Open the door\", \"Run away\"]}."
        )
        return "\n".join(parts)


# ── Generator (DB persistence) ──────────────────────────────────────


class StoryGenerator:
    """Create and persist stories and chapters in the database."""

    def __init__(self) -> None:
        from cortex.stories.library import StoryLibrary

        self.prompt_builder = StoryPromptBuilder()
        self.library = StoryLibrary()

    async def generate_outline(
        self,
        genre: str,
        age_group: str = "child",
        theme: str = "",
        num_chapters: int = 5,
    ) -> StoryOutline:
        """Return a *template* StoryOutline — caller feeds the prompt to an LLM."""

        genre = genre if genre in VALID_GENRES else "adventure"
        age_group = age_group if age_group in VALID_AGE_GROUPS else "child"

        chapters = [
            {"title": f"Chapter {i}", "summary": "", "is_branching": False}
            for i in range(1, num_chapters + 1)
        ]
        return StoryOutline(
            title="",
            genre=genre,
            target_age=age_group,
            characters=[],
            chapters=chapters,
            moral=theme,
        )

    async def create_story(
        self,
        title: str,
        genre: str,
        age_group: str = "child",
        num_chapters: int = 5,
        interactive: bool = False,
    ) -> int:
        """Insert a story record and return story_id."""

        init_db()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO stories (title, genre, target_age_group, total_chapters, "
            "is_interactive) VALUES (?, ?, ?, ?, ?)",
            (title, genre, age_group, num_chapters, int(interactive)),
        )
        conn.commit()
        story_id: int = cur.lastrowid  # type: ignore[assignment]
        log.info("Created story %s (id=%d)", title, story_id)
        return story_id

    async def save_chapter(
        self,
        story_id: int,
        chapter_number: int,
        title: str,
        content: str,
        narrator_voice: str = "default",
        choices: list[str] | None = None,
    ) -> int:
        """Persist a chapter and return chapter_id."""

        choices = choices or []
        init_db()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO story_chapters (story_id, chapter_number, title, content, "
            "narrator_voice, choices) VALUES (?, ?, ?, ?, ?, ?)",
            (story_id, chapter_number, title, content, narrator_voice, json.dumps(choices)),
        )
        conn.commit()
        chapter_id: int = cur.lastrowid  # type: ignore[assignment]
        log.info("Saved chapter %d for story %d", chapter_number, story_id)
        return chapter_id

    async def get_story(self, story_id: int) -> dict | None:
        """Return story dict with chapters and characters, or None."""

        init_db()
        conn = get_db()
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        if row is None:
            return None

        story = dict(row)
        chapters = conn.execute(
            "SELECT * FROM story_chapters WHERE story_id = ? ORDER BY chapter_number",
            (story_id,),
        ).fetchall()
        story["chapters"] = [dict(c) for c in chapters]

        characters = conn.execute(
            "SELECT * FROM story_characters WHERE story_id = ?", (story_id,)
        ).fetchall()
        story["characters"] = [dict(c) for c in characters]
        return story

    async def list_stories(
        self, genre: str = "", age_group: str = ""
    ) -> list[dict]:
        """List stories, optionally filtered by genre / age group."""

        init_db()
        conn = get_db()
        query = "SELECT * FROM stories WHERE 1=1"
        params: list[str] = []
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if age_group:
            query += " AND target_age_group = ?"
            params.append(age_group)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
