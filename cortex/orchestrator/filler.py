"""Filler phrase dispatch for the orchestrator.

Handles the "thinking pause" filler: cache lookup → Orpheus stream → fallback.
Used by voice.py during the LLM streaming path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from cortex.speech import synthesize_speech

logger = logging.getLogger(__name__)


async def play_filler(
    conn: Any,
    text: str,
    voice: str,
    satellite_id: str,
    pause: float = 0.35,
) -> None:
    """Play a filler phrase to the satellite while the LLM generates.

    Tries cached filler first, then Orpheus streaming, then fallback TTS.
    Includes a natural thinking pause before playback.
    """
    from cortex.orchestrator.voice import (
        _stream_audio_to_satellite,
        _stream_orpheus_to_satellite,
    )

    await asyncio.sleep(pause)

    try:
        # Try pre-generated cache first (fastest — no TTS call)
        from cortex.filler.cache import get_filler_cache
        cache = get_filler_cache()
        cached = cache.get("question") if cache.ready else None
        if cached:
            logger.info(
                "Cached filler for %s: %r (%.1fs, %d bytes)",
                satellite_id, cached.phrase,
                cached.duration_ms / 1000, len(cached.audio))
            await _stream_audio_to_satellite(
                conn, cached.audio, cached.sample_rate,
                cached.phrase, is_filler=True)
            return

        # Stream filler via Orpheus (faster than buffered)
        filler_bytes, filler_elapsed = await _stream_orpheus_to_satellite(
            conn, text, voice, is_filler=True)
        if filler_bytes > 0:
            logger.info(
                "Filler TTS for %s: %r (%.0fms, %d bytes)",
                satellite_id, text,
                filler_elapsed * 1000, filler_bytes)
            return

        # Fallback: synthesize_speech (Kokoro/Piper)
        audio, rate, prov = await synthesize_speech(text, voice, fast=True)
        if audio:
            logger.info(
                "Filler TTS [%s] for %s: %r (%d bytes)",
                prov, satellite_id, text, len(audio))
            await _stream_audio_to_satellite(
                conn, audio, rate, text, is_filler=True)

    except Exception as e:
        logger.warning("Filler failed for %s: %s", satellite_id, e)
