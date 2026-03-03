"""Mock TTS server matching OpenAI /v1/audio/speech and Kokoro API.

Returns synthesized silence/sine-wave PCM audio with realistic timing
derived from Kokoro TTS benchmarks on Intel Arc B580.

Timing model: 200ms base + 180ms per word (from real Kokoro measurements).
Audio format: PCM 24kHz 16-bit mono.

Usage:
    python -m mocks.mock_tts_server --port 8880
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import struct
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock TTS (Atlas Cortex)")

# TTS timing model from real Kokoro benchmarks
TTS_BASE_MS = 200
TTS_PER_WORD_MS = 180
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _generate_pcm_audio(duration_ms: float, frequency: float = 220.0) -> bytes:
    """Generate PCM audio with a soft tone (more realistic than silence)."""
    num_samples = int(SAMPLE_RATE * duration_ms / 1000)
    amplitude = 2000  # Soft volume

    samples = []
    for i in range(num_samples):
        t = i / SAMPLE_RATE
        # Gentle fade in/out
        envelope = 1.0
        fade_samples = int(SAMPLE_RATE * 0.05)  # 50ms fade
        if i < fade_samples:
            envelope = i / fade_samples
        elif i > num_samples - fade_samples:
            envelope = (num_samples - i) / fade_samples

        value = int(amplitude * envelope * math.sin(2 * math.pi * frequency * t))
        samples.append(struct.pack("<h", max(-32768, min(32767, value))))

    return b"".join(samples)


def _estimate_audio_duration_ms(text: str) -> float:
    """Estimate how long the spoken audio would be."""
    word_count = len(text.split())
    return max(200, word_count * 130)  # ~130ms per word at normal speech


def _estimate_synthesis_ms(text: str) -> float:
    """Estimate how long synthesis would take on real hardware."""
    word_count = len(text.split())
    return TTS_BASE_MS + word_count * TTS_PER_WORD_MS


@app.post("/v1/audio/speech")
async def speech(request: Request):
    """Mock OpenAI-compatible TTS endpoint."""
    body = await request.json()
    text = body.get("input", "")
    voice = body.get("voice", "default")
    model = body.get("model", "tts-1")
    stream = body.get("stream", False)

    # Simulate synthesis time
    synthesis_ms = _estimate_synthesis_ms(text)
    audio_duration_ms = _estimate_audio_duration_ms(text)

    if stream:
        # Stream audio in chunks (simulating progressive synthesis)
        async def stream_audio():
            chunk_duration_ms = 500  # 500ms chunks
            total_chunks = max(1, int(audio_duration_ms / chunk_duration_ms))
            synthesis_per_chunk = synthesis_ms / total_chunks

            for i in range(total_chunks):
                await asyncio.sleep(synthesis_per_chunk / 1000)
                chunk = _generate_pcm_audio(chunk_duration_ms)
                yield chunk

        return StreamingResponse(
            stream_audio(),
            media_type="audio/pcm",
            headers={
                "X-Sample-Rate": str(SAMPLE_RATE),
                "X-Audio-Duration-Ms": str(int(audio_duration_ms)),
                "X-Synthesis-Ms": str(int(synthesis_ms)),
            },
        )

    # Non-streaming: wait for full synthesis, return all audio
    await asyncio.sleep(synthesis_ms / 1000)
    audio = _generate_pcm_audio(audio_duration_ms)

    return Response(
        content=audio,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Audio-Duration-Ms": str(int(audio_duration_ms)),
            "X-Synthesis-Ms": str(int(synthesis_ms)),
            "X-Word-Count": str(len(text.split())),
        },
    )


@app.get("/v1/voices")
async def list_voices():
    """Mock voice listing."""
    return {
        "voices": [
            {"id": "af_heart", "name": "Heart", "gender": "female", "language": "en"},
            {"id": "af_bella", "name": "Bella", "gender": "female", "language": "en"},
            {"id": "am_adam", "name": "Adam", "gender": "male", "language": "en"},
            {"id": "am_michael", "name": "Michael", "gender": "male", "language": "en"},
        ]
    }


@app.get("/")
async def health():
    return {"status": "ok", "mock": True, "provider": "kokoro-mock"}


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
