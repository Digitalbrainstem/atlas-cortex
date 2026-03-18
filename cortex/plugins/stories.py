"""Interactive story plugin — Layer 2 plugin for Atlas Cortex.

Handles story commands: start, continue, list, interactive choices, and
audio coordination via the TTS hot-swap manager.

Owner: cortex.plugins
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Match patterns
# ---------------------------------------------------------------------------

# New story triggers
_NEW_STORY_RE = re.compile(
    r"\b(?:tell\s+(?:me\s+)?a\s+(?:\w+\s+)?story"
    r"|story\s+time"
    r"|read\s+(?:me\s+)?a\s+(?:\w+\s+)?story"
    r"|bedtime\s+story"
    r"|adventure\s+story"
    r"|let'?s?\s+hear\s+a\s+story"
    r"|start\s+a\s+(?:new\s+)?story)\b",
    re.IGNORECASE,
)

# Continue / next chapter
_CONTINUE_RE = re.compile(
    r"\b(?:continue\s+(?:the\s+)?story"
    r"|what\s+happens\s+next"
    r"|next\s+chapter"
    r"|keep\s+(?:going|reading))\b",
    re.IGNORECASE,
)

# Interactive choice
_CHOICE_RE = re.compile(
    r"\b(?:choose|pick|select|go\s+with)\s+(?:option\s+)?(\d+)\b",
    re.IGNORECASE,
)

# List stories
_LIST_RE = re.compile(
    r"\b(?:my\s+stories"
    r"|what\s+stories\s+(?:do\s+I\s+have|are\s+there)"
    r"|list\s+(?:my\s+)?stories"
    r"|show\s+(?:my\s+)?stories)\b",
    re.IGNORECASE,
)

# Specific title: "tell me the story about the dragon"
_SPECIFIC_RE = re.compile(
    r"\b(?:tell\s+(?:me\s+)?the\s+story\s+(?:about|of|called)\s+)(.+)",
    re.IGNORECASE,
)

# Genre extraction from free-form request
_GENRE_RE = re.compile(
    r"\b(adventure|fantasy|science|bedtime|mystery)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Story Plugin
# ---------------------------------------------------------------------------

class StoryPlugin(CortexPlugin):
    """Interactive story-time plugin (Layer 2).

    Supports new stories, continuation, branching choices, listing,
    and TTS audio coordination via the HotSwapManager.
    """

    plugin_id: str = "stories"
    display_name: str = "Story Time"
    plugin_type: str = "action"
    supports_learning: bool = True
    version: str = "1.0.0"
    author: str = "Atlas"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, config: dict | None = None) -> bool:  # type: ignore[override]
        logger.info("StoryPlugin setup complete")
        return True

    async def health(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Match
    # ------------------------------------------------------------------

    async def match(self, message: str, context: dict | None = None) -> CommandMatch:  # type: ignore[override]
        context = context or {}

        # Specific title
        m = _SPECIFIC_RE.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="story_specific",
                entities=[m.group(1).strip()],
                confidence=0.95,
                metadata={"title_query": m.group(1).strip()},
            )

        # Interactive choice
        m = _CHOICE_RE.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="story_choice",
                entities=[m.group(1)],
                confidence=0.95,
                metadata={"choice_index": int(m.group(1))},
            )

        # Continue
        if _CONTINUE_RE.search(message):
            return CommandMatch(
                matched=True,
                intent="story_continue",
                confidence=0.90,
            )

        # List
        if _LIST_RE.search(message):
            return CommandMatch(
                matched=True,
                intent="story_list",
                confidence=0.90,
            )

        # New story
        if _NEW_STORY_RE.search(message):
            genre = ""
            gm = _GENRE_RE.search(message)
            if gm:
                genre = gm.group(1).lower()
            return CommandMatch(
                matched=True,
                intent="story_new",
                confidence=0.90,
                metadata={"genre": genre},
            )

        return CommandMatch(matched=False)

    # ------------------------------------------------------------------
    # Handle
    # ------------------------------------------------------------------

    async def handle(self, message: str, match: CommandMatch, context: dict | None = None) -> CommandResult:  # type: ignore[override]
        context = context or {}
        intent = match.intent
        user_id = context.get("user_id", "default")
        age_group = context.get("age_group", "child")

        try:
            if intent == "story_new":
                return await self._handle_new(match, user_id, age_group)
            elif intent == "story_continue":
                return await self._handle_continue(user_id)
            elif intent == "story_choice":
                return await self._handle_choice(match, user_id)
            elif intent == "story_list":
                return await self._handle_list(user_id)
            elif intent == "story_specific":
                return await self._handle_specific(match, user_id)
            else:
                return CommandResult(
                    success=False,
                    response="I'm not sure what story action you'd like.",
                )
        except Exception:
            logger.exception("Error handling story intent=%s", intent)
            return CommandResult(
                success=False,
                response="Something went wrong with story time. Let's try again!",
            )

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_new(
        self, match: CommandMatch, user_id: str, age_group: str,
    ) -> CommandResult:
        from cortex.stories.generator import StoryGenerator, StoryPromptBuilder

        genre = match.metadata.get("genre", "")
        if not genre:
            genre = _default_genre_for_age(age_group)

        builder = StoryPromptBuilder()
        prompt = builder.build_outline_prompt(genre=genre, age_group=age_group)

        gen = StoryGenerator()
        outline = await gen.generate_outline(genre=genre, age_group=age_group)
        story_id = await gen.create_story(
            title=outline.title,
            genre=genre,
            age_group=age_group,
            num_chapters=len(outline.chapters),
            interactive=True,
        )

        # Coordinate TTS hot-swap if Fish Audio S2 is available
        await self._request_story_audio()

        response = (
            f"📖 Starting a new {genre} story: **{outline.title}**\n\n"
            f"This story has {len(outline.chapters)} chapters."
        )
        if outline.characters:
            names = ", ".join(c.get("name", "?") for c in outline.characters)
            response += f"\nCharacters: {names}"
        response += "\n\nSay **\"next chapter\"** to begin!"

        return CommandResult(
            success=True,
            response=response,
            metadata={
                "story_id": story_id,
                "genre": genre,
                "prompt": prompt,
                "outline": {
                    "title": outline.title,
                    "chapters": outline.chapters,
                    "characters": outline.characters,
                },
            },
        )

    async def _handle_continue(self, user_id: str) -> CommandResult:
        from cortex.stories.library import StoryLibrary
        from cortex.stories.generator import StoryGenerator, StoryPromptBuilder

        lib = StoryLibrary()
        in_progress = await lib.list_in_progress(user_id)
        if not in_progress:
            return CommandResult(
                success=False,
                response=(
                    "You don't have any stories in progress. "
                    'Say "tell me a story" to start one!'
                ),
            )

        # Continue the most recently active story
        latest = in_progress[0]
        story_id = latest["id"]
        progress = await lib.get_progress(user_id, story_id)
        chapter_num = progress["current_chapter"] if progress else 1

        gen = StoryGenerator()
        story = await gen.get_story(story_id)
        if not story:
            return CommandResult(
                success=False,
                response="I couldn't find that story. Let's start a new one!",
            )

        builder = StoryPromptBuilder()
        # Build a simple outline-like object for the prompt builder
        from cortex.stories.generator import StoryOutline

        outline = StoryOutline(
            title=story["title"],
            genre=story.get("genre", "adventure"),
            target_age=story.get("target_age_group", "child"),
            characters=story.get("characters", []),
            chapters=story.get("chapters", []),
        )
        prompt = builder.build_chapter_prompt(outline, chapter_num)

        await self._request_story_audio()

        return CommandResult(
            success=True,
            response=(
                f"📖 Continuing **{story['title']}** — Chapter {chapter_num}\n\n"
                "Let me generate the next part of the story..."
            ),
            metadata={
                "story_id": story_id,
                "chapter_number": chapter_num,
                "prompt": prompt,
            },
        )

    async def _handle_choice(
        self, match: CommandMatch, user_id: str,
    ) -> CommandResult:
        from cortex.stories.library import StoryLibrary
        from cortex.stories.generator import StoryGenerator, StoryPromptBuilder, StoryOutline

        choice_idx = match.metadata.get("choice_index", 1)

        lib = StoryLibrary()
        in_progress = await lib.list_in_progress(user_id)
        if not in_progress:
            return CommandResult(
                success=False,
                response="You don't have a story in progress to make a choice for.",
            )

        latest = in_progress[0]
        story_id = latest["id"]
        progress = await lib.get_progress(user_id, story_id)
        chapter_num = progress["current_chapter"] if progress else 1

        # Save the choice
        await lib.save_progress(user_id, story_id, chapter_num, choice_index=choice_idx)

        gen = StoryGenerator()
        story = await gen.get_story(story_id)
        if not story:
            return CommandResult(
                success=False,
                response="I couldn't find that story.",
            )

        builder = StoryPromptBuilder()
        outline = StoryOutline(
            title=story["title"],
            genre=story.get("genre", "adventure"),
            target_age=story.get("target_age_group", "child"),
            characters=story.get("characters", []),
            chapters=story.get("chapters", []),
        )
        next_chapter = min(chapter_num + 1, len(outline.chapters) or 1)
        prompt = builder.build_chapter_prompt(
            outline, next_chapter, choice_made=f"Option {choice_idx}",
        )

        return CommandResult(
            success=True,
            response=(
                f"You chose **option {choice_idx}**! "
                "Let me continue the story with your choice..."
            ),
            metadata={
                "story_id": story_id,
                "chapter_number": chapter_num + 1,
                "choice_made": choice_idx,
                "prompt": prompt,
            },
        )

    async def _handle_list(self, user_id: str) -> CommandResult:
        from cortex.stories.library import StoryLibrary
        from cortex.stories.generator import StoryGenerator

        lib = StoryLibrary()
        gen = StoryGenerator()

        in_progress = await lib.list_in_progress(user_id)
        favourites = await lib.get_favorites(user_id)
        all_stories = await gen.list_stories()

        lines: list[str] = ["📚 **Your Stories**\n"]

        if in_progress:
            lines.append("**In Progress:**")
            for p in in_progress:
                lines.append(f"  • Story #{p['id']} — Chapter {p['current_chapter']}")
        else:
            lines.append("No stories in progress.")

        if favourites:
            lines.append("\n**Completed:**")
            for f in favourites:
                lines.append(f"  ★ Story #{f['id']}")

        if all_stories:
            lines.append(f"\n{len(all_stories)} total stories in the library.")

        return CommandResult(success=True, response="\n".join(lines))

    async def _handle_specific(
        self, match: CommandMatch, user_id: str,
    ) -> CommandResult:
        from cortex.stories.generator import StoryGenerator

        title_query = match.metadata.get("title_query", "")
        gen = StoryGenerator()
        all_stories = await gen.list_stories()

        # Simple title search
        found = None
        for s in all_stories:
            if title_query.lower() in s.get("title", "").lower():
                found = s
                break

        if not found:
            return CommandResult(
                success=False,
                response=f'I couldn\'t find a story about "{title_query}". '
                         'Would you like me to create one?',
            )

        return CommandResult(
            success=True,
            response=(
                f"📖 Found **{found['title']}** ({found.get('genre', 'adventure')})\n"
                f"Say **\"continue the story\"** to pick up where you left off!"
            ),
            metadata={"story_id": found.get("id")},
        )

    # ------------------------------------------------------------------
    # Audio coordination
    # ------------------------------------------------------------------

    async def _request_story_audio(self) -> None:
        """Request TTS hot-swap to Fish Audio S2 for story narration."""
        try:
            from cortex.speech.hotswap import get_hotswap_manager

            manager = get_hotswap_manager()
            provider = await manager.request_provider(purpose="story")
            logger.info("Story audio provider: %s", provider)
        except Exception:
            logger.debug("Hot-swap not available; story will use default TTS")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_genre_for_age(age_group: str) -> str:
    """Pick a sensible default genre based on age group."""
    return {
        "child": "adventure",
        "teen": "mystery",
        "adult": "fantasy",
    }.get(age_group, "adventure")
