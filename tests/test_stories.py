"""Tests for the Story Time Engine — generator, characters, library, DB."""
from __future__ import annotations

import json
import sqlite3

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.stories.characters import CharacterVoiceSystem, CharacterVoice
from cortex.stories.generator import StoryGenerator, StoryOutline, StoryPromptBuilder
from cortex.stories.library import StoryLibrary


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "stories_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def conn(db_path):
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── DB table CRUD ───────────────────────────────────────────────────


class TestDBTables:
    def test_stories_table_exists(self, conn):
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "stories" in tables
        assert "story_chapters" in tables
        assert "story_characters" in tables
        assert "story_progress" in tables

    def test_stories_crud(self, conn):
        conn.execute(
            "INSERT INTO stories (title, genre, target_age_group) "
            "VALUES ('Test Story', 'adventure', 'child')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM stories WHERE title = 'Test Story'").fetchone()
        assert row is not None
        assert row["genre"] == "adventure"
        assert row["target_age_group"] == "child"
        assert row["is_interactive"] == 0
        assert row["parent_approved"] == 1

    def test_story_chapters_fk(self, conn):
        conn.execute(
            "INSERT INTO stories (id, title) VALUES (99, 'FK Test')"
        )
        conn.execute(
            "INSERT INTO story_chapters (story_id, chapter_number, content) "
            "VALUES (99, 1, 'Once upon a time...')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM story_chapters WHERE story_id = 99"
        ).fetchone()
        assert row["content"] == "Once upon a time..."
        assert row["narrator_voice"] == "default"

    def test_story_chapters_cascade_delete(self, conn):
        conn.execute("INSERT INTO stories (id, title) VALUES (100, 'Delete Me')")
        conn.execute(
            "INSERT INTO story_chapters (story_id, chapter_number, content) "
            "VALUES (100, 1, 'text')"
        )
        conn.commit()
        conn.execute("DELETE FROM stories WHERE id = 100")
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM story_chapters WHERE story_id = 100"
        ).fetchall()
        assert len(rows) == 0

    def test_story_characters_crud(self, conn):
        conn.execute("INSERT INTO stories (id, title) VALUES (101, 'Char Test')")
        conn.execute(
            "INSERT INTO story_characters (story_id, name, voice_id, voice_style) "
            "VALUES (101, 'Gandalf', 'wizard_v1', '[wise, old]')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM story_characters WHERE story_id = 101"
        ).fetchone()
        assert row["name"] == "Gandalf"
        assert row["voice_style"] == "[wise, old]"

    def test_story_progress_unique(self, conn):
        conn.execute("INSERT INTO stories (id, title) VALUES (102, 'Progress Test')")
        conn.execute(
            "INSERT INTO story_progress (user_id, story_id, current_chapter) "
            "VALUES ('user1', 102, 1)"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO story_progress (user_id, story_id, current_chapter) "
                "VALUES ('user1', 102, 2)"
            )


# ── StoryPromptBuilder ──────────────────────────────────────────────


