"""Orpheus TTS — single container: llama.cpp CUDA + SNAC streaming.

Exposes POST /v1/audio/speech (OpenAI-compatible) that cortex calls.
Runs llama.cpp server (CUDA) for token generation, SNAC (CPU) for audio decoding.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import struct
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiohttp
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from snac import SNAC

# ── Configuration ──────────────────────────────────────────────────

MODEL_PATH = os.environ.get(
    "ORPHEUS_MODEL_PATH",
    "/models/Orpheus-3b-FT-Q4_K_M.gguf",
)
SNAC_MODEL = os.environ.get("SNAC_MODEL", "hubertsiuzdak/snac_24khz")
LLAMA_PORT = int(os.environ.get("LLAMA_PORT", "8080"))
LLAMA_GPU_LAYERS = int(os.environ.get("LLAMA_GPU_LAYERS", "99"))
LLAMA_CTX_SIZE = int(os.environ.get("LLAMA_CTX_SIZE", "4096"))
SERVER_PORT = int(os.environ.get("ORPHEUS_PORT", "5005"))
SERVER_HOST = os.environ.get("ORPHEUS_HOST", "0.0.0.0")
SAMPLE_RATE = 24000
BITS_PER_SAMPLE = 16
CHANNELS = 1

# Orpheus token constants
CODE_TOKEN_OFFSET = 128266
STOP_TOKEN = "<custom_token_2>"

# Streaming chunk sizes (in 7-token SNAC groups)
INITIAL_CHUNK_GROUPS = 3   # ~0.25s first audio for low latency
STREAM_CHUNK_GROUPS = 30   # ~2.5s subsequent chunks

DEFAULT_TEMPERATURE = 0.6
DEFAULT_TOP_P = 0.9
DEFAULT_REPEAT_PENALTY = 1.1
DEFAULT_MAX_TOKENS = 4096

VALID_VOICES = {"tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("orpheus-tts")

# ── Globals ────────────────────────────────────────────────────────

snac_model: SNAC | None = None
llama_process: subprocess.Popen | None = None
SNAC_DEVICE = "cpu"  # CPU to leave GPU memory for llama.cpp


# ── llama.cpp subprocess ───────────────────────────────────────────

def start_llama() -> subprocess.Popen:
    """Launch llama-server as a subprocess."""
    cmd = [
        "/app/llama-server",
        "-m", MODEL_PATH,
        "--host", "127.0.0.1",
        "--port", str(LLAMA_PORT),
        "--n-gpu-layers", str(LLAMA_GPU_LAYERS),
        "--ctx-size", str(LLAMA_CTX_SIZE),
        "--n-predict", str(DEFAULT_MAX_TOKENS),
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
        "--no-mmap",
    ]
    logger.info("Starting llama.cpp: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)


async def wait_for_llama(timeout: int = 300) -> None:
    """Poll llama.cpp health until ready."""
    url = f"http://127.0.0.1:{LLAMA_PORT}/health"
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("status") == "ok":
                            logger.info("llama.cpp ready (%.1fs)", time.monotonic() - start)
                            return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"llama.cpp failed to start within {timeout}s")


# ── SNAC decoding ──────────────────────────────────────────────────

def redistribute_codes(code_list: list[int]) -> torch.Tensor:
    """Redistribute flat SNAC tokens into 3-layer format and decode."""
    if not code_list:
        return torch.tensor([[]], dtype=torch.float32)

    num_groups = len(code_list) // 7
    if num_groups == 0:
        return torch.tensor([[]], dtype=torch.float32)

    code_list = code_list[:num_groups * 7]
    layer_1, layer_2, layer_3 = [], [], []

    for i in range(num_groups):
        b = 7 * i
        layer_1.append(code_list[b])
        layer_2.append(code_list[b + 1] - 4096)
        layer_3.append(code_list[b + 2] - 2 * 4096)
        layer_3.append(code_list[b + 3] - 3 * 4096)
        layer_2.append(code_list[b + 4] - 4 * 4096)
        layer_3.append(code_list[b + 5] - 5 * 4096)
        layer_3.append(code_list[b + 6] - 6 * 4096)

    codes = [
        torch.tensor(layer_1, device=SNAC_DEVICE).unsqueeze(0),
        torch.tensor(layer_2, device=SNAC_DEVICE).unsqueeze(0),
        torch.tensor(layer_3, device=SNAC_DEVICE).unsqueeze(0),
    ]
    with torch.no_grad():
        audio_hat = snac_model.decode(codes)
    return audio_hat


def audio_to_pcm16(audio_tensor: torch.Tensor, fade_ms: int = 5) -> bytes:
    """Convert SNAC output tensor to 16-bit PCM bytes with fade."""
    if audio_tensor is None or audio_tensor.numel() == 0:
        return b""

    audio = audio_tensor.squeeze().cpu().float().numpy()
    audio = np.clip(audio, -1.0, 1.0)

    fade_samples = int(SAMPLE_RATE * fade_ms / 1000)
    if len(audio) > 2 * fade_samples and fade_samples > 0:
        fade_in = np.linspace(0, 1, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1, 0, fade_samples, dtype=np.float32)
        audio[:fade_samples] *= fade_in
        audio[-fade_samples:] *= fade_out

    pcm = (audio * 32767).astype(np.int16)
    return pcm.tobytes()


def make_wav_header(data_size: int = 0x7FFFFFFF) -> bytes:
    """Create a WAV header for streaming (unknown length)."""
    hdr = io.BytesIO()
    hdr.write(b"RIFF")
    hdr.write(struct.pack("<I", data_size + 36))
    hdr.write(b"WAVE")
    hdr.write(b"fmt ")
    hdr.write(struct.pack("<I", 16))
    hdr.write(struct.pack("<H", 1))  # PCM
    hdr.write(struct.pack("<H", CHANNELS))
    hdr.write(struct.pack("<I", SAMPLE_RATE))
    byte_rate = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    hdr.write(struct.pack("<I", byte_rate))
    hdr.write(struct.pack("<H", CHANNELS * BITS_PER_SAMPLE // 8))
    hdr.write(struct.pack("<H", BITS_PER_SAMPLE))
    hdr.write(b"data")
    hdr.write(struct.pack("<I", data_size))
    return hdr.getvalue()


# ── Request model ──────────────────────────────────────────────────

class SpeechRequest(BaseModel):
    """OpenAI-compatible /v1/audio/speech request."""
    input: str
    model: str = "orpheus"
    voice: str = "tara"
    response_format: str = "wav"
    stream: bool = True
    emotion: str | None = None
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float = DEFAULT_TOP_P


# ── Streaming audio generation ─────────────────────────────────────

async def generate_audio_stream(
    text: str,
    voice: str = "tara",
    emotion: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    top_p: float = DEFAULT_TOP_P,
) -> AsyncGenerator[bytes, None]:
    """Stream WAV audio via llama.cpp + SNAC."""
    loop = asyncio.get_event_loop()

    # WAV header first for immediate client buffering
    yield make_wav_header()

    # Format prompt: "voice, emotion: text" wrapped in Orpheus special tokens
    bare_voice = voice.replace("orpheus_", "")
    if bare_voice not in VALID_VOICES:
        bare_voice = "tara"

    if emotion:
        inner = f"{bare_voice}, {emotion}: {text}"
    else:
        inner = f"{bare_voice}: {text}"

    prompt = f"<|audio|>{inner}<|eot_id|>"
    logger.info("Generating: voice=%s text='%s'", bare_voice, text[:80])

    # Call llama.cpp /completion endpoint with streaming
    url = f"http://127.0.0.1:{LLAMA_PORT}/completion"
    payload = {
        "prompt": prompt,
        "n_predict": DEFAULT_MAX_TOKENS,
        "temperature": temperature,
        "top_p": top_p,
        "repeat_penalty": DEFAULT_REPEAT_PENALTY,
        "stream": True,
        "stop": [STOP_TOKEN],
    }

    collected_tokens: list[int] = []
    processed_count = 0
    first_chunk = True

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    logger.error("llama.cpp error: %d", resp.status)
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # strip "data: " prefix
                    if data_str == "[DONE]":
                        break

                    try:
                        obj = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # llama.cpp streams tokens with content and token IDs
                    # Extract tokens from the completion response
                    if obj.get("stop"):
                        break

                    # Get token IDs from the "tokens" field if available,
                    # otherwise parse from content
                    token_ids = obj.get("tokens", [])
                    if not token_ids and "content" in obj:
                        content = obj["content"]
                        # Check for custom tokens like <custom_token_N>
                        if "<custom_token_" in content:
                            import re
                            matches = re.findall(r"<custom_token_(\d+)>", content)
                            for m in matches:
                                tid = 128256 + int(m)
                                if tid >= CODE_TOKEN_OFFSET:
                                    collected_tokens.append(tid - CODE_TOKEN_OFFSET)
                            continue

                    for tid in token_ids:
                        if tid >= CODE_TOKEN_OFFSET:
                            collected_tokens.append(tid - CODE_TOKEN_OFFSET)

                    # Check if we have enough tokens for a SNAC chunk
                    total = len(collected_tokens)
                    chunk_groups = (
                        INITIAL_CHUNK_GROUPS if first_chunk
                        else STREAM_CHUNK_GROUPS
                    )
                    chunk_size = chunk_groups * 7

                    if total >= processed_count + chunk_size:
                        n = ((total - processed_count) // chunk_size) * chunk_size
                        end = processed_count + n
                        codes = collected_tokens[processed_count:end]

                        audio_hat = await loop.run_in_executor(
                            None, redistribute_codes, codes
                        )
                        pcm = audio_to_pcm16(audio_hat, fade_ms=5)
                        if pcm:
                            yield pcm
                            first_chunk = False

                        processed_count = end

        # Flush remaining tokens
        if len(collected_tokens) > processed_count:
            remaining = collected_tokens[processed_count:]
            final_len = (len(remaining) // 7) * 7
            if final_len > 0:
                codes = remaining[:final_len]
                audio_hat = await loop.run_in_executor(
                    None, redistribute_codes, codes
                )
                pcm = audio_to_pcm16(audio_hat, fade_ms=5)
                if pcm:
                    yield pcm

        logger.info("Stream complete: '%s'", text[:50])

    except Exception as e:
        logger.error("Audio generation failed: %s", e, exc_info=True)


# ── FastAPI app ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start llama.cpp, load SNAC."""
    global snac_model, llama_process

    logger.info("Loading SNAC model: %s on %s", SNAC_MODEL, SNAC_DEVICE)
    snac_model = SNAC.from_pretrained(SNAC_MODEL)
    snac_model = snac_model.to(SNAC_DEVICE)
    snac_model.eval()
    logger.info("SNAC model loaded")

    logger.info("Starting llama.cpp CUDA server...")
    llama_process = start_llama()
    await wait_for_llama(timeout=300)

    logger.info("Orpheus TTS ready on port %d", SERVER_PORT)
    yield

    if llama_process and llama_process.poll() is None:
        logger.info("Stopping llama.cpp...")
        llama_process.terminate()
        llama_process.wait(timeout=10)


