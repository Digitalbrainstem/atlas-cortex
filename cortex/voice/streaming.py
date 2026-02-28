"""Sentence-boundary streaming — pipeline audio while LLM is still generating (C11.5)."""

from __future__ import annotations

import re
from typing import AsyncGenerator, AsyncIterator

from cortex.voice.composer import EmotionComposer

# Sentence-ending pattern: '.', '!', '?' optionally followed by quotes/parens/spaces
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])["\')]?\s+')

_composer = EmotionComposer()


def extract_complete_sentence(text: str) -> tuple[str, str]:
    """Split *text* at the first sentence boundary.

    Returns ``(sentence, remainder)`` where *sentence* is the first complete
    sentence (including its terminal punctuation) and *remainder* is the rest.
    If no sentence boundary is found, returns ``('', text)``.
    """
    m = _SENTENCE_END_RE.search(text)
    if m:
        return text[: m.start() + 1].strip(), text[m.end():]
    return "", text


async def stream_speech(
    text_stream: AsyncIterator[str],
    sentiment,
    user_profile: dict | None = None,
    context: dict | None = None,
    provider=None,
) -> AsyncGenerator[dict, None]:
    """Stream TTS audio as sentences complete while the LLM is still generating.

    Yields dicts with keys:
      ``audio``    — raw audio bytes chunk
      ``text``     — the sentence being spoken
      ``emotion``  — emotion label string
      ``phonemes`` — phoneme timing data if available (else None)
    """
    from cortex.voice.providers import get_tts_provider

    user_profile = user_profile or {}
    context = context or {}
    tts = provider or get_tts_provider()

    voice = user_profile.get("preferred_voice", "tara")
    emotion_label = getattr(sentiment, "label", "neutral")

    buffer = ""

    async def _speak_sentence(sentence: str) -> AsyncGenerator[dict, None]:
        tagged = _composer.compose(
            sentence,
            sentiment,
            confidence=user_profile.get("confidence", 0.8),
            user_profile=user_profile,
            context=context,
            provider=tts,
        )
        # Parler returns (text, description) — use just the text for audio
        if isinstance(tagged, tuple):
            tagged = tagged[0]

        async for audio_chunk in tts.synthesize(tagged, voice=voice, stream=True):
            yield {
                "audio": audio_chunk,
                "text": sentence,
                "emotion": emotion_label,
                "phonemes": None,
            }

    async for token in text_stream:
        buffer += token
        sentence, remaining = extract_complete_sentence(buffer)
        if sentence:
            buffer = remaining
            async for item in _speak_sentence(sentence):
                yield item

    # Flush any remaining text
    if buffer.strip():
        async for item in _speak_sentence(buffer.strip()):
            yield item