class TestStoryPromptBuilder:
    def setup_method(self):
        self.builder = StoryPromptBuilder()

    def test_outline_prompt_includes_genre(self):
        prompt = self.builder.build_outline_prompt("fantasy", "child")
        assert "fantasy" in prompt.lower()
        assert "child" in prompt.lower()
        assert "JSON" in prompt

    def test_outline_prompt_with_theme_and_characters(self):
        prompt = self.builder.build_outline_prompt(
            "adventure", "teen", theme="friendship", characters=["Alice", "Bob"]
        )
        assert "friendship" in prompt
        assert "Alice" in prompt
        assert "Bob" in prompt

    def test_chapter_prompt_includes_context(self):
        outline = StoryOutline(
            title="Magic Forest",
            genre="fantasy",
            target_age="child",
            characters=[{"name": "Ella", "description": "brave girl", "role": "hero"}],
            chapters=[
                {"title": "The Beginning", "summary": "Ella finds a map", "is_branching": False}
            ],
            moral="courage",
        )
        prompt = self.builder.build_chapter_prompt(
            outline, 1, previous_summary="Ella woke up."
        )
        assert "Magic Forest" in prompt
        assert "Ella" in prompt
        assert "Ella woke up" in prompt
        assert "courage" in prompt

    def test_chapter_prompt_with_choice(self):
        outline = StoryOutline(
            title="Quest",
            genre="adventure",
            target_age="child",
            chapters=[{"title": "Fork", "summary": "A choice", "is_branching": True}],
        )
        prompt = self.builder.build_chapter_prompt(
            outline, 1, choice_made="Go left"
        )
        assert "Go left" in prompt

    def test_chapter_prompt_invalid_number(self):
        outline = StoryOutline(
            title="X", genre="adventure", target_age="child", chapters=[]
        )
        with pytest.raises(ValueError, match="out of range"):
            self.builder.build_chapter_prompt(outline, 1)

    def test_branching_prompt(self):
        outline = StoryOutline(
            title="Choose",
            genre="mystery",
            target_age="teen",
            chapters=[
                {"title": "Crossroads", "summary": "Two paths", "is_branching": True}
            ],
        )
        prompt = self.builder.build_branching_prompt(outline, 1, context="It was dark.")
        assert "interactive" in prompt.lower()
        assert "choices" in prompt.lower()
        assert "It was dark." in prompt

    def test_branching_prompt_invalid_number(self):
        outline = StoryOutline(
            title="X", genre="adventure", target_age="child", chapters=[]
        )
        with pytest.raises(ValueError, match="out of range"):
            self.builder.build_branching_prompt(outline, 1)


# ── StoryGenerator ──────────────────────────────────────────────────


class TestStoryGenerator:
    @pytest.fixture(autouse=True)
    def _setup(self, db_path):
        self.gen = StoryGenerator()

    async def test_generate_outline(self):
        outline = await self.gen.generate_outline("fantasy", num_chapters=3)
        assert isinstance(outline, StoryOutline)
        assert outline.genre == "fantasy"
        assert outline.target_age == "child"
        assert len(outline.chapters) == 3

    async def test_generate_outline_invalid_genre(self):
        outline = await self.gen.generate_outline("horror")
        assert outline.genre == "adventure"  # falls back

    async def test_create_story(self):
        sid = await self.gen.create_story("Test", "fantasy", num_chapters=3)
        assert isinstance(sid, int)
        assert sid > 0

    async def test_save_chapter(self):
        sid = await self.gen.create_story("Ch Test", "adventure")
        cid = await self.gen.save_chapter(sid, 1, "Opening", "Once upon a time...")
        assert isinstance(cid, int)
        assert cid > 0

    async def test_save_chapter_with_choices(self):
        sid = await self.gen.create_story("Interactive", "mystery", interactive=True)
        cid = await self.gen.save_chapter(
            sid, 1, "Fork", "Two paths ahead.", choices=["Go left", "Go right"]
        )
        story = await self.gen.get_story(sid)
        ch = story["chapters"][0]
        assert json.loads(ch["choices"]) == ["Go left", "Go right"]

    async def test_get_story(self):
        sid = await self.gen.create_story("Full", "fantasy", num_chapters=2)
        await self.gen.save_chapter(sid, 1, "Ch1", "First chapter text")
        await self.gen.save_chapter(sid, 2, "Ch2", "Second chapter text")
        story = await self.gen.get_story(sid)
        assert story is not None
        assert story["title"] == "Full"
        assert len(story["chapters"]) == 2
        assert len(story["characters"]) == 0

    async def test_get_story_missing(self):
        result = await self.gen.get_story(99999)
        assert result is None

    async def test_list_stories(self):
        await self.gen.create_story("A", "fantasy")
        await self.gen.create_story("B", "adventure")
        all_stories = await self.gen.list_stories()
        assert len(all_stories) == 2
        fantasy = await self.gen.list_stories(genre="fantasy")
        assert len(fantasy) == 1
        assert fantasy[0]["title"] == "A"

    async def test_list_stories_by_age(self):
        await self.gen.create_story("Kid Story", "adventure", age_group="child")
        await self.gen.create_story("Teen Story", "mystery", age_group="teen")
        kids = await self.gen.list_stories(age_group="child")
        assert len(kids) == 1
        assert kids[0]["title"] == "Kid Story"


