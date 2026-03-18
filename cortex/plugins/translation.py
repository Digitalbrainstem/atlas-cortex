"""Translation plugin — translate text via LibreTranslate.

Module ownership: Layer 2 fast-path plugin for translation requests.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Language mapping ─────────────────────────────────────────────
LANGUAGE_CODES: dict[str, str] = {
    "arabic": "ar",
    "chinese": "zh",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "indonesian": "id",
    "irish": "ga",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "norwegian": "no",
    "persian": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "romanian": "ro",
    "russian": "ru",
    "spanish": "es",
    "swedish": "sv",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}

_LANG_NAMES = "|".join(LANGUAGE_CODES.keys())

# ── Match patterns ───────────────────────────────────────────────
_TRANSLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\btranslat(?:e|ion)\b", re.I),
    re.compile(r"\bhow\s+do\s+you\s+say\b", re.I),
    re.compile(rf"\bin\s+({_LANG_NAMES})\b", re.I),
    re.compile(rf"\bto\s+({_LANG_NAMES})\b", re.I),
    re.compile(r"\bwhat\s+does\b.+\bmean\s+in\b", re.I),
]

# ── Parse helpers ────────────────────────────────────────────────
_PARSE_HOW_SAY = re.compile(
    rf"how\s+do\s+you\s+say\s+[\"']?(.+?)[\"']?\s+in\s+({_LANG_NAMES})",
    re.I,
)
_PARSE_TRANSLATE_TO = re.compile(
    rf"translat(?:e|ion)\s+[\"']?(.+?)[\"']?\s+(?:to|into)\s+({_LANG_NAMES})",
    re.I,
)
_PARSE_WHAT_MEAN = re.compile(
    rf"what\s+does\s+[\"']?(.+?)[\"']?\s+mean\s+in\s+({_LANG_NAMES})",
    re.I,
)
_PARSE_TRANSLATE_FROM_TO = re.compile(
    rf"translat(?:e|ion)\s+[\"']?(.+?)[\"']?\s+from\s+({_LANG_NAMES})\s+to\s+({_LANG_NAMES})",
    re.I,
)


def _detect_target_language(message: str) -> str | None:
    """Return language code from the message, or None."""
    lower = message.lower()
    for name, code in LANGUAGE_CODES.items():
        if name in lower:
            return code
    return None


def _parse_request(message: str) -> tuple[str, str, str]:
    """Parse (text, source_lang_code, target_lang_code) from *message*.

    Returns empty strings for fields it cannot determine.
    """
    # "translate X from Y to Z"
    m = _PARSE_TRANSLATE_FROM_TO.search(message)
    if m:
        text = m.group(1).strip()
        src = LANGUAGE_CODES.get(m.group(2).lower(), "")
        tgt = LANGUAGE_CODES.get(m.group(3).lower(), "")
        return text, src, tgt

    # "how do you say X in Y"
    m = _PARSE_HOW_SAY.search(message)
    if m:
        return m.group(1).strip(), "en", LANGUAGE_CODES.get(m.group(2).lower(), "")

    # "translate X to Y"
    m = _PARSE_TRANSLATE_TO.search(message)
    if m:
        return m.group(1).strip(), "auto", LANGUAGE_CODES.get(m.group(2).lower(), "")

    # "what does X mean in Y"
    m = _PARSE_WHAT_MEAN.search(message)
    if m:
        return m.group(1).strip(), "auto", LANGUAGE_CODES.get(m.group(2).lower(), "")

    return "", "", ""


class TranslationPlugin(CortexPlugin):
    """Translate text using a self-hosted LibreTranslate instance."""

    plugin_id = "translation"
    display_name = "Translation"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        super().__init__()
        self._host: str = ""

    # ── Lifecycle ────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        self._host = config.get("host", "").rstrip("/")
        return True

    async def health(self) -> bool:
        if not self._host:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._host}/frontend/settings")
                return resp.status_code == 200
        except Exception:
            return False

    # ── Match ────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        for pat in _TRANSLATE_PATTERNS:
            if pat.search(message):
                text, src, tgt = _parse_request(message)
                if text and tgt:
                    return CommandMatch(
                        matched=True,
                        intent="translate",
                        confidence=0.92,
                        metadata={"text": text, "source": src, "target": tgt},
                    )
                # Pattern matched but we couldn't parse well enough
                if _detect_target_language(message):
                    return CommandMatch(
                        matched=True,
                        intent="translate",
                        confidence=0.70,
                        metadata={},
                    )
        return CommandMatch(matched=False)

    # ── Handle ───────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        text = match.metadata.get("text", "")
        source = match.metadata.get("source", "auto")
        target = match.metadata.get("target", "")

        if not text or not target:
            return CommandResult(
                success=False,
                response=(
                    "I couldn't figure out what to translate. "
                    'Try something like "translate hello to Spanish".'
                ),
            )

        if not self._host:
            return CommandResult(
                success=False,
                response=(
                    "Translation isn't configured yet. "
                    "Set up a LibreTranslate host to enable translations."
                ),
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                payload: dict[str, str] = {
                    "q": text,
                    "source": source,
                    "target": target,
                    "format": "text",
                }
                resp = await client.post(
                    f"{self._host}/translate", json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                translated = data.get("translatedText", "")
        except Exception as exc:
            logger.warning("Translation request failed: %s", exc)
            return CommandResult(
                success=False,
                response="Translation service is unavailable right now.",
            )

        if not translated:
            return CommandResult(success=False, response="Got an empty translation back.")

        lang_name = next(
            (n for n, c in LANGUAGE_CODES.items() if c == target), target,
        )
        return CommandResult(
            success=True,
            response=f'"{text}" in {lang_name.title()} is: "{translated}"',
        )
