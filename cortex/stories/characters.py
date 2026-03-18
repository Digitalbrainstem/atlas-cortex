# Module ownership: Character voice assignment and dialogue parsing
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from cortex.db import get_db, init_db

log = logging.getLogger(__name__)

# ── Data structures ─────────────────────────────────────────────────


@dataclass
class CharacterVoice:
    name: str
    voice_id: str  # TTS provider voice ID
    voice_style: str  # Emotion/style tags (e.g., "[old wizard voice]")
    reference_audio: str = ""  # Path for zero-shot voice cloning


# Regex: "Name: \"dialogue\"" or "Name: 'dialogue'"
_DIALOGUE_RE = re.compile(
    r'^([A-Z][A-Za-z _\'-]+?):\s*["\u201c](.+?)["\u201d]\s*$',
    re.MULTILINE,
)


# ── Voice system ────────────────────────────────────────────────────


class CharacterVoiceSystem:
    """Assign TTS voices to story characters and parse dialogue."""

    ARCHETYPES: dict[str, dict[str, str]] = {
        "narrator": {"voice_id": "default", "voice_style": "[calm, storytelling]"},
        "hero": {"voice_id": "default", "voice_style": "[brave, young]"},
        "villain": {"voice_id": "default", "voice_style": "[menacing, deep]"},
        "wise_elder": {"voice_id": "default", "voice_style": "[gentle, wise, old]"},
        "fairy": {"voice_id": "default", "voice_style": "[light, magical, whispery]"},
        "animal": {"voice_id": "default", "voice_style": "[playful, cute]"},
        "robot": {"voice_id": "default", "voice_style": "[monotone, mechanical]"},
    }

    async def assign_voice(
        self,
        story_id: int,
        character_name: str,
        archetype: str = "",
        voice_id: str = "",
        voice_style: str = "",
        reference_audio: str = "",
    ) -> int:
        """Assign a voice to a story character.  Returns character_id."""

        defaults = self.ARCHETYPES.get(archetype, {})
        voice_id = voice_id or defaults.get("voice_id", "default")
        voice_style = voice_style or defaults.get("voice_style", "")

        init_db()
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO story_characters "
            "(story_id, name, voice_id, voice_style, reference_audio) "
            "VALUES (?, ?, ?, ?, ?)",
            (story_id, character_name, voice_id, voice_style, reference_audio),
        )
        conn.commit()
        char_id: int = cur.lastrowid  # type: ignore[assignment]
        log.info("Assigned voice to %s (id=%d) in story %d", character_name, char_id, story_id)
        return char_id

    async def get_voices(self, story_id: int) -> list[CharacterVoice]:
        """Get all character voices for a story."""

        init_db()
        conn = get_db()
        rows = conn.execute(
            "SELECT name, voice_id, voice_style, reference_audio "
            "FROM story_characters WHERE story_id = ?",
            (story_id,),
        ).fetchall()
        return [
            CharacterVoice(
                name=r["name"],
                voice_id=r["voice_id"],
                voice_style=r["voice_style"],
                reference_audio=r["reference_audio"],
            )
            for r in rows
        ]

    async def get_voice_for_line(
        self, story_id: int, character_name: str
    ) -> CharacterVoice:
        """Get the voice for a character, falling back to the narrator archetype."""

        init_db()
        conn = get_db()
        row = conn.execute(
            "SELECT name, voice_id, voice_style, reference_audio "
            "FROM story_characters WHERE story_id = ? AND name = ?",
            (story_id, character_name),
        ).fetchone()
        if row:
            return CharacterVoice(
                name=row["name"],
                voice_id=row["voice_id"],
                voice_style=row["voice_style"],
                reference_audio=row["reference_audio"],
            )
        # Fallback: narrator defaults
        defaults = self.ARCHETYPES["narrator"]
        return CharacterVoice(
            name="narrator",
            voice_id=defaults["voice_id"],
            voice_style=defaults["voice_style"],
        )

    def parse_dialogue(self, chapter_text: str) -> list[dict]:
        """Parse chapter text into segments.

        Returns a list of ``{speaker, text, is_dialogue}`` dicts.
        Narrator segments use ``speaker="narrator"``.
        """

        segments: list[dict] = []
        last_end = 0

        for match in _DIALOGUE_RE.finditer(chapter_text):
            # Capture any narrator text *before* this dialogue line
            preceding = chapter_text[last_end : match.start()].strip()
            if preceding:
                segments.append(
                    {"speaker": "narrator", "text": preceding, "is_dialogue": False}
                )
            segments.append(
                {
                    "speaker": match.group(1).strip(),
                    "text": match.group(2).strip(),
                    "is_dialogue": True,
                }
            )
            last_end = match.end()

        # Trailing narrator text
        trailing = chapter_text[last_end:].strip()
        if trailing:
            segments.append(
                {"speaker": "narrator", "text": trailing, "is_dialogue": False}
            )

        # If nothing was parsed (no dialogue at all), return the whole text
        if not segments and chapter_text.strip():
            segments.append(
                {"speaker": "narrator", "text": chapter_text.strip(), "is_dialogue": False}
            )

        return segments
