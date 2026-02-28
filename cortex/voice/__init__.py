"""cortex.voice — Voice & Speech Engine (C11).

Public re-exports for convenient import.
"""

from cortex.voice.base import TTSProvider
from cortex.voice.composer import EmotionComposer
from cortex.voice.identity import IdentifyResult, SpeakerIdentifier
from cortex.voice.providers import OrpheusTTSProvider, PiperTTSProvider, get_tts_provider
from cortex.voice.registry import (
    init_voice_registry,
    get_default_voice,
    list_voices,
)
from cortex.voice.streaming import extract_complete_sentence, stream_speech

__all__ = [
    "TTSProvider",
    "EmotionComposer",
    "IdentifyResult",
    "SpeakerIdentifier",
    "OrpheusTTSProvider",
    "PiperTTSProvider",
    "get_tts_provider",
    "init_voice_registry",
    "get_default_voice",
    "list_voices",
    "extract_complete_sentence",
    "stream_speech",
]
