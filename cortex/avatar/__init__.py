"""Avatar system — lip-sync, expressions, and visual feedback.

Manages avatar state including mouth visemes (from phoneme timing),
facial expressions (from emotional state), and visual feedback.
Designed to drive browser-based SVG or canvas avatars on satellite displays.

See docs/avatar-system.md for full design (Phase C7).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Phoneme → Viseme mapping (simplified IPA, Oculus/Microsoft standard)
# ──────────────────────────────────────────────────────────────────

VISEME_MAP: dict[str, str] = {
    # Silence
    "sil": "IDLE",
    # Bilabials — lips pressed
    "p": "PP", "b": "PP", "m": "PP",
    # Labiodentals — bottom lip tucked
    "f": "FF", "v": "FF",
    # Dentals (th sounds)
    "T": "TH", "D": "TH",
    # Alveolars — tongue tip up
    "t": "DD", "d": "DD", "n": "DD",
    # Velars
    "k": "KK", "g": "KK",
    # Sibilants
    "s": "SS", "z": "SS",
    # Post-alveolar fricatives (sh, zh)
    "S": "SH", "Z": "SH",
    # Rhotic
    "r": "RR",
    # Lateral / glides
    "l": "NN", "j": "NN", "w": "NN",
    # Close front vowels
    "i": "IH", "I": "IH",
    # Open-mid front vowels
    "e": "EH", "E": "EH",
    # Open vowels
    "a": "AA", "A": "AA",
    # Close-mid back vowels
    "o": "OH", "O": "OH",
    # Close back vowels
    "u": "OU", "U": "OU",
}

# All valid viseme categories
VISEME_CATEGORIES: set[str] = {
    "IDLE", "PP", "FF", "TH", "DD", "KK", "SS", "SH",
    "RR", "NN", "IH", "EH", "AA", "OH", "OU",
}

# ──────────────────────────────────────────────────────────────────
# Simple character → phoneme heuristic (no full G2P engine)
# ──────────────────────────────────────────────────────────────────

# Digraphs checked first, then single characters
_DIGRAPH_MAP: list[tuple[str, str]] = [
    ("th", "T"),
    ("sh", "S"),
    ("ch", "S"),
    ("ph", "f"),
    ("wh", "w"),
    ("ck", "k"),
    ("ng", "n"),
    ("qu", "k"),
]

_CHAR_PHONEME: dict[str, str] = {
    "a": "a", "b": "b", "c": "k", "d": "d", "e": "e",
    "f": "f", "g": "g", "h": "sil", "i": "i", "j": "j",
    "k": "k", "l": "l", "m": "m", "n": "n", "o": "o",
    "p": "p", "q": "k", "r": "r", "s": "s", "t": "t",
    "u": "u", "v": "v", "w": "w", "x": "k", "y": "i",
    "z": "z",
}


def _text_to_phonemes(text: str) -> list[str]:
    """Convert text to a rough phoneme sequence using character heuristics.

    This is intentionally simple — a real system would use espeak-ng or
    Piper's built-in phoneme output.  Good enough for approximate lip-sync.
    """
    text = text.lower().strip()
    phonemes: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        # Non-alpha → silence gap
        if not ch.isalpha():
            if not phonemes or phonemes[-1] != "sil":
                phonemes.append("sil")
            i += 1
            continue
        # Try digraphs first
        matched = False
        if i + 1 < len(text):
            pair = text[i : i + 2]
            for digraph, phoneme in _DIGRAPH_MAP:
                if pair == digraph:
                    phonemes.append(phoneme)
                    i += 2
                    matched = True
                    break
        if not matched:
            phonemes.append(_CHAR_PHONEME.get(ch, "sil"))
            i += 1
    return phonemes


# ──────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────

@dataclass
class VisemeFrame:
    """A single viseme keyframe for lip-sync animation."""

    viseme: str        # "PP", "AA", "IDLE", etc.
    start_ms: int      # start time in milliseconds
    duration_ms: int   # how long to hold this viseme
    intensity: float   # 0.0–1.0 mouth openness


@dataclass
class AvatarExpression:
    """Facial expression parameters for avatar rendering."""

    name: str              # "neutral", "happy", "thinking", etc.
    eyebrow_raise: float   # -1.0 (frown) to 1.0 (raise)
    eye_squint: float      # 0.0–1.0
    mouth_smile: float     # -1.0 (frown) to 1.0 (smile)
    head_tilt: float       # -1.0 to 1.0 (degrees normalised)
    blink_rate: float      # blinks per second


# ──────────────────────────────────────────────────────────────────
# Expression presets (driven by emotion / sentiment)
# ──────────────────────────────────────────────────────────────────

EXPRESSIONS: dict[str, AvatarExpression] = {
    "neutral":   AvatarExpression("neutral",   0.0,  0.0, 0.0,  0.0,   0.3),
    "happy":     AvatarExpression("happy",     0.2,  0.2, 0.8,  0.0,   0.3),
    "thinking":  AvatarExpression("thinking",  0.5,  0.3, 0.0,  0.15,  0.15),
    "surprised": AvatarExpression("surprised", 0.8,  0.0, 0.3,  0.0,   0.5),
    "sad":       AvatarExpression("sad",      -0.3,  0.0, -0.5, -0.1,  0.2),
    "excited":   AvatarExpression("excited",   0.6,  0.0, 0.9,  0.0,   0.4),
    "concerned": AvatarExpression("concerned", 0.3,  0.2, -0.2,  0.1,  0.25),
    "listening": AvatarExpression("listening", 0.1,  0.0, 0.1,  0.05,  0.3),
}

# Pipeline sentiment → avatar expression
_SENTIMENT_EXPRESSION: dict[str, str] = {
    "greeting":   "happy",
    "casual":     "neutral",
    "question":   "thinking",
    "command":    "neutral",
    "frustrated": "concerned",
    "positive":   "happy",
    "negative":   "sad",
    "excited":    "excited",
}

# Vowel visemes get higher mouth openness than consonants
_VOWEL_VISEMES: set[str] = {"AA", "EH", "IH", "OH", "OU"}


# ──────────────────────────────────────────────────────────────────
# AvatarState
# ──────────────────────────────────────────────────────────────────

class AvatarState:
    """Manages current avatar visual state.

    Holds the active expression, a queue of viseme frames for lip-sync,
    and speaking/listening flags.  Serialises to JSON for WebSocket
    broadcast to satellite displays.
    """

    def __init__(self) -> None:
        self.expression: AvatarExpression = EXPRESSIONS["neutral"]
        self.viseme_queue: list[VisemeFrame] = []
        self.is_speaking: bool = False
        self.is_listening: bool = False

    # ── expression helpers ────────────────────────────────────────

    def set_expression(self, emotion: str) -> None:
        """Set facial expression from emotion name.

        Falls back to ``"neutral"`` for unknown emotions.
        """
        self.expression = EXPRESSIONS.get(emotion, EXPRESSIONS["neutral"])
        logger.debug("expression → %s", self.expression.name)

    def expression_from_sentiment(
        self, sentiment: str, confidence: float = 1.0,
    ) -> AvatarExpression:
        """Map pipeline sentiment label to an avatar expression.

        Args:
            sentiment: Sentiment label from the pipeline (e.g. ``"greeting"``).
            confidence: 0.0–1.0 — scales expression intensity.

        Returns:
            The matching :class:`AvatarExpression` (also sets it on *self*).
        """
        expr_name = _SENTIMENT_EXPRESSION.get(sentiment, "neutral")
        base = EXPRESSIONS[expr_name]
        if confidence < 1.0:
            neutral = EXPRESSIONS["neutral"]
            scaled = AvatarExpression(
                name=base.name,
                eyebrow_raise=neutral.eyebrow_raise + (base.eyebrow_raise - neutral.eyebrow_raise) * confidence,
                eye_squint=neutral.eye_squint + (base.eye_squint - neutral.eye_squint) * confidence,
                mouth_smile=neutral.mouth_smile + (base.mouth_smile - neutral.mouth_smile) * confidence,
                head_tilt=neutral.head_tilt + (base.head_tilt - neutral.head_tilt) * confidence,
                blink_rate=neutral.blink_rate + (base.blink_rate - neutral.blink_rate) * confidence,
            )
        else:
            scaled = base
        self.expression = scaled
        logger.debug("sentiment %r (%.2f) → expression %s", sentiment, confidence, scaled.name)
        return scaled

    # ── lip-sync ──────────────────────────────────────────────────

    def text_to_visemes(self, text: str, wpm: int = 150) -> list[VisemeFrame]:
        """Convert text to an approximate viseme sequence for lip-sync.

        Uses simple character-to-phoneme heuristics (no full G2P engine).
        *wpm* controls speaking speed.  Returns a list of
        :class:`VisemeFrame` objects and stores them in *viseme_queue*.
        """
        phonemes = _text_to_phonemes(text)
        if not phonemes:
            return []

        # Estimate duration per phoneme from wpm.
        # Average word ≈ 5 chars ≈ 5 phonemes in our simple model.
        phonemes_per_sec = (wpm * 5) / 60
        ms_per_phoneme = int(1000 / phonemes_per_sec) if phonemes_per_sec > 0 else 80

        frames: list[VisemeFrame] = []
        cursor_ms = 0
        for ph in phonemes:
            viseme = VISEME_MAP.get(ph, "IDLE")
            intensity = 0.7 if viseme in _VOWEL_VISEMES else 0.4
            if viseme == "IDLE":
                intensity = 0.0
            frames.append(VisemeFrame(
                viseme=viseme,
                start_ms=cursor_ms,
                duration_ms=ms_per_phoneme,
                intensity=round(intensity, 2),
            ))
            cursor_ms += ms_per_phoneme

        self.viseme_queue = frames
        self.is_speaking = True
        logger.debug("text_to_visemes: %d frames, %d ms total", len(frames), cursor_ms)
        return frames

    # ── serialisation ─────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        """Serialise current state for WebSocket broadcast to avatar clients."""
        return {
            "expression": {
                "name": self.expression.name,
                "eyebrow_raise": self.expression.eyebrow_raise,
                "eye_squint": self.expression.eye_squint,
                "mouth_smile": self.expression.mouth_smile,
                "head_tilt": self.expression.head_tilt,
                "blink_rate": self.expression.blink_rate,
            },
            "viseme_queue": [
                {
                    "viseme": f.viseme,
                    "start_ms": f.start_ms,
                    "duration_ms": f.duration_ms,
                    "intensity": f.intensity,
                }
                for f in self.viseme_queue
            ],
            "is_speaking": self.is_speaking,
            "is_listening": self.is_listening,
        }


__all__ = [
    "VISEME_MAP",
    "VISEME_CATEGORIES",
    "EXPRESSIONS",
    "VisemeFrame",
    "AvatarExpression",
    "AvatarState",
]
