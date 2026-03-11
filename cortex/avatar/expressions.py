"""Avatar expression presets and sentiment/content mapping.

OWNERSHIP: This module owns all expression definitions and resolution logic.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AvatarExpression:
    """Facial expression parameters for avatar rendering."""
    name: str
    eyebrow_raise: float   # -1.0 (frown) to 1.0 (raise)
    eye_squint: float       # 0.0–1.0
    mouth_smile: float      # -1.0 (frown) to 1.0 (smile)
    head_tilt: float        # -1.0 to 1.0 (degrees normalised)
    blink_rate: float       # blinks per second


# 19 emotion presets
EXPRESSIONS: dict[str, AvatarExpression] = {
    "neutral":   AvatarExpression("neutral",   0.0,  0.0, 0.0,  0.0,   0.3),
    "happy":     AvatarExpression("happy",     0.2,  0.2, 0.8,  0.0,   0.3),
    "thinking":  AvatarExpression("thinking",  0.5,  0.3, 0.0,  0.15,  0.15),
    "surprised": AvatarExpression("surprised", 0.8,  0.0, 0.3,  0.0,   0.5),
    "sad":       AvatarExpression("sad",      -0.3,  0.0, -0.5, -0.1,  0.2),
    "excited":   AvatarExpression("excited",   0.6,  0.0, 0.9,  0.0,   0.4),
    "concerned": AvatarExpression("concerned", 0.3,  0.2, -0.2,  0.1,  0.25),
    "listening": AvatarExpression("listening", 0.1,  0.0, 0.1,  0.05,  0.3),
    "laughing":  AvatarExpression("laughing",  0.3,  0.5, 1.0,  0.0,   0.1),
    "crying":    AvatarExpression("crying",   -0.4,  0.0, -0.8, -0.1,  0.15),
    "silly":     AvatarExpression("silly",     0.4,  0.1, 0.6,  0.2,   0.35),
    "winking":   AvatarExpression("winking",   0.2,  0.0, 0.5,  0.1,   0.25),
    "angry":     AvatarExpression("angry",    -0.6,  0.4, -0.6,  0.0,  0.2),
    "confused":  AvatarExpression("confused",  0.4,  0.1, 0.0,  0.2,   0.3),
    "love":      AvatarExpression("love",      0.3,  0.0, 0.9,  0.0,   0.2),
    "sleepy":    AvatarExpression("sleepy",   -0.1,  0.5, 0.1, -0.1,   0.1),
    "proud":     AvatarExpression("proud",     0.3,  0.3, 0.7,  0.0,   0.25),
    "scared":    AvatarExpression("scared",    0.7,  0.0, -0.3,  0.0,  0.6),
}

# Pipeline sentiment → avatar expression name
_SENTIMENT_EXPRESSION: dict[str, str] = {
    "greeting":   "happy",
    "casual":     "neutral",
    "question":   "thinking",
    "command":    "neutral",
    "frustrated": "angry",
    "positive":   "happy",
    "negative":   "sad",
    "excited":    "excited",
    "neutral":    "neutral",
}

# Content-aware expression hints (regex pattern → expression name)
_CONTENT_EXPRESSION_PATTERNS: list[tuple[str, str]] = [
    (r"\bjoke\b|\bfunny\b|\blaugh\b|\bhaha\b|\blol\b|\bhumor\b", "silly"),
    (r"\blove\b|\bheart\b|\badore\b|\bsweet\b|\bcute\b", "love"),
    (r"\bconfus\w*\b|\bwhat\??$|\bhuh\b|\bweird\b", "confused"),
    (r"\bscar[ey]\w*\b|\bafraid\b|\bfrightened\b|\bcreepy\b|\bhorror\b", "scared"),
    (r"\bangr[iy]\w*\b|\bfurious\b|\bmad\b|\bhate\b", "angry"),
    (r"\bcry\w*\b|\bterribl\w*\b|\bawful\b|\bdevast\w*\b|\bmiss(?:ing)?\b", "crying"),
    (r"\bproud\b|\baccomplish\w*\b|\bdid it\b|\bnailed\b|\bawesome\b", "proud"),
    (r"\btired\b|\bsleep\w*\b|\bbed\s*time\b|\byawn\b|\bnap\b", "sleepy"),
    (r"\bwow\b|\bamazing\b|\bincredible\b|\bexcit\w*\b|\byay\b|\byeah\b", "excited"),
    (r"\breally\?|\bno way\b|\bsurpris\w*\b|\bunbeliev\w*\b|\bwhoa\b", "surprised"),
    (r"\bsecret\b|\bguess what\b|\bhint\b|\bwink\b|\bshhh\b", "winking"),
]


def resolve_from_sentiment(sentiment: str, confidence: float = 1.0) -> AvatarExpression:
    """Map pipeline sentiment label to an avatar expression.

    When confidence < 1.0, blends toward neutral proportionally.
    """
    expr_name = _SENTIMENT_EXPRESSION.get(sentiment, "neutral")
    base = EXPRESSIONS[expr_name]
    if confidence < 1.0:
        neutral = EXPRESSIONS["neutral"]
        return AvatarExpression(
            name=base.name,
            eyebrow_raise=neutral.eyebrow_raise + (base.eyebrow_raise - neutral.eyebrow_raise) * confidence,
            eye_squint=neutral.eye_squint + (base.eye_squint - neutral.eye_squint) * confidence,
            mouth_smile=neutral.mouth_smile + (base.mouth_smile - neutral.mouth_smile) * confidence,
            head_tilt=neutral.head_tilt + (base.head_tilt - neutral.head_tilt) * confidence,
            blink_rate=neutral.blink_rate + (base.blink_rate - neutral.blink_rate) * confidence,
        )
    return base


def resolve_from_content(text: str) -> AvatarExpression | None:
    """Detect expression from message content using pattern matching.

    Returns the expression if a content pattern matches, or None.
    """
    lower = text.lower()
    for pattern, expr_name in _CONTENT_EXPRESSION_PATTERNS:
        if re.search(pattern, lower):
            expr = EXPRESSIONS.get(expr_name)
            if expr:
                logger.debug("content match %r → expression %s", pattern, expr_name)
                return expr
    return None
