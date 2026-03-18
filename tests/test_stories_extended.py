"""Tests for Part 10 Wave 2 — TTS hot-swap, Fish Audio S2, StoryPlugin, StoryAudioGenerator."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.speech.hotswap import (
    GPUSlot,
    HotSwapManager,
    PROVIDER_FISH_S2,
    PROVIDER_KOKORO,
    PROVIDER_ORPHEUS,
    PROVIDER_PIPER,
    PROVIDER_QWEN3_TTS,
    get_hotswap_manager,
    reset_hotswap_manager,
)
from cortex.speech.fish_audio import FishAudioProvider
from cortex.plugins.stories import StoryPlugin
from cortex.stories.audio import StoryAudioGenerator
from cortex.stories.characters import CharacterVoiceSystem
from cortex.stories.generator import StoryGenerator, StoryOutline, StoryPromptBuilder
from cortex.stories.library import StoryLibrary


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "stories_ext_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def conn(db_path):
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


@pytest.fixture()
def manager():
    """Fresh HotSwapManager for each test."""
    reset_hotswap_manager()
    m = HotSwapManager()
    return m


@pytest.fixture()
def fish():
    return FishAudioProvider()


@pytest.fixture()
def plugin(db_path):
    return StoryPlugin()


@pytest.fixture()
def audio_gen(tmp_path):
    return StoryAudioGenerator(cache_dir=tmp_path / "audio_cache")


# ═══════════════════════════════════════════════════════════════════
# HotSwapManager
# ═══════════════════════════════════════════════════════════════════


class TestGPUSlot:
    def test_defaults(self):
        slot = GPUSlot()
        assert slot.gpu_id == "cuda:0"
        assert slot.current_model is None
        assert slot.vram_mb == 8192


class TestHotSwapManager:
    async def test_default_provider_is_qwen3(self, manager):
        assert manager.current_provider == PROVIDER_QWEN3_TTS
        assert not manager.is_swapping

    async def test_request_provider_conversation(self, manager):
        provider = await manager.request_provider(purpose="conversation")
        assert provider == PROVIDER_QWEN3_TTS

    async def test_request_provider_story_triggers_swap(self, manager):
        provider = await manager.request_provider(purpose="story")
        assert provider == PROVIDER_FISH_S2
        assert manager.current_provider == PROVIDER_FISH_S2

    async def test_swap_to_story_mode(self, manager):
        ok = await manager.swap_to_story_mode()
        assert ok is True
        assert manager.current_provider == PROVIDER_FISH_S2

    async def test_swap_to_conversation_mode(self, manager):
        await manager.swap_to_story_mode()
        ok = await manager.swap_to_conversation_mode()
        assert ok is True
        assert manager.current_provider == PROVIDER_QWEN3_TTS

    async def test_swap_idempotent(self, manager):
        await manager.swap_to_story_mode()
        ok = await manager.swap_to_story_mode()
        assert ok is True
        assert manager.current_provider == PROVIDER_FISH_S2

    async def test_conversation_mode_idempotent(self, manager):
        ok = await manager.swap_to_conversation_mode()
        assert ok is True
        assert manager.current_provider == PROVIDER_QWEN3_TTS

    async def test_swap_lifecycle(self, manager):
        """Full cycle: conversation → story → conversation."""
        assert manager.current_provider == PROVIDER_QWEN3_TTS
        await manager.swap_to_story_mode()
        assert manager.current_provider == PROVIDER_FISH_S2
        await manager.swap_to_conversation_mode()
        assert manager.current_provider == PROVIDER_QWEN3_TTS

    async def test_swap_count_increments(self, manager):
        status = await manager.get_status()
        assert status["swap_count"] == 0
        await manager.swap_to_story_mode()
        status = await manager.get_status()
        assert status["swap_count"] == 1
        await manager.swap_to_conversation_mode()
        status = await manager.get_status()
        assert status["swap_count"] == 2

    async def test_lock_prevents_concurrent_swaps(self, manager):
        """Two concurrent swap requests should serialize via the lock."""
        results = await asyncio.gather(
            manager.swap_to_story_mode(),
            manager.swap_to_story_mode(),
        )
        # Both should succeed (one does the swap, other finds it done)
        assert all(results)
        assert manager.current_provider == PROVIDER_FISH_S2

    async def test_fallback_during_swap(self, manager):
        fb = await manager.get_fallback_provider()
        assert fb in (PROVIDER_ORPHEUS, PROVIDER_KOKORO, PROVIDER_PIPER)

    async def test_request_provider_returns_fallback_during_swap(self, manager):
        """If swap in progress, conversation requests get fallback."""
        manager._is_swapping = True
        provider = await manager.request_provider(purpose="conversation")
        assert provider in (PROVIDER_ORPHEUS, PROVIDER_KOKORO, PROVIDER_PIPER)
        manager._is_swapping = False

    async def test_load_failure_rolls_back(self, manager):
        """If loading Fish S2 fails, the previous model is reloaded."""
        call_count = 0

        async def fail_on_fish(provider_id):
            nonlocal call_count
            call_count += 1
            if provider_id == PROVIDER_FISH_S2:
                return False
            return True

        manager._load_fn = fail_on_fish
        ok = await manager.swap_to_story_mode()
        assert ok is False
        # Previous model should be restored
        assert manager.current_provider == PROVIDER_QWEN3_TTS

    async def test_unload_failure_aborts(self, manager):
        async def fail_unload(_):
            return False

        manager._unload_fn = fail_unload
        ok = await manager.swap_to_story_mode()
        assert ok is False
        assert manager.current_provider == PROVIDER_QWEN3_TTS

    async def test_get_status(self, manager):
        status = await manager.get_status()
        assert "gpu_id" in status
        assert "vram_mb" in status
        assert "current_model" in status
        assert "current_provider" in status
        assert "is_swapping" in status
        assert "swap_count" in status

    async def test_fallback_when_no_healthy_providers(self, manager):
        """If all fallbacks are unhealthy, still returns piper."""

        async def all_unhealthy(_):
            return False

        manager._health_fn = all_unhealthy
        fb = await manager.get_fallback_provider()
        assert fb == PROVIDER_PIPER


class TestHotSwapSingleton:
    def test_singleton_returns_same_instance(self):
        reset_hotswap_manager()
        a = get_hotswap_manager()
        b = get_hotswap_manager()
        assert a is b
        reset_hotswap_manager()

    def test_reset_clears_singleton(self):
        reset_hotswap_manager()
        a = get_hotswap_manager()
        reset_hotswap_manager()
        b = get_hotswap_manager()
        assert a is not b
        reset_hotswap_manager()


# ═══════════════════════════════════════════════════════════════════
# FishAudioProvider
# ═══════════════════════════════════════════════════════════════════


class TestFishAudioProvider:
    async def test_synthesize_returns_bytes(self, fish):
        result = await fish.synthesize("Hello world", voice_id="narrator")
        assert isinstance(result, bytes)

    async def test_synthesize_with_style_tags(self, fish):
        result = await fish.synthesize(
            "Exciting!", voice_id="hero", style_tags="[cheerful]",
        )
        assert isinstance(result, bytes)

    async def test_synthesize_dialogue_returns_bytes(self, fish):
        segments = [
            {"speaker": "narrator", "text": "Once upon a time...", "voice_id": "n1", "style": ""},
            {"speaker": "hero", "text": "I shall defeat you!", "voice_id": "h1", "style": "[brave]"},
        ]
        result = await fish.synthesize_dialogue(segments)
        assert isinstance(result, bytes)

    async def test_synthesize_dialogue_empty(self, fish):
        result = await fish.synthesize_dialogue([])
        assert isinstance(result, bytes)

    async def test_health_returns_false(self, fish):
        ok = await fish.health()
        assert ok is False

    def test_provider_id(self, fish):
        assert fish.provider_id == "fish_audio_s2"


# ═══════════════════════════════════════════════════════════════════
# StoryPlugin — Match
# ═══════════════════════════════════════════════════════════════════


class TestStoryPluginMatch:
    async def test_match_tell_me_a_story(self, plugin):
        m = await plugin.match("tell me a story")
        assert m.matched
        assert m.intent == "story_new"

    async def test_match_story_time(self, plugin):
        m = await plugin.match("hey it's story time")
        assert m.matched
        assert m.intent == "story_new"

    async def test_match_read_me_a_story(self, plugin):
        m = await plugin.match("read me a story please")
        assert m.matched
        assert m.intent == "story_new"

    async def test_match_bedtime_story(self, plugin):
        m = await plugin.match("I want a bedtime story")
        assert m.matched
        assert m.intent == "story_new"
        assert m.metadata.get("genre") == "bedtime"

    async def test_match_adventure_story(self, plugin):
        m = await plugin.match("tell me an adventure story")
        assert m.matched
        assert m.intent == "story_new"
        assert m.metadata.get("genre") == "adventure"

    async def test_match_lets_hear_a_story(self, plugin):
        m = await plugin.match("let's hear a story")
        assert m.matched
        assert m.intent == "story_new"

    async def test_match_continue_story(self, plugin):
        m = await plugin.match("continue the story")
        assert m.matched
        assert m.intent == "story_continue"

    async def test_match_what_happens_next(self, plugin):
        m = await plugin.match("what happens next")
        assert m.matched
        assert m.intent == "story_continue"

    async def test_match_next_chapter(self, plugin):
        m = await plugin.match("next chapter please")
        assert m.matched
        assert m.intent == "story_continue"

    async def test_match_choose_option(self, plugin):
        m = await plugin.match("choose option 2")
        assert m.matched
        assert m.intent == "story_choice"
        assert m.metadata.get("choice_index") == 2

    async def test_match_pick_option(self, plugin):
        m = await plugin.match("pick 3")
        assert m.matched
        assert m.intent == "story_choice"
        assert m.metadata.get("choice_index") == 3

    async def test_match_select_option_1(self, plugin):
        m = await plugin.match("select option 1")
        assert m.matched
        assert m.intent == "story_choice"
        assert m.metadata.get("choice_index") == 1

    async def test_match_my_stories(self, plugin):
        m = await plugin.match("my stories")
        assert m.matched
        assert m.intent == "story_list"

    async def test_match_what_stories_do_i_have(self, plugin):
        m = await plugin.match("what stories do I have")
        assert m.matched
        assert m.intent == "story_list"

    async def test_match_list_stories(self, plugin):
        m = await plugin.match("list my stories")
        assert m.matched
        assert m.intent == "story_list"

    async def test_match_specific_story(self, plugin):
        m = await plugin.match("tell me the story about the dragon")
        assert m.matched
        assert m.intent == "story_specific"
        assert "dragon" in m.metadata.get("title_query", "").lower()

    async def test_no_match(self, plugin):
        m = await plugin.match("what is the weather like")
        assert not m.matched

    async def test_no_match_random(self, plugin):
        m = await plugin.match("turn on the lights")
        assert not m.matched

    async def test_match_keep_going(self, plugin):
        m = await plugin.match("keep going")
        assert m.matched
        assert m.intent == "story_continue"

    async def test_match_start_a_new_story(self, plugin):
        m = await plugin.match("start a new story")
        assert m.matched
        assert m.intent == "story_new"

    async def test_match_mystery_genre(self, plugin):
        m = await plugin.match("tell me a mystery story")
        assert m.matched
        assert m.metadata.get("genre") == "mystery"


# ═══════════════════════════════════════════════════════════════════
# StoryPlugin — Handle
# ═══════════════════════════════════════════════════════════════════


class TestStoryPluginHandle:
    @pytest.fixture(autouse=True)
    def _reset_hotswap(self):
        reset_hotswap_manager()
        yield
        reset_hotswap_manager()

    async def test_handle_new_story(self, plugin, db_path):
        m = await plugin.match("tell me a fantasy story")
        result = await plugin.handle("tell me a fantasy story", m, {"user_id": "u1"})
        assert result.success
        assert "story" in result.response.lower()
        assert result.metadata.get("story_id") is not None
        assert result.metadata.get("genre") == "fantasy"

    async def test_handle_list_empty(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        m = CommandMatch(matched=True, intent="story_list")
        result = await plugin.handle("my stories", m, {"user_id": "u1"})
        assert result.success
        assert "no stories in progress" in result.response.lower()

    async def test_handle_list_with_stories(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        # Create a story and save progress
        gen = StoryGenerator()
        sid = await gen.create_story("Test Story", "adventure")
        lib = StoryLibrary()
        await lib.save_progress("u1", sid, 2)

        m = CommandMatch(matched=True, intent="story_list")
        result = await plugin.handle("my stories", m, {"user_id": "u1"})
        assert result.success
        assert "in progress" in result.response.lower()

    async def test_handle_continue_no_story(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        m = CommandMatch(matched=True, intent="story_continue")
        result = await plugin.handle("continue the story", m, {"user_id": "u1"})
        assert not result.success
        assert "don't have any stories" in result.response.lower()

    async def test_handle_continue_with_story(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        gen = StoryGenerator()
        sid = await gen.create_story("Dragon Quest", "fantasy", num_chapters=5)
        await gen.save_chapter(sid, 1, "The Beginning", "Once upon a time...")
        lib = StoryLibrary()
        await lib.save_progress("u1", sid, 1)

        m = CommandMatch(matched=True, intent="story_continue")
        result = await plugin.handle("continue the story", m, {"user_id": "u1"})
        assert result.success
        assert "continuing" in result.response.lower()
        assert result.metadata.get("story_id") == sid

    async def test_handle_choice_no_story(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        m = CommandMatch(
            matched=True, intent="story_choice",
            metadata={"choice_index": 1},
        )
        result = await plugin.handle("choose option 1", m, {"user_id": "u1"})
        assert not result.success

    async def test_handle_choice_with_story(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        gen = StoryGenerator()
        sid = await gen.create_story("Choose Your Path", "adventure", interactive=True)
        await gen.save_chapter(
            sid, 1, "The Fork", "Two paths diverge...",
            choices=["Go left", "Go right"],
        )
        lib = StoryLibrary()
        await lib.save_progress("u1", sid, 1)

        m = CommandMatch(
            matched=True, intent="story_choice",
            entities=["2"], metadata={"choice_index": 2},
        )
        result = await plugin.handle("choose option 2", m, {"user_id": "u1"})
        assert result.success
        assert "option 2" in result.response.lower()
        assert result.metadata.get("choice_made") == 2

    async def test_handle_specific_not_found(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        m = CommandMatch(
            matched=True, intent="story_specific",
            metadata={"title_query": "nonexistent"},
        )
        result = await plugin.handle(
            "tell me the story about nonexistent", m, {"user_id": "u1"},
        )
        assert not result.success
        assert "couldn't find" in result.response.lower()

    async def test_handle_specific_found(self, plugin, db_path):
        from cortex.plugins.base import CommandMatch

        gen = StoryGenerator()
        await gen.create_story("The Brave Dragon", "fantasy")

        m = CommandMatch(
            matched=True, intent="story_specific",
            metadata={"title_query": "dragon"},
        )
        result = await plugin.handle(
            "tell me the story about the dragon", m, {"user_id": "u1"},
        )
        assert result.success
        assert "dragon" in result.response.lower()


# ═══════════════════════════════════════════════════════════════════
# StoryPlugin — Metadata
# ═══════════════════════════════════════════════════════════════════


class TestStoryPluginMeta:
    def test_plugin_id(self, plugin):
        assert plugin.plugin_id == "stories"

    def test_display_name(self, plugin):
        assert plugin.display_name == "Story Time"

    def test_plugin_type(self, plugin):
        assert plugin.plugin_type == "action"

    def test_version(self, plugin):
        assert plugin.version == "1.0.0"

    def test_supports_learning(self, plugin):
        assert plugin.supports_learning is True

    async def test_setup(self, plugin):
        ok = await plugin.setup()
        assert ok is True

    async def test_health(self, plugin):
        ok = await plugin.health()
        assert ok is True


# ═══════════════════════════════════════════════════════════════════
# StoryAudioGenerator
# ═══════════════════════════════════════════════════════════════════


class TestStoryAudioGenerator:
    @pytest.fixture(autouse=True)
    def _reset_hotswap(self):
        reset_hotswap_manager()
        yield
        reset_hotswap_manager()

    async def test_generate_chapter_audio(self, audio_gen, db_path):
        gen = StoryGenerator()
        sid = await gen.create_story("Audio Story", "fantasy")
        ch_id = await gen.save_chapter(
            sid, 1, "Opening",
            'The forest was dark.\nHero: "I must find the sword!"\nThe wind howled.',
        )

        # Assign a character voice
        voice_sys = CharacterVoiceSystem()
        await voice_sys.assign_voice(sid, "Hero", archetype="hero")

        path = await audio_gen.generate_chapter_audio(sid, ch_id)
        assert path  # non-empty path returned
        assert Path(path).exists()

    async def test_generate_chapter_no_story(self, audio_gen, db_path):
        path = await audio_gen.generate_chapter_audio(9999, 9999)
        assert path == ""

    async def test_generate_chapter_no_chapter(self, audio_gen, db_path):
        gen = StoryGenerator()
        sid = await gen.create_story("Missing Chapter", "adventure")
        path = await audio_gen.generate_chapter_audio(sid, 9999)
        assert path == ""

    async def test_get_cached_audio_miss(self, audio_gen):
        result = await audio_gen.get_cached_audio(999)
        assert result is None

    async def test_get_cached_audio_hit(self, audio_gen, db_path):
        gen = StoryGenerator()
        sid = await gen.create_story("Cached Story", "bedtime")
        ch_id = await gen.save_chapter(sid, 1, "Chapter 1", "Once upon a time...")

        # Generate so it caches
        await audio_gen.generate_chapter_audio(sid, ch_id)
        cached = await audio_gen.get_cached_audio(ch_id)
        # Stub produces empty audio so file is zero-length; get_cached_audio
        # checks st_size > 0, so this correctly returns None for stubs.
        # That's expected behaviour.

    async def test_invalidate_cache(self, audio_gen, tmp_path):
        # Create fake cache entry
        story_dir = audio_gen._cache_dir / "story_42"
        story_dir.mkdir(parents=True)
        fake_file = story_dir / "story42_ch1.pcm"
        fake_file.write_bytes(b"\x00" * 100)

        await audio_gen.invalidate_cache(42)
        assert not story_dir.exists()

    async def test_invalidate_cache_nonexistent(self, audio_gen):
        # Should not raise
        await audio_gen.invalidate_cache(9999)

    async def test_audio_path_format(self, audio_gen):
        p = audio_gen._audio_path(5, 10)
        assert "story_5" in str(p)
        assert "story5_ch10.pcm" in str(p)


# ═══════════════════════════════════════════════════════════════════
# Integration: story creation → chapter → audio caching
# ═══════════════════════════════════════════════════════════════════


class TestStoryIntegration:
    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_hotswap_manager()
        yield
        reset_hotswap_manager()

    async def test_full_story_lifecycle(self, db_path, tmp_path):
        """Create story → save chapter → generate audio → cache → swap back."""
        # 1. Create story
        gen = StoryGenerator()
        builder = StoryPromptBuilder()
        outline = await gen.generate_outline(genre="adventure", age_group="child")
        sid = await gen.create_story(
            outline.title, "adventure", "child",
            num_chapters=len(outline.chapters), interactive=True,
        )
        assert sid > 0

        # 2. Save a chapter with dialogue
        ch_id = await gen.save_chapter(
            sid, 1, "The Quest Begins",
            (
                "The morning sun painted the sky gold.\n"
                'Hero: "Today is the day!"\n'
                'Wizard: "Be careful, young one."\n'
                "They set off into the unknown."
            ),
            choices=["Take the mountain path", "Follow the river"],
        )
        assert ch_id > 0

        # 3. Assign character voices
        voice_sys = CharacterVoiceSystem()
        await voice_sys.assign_voice(sid, "Hero", archetype="hero")
        await voice_sys.assign_voice(sid, "Wizard", archetype="wise_elder")

        # 4. Parse dialogue
        story = await gen.get_story(sid)
        chapter_content = story["chapters"][0]["content"]
        segments = voice_sys.parse_dialogue(chapter_content)
        assert len(segments) >= 3  # narrator + 2 dialogue lines + narrator

        # 5. Generate audio
        audio_gen = StoryAudioGenerator(cache_dir=tmp_path / "audio")
        path = await audio_gen.generate_chapter_audio(sid, ch_id)
        assert path != ""

        # 6. Verify hot-swap manager returned to conversation mode
        manager = get_hotswap_manager()
        assert manager.current_provider == PROVIDER_QWEN3_TTS

        # 7. Save progress
        lib = StoryLibrary()
        await lib.save_progress("test_user", sid, 1, choice_index=1)
        progress = await lib.get_progress("test_user", sid)
        assert progress is not None
        assert progress["current_chapter"] == 1

    async def test_plugin_to_audio_flow(self, db_path, tmp_path):
        """Plugin match → handle → hot-swap coordination."""
        plugin = StoryPlugin()
        await plugin.setup()

        # Match a new story request
        m = await plugin.match("tell me an adventure story")
        assert m.matched
        assert m.intent == "story_new"

        # Handle it
        result = await plugin.handle(
            "tell me an adventure story", m,
            {"user_id": "u1", "age_group": "child"},
        )
        assert result.success
        assert result.metadata.get("story_id") is not None


# ═══════════════════════════════════════════════════════════════════
# BUILTIN_PLUGINS registration
# ═══════════════════════════════════════════════════════════════════


class TestPluginRegistration:
    def test_stories_in_builtin_plugins(self):
        from cortex.plugins.loader import BUILTIN_PLUGINS

        assert "stories" in BUILTIN_PLUGINS
        assert BUILTIN_PLUGINS["stories"] == "cortex.plugins.stories:StoryPlugin"

    def test_import_story_plugin(self):
        from cortex.plugins.loader import _import_plugin_class

        cls = _import_plugin_class("cortex.plugins.stories:StoryPlugin")
        assert cls is StoryPlugin
