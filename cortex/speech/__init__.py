"""cortex.speech — Speech Service (TTS & STT)

OWNERSHIP: This module owns ALL audio synthesis and transcription.
ENTRY POINT: Import from this file only.
FORBIDDEN: No other module should directly call Kokoro, Orpheus, Piper, or Whisper.
"""

# Module ownership: All audio synthesis and transcription
from __future__ import annotations

from cortex.speech.tts import synthesize_speech, stream_orpheus, extract_pcm
from cortex.speech.stt import transcribe, is_hallucinated
from cortex.speech.voices import resolve_voice, to_orpheus_voice, ORPHEUS_VOICES

__all__ = [
    "synthesize_speech", "stream_orpheus", "extract_pcm",
    "transcribe", "is_hallucinated",
    "resolve_voice", "to_orpheus_voice", "ORPHEUS_VOICES",
]
