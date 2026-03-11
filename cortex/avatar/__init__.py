"""Avatar system — lip-sync, expressions, and visual feedback.

Manages avatar state including mouth visemes (from phoneme timing),
facial expressions (from emotional state), and visual feedback.
Designed to drive browser-based SVG or canvas avatars on satellite displays.

Sub-modules:
  expressions — Expression presets and sentiment/content mapping
  visemes     — Phoneme-to-viseme generation
  skins       — Skin resolution from DB
  broadcast   — WebSocket broadcast to display clients
  controller  — Single entry point for pipeline/orchestrator

See docs/avatar-system.md for full design (Phase C7).
"""

from __future__ import annotations

# Re-export public API from submodules for backward compatibility
from cortex.avatar.expressions import AvatarExpression, EXPRESSIONS
from cortex.avatar.visemes import VisemeFrame, VISEME_MAP, VISEME_CATEGORIES

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Backward-compatible imports ──────────────────────────────────
# These are used by existing code that imports from cortex.avatar directly.
# New code should import from submodules or use the controller.

from cortex.avatar.expressions import (
    _SENTIMENT_EXPRESSION,
    _CONTENT_EXPRESSION_PATTERNS,
    resolve_from_sentiment,
    resolve_from_content,
)
from cortex.avatar.visemes import (
    _DIGRAPH_MAP,
    _CHAR_PHONEME,
    _text_to_phonemes,
    _VOWEL_VISEMES,
    text_to_visemes,
)


class AvatarState:
    """Manages current avatar visual state.

    DEPRECATED: New code should use cortex.avatar.controller instead.
    Kept for backward compatibility with existing callers.
    """

    def __init__(self) -> None:
        self.expression: AvatarExpression = EXPRESSIONS["neutral"]
        self.viseme_queue: list[VisemeFrame] = []
        self.is_speaking: bool = False
        self.is_listening: bool = False

    def set_expression(self, emotion: str) -> None:
        self.expression = EXPRESSIONS.get(emotion, EXPRESSIONS["neutral"])
        logger.debug("expression → %s", self.expression.name)

    def expression_from_sentiment(
        self, sentiment: str, confidence: float = 1.0,
    ) -> AvatarExpression:
        expr = resolve_from_sentiment(sentiment, confidence)
        self.expression = expr
        return expr

    def expression_from_content(self, text: str) -> AvatarExpression | None:
        expr = resolve_from_content(text)
        if expr:
            self.expression = expr
        return expr

    def text_to_visemes(self, text: str, wpm: int = 150) -> list[VisemeFrame]:
        frames = text_to_visemes(text, wpm)
        self.viseme_queue = frames
        self.is_speaking = True
        return frames

    def to_json(self) -> dict[str, Any]:
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