app = FastAPI(title="Orpheus TTS (llama.cpp CUDA)", lifespan=lifespan)


@app.post("/v1/audio/speech")
async def create_speech(request: SpeechRequest):
    """OpenAI-compatible speech endpoint — streams WAV audio."""
    if not request.input.strip():
        raise HTTPException(400, "input text is required")

    text = request.input
    emotion = request.emotion
    voice = request.voice.replace("orpheus_", "")

    # Support "voice, emotion: text" prompt format from cortex
    if ", " in text and ": " in text:
        parts = text.split(": ", 1)
        if len(parts) == 2:
            prefix, actual_text = parts
            prefix_parts = [p.strip() for p in prefix.split(",")]
            if prefix_parts[0] in VALID_VOICES:
                voice = prefix_parts[0]
                if len(prefix_parts) > 1:
                    emotion = prefix_parts[1]
                text = actual_text

    return StreamingResponse(
        generate_audio_stream(text, voice, emotion,
                              request.temperature, request.top_p),
        media_type="audio/wav",
        headers={"Transfer-Encoding": "chunked"},
    )


@app.get("/v1/audio/voices")
async def list_voices():
    """List available voices."""
    return {
        "voices": [
            {"id": f"orpheus_{v}", "name": v.title(), "provider": "orpheus"}
            for v in sorted(VALID_VOICES)
        ]
    }


@app.get("/health")
@app.get("/docs")
async def health():
    """Health check."""
    ok = llama_process is not None and llama_process.poll() is None
    return {
        "status": "ok" if ok else "degraded",
        "model": MODEL_PATH,
        "backend": "llama.cpp CUDA",
        "snac_device": SNAC_DEVICE,
    }


@app.get("/")
async def root():
    return {"message": "Orpheus TTS (llama.cpp CUDA + SNAC) — single container"}


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info",
    )
