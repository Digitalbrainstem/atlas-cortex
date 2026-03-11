"""Text processing helpers for the orchestrator.

Sentence splitting, help-offer detection, and text cleanup utilities.
"""
from __future__ import annotations

import re

# Sentence boundary for splitting full text
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

# Sentence boundary for streaming: punctuation followed by whitespace
_STREAM_SENT_RE = re.compile(r'[.!?]\s+')


# Generic LLM "help offer" closers that should NOT trigger auto-listen.
_HELP_OFFER_PATTERNS = (
    "what can i help",
    "how can i help",
    "how can i assist",
    "what would you like",
    "what do you need",
    "what information are you looking for",
    "what are you looking for",
    "what else can i",
    "anything else",
    "is there anything else",
    "what's the next question",
    "what's your next question",
    "need help with anything",
    "what topic",
    "what question",
    "where would you like to go",
    "what can i do for you",
    "how may i help",
    "what would you like to know",
    "what do you want to know",
    "what specific",
)


def is_help_offer(sentence: str) -> bool:
    """Return True if sentence is a generic LLM help-offer closer."""
    lower = sentence.lower().strip()
    return any(lower.startswith(p) or p in lower for p in _HELP_OFFER_PATTERNS)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences at punctuation boundaries.

    Keeps short fragments together to avoid tiny TTS calls.
    Minimum sentence length ~20 chars before splitting.
    """
    raw = _SENTENCE_RE.split(text.strip())
    if not raw:
        return [text.strip()] if text.strip() else []

    sentences: list[str] = []
    buf = ""
    for part in raw:
        buf = (buf + " " + part).strip() if buf else part
        if len(buf) >= 20:
            sentences.append(buf)
            buf = ""
    if buf:
        if sentences:
            sentences[-1] = sentences[-1] + " " + buf
        else:
            sentences.append(buf)
    return sentences


def should_auto_listen(full_response: str) -> bool:
    """Determine if Atlas should auto-listen after this response.

    Returns True for direct questions to the user, but NOT for generic
    LLM help-offer closers like "What can I help you with?"
    """
    last_sentence = full_response.rstrip().rsplit(".", 1)[-1].strip()
    return (
        last_sentence.endswith("?")
        and len(last_sentence) < 100
        and not last_sentence.lower().startswith(("i wonder", "who knows"))
        and not is_help_offer(last_sentence)
    )
