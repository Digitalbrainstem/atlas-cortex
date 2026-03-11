"""Avatar viseme generation — text to lip-sync frames.

OWNERSHIP: This module owns phoneme mapping and viseme frame generation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VisemeFrame:
    """A single viseme keyframe for lip-sync animation."""
    viseme: str        # "PP", "AA", "IDLE", etc.
    start_ms: int      # start time in milliseconds
    duration_ms: int   # how long to hold this viseme
    intensity: float   # 0.0–1.0 mouth openness


VISEME_MAP: dict[str, str] = {
    "sil": "IDLE",
    "p": "PP", "b": "PP", "m": "PP",
    "f": "FF", "v": "FF",
    "T": "TH", "D": "TH",
    "t": "DD", "d": "DD", "n": "DD",
    "k": "KK", "g": "KK",
    "s": "SS", "z": "SS",
    "S": "SH", "Z": "SH",
    "r": "RR",
    "l": "NN", "j": "NN", "w": "NN",
    "i": "IH", "I": "IH",
    "e": "EH", "E": "EH",
    "a": "AA", "A": "AA",
    "o": "OH", "O": "OH",
    "u": "OU", "U": "OU",
}

VISEME_CATEGORIES: set[str] = {
    "IDLE", "PP", "FF", "TH", "DD", "KK", "SS", "SH",
    "RR", "NN", "IH", "EH", "AA", "OH", "OU",
}

_VOWEL_VISEMES: set[str] = {"AA", "EH", "IH", "OH", "OU"}

_DIGRAPH_MAP: list[tuple[str, str]] = [
    ("th", "T"), ("sh", "S"), ("ch", "S"), ("ph", "f"),
    ("wh", "w"), ("ck", "k"), ("ng", "n"), ("qu", "k"),
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
    """Convert text to a rough phoneme sequence using character heuristics."""
    text = text.lower().strip()
    phonemes: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if not ch.isalpha():
            if not phonemes or phonemes[-1] != "sil":
                phonemes.append("sil")
            i += 1
            continue
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


def text_to_visemes(text: str, wpm: int = 150) -> list[VisemeFrame]:
    """Convert text to an approximate viseme sequence for lip-sync.

    Uses simple character-to-phoneme heuristics.
    *wpm* controls speaking speed.
    """
    phonemes = _text_to_phonemes(text)
    if not phonemes:
        return []

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

    logger.debug("text_to_visemes: %d frames, %d ms total", len(frames), cursor_ms)
    return frames