# ── CharacterVoiceSystem ────────────────────────────────────────────


class TestCharacterVoiceSystem:
    @pytest.fixture(autouse=True)
    def _setup(self, db_path):
        self.voices = CharacterVoiceSystem()
        self.gen = StoryGenerator()

    async def test_assign_voice_with_archetype(self):
        sid = await self.gen.create_story("Voice Test", "fantasy")
        cid = await self.voices.assign_voice(sid, "Merlin", archetype="wise_elder")
        assert cid > 0
        voices = await self.voices.get_voices(sid)
        assert len(voices) == 1
        assert voices[0].name == "Merlin"
        assert "wise" in voices[0].voice_style

    async def test_assign_voice_custom(self):
        sid = await self.gen.create_story("Custom Voice", "adventure")
        cid = await self.voices.assign_voice(
            sid, "Robot Bob", voice_id="robo_v2", voice_style="[monotone, friendly]"
        )
        voices = await self.voices.get_voices(sid)
        assert voices[0].voice_id == "robo_v2"
        assert voices[0].voice_style == "[monotone, friendly]"

    async def test_get_voice_for_line_exists(self):
        sid = await self.gen.create_story("Line Test", "fantasy")
        await self.voices.assign_voice(sid, "Hero", archetype="hero")
        voice = await self.voices.get_voice_for_line(sid, "Hero")
        assert voice.name == "Hero"
        assert "brave" in voice.voice_style

    async def test_get_voice_for_line_fallback(self):
        sid = await self.gen.create_story("Fallback Test", "fantasy")
        voice = await self.voices.get_voice_for_line(sid, "Unknown Character")
        assert voice.name == "narrator"
        assert "calm" in voice.voice_style

    async def test_get_voices_empty(self):
        sid = await self.gen.create_story("Empty Voices", "adventure")
        voices = await self.voices.get_voices(sid)
        assert voices == []

    def test_archetype_keys(self):
        expected = {"narrator", "hero", "villain", "wise_elder", "fairy", "animal", "robot"}
        assert set(CharacterVoiceSystem.ARCHETYPES.keys()) == expected


class TestParseDialogue:
    def setup_method(self):
        self.voices = CharacterVoiceSystem()

    def test_narrator_only(self):
        text = "The sun rose over the hill. Birds sang."
        segments = self.voices.parse_dialogue(text)
        assert len(segments) == 1
        assert segments[0]["speaker"] == "narrator"
        assert segments[0]["is_dialogue"] is False

    def test_dialogue_and_narrator(self):
        text = (
            "The knight approached the gate.\n"
            'Knight: "Who goes there?"\n'
            "The guard looked up.\n"
            'Guard: "State your name!"'
        )
        segments = self.voices.parse_dialogue(text)
        assert len(segments) == 4
        assert segments[0]["speaker"] == "narrator"
        assert segments[1]["speaker"] == "Knight"
        assert segments[1]["is_dialogue"] is True
        assert segments[1]["text"] == "Who goes there?"
        assert segments[2]["speaker"] == "narrator"
        assert segments[3]["speaker"] == "Guard"

    def test_consecutive_dialogue(self):
        text = (
            'Alice: "Hello!"\n'
            'Bob: "Hi there!"'
        )
        segments = self.voices.parse_dialogue(text)
        assert len(segments) == 2
        assert segments[0]["speaker"] == "Alice"
        assert segments[1]["speaker"] == "Bob"

    def test_empty_text(self):
        segments = self.voices.parse_dialogue("")
        assert segments == []

    def test_whitespace_only(self):
        segments = self.voices.parse_dialogue("   \n  ")
        assert segments == []


# ── StoryLibrary ────────────────────────────────────────────────────


