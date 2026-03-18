"""Sound Library plugin — describe sounds for future audio playback.

Module ownership: Layer 2 fast-path plugin for "what does X sound like" queries.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Match patterns ───────────────────────────────────────────────
_SOUND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwhat\s+does\s+(?:a\s+|an\s+)?(.+?)\s+sound\s+like\b", re.I),
    re.compile(r"\bplay\s+(?:a\s+|an\s+|the\s+)?(.+?)\s+sound\b", re.I),
    re.compile(r"\bsound\s+of\s+(?:a\s+|an\s+|the\s+)?(.+)\b", re.I),
    re.compile(r"\banimal\s+sounds?\b", re.I),
    re.compile(r"\bnature\s+sounds?\b", re.I),
]

_SUBJECT_EXTRACT = re.compile(
    r"(?:what\s+does\s+(?:a\s+|an\s+)?(.+?)\s+sound\s+like"
    r"|play\s+(?:a\s+|an\s+|the\s+)?(.+?)\s+sound"
    r"|sound\s+of\s+(?:a\s+|an\s+|the\s+)?(.+))",
    re.I,
)

# ── Sound catalog ────────────────────────────────────────────────
SOUND_CATALOG: dict[str, dict[str, str]] = {
    # ── Animals ──
    "cat": {
        "category": "animals",
        "description": "A soft, rhythmic purring or a sharp 'meow' — cats vary from gentle trills to demanding yowls.",
    },
    "dog": {
        "category": "animals",
        "description": "A deep, resonant 'woof' or a series of excited barks, sometimes followed by a playful whine.",
    },
    "bird": {
        "category": "animals",
        "description": "A melodic chirping or trilling song, varying from short tweets to complex warbling melodies.",
    },
    "wolf": {
        "category": "animals",
        "description": "A long, haunting howl that rises and falls, often answered by others in the distance.",
    },
    "lion": {
        "category": "animals",
        "description": "A deep, rumbling roar that can be heard for miles — powerful and unmistakable.",
    },
    "frog": {
        "category": "animals",
        "description": "A rhythmic croaking — 'ribbit ribbit' — especially loud on warm, humid evenings.",
    },
    "owl": {
        "category": "animals",
        "description": "A low, soft 'hoo-hoo-hoo' echoing through the trees at night.",
    },
    "cow": {
        "category": "animals",
        "description": "A long, drawn-out 'moo' — low-pitched and resonant.",
    },
    "horse": {
        "category": "animals",
        "description": "A bright, forceful neigh or whinny, sometimes accompanied by the clip-clop of hooves.",
    },
    "elephant": {
        "category": "animals",
        "description": "A loud, brassy trumpet blast, unmistakable and awe-inspiring.",
    },
    # ── Nature ──
    "rain": {
        "category": "nature",
        "description": "A gentle patter on leaves and rooftops, building from a whisper to a steady, soothing drumming.",
    },
    "thunder": {
        "category": "nature",
        "description": "A low, distant rumble that builds into a sharp, cracking boom that shakes the air.",
    },
    "ocean": {
        "category": "nature",
        "description": "Waves rolling in with a steady whoosh, crashing onto the shore, then hissing as they retreat.",
    },
    "wind": {
        "category": "nature",
        "description": "A soft whistling that builds to a howl, rustling leaves and bending branches.",
    },
    "waterfall": {
        "category": "nature",
        "description": "A constant, powerful rush of water cascading over rocks — white noise from nature.",
    },
    "fire": {
        "category": "nature",
        "description": "A warm crackling and popping, with occasional snaps as logs shift in the flames.",
    },
    # ── Vehicles ──
    "car": {
        "category": "vehicles",
        "description": "A smooth engine hum that rises in pitch with acceleration, tires whispering on asphalt.",
    },
    "train": {
        "category": "vehicles",
        "description": "A rhythmic clack-clack-clack on the rails, building with a distant whistle and the rush of passing carriages.",
    },
    "motorcycle": {
        "category": "vehicles",
        "description": "A throaty rumble at idle that opens into a roaring growl as the throttle twists.",
    },
    "helicopter": {
        "category": "vehicles",
        "description": "A rhythmic thwop-thwop-thwop of spinning rotors, felt as much as heard.",
    },
    # ── Instruments ──
    "piano": {
        "category": "instruments",
        "description": "Bright, resonant tones from hammered strings — warm in the low register, sparkling in the high.",
    },
    "guitar": {
        "category": "instruments",
        "description": "Rich, vibrating strings — mellow and warm on acoustic, bright and cutting on electric.",
    },
    "drums": {
        "category": "instruments",
        "description": "A punchy kick, a snappy snare, shimmering cymbals — the heartbeat of music.",
    },
    "violin": {
        "category": "instruments",
        "description": "A soaring, expressive voice — capable of sweet tenderness or fiery intensity.",
    },
    "trumpet": {
        "category": "instruments",
        "description": "A bright, brassy call — bold and piercing, from mellow jazz to triumphant fanfares.",
    },
    "flute": {
        "category": "instruments",
        "description": "A pure, airy tone that floats above other instruments — light and ethereal.",
    },
}

_CATEGORIES: dict[str, list[str]] = {}
for _name, _info in SOUND_CATALOG.items():
    _CATEGORIES.setdefault(_info["category"], []).append(_name)


def _find_sound(subject: str) -> tuple[str, dict[str, str]] | None:
    """Look up *subject* in the catalog, with fuzzy matching."""
    lower = subject.lower().strip()
    # Exact match
    if lower in SOUND_CATALOG:
        return lower, SOUND_CATALOG[lower]
    # Substring match
    for name, info in SOUND_CATALOG.items():
        if name in lower or lower in name:
            return name, info
    return None


class SoundLibraryPlugin(CortexPlugin):
    """Describe what things sound like — future gateway to audio playback."""

    plugin_id = "sound_library"
    display_name = "Sound Library"
    plugin_type = "action"
    supports_learning = True
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        super().__init__()

    # ── Lifecycle ────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    # ── Match ────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        for pat in _SOUND_PATTERNS:
            if pat.search(message):
                subject = self._extract_subject(message)
                return CommandMatch(
                    matched=True,
                    intent="describe_sound",
                    confidence=0.85,
                    metadata={"subject": subject},
                )
        return CommandMatch(matched=False)

    # ── Handle ───────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        subject = match.metadata.get("subject", "")

        # Category listing
        lower = subject.lower() if subject else message.lower()
        if "animal" in lower:
            return self._list_category("animals")
        if "nature" in lower:
            return self._list_category("nature")
        if "vehicle" in lower:
            return self._list_category("vehicles")
        if "instrument" in lower:
            return self._list_category("instruments")

        if not subject:
            return CommandResult(
                success=True,
                response=(
                    "I have sounds for animals, nature, vehicles, and instruments. "
                    'Try asking "what does a cat sound like?" or "play rain sound".'
                ),
            )

        result = _find_sound(subject)
        if not result:
            return CommandResult(
                success=True,
                response=(
                    f"I don't have a sound description for \"{subject}\" yet. "
                    "I know animals, nature, vehicles, and instruments."
                ),
                metadata={"found": False},
            )

        name, info = result
        return CommandResult(
            success=True,
            response=f"🔊 **{name.title()}** ({info['category']}): {info['description']}",
            metadata={
                "found": True,
                "sound_name": name,
                "category": info["category"],
                "audio_available": False,
            },
        )

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    def _extract_subject(message: str) -> str:
        m = _SUBJECT_EXTRACT.search(message)
        if m:
            return next((g for g in m.groups() if g), "")
        return ""

    @staticmethod
    def _list_category(category: str) -> CommandResult:
        items = _CATEGORIES.get(category, [])
        if not items:
            return CommandResult(
                success=True,
                response=f"No sounds in the {category} category yet.",
            )
        listing = ", ".join(sorted(items))
        return CommandResult(
            success=True,
            response=f"**{category.title()}** sounds available: {listing}",
            metadata={"category": category, "items": sorted(items)},
        )
