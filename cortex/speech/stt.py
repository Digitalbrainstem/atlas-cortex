"""Speech-to-text transcription service.

Supports whisper.cpp (HTTP) and Wyoming (TCP) backends.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_STT_BACKEND = os.environ.get("STT_BACKEND", "whisper_cpp")
_STT_HOST = os.environ.get("STT_HOST", "localhost")
_STT_PORT = int(os.environ.get("STT_PORT", "10300"))


def is_hallucinated(transcript: str) -> bool:
    """Detect whisper hallucination patterns (repeated phrases, noise)."""
    lower = transcript.lower().strip().rstrip(".")
    hallucination_exact = {
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "transcription by castingwords",
        "subtitles by the amara.org community",
        "you",
        "...",
        "okay",
        "thank you",
        "thanks",
        "bye",
        "goodbye",
        "hmm",
    }
    if lower in hallucination_exact:
        return True

    segments = [s.strip() for s in transcript.replace("\n", " ").split(".") if s.strip()]
    if len(segments) >= 4:
        unique = set(s.lower() for s in segments)
        if len(unique) <= 2:
            return True

    hallucination_prefixes = (
        "i'm going to go",
        "i'm going to get",
        "i'm going to do",
        "i'm going to take",
        "i'm going to have",
        "so i'm going to",
        "and i'm going to",
    )
    if lower.startswith(hallucination_prefixes):
        return True

    return False


async def transcribe(audio_data: bytes, *, sample_rate: int = 16000) -> str:
    """Transcribe audio to text using the configured STT backend.

    Returns the transcript string (may be empty if no speech detected).
    Raises on STT backend failure.
    """
    if _STT_BACKEND == "whisper_cpp":
        from cortex.voice.whisper_cpp import WhisperCppClient
        client = WhisperCppClient(_STT_HOST, _STT_PORT, timeout=60.0)
        return await client.transcribe(audio_data, sample_rate=sample_rate)
    else:
        from cortex.voice.wyoming import WyomingClient
        client = WyomingClient(_STT_HOST, _STT_PORT, timeout=30.0)
        return await client.transcribe(audio_data, sample_rate=sample_rate)
