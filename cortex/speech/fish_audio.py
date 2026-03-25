"""Fish Audio S2 TTS provider for multi-character story narration.

Fish Audio S2 / Fish Speech 1.5 is a high-quality TTS engine with
multi-speaker dialogue in a single pass and zero-shot voice cloning
from 10-30 seconds of reference audio.

The server exposes an OpenAI-compatible API at ``/v1/audio/speech``
as well as a batch dialogue endpoint.

Owner: cortex.speech
"""
from __future__ import annotations

import io
import logging
import os
import wave

import aiohttp

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
    - Multi-speaker dialogue in single pass
    - Zero-shot voice cloning from 10-30s reference audio
    - OpenAI-compatible ``/v1/audio/speech`` endpoint
    """

    provider_id: str = "fish_audio_s2"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self._host = host or FISH_AUDIO_HOST
        self._port = port or FISH_AUDIO_PORT
        self._api_key = api_key or FISH_AUDIO_API_KEY
        self._base_url = f"http://{self._host}:{self._port}"
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
        """Generate speech for *text* via the OpenAI-compatible endpoint.

        Parameters
        ----------
        text:
            The text to synthesize.
        voice_id:
            Target voice identifier (Fish Audio voice catalogue).
        style_tags:
            Inline emotion/style tags prepended to the text.
        reference_audio:
            Path to a reference WAV for zero-shot voice cloning.

        Returns
        -------
        bytes
            Raw PCM audio bytes (extracted from WAV response).
        """
        url = f"{self._base_url}/v1/audio/speech"

        # Prepend style tags to the input text when present
        input_text = f"{style_tags} {text}" if style_tags else text

        payload: dict = {
            "model": "fish-speech-1.5",
            "input": input_text,
            "voice": voice_id or "default",
            "response_format": "wav",
        }

        headers = self._auth_headers()

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.warning(
                            "FishAudio synthesize failed (%d): %s",
                            resp.status, error[:200],
                        )
                        return b""
                    wav_data = await resp.read()
                    return _wav_to_pcm(wav_data)
        except Exception as exc:
            logger.warning("FishAudio synthesize error: %s", exc)
            return b""

    async def synthesize_dialogue(self, segments: list[dict]) -> bytes:
        """Generate multi-speaker dialogue in a single pass.

        Parameters
        ----------
        segments:
            List of ``{speaker, text, voice_id, style}`` dicts describing
            each dialogue line.

        Returns
        -------
        bytes
            Raw PCM audio bytes for the complete dialogue.
        """
        speakers = {s.get("speaker", "?") for s in segments}
        logger.info(
            "FishAudio synthesize_dialogue: %d segments, speakers=%s",
            len(segments), speakers,
        )

        # Build the combined text payload with speaker annotations
        parts = []
        for seg in segments:
            speaker = seg.get("speaker", "narrator")
            text = seg.get("text", "")
            style = seg.get("style", "")
            prefix = f"[{speaker}]" if speaker else ""
            style_prefix = f"({style})" if style else ""
            parts.append(f"{prefix}{style_prefix} {text}".strip())

        combined_text = "\n".join(parts)
        primary_voice = segments[0].get("voice_id", "default") if segments else "default"

        url = f"{self._base_url}/v1/audio/speech"
        payload: dict = {
            "model": "fish-speech-1.5",
            "input": combined_text,
            "voice": primary_voice,
            "response_format": "wav",
        }

        headers = self._auth_headers()

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.warning(
                            "FishAudio dialogue failed (%d): %s",
                            resp.status, error[:200],
                        )
                        return b""
                    wav_data = await resp.read()
                    return _wav_to_pcm(wav_data)
        except Exception as exc:
            logger.warning("FishAudio dialogue error: %s", exc)
            return b""

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Check if Fish Audio server is reachable."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                # Fish Speech serves docs at root; any 2xx means alive
                async with session.get(f"{self._base_url}/v1/models") as resp:
                    return resp.status < 500
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Build authorization headers when an API key is configured."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}


def _wav_to_pcm(wav_data: bytes) -> bytes:
    """Extract raw PCM frames from WAV data."""
    if not wav_data:
        return b""
    if wav_data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wf:
                return wf.readframes(wf.getnframes())
        except Exception:
            pass
    return wav_data
