"""Abstract TTS provider interface (C11.1)."""

from __future__ import annotations


class TTSProvider:
    """Abstract interface for any TTS backend."""

    def __init__(self, config: dict | None = None):
        pass

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        emotion: str | None = None,
        speed: float = 1.0,
        stream: bool = True,
        **kwargs,
    ):
        """Convert text to audio.

        Yields audio chunks (bytes) when stream=True,
        or returns a single bytes object when stream=False.
        """
        raise NotImplementedError

    async def list_voices(self) -> list[dict]:
        """Return available voices as list of dicts with id/name/gender/style/language."""
        raise NotImplementedError

    def supports_emotion(self) -> bool:
        """Whether this provider supports emotional speech synthesis."""
        return False

    def supports_streaming(self) -> bool:
        """Whether this provider supports chunked audio streaming."""
        return False

    def supports_phonemes(self) -> bool:
        """Whether this provider outputs phoneme timing for avatar lip-sync."""
        return False

    def get_emotion_format(self) -> str | None:
        """How to encode emotion for this provider.

        Returns one of: 'tags' | 'description' | 'ssml' | None
        """
        return None
