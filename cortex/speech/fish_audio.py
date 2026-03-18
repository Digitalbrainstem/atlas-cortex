"""Fish Audio S2 TTS provider stub for multi-character story narration.

Fish Audio S2 is a high-quality TTS engine with 15,000+ inline emotion/style
tags, multi-speaker dialogue in a single pass, and zero-shot voice cloning
from 10-30 seconds of reference audio.

Owner: cortex.speech
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FISH_AUDIO_HOST = os.environ.get("FISH_AUDIO_HOST", "localhost")
FISH_AUDIO_PORT = int(os.environ.get("FISH_AUDIO_PORT", "8860"))
FISH_AUDIO_API_KEY = os.environ.get("FISH_AUDIO_API_KEY", "")


class FishAudioProvider:
    """Fish Audio S2 TTS provider for multi-character story narration.

    Features
    --------
    - 15,000+ inline emotion/style tags
    - Multi-speaker dialogue in single pass
    - Zero-shot voice cloning from 10-30s reference audio

    Currently a stub — methods log what they would do and return empty bytes.
    """

    provider_id: str = "fish_audio_s2"

    def __init__(self) -> None:
        self._base_url = (
            f"http://{FISH_AUDIO_HOST}:{FISH_AUDIO_PORT}"
        )
        logger.info("FishAudioProvider initialised (base_url=%s)", self._base_url)

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice_id: str = "",
        *,
        style_tags: str = "",
        reference_audio: str = "",
    ) -> bytes:
        """Generate speech for *text*.

        Parameters
        ----------
        text:
            The text to synthesize.
        voice_id:
            Target voice identifier (Fish Audio voice catalogue).
        style_tags:
            Inline emotion/style tags, e.g. ``"[cheerful, fast]"``.
        reference_audio:
            Path to a reference WAV for zero-shot voice cloning.

        Returns
        -------
        bytes
            PCM audio bytes.  Currently a stub — returns empty bytes.
        """
        logger.info(
            "[STUB] FishAudio synthesize: text=%r voice=%s style=%s ref=%s",
            text[:60], voice_id, style_tags, reference_audio,
        )
        return b""

    async def synthesize_dialogue(self, segments: list[dict]) -> bytes:
        """Generate multi-speaker dialogue in a single pass.

        Parameters
        ----------
        segments:
            List of ``{speaker, text, voice_id, style}`` dicts describing
            each dialogue line.  Fish Audio S2 can render all speakers in
            one forward pass.

        Returns
        -------
        bytes
            PCM audio bytes.  Currently a stub — returns empty bytes.
        """
        speakers = {s.get("speaker", "?") for s in segments}
        logger.info(
            "[STUB] FishAudio synthesize_dialogue: %d segments, speakers=%s",
            len(segments), speakers,
        )
        return b""

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Check if Fish Audio S2 server is running.

        Currently a stub — always returns ``False`` (server not deployed).
        """
        logger.debug("[STUB] FishAudio health check → False")
        return False
