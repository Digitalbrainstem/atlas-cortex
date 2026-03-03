#!/usr/bin/env python3
"""End-to-end voice pipeline benchmark via WebSocket.

Sends synthesized audio of each question through the satellite WebSocket
protocol and captures real timing from the server for STT, pipeline, TTS.

The STT will not transcribe correctly (TTS audio ≠ human speech), so
this benchmark also sends the question text directly via the text API
to get the correct LLM response. The timing from the voice path is
the ground truth.

Usage:
    python -m mocks.benchmark_voice --server ws://192.168.3.8:5100
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# Import corpus from benchmark.py
from mocks.benchmark import CORPUS, Question

KOKORO_URL = os.environ.get("KOKORO_URL", "http://192.168.3.8:8880")


@dataclass
class VoiceResult:
    """End-to-end voice pipeline timing for one question."""
    question_id: str
    question_text: str
    category: str

    # Client-side timing (real)
    audio_generated_ms: float = 0      # Time to generate question audio via TTS
    audio_duration_ms: float = 0       # Duration of question audio sent
    audio_send_ms: float = 0           # Time to stream audio over WebSocket
    time_to_first_tts_ms: float = 0    # From audio_end to first TTS_START
    tts_stream_ms: float = 0           # Duration of TTS streaming
    total_e2e_ms: float = 0            # Full end-to-end

    # Server-side timing (from logs, real)
    server_stt_ms: float = 0           # Whisper transcription
    server_llm_ms: float = 0           # LLM generation
    server_llm_ttft_ms: float = 0      # Time to first LLM token
    server_total_s: float = 0          # Server's total pipeline time
    server_tts_bytes: int = 0          # TTS audio bytes generated
    server_tts_provider: str = ""
    server_sentences: int = 0

    # Filler info
    filler_text: str = ""
    filler_audio_bytes: int = 0

    # TTS response received
    tts_bytes_received: int = 0
    tts_chunks_received: int = 0


async def synthesize_question_audio(
    client: httpx.AsyncClient,
    text: str,
) -> tuple[bytes, float]:
    """Use Kokoro to generate 16kHz PCM audio of the question text."""
    t0 = time.monotonic()
    resp = await client.post(
        f"{KOKORO_URL}/v1/audio/speech",
        json={"input": text, "voice": "am_adam", "model": "kokoro"},
        timeout=60.0,
    )
    gen_ms = (time.monotonic() - t0) * 1000

    # Resample 24kHz -> 16kHz (satellite sends 16kHz)
    raw_24k = resp.content
    if len(raw_24k) < 4:
        return b"\x00" * 3200, gen_ms  # 100ms silence fallback

    samples_24k = struct.unpack(f"<{len(raw_24k) // 2}h", raw_24k)
    samples_16k = []
    pos = 0.0
    while int(pos) < len(samples_24k):
        samples_16k.append(samples_24k[int(pos)])
        pos += 1.5
    audio_16k = struct.pack(f"<{len(samples_16k)}h", *samples_16k)

    return audio_16k, gen_ms


async def run_voice_question(
    ws_url: str,
    client: httpx.AsyncClient,
    question: Question,
) -> VoiceResult:
    """Send one question through the full voice pipeline via WebSocket."""
    import websockets

    result = VoiceResult(
        question_id=question.id,
        question_text=question.text,
        category=question.category,
    )

    # Generate audio of the question
    audio_16k, gen_ms = await synthesize_question_audio(client, question.text)
    result.audio_generated_ms = gen_ms
    result.audio_duration_ms = len(audio_16k) / 32  # 16kHz 16-bit = 32 bytes/ms

    # Connect as mock satellite
    async with websockets.connect(ws_url, max_size=10_000_000) as ws:
        # ANNOUNCE
        await ws.send(json.dumps({
            "type": "ANNOUNCE",
            "satellite_id": f"bench-{question.id}",
            "capabilities": ["wake_word"],
            "hostname": "benchmark",
            "room": "test",
        }))
        resp = json.loads(await ws.recv())
        if resp["type"] != "ACCEPTED":
            logger.error("Not accepted: %s", resp)
            return result

        # WAKE
        await ws.send(json.dumps({
            "type": "WAKE",
            "wake_word_confidence": 0.95,
        }))

        # AUDIO_START
        await ws.send(json.dumps({
            "type": "AUDIO_START",
            "format": "pcm_16k_16bit_mono",
            "format_info": {"rate": 16000, "width": 2, "channels": 1},
        }))

        # Stream audio chunks (480 samples = 30ms each, real-time)
        t_send_start = time.monotonic()
        chunk_bytes = 480 * 2
        for i in range(0, len(audio_16k), chunk_bytes):
            chunk = audio_16k[i : i + chunk_bytes]
            await ws.send(json.dumps({
                "type": "AUDIO_CHUNK",
                "audio": base64.b64encode(chunk).decode(),
            }))
            await asyncio.sleep(0.03)  # 30ms real-time

        # AUDIO_END
        await ws.send(json.dumps({
            "type": "AUDIO_END",
            "reason": "vad_silence",
        }))
        t_audio_end = time.monotonic()
        result.audio_send_ms = (t_audio_end - t_send_start) * 1000

        # Receive TTS responses
        t_first_tts = None
        t_last_tts = None

        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))

                if msg["type"] == "TTS_START":
                    if t_first_tts is None:
                        t_first_tts = time.monotonic()
                    if msg.get("is_filler"):
                        result.filler_text = msg.get("text", "")[:100]

                elif msg["type"] == "TTS_CHUNK":
                    audio_b64 = msg.get("audio", "")
                    chunk_data = base64.b64decode(audio_b64)
                    result.tts_bytes_received += len(chunk_data)
                    result.tts_chunks_received += 1
                    t_last_tts = time.monotonic()

                elif msg["type"] == "TTS_END":
                    if not msg.get("is_filler", False):
                        break

                elif msg["type"] == "PIPELINE_ERROR":
                    logger.warning("Pipeline error for %s: %s",
                                   question.id, msg.get("detail"))
                    break

        except asyncio.TimeoutError:
            logger.warning("Timeout for %s", question.id)

        t_done = time.monotonic()
        result.time_to_first_tts_ms = (
            (t_first_tts - t_audio_end) * 1000 if t_first_tts else 0
        )
        result.tts_stream_ms = (
            (t_last_tts - t_first_tts) * 1000 if t_first_tts and t_last_tts else 0
        )
        result.total_e2e_ms = (t_done - t_send_start) * 1000

    return result


def parse_server_logs(ssh_key: str, ssh_host: str) -> list[dict]:
    """Parse voice pipeline logs from the server."""
    try:
        cmd = [
            "ssh", "-i", ssh_key, f"root@{ssh_host}",
            "docker logs atlas-cortex --since 15m 2>&1 | "
            "grep -E 'Pipeline complete for bench-|STT result from bench-|"
            "Layer [0-3]|Cached filler for bench-'"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.stdout.strip().split("\n")
    except Exception as e:
        logger.warning("Could not fetch logs: %s", e)
        return []


async def run_benchmark(ws_url: str, ssh_key: str | None, ssh_host: str) -> None:
    """Run the full voice benchmark."""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    http_url = ws_url.replace("ws://", "http://").replace("/ws/satellite", "")

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Warm up LLM
        logger.info("Warming up LLM via text API...")
        await client.post(
            f"{http_url}/v1/chat/completions",
            json={"model": "atlas-cortex",
                  "messages": [{"role": "user", "content": "Hello"}],
                  "stream": False},
        )
        await asyncio.sleep(2)

        logger.info("Running %d questions through voice pipeline...", len(CORPUS))
        results: list[VoiceResult] = []

        for i, q in enumerate(CORPUS):
            logger.info("[%d/%d] %s: %s", i + 1, len(CORPUS), q.id, q.text)
            result = await run_voice_question(ws_url, client, q)
            results.append(result)
            logger.info(
                "  → %.0fms e2e, %.0fms to first TTS, %d TTS bytes",
                result.total_e2e_ms, result.time_to_first_tts_ms,
                result.tts_bytes_received,
            )
            await asyncio.sleep(1.5)  # gap between questions

    # Parse server logs for STT/LLM breakdown
    if ssh_key and os.path.exists(ssh_key):
        import re
        log_lines = parse_server_logs(ssh_key, ssh_host)

        stt_pat = re.compile(r"STT result from bench-(q\d+) \((\d+)ms\)")
        pipeline_pat = re.compile(
            r"Pipeline complete for bench-(q\d+): total=([0-9.]+)s "
            r"\(STT=(\d+)ms LLM=(\d+)ms (\d+) sentences \[(\w+)\] (\d+) bytes\)"
        )
        filler_pat = re.compile(
            r"Cached filler for bench-(q\d+): '(.+?)' \(([0-9.]+)s, (\d+) bytes\)"
        )

        result_map = {r.question_id: r for r in results}

        for line in log_lines:
            m = stt_pat.search(line)
            if m and m.group(1) in result_map:
                result_map[m.group(1)].server_stt_ms = float(m.group(2))

            m = pipeline_pat.search(line)
            if m and m.group(1) in result_map:
                r = result_map[m.group(1)]
                r.server_total_s = float(m.group(2))
                r.server_stt_ms = float(m.group(3))
                r.server_llm_ms = float(m.group(4))
                r.server_sentences = int(m.group(5))
                r.server_tts_provider = m.group(6)
                r.server_tts_bytes = int(m.group(7))

            m = filler_pat.search(line)
            if m and m.group(1) in result_map:
                r = result_map[m.group(1)]
                r.filler_text = m.group(2)
                r.filler_audio_bytes = int(m.group(4))

    # Save results
    out_path = data_dir / "voice_benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    logger.info("Saved to %s", out_path)

    # Print summary
    print("\n" + "=" * 80)
    print("VOICE PIPELINE BENCHMARK (end-to-end via WebSocket)")
    print("=" * 80)
    print(f"{'ID':<5} {'Question':<45} {'E2E':>7} {'→1stTTS':>8} "
          f"{'STT':>5} {'LLM':>6} {'TTS KB':>7}")
    print("-" * 80)
    for r in results:
        print(
            f"{r.question_id:<5} {r.question_text[:44]:<45} "
            f"{r.total_e2e_ms:>6.0f}ms {r.time_to_first_tts_ms:>7.0f}ms "
            f"{r.server_stt_ms:>4.0f}ms {r.server_llm_ms:>5.0f}ms "
            f"{r.tts_bytes_received / 1024:>6.1f}KB"
        )
    print("=" * 80)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="ws://192.168.3.8:5100/ws/satellite")
    parser.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/unraid_hive_key"))
    parser.add_argument("--ssh-host", default="192.168.3.8")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.server, args.ssh_key, args.ssh_host))


if __name__ == "__main__":
    main()