class TestStoryLibrary:
    @pytest.fixture(autouse=True)
    def _setup(self, db_path):
        self.lib = StoryLibrary()
        self.gen = StoryGenerator()

    async def test_save_and_get_progress(self):
        sid = await self.gen.create_story("Progress", "adventure")
        await self.lib.save_progress("user1", sid, 2)
        prog = await self.lib.get_progress("user1", sid)
        assert prog is not None
        assert prog["current_chapter"] == 2
        assert prog["completed"] == 0

    async def test_save_progress_with_choice(self):
        sid = await self.gen.create_story("Choice Progress", "mystery")
        await self.lib.save_progress("user1", sid, 1, choice_index=0)
        await self.lib.save_progress("user1", sid, 2, choice_index=1)
        prog = await self.lib.get_progress("user1", sid)
        assert prog["current_chapter"] == 2
        assert prog["choices_made"] == [0, 1]

    async def test_get_progress_missing(self):
        prog = await self.lib.get_progress("nobody", 99999)
        assert prog is None

    async def test_mark_complete(self):
        sid = await self.gen.create_story("Complete Me", "fantasy")
        await self.lib.save_progress("user1", sid, 1)
        await self.lib.mark_complete("user1", sid)
        prog = await self.lib.get_progress("user1", sid)
        assert prog["completed"] == 1

    async def test_mark_complete_no_prior_progress(self):
        sid = await self.gen.create_story("Complete Direct", "adventure")
        await self.lib.mark_complete("user1", sid)
        prog = await self.lib.get_progress("user1", sid)
        assert prog is not None
        assert prog["completed"] == 1

    async def test_get_favorites(self):
        sid = await self.gen.create_story("Fav Story", "fantasy")
        await self.lib.mark_complete("user1", sid)
        favs = await self.lib.get_favorites("user1")
        assert len(favs) == 1
        assert favs[0]["title"] == "Fav Story"

    async def test_list_in_progress(self):
        sid1 = await self.gen.create_story("In Progress 1", "adventure")
        sid2 = await self.gen.create_story("In Progress 2", "fantasy")
        await self.lib.save_progress("user1", sid1, 2)
        await self.lib.save_progress("user1", sid2, 1)
        await self.lib.mark_complete("user1", sid2)
        in_prog = await self.lib.list_in_progress("user1")
        assert len(in_prog) == 1
        assert in_prog[0]["title"] == "In Progress 1"

    async def test_get_recommendations(self):
        sid1 = await self.gen.create_story("Rec 1", "fantasy", age_group="child")
        sid2 = await self.gen.create_story("Rec 2", "adventure", age_group="child")
        sid3 = await self.gen.create_story("Teen Story", "mystery", age_group="teen")
        await self.lib.save_progress("user1", sid1, 1)
        recs = await self.lib.get_recommendations("user1", "child")
        assert len(recs) == 1
        assert recs[0]["title"] == "Rec 2"

    async def test_get_recommendations_excludes_unapproved(self):
        sid = await self.gen.create_story("Unapproved", "adventure", age_group="child")
        conn = get_db()
        conn.execute("UPDATE stories SET parent_approved = 0 WHERE id = ?", (sid,))
        conn.commit()
        recs = await self.lib.get_recommendations("user1", "child")
        assert len(recs) == 0


# ── Edge Cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.fixture(autouse=True)
    def _setup(self, db_path):
        self.gen = StoryGenerator()
        self.voices = CharacterVoiceSystem()

    async def test_empty_story_no_chapters(self):
        sid = await self.gen.create_story("Empty", "adventure")
        story = await self.gen.get_story(sid)
        assert story["chapters"] == []
        assert story["characters"] == []

    async def test_story_with_characters_no_chapters(self):
        sid = await self.gen.create_story("Chars Only", "fantasy")
        await self.voices.assign_voice(sid, "Alice", archetype="hero")
        story = await self.gen.get_story(sid)
        assert len(story["characters"]) == 1
        assert story["chapters"] == []

    async def test_multiple_characters_same_story(self):
        sid = await self.gen.create_story("Multi", "fantasy")
        await self.voices.assign_voice(sid, "Hero", archetype="hero")
        await self.voices.assign_voice(sid, "Villain", archetype="villain")
        await self.voices.assign_voice(sid, "Elder", archetype="wise_elder")
        voices = await self.voices.get_voices(sid)
        assert len(voices) == 3
        names = {v.name for v in voices}
        assert names == {"Hero", "Villain", "Elder"}

    async def test_list_stories_no_results(self):
        result = await self.gen.list_stories(genre="nonexistent")
        assert result == []
