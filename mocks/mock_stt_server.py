"""Mock STT (Whisper) server matching the whisper.cpp HTTP API.

Returns transcriptions from a lookup table keyed by audio length.
Simulates realistic Whisper transcription latency based on benchmark data.

Usage:
    python -m mocks.mock_stt_server --port 10300
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock Whisper STT (Atlas Cortex)")

_DATA_DIR = Path(__file__).parent / "data"

# Pre-loaded transcriptions keyed by approximate audio duration
_TRANSCRIPTIONS: list[dict] = []


def _load_data() -> None:
    results_path = _DATA_DIR / "benchmark_results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text())
        for r in results:
            _TRANSCRIPTIONS.append({
                "text": r["question_text"],
                "utterance_ms": r["estimated_utterance_duration_ms"],
                "stt_ms": r["estimated_stt_ms"],
            })
        logger.info("Loaded %d transcriptions", len(_TRANSCRIPTIONS))


@app.on_event("startup")
async def startup():
    _load_data()


@app.post("/inference")
async def inference(
    file: UploadFile = File(...),
    temperature: float = Form(0.0),
    response_format: str = Form("json"),
):
    """Mock whisper.cpp /inference endpoint."""
    audio_data = await file.read()
    audio_duration_ms = len(audio_data) / 32  # 16kHz 16-bit mono = 32 bytes/ms

    # Find closest matching transcription by audio duration
    best = None
    best_diff = float("inf")
    for t in _TRANSCRIPTIONS:
        diff = abs(t["utterance_ms"] - audio_duration_ms)
        if diff < best_diff:
            best_diff = diff
            best = t

    if best is None:
        best = {"text": "Hello", "stt_ms": 300}

    # Simulate STT processing time (scaled by audio length)
    # Base: 200ms + 50ms per second of audio
    base_ms = 200
    audio_seconds = audio_duration_ms / 1000
    sim_stt_ms = base_ms + audio_seconds * 50
    await asyncio.sleep(sim_stt_ms / 1000)

    return JSONResponse({
        "text": best["text"],
        "segments": [{
            "start": 0,
            "end": audio_duration_ms / 1000,
            "text": best["text"],
        }],
        "processing_ms": sim_stt_ms,
    })


@app.get("/")
async def health():
    return {"status": "ok", "mock": True, "model": "whisper-mock"}


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=10300)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
