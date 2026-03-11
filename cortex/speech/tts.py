"""Text-to-speech synthesis service.

Multi-provider TTS with automatic fallback:
  Orpheus (GPU, highest quality) → Kokoro (CPU, fast) → Piper (CPU, last resort).
"""
from __future__ import annotations

import io
import logging
import os
import time
import wave
from collections.abc import AsyncGenerator

from cortex.speech.voices import to_orpheus_voice, KOKORO_VOICE

logger = logging.getLogger(__name__)

# Provider configuration — mirrors cortex/satellite/websocket.py env vars
_TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "orpheus")
_ORPHEUS_URL = os.environ.get("ORPHEUS_FASTAPI_URL", "http://localhost:5005")
_KOKORO_HOST = os.environ.get("KOKORO_HOST", "localhost")
_KOKORO_PORT = int(os.environ.get("KOKORO_PORT", "8880"))
_PIPER_HOST = os.environ.get("PIPER_HOST", os.environ.get("TTS_HOST", "localhost"))
_PIPER_PORT = int(os.environ.get("PIPER_PORT", os.environ.get("TTS_PORT", "10200")))


def extract_pcm(raw_audio: bytes, default_rate: int = 24000) -> tuple[bytes, int]:
    """Extract PCM data and sample rate from WAV or raw audio."""
    if raw_audio and raw_audio[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw_audio), "rb") as wf:
            return wf.readframes(wf.getnframes()), wf.getframerate()
    return raw_audio, default_rate


async def synthesize_speech(
    text: str, voice: str, *, fast: bool = False
) -> tuple[bytes, int, str]:
    """Synthesize text to PCM audio using available TTS providers.

    Returns ``(pcm_audio, sample_rate, provider_name)``.

    Provider priority: Orpheus (GPU) → Kokoro → Piper.
    When *fast* is True, prefer Kokoro (CPU, ~200 ms) over Orpheus (GPU, ~5 s)
    for latency-sensitive paths like instant answers and fillers.
    """
    from cortex.voice.wyoming import WyomingClient

    # --- Fast path: Kokoro first for instant answers/fillers ---
    if fast or _TTS_PROVIDER == "kokoro":
        try:
            from cortex.voice.kokoro import KokoroClient
            kokoro = KokoroClient(_KOKORO_HOST, _KOKORO_PORT, timeout=15.0)
            kokoro_voice = voice if voice and not voice.startswith("orpheus_") else KOKORO_VOICE
            raw, info = await kokoro.synthesize(text, voice=kokoro_voice, response_format="wav")
            if raw:
                pcm, rate = extract_pcm(raw, info.get("rate", 24000))
                return pcm, rate, "kokoro"
        except Exception as e:
            logger.warning("Kokoro TTS failed: %s", e)

    # --- Orpheus (CUDA GPU, higher quality) ---
    if _TTS_PROVIDER in ("orpheus", "auto"):
        try:
            import aiohttp
            bare_voice = to_orpheus_voice(voice)
            payload = {
                "input": text,
                "model": "orpheus",
                "voice": bare_voice,
                "response_format": "wav",
                "stream": False,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_ORPHEUS_URL}/v1/audio/speech",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if raw:
                            pcm, rate = extract_pcm(raw, 24000)
                            return pcm, rate, "orpheus"
                    else:
                        error = await resp.text()
                        logger.warning("Orpheus TTS failed (%d): %s", resp.status, error[:200])
        except Exception as e:
            logger.warning("Orpheus TTS failed: %s", e)

    # --- Kokoro (fallback if Orpheus fails) ---
    if not fast:
        try:
            from cortex.voice.kokoro import KokoroClient
            kokoro = KokoroClient(_KOKORO_HOST, _KOKORO_PORT, timeout=15.0)
            kokoro_voice = voice if voice and not voice.startswith("orpheus_") else KOKORO_VOICE
            raw, info = await kokoro.synthesize(text, voice=kokoro_voice, response_format="wav")
            if raw:
                pcm, rate = extract_pcm(raw, info.get("rate", 24000))
                return pcm, rate, "kokoro"
        except Exception as e:
            logger.warning("Kokoro TTS failed: %s", e)

    # --- Piper (last resort) ---
    try:
        piper = WyomingClient(_PIPER_HOST, _PIPER_PORT, timeout=15.0)
        piper_voice = voice if voice and not voice.startswith("orpheus_") else None
        audio, info = await piper.synthesize(text, voice=piper_voice)
        return audio, info.get("rate", 22050), "piper"
    except Exception as e:
        logger.warning("Piper TTS failed: %s", e)

    return b"", 24000, "none"


async def stream_orpheus(
    text: str,
    voice: str,
    *,
    timeout: float = 60,
) -> AsyncGenerator[tuple[bytes, bool], None]:
    """Stream PCM audio from Orpheus TTS.

    Yields ``(pcm_chunk, is_first_chunk)`` tuples.
    Handles WAV header skipping and chunk buffering internally.
    The caller is responsible for framing (TTS_START/CHUNK/END messages).
    """
    import aiohttp

    bare_voice = to_orpheus_voice(voice)
    payload = {
        "input": text,
        "model": "orpheus",
        "voice": bare_voice,
        "response_format": "wav",
        "stream": True,
    }

    wav_header_skipped = False
    pcm_buffer = bytearray()
    first_yielded = False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_ORPHEUS_URL}/v1/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.warning("Orpheus stream failed (%d): %s", resp.status, error[:200])
                    return

                async for chunk in resp.content.iter_any():
                    if not chunk:
                        continue

                    pcm_buffer.extend(chunk)

                    # Skip WAV header (44 bytes) on first data
                    if not wav_header_skipped:
                        if len(pcm_buffer) < 44:
                            continue
                        if pcm_buffer[:4] == b"RIFF":
                            wav_header_skipped = True
                            pcm_buffer = pcm_buffer[44:]
                        else:
                            wav_header_skipped = True

                    # Yield 4096-byte PCM chunks as they arrive
                    while len(pcm_buffer) >= 4096:
                        out = bytes(pcm_buffer[:4096])
                        pcm_buffer = pcm_buffer[4096:]
                        is_first = not first_yielded
                        first_yielded = True
                        yield out, is_first

                # Flush remaining PCM
                if pcm_buffer:
                    is_first = not first_yielded
                    first_yielded = True
                    yield bytes(pcm_buffer), is_first

    except Exception as e:
        logger.warning("Orpheus streaming failed: %s", e)
