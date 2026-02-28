"""Emotion composer — translates Atlas sentiment into TTS emotion instructions (C11.3)."""

from __future__ import annotations

import re
from datetime import datetime

# Orpheus inline emotion tags recognised by the model
_ORPHEUS_TAGS = {"<laugh>", "<chuckle>", "<sigh>", "<gasp>", "<cough>", "<sniffle>"}

# Mapping VADER compound bucket → Orpheus emotion descriptor
_ORPHEUS_EMOTION_MAP = {
    "very_positive": "happy",
    "positive": "warm",
    "neutral": None,
    "negative": "concerned",
    "very_negative": "sad",
    "excited": "excited",
    "amused": "amused",
    "empathetic": "gentle",
    "serious": "serious",
    "encouraging": "enthusiastic",
}

# Mapping sentiment category → Parler natural-language descriptor fragment
_PARLER_EMOTION_MAP = {
    "positive": "friendly and warm",
    "negative": "calm and empathetic",
    "neutral": "conversational and clear",
    "excited": "energetic and lively",
    "frustrated": "patient and understanding",
}


def _bucket_compound(compound: float) -> str:
    """Convert a VADER compound score to a sentiment bucket name."""
    if compound >= 0.5:
        return "very_positive"
    if compound >= 0.1:
        return "positive"
    if compound <= -0.5:
        return "very_negative"
    if compound <= -0.1:
        return "negative"
    return "neutral"


class EmotionComposer:
    """Translates Atlas sentiment/context into TTS emotion instructions."""

    def __init__(self):
        self._last_paralingual: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compose(
        self,
        text: str,
        sentiment,  # object with .label (str) and .compound (float) or a plain dict
        confidence: float = 0.8,
        user_profile: dict | None = None,
        context: dict | None = None,
        provider=None,
    ) -> str | tuple[str, str]:
        """Add emotion markup to *text* for the current TTS provider.

        Returns a plain string for Orpheus/Piper/plain, or a (text, description)
        tuple for Parler.
        """
        user_profile = user_profile or {}
        context = context or {}

        if provider is None:
            from cortex.voice.providers import get_tts_provider
            provider = get_tts_provider()

        fmt = provider.get_emotion_format() if provider else None

        # Apply night-mode / context modifiers
        context = self._apply_context_modifiers(context)

        if fmt == "tags":
            return self._compose_orpheus(text, sentiment, confidence, context, user_profile)
        if fmt == "description":
            return self._compose_parler(text, sentiment, confidence, user_profile, context)
        if fmt == "ssml":
            return self._compose_ssml(text, sentiment)
        return text  # plain-text fallback

    # ------------------------------------------------------------------
    # Provider-specific composers
    # ------------------------------------------------------------------

    def _compose_orpheus(
        self,
        text: str,
        sentiment,
        confidence: float,
        context: dict,
        user_profile: dict,
    ) -> str:
        if isinstance(sentiment, str):
            label = sentiment
            compound = 0.0
        else:
            label = getattr(sentiment, "label", "neutral") or "neutral"
            compound = getattr(sentiment, "compound", 0.0)
        bucket = _bucket_compound(compound) if isinstance(compound, float) else "neutral"

        age_group = user_profile.get("age_group", "adult")

        # Fast-path context overrides
        if context.get("is_whisper") or context.get("is_secret"):
            return f"whisper: {text}"
        if context.get("is_excited"):
            return f"happy, fast: {text}"
        if context.get("night_mode") and context.get("slow"):
            return f"calm, slow: {text}"

        # Paralingual injection (never repeat the same one back-to-back)
        prefix_para = ""
        suffix_para = ""
        if age_group not in ("toddler", "child"):
            if (context.get("is_joke") or label == "amused") and self._can_use("<chuckle>"):
                suffix_para = " <chuckle>"
                self._last_paralingual = "<chuckle>"
            elif label == "frustrated_user" and confidence > 0.8 and self._can_use("<sigh>"):
                prefix_para = "<sigh> "
                self._last_paralingual = "<sigh>"
            elif context.get("is_surprised") and self._can_use("<gasp>"):
                prefix_para = "<gasp> "
                self._last_paralingual = "<gasp>"
        else:
            self._last_paralingual = None  # reset for safe delivery

        # Determine base emotion descriptor
        emotion = _ORPHEUS_EMOTION_MAP.get(label) or _ORPHEUS_EMOTION_MAP.get(bucket)

        tagged = f"{prefix_para}{text}{suffix_para}"
        if emotion:
            return f"{emotion}: {tagged}"
        return tagged

    def _compose_parler(
        self,
        text: str,
        sentiment,
        confidence: float,
        user_profile: dict,
        context: dict,
    ) -> tuple[str, str]:
        voice_desc = user_profile.get("preferred_voice_description", "A warm, clear adult voice")
        label = getattr(sentiment, "label", "neutral")
        category = getattr(sentiment, "category", None) or label
        emotion_adj = _PARLER_EMOTION_MAP.get(category, "natural and clear")
        description = f"{voice_desc}, {emotion_adj} tone, moderate pace"
        return text, description

    def _compose_ssml(self, text: str, sentiment) -> str:
        """Minimal SSML wrapper for Piper (limited support)."""
        label = getattr(sentiment, "label", "neutral")
        rate = "fast" if label in ("excited", "very_positive") else "medium"
        return f'<speak><prosody rate="{rate}">{text}</prosody></speak>'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _can_use(self, tag: str) -> bool:
        return self._last_paralingual != tag

    @staticmethod
    def _apply_context_modifiers(context: dict) -> dict:
        """Inject time-of-day / night-mode flags into context."""
        context = dict(context)
        hour = context.get("hour", datetime.now().hour)
        if 22 <= hour or hour < 6:
            context.setdefault("night_mode", True)
            context.setdefault("slow", True)
        return context
