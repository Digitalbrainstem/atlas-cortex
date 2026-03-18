"""Story audio pre-generation — chapter-level audio caching.

Parses chapter text into narrator + character dialogue segments,
coordinates TTS hot-swap to Fish Audio S2, generates audio for each
segment, concatenates, and caches the result.

Owner: cortex.stories
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Default cache directory
_CACHE_DIR = Path(
    os.environ.get("STORY_AUDIO_CACHE", tempfile.gettempdir())
) / "atlas_story_audio"


class StoryAudioGenerator:
    """Pre-generate all audio segments for a story chapter."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or _CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_chapter_audio(
        self, story_id: int, chapter_id: int,
    ) -> str:
        """Generate audio for all dialogue segments in a chapter.

        Steps
        -----
        1. Parse chapter text into segments (narrator + characters).
        2. Request hot-swap to Fish Audio S2.
        3. Generate each segment with character voice.
        4. Concatenate and cache.
        5. Swap back to Qwen3-TTS.

        Returns
        -------
        str
            Path to the cached audio file.
        """
        from cortex.stories.characters import CharacterVoiceSystem
        from cortex.stories.generator import StoryGenerator
        from cortex.speech.hotswap import get_hotswap_manager
        from cortex.speech.fish_audio import FishAudioProvider

        # 1 — Fetch chapter content
        gen = StoryGenerator()
        story = await gen.get_story(story_id)
        if not story:
            logger.error("Story %d not found", story_id)
            return ""

        chapter = None
        for ch in story.get("chapters", []):
            if ch.get("id") == chapter_id:
                chapter = ch
                break
        if not chapter:
            logger.error("Chapter %d not found in story %d", chapter_id, story_id)
            return ""

        content = chapter.get("content", "")
        if not content:
            logger.warning("Chapter %d has no content", chapter_id)
            return ""

        # 2 — Parse into dialogue segments
        voice_sys = CharacterVoiceSystem()
        segments = voice_sys.parse_dialogue(content)
        logger.info(
            "Chapter %d: %d dialogue segments parsed", chapter_id, len(segments),
        )

        # 3 — Request hot-swap to Fish Audio S2
        manager = get_hotswap_manager()
        provider_id = await manager.request_provider(purpose="story")
        logger.info("Audio generation using provider: %s", provider_id)

        fish = FishAudioProvider()

        # 4 — Build Fish Audio segment list and synthesize
        fish_segments: list[dict] = []
        for seg in segments:
            voice = await voice_sys.get_voice_for_line(story_id, seg.get("speaker", "narrator"))
            fish_segments.append({
                "speaker": seg.get("speaker", "narrator"),
                "text": seg.get("text", ""),
                "voice_id": voice.voice_id,
                "style": voice.voice_style,
            })

        audio_bytes = await fish.synthesize_dialogue(fish_segments)

        # 5 — Cache
        cache_path = self._audio_path(story_id, chapter_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(audio_bytes)
        logger.info("Cached chapter audio → %s (%d bytes)", cache_path, len(audio_bytes))

        # 6 — Swap back to conversational TTS
        await manager.swap_to_conversation_mode()

        return str(cache_path)

    async def get_cached_audio(self, chapter_id: int) -> str | None:
        """Return cached audio path if available."""
        # Scan cache directory for any file matching chapter_id
        for path in self._cache_dir.rglob(f"*_ch{chapter_id}.pcm"):
            if path.exists() and path.stat().st_size > 0:
                return str(path)
        return None

    async def invalidate_cache(self, story_id: int) -> None:
        """Clear cached audio for a story."""
        story_dir = self._cache_dir / f"story_{story_id}"
        if story_dir.exists():
            for f in story_dir.iterdir():
                f.unlink(missing_ok=True)
            story_dir.rmdir()
            logger.info("Invalidated audio cache for story %d", story_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _audio_path(self, story_id: int, chapter_id: int) -> Path:
        return self._cache_dir / f"story_{story_id}" / f"story{story_id}_ch{chapter_id}.pcm"
