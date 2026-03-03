#!/usr/bin/env python3
"""Benchmark Atlas Cortex pipeline with realistic spoken questions.

Sends 35 questions through the live server, captures per-layer timing from
server logs, measures TTS synthesis time, and estimates satellite overhead
from historical log data.

Usage:
    python -m mocks.benchmark --server http://192.168.3.8:5100

Output:
    mocks/data/benchmark_results.json — raw timing data per question
    mocks/data/timing_profiles.json  — aggregated profiles by category
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── Satellite overhead estimates (from live Pi Zero 2W logs) ──────
# These are derived from real satellite logs with ReSpeaker 2-mic HAT.
SATELLITE_ESTIMATES = {
    "wake_word_detection_ms": 150,    # openwakeword inference per 80ms frame
    "wake_to_listen_transition_ms": 50,  # state machine transition
    "vad_speech_start_ms": 90,        # energy VAD detects speech onset
    "vad_silence_detect_ms": 450,     # 15 frames × 30ms
    "audio_network_overhead_ms": 30,  # WebSocket frame transmission
    "echo_suppression_ms": 3000,      # suppression window after TTS playback
}

# ── Question Corpus ──────────────────────────────────────────────

@dataclass
class Question:
    """A test question with metadata."""
    id: str
    text: str                          # What the user says (spoken form)
    category: str                      # short | medium | complex
    expected_layer: str                # layer1 | layer2 | layer3
    target_function: str               # instant_time | instant_math | ha_plugin | llm_factual | etc.
    expected_response_style: str       # one_word | one_sentence | paragraph | multi_paragraph
    notes: str = ""

CORPUS: list[Question] = [
    # ── Layer 1: Instant Answers (no LLM) ─────────────────────
    Question("q01", "What time is it?", "short", "layer1", "instant_time", "one_sentence"),
    Question("q02", "What's today's date?", "short", "layer1", "instant_date", "one_sentence"),
    Question("q03", "What day of the week is it?", "short", "layer1", "instant_date", "one_sentence"),
    Question("q04", "What's two plus two?", "short", "layer1", "instant_math", "one_word"),
    Question("q05", "What's fifteen percent of eighty-four?", "short", "layer1", "instant_math", "one_word"),
    Question("q06", "What's the square root of one forty four?", "short", "layer1", "instant_math", "one_word"),
    Question("q07", "Hey, good morning!", "short", "layer1", "instant_greeting", "one_sentence"),
    Question("q08", "Who are you?", "short", "layer1", "instant_identity", "one_sentence"),

    # ── Layer 2: Plugin Dispatch (no LLM, but needs external) ──
    Question("q09", "Turn on the living room lights", "short", "layer2", "ha_plugin",
             "one_sentence", "Needs HA integration — will fall to L3 if not configured"),
    Question("q10", "What's the temperature in the house?", "short", "layer2", "ha_plugin",
             "one_sentence", "Needs HA integration"),
    Question("q11", "Add milk to the grocery list", "short", "layer2", "list_plugin",
             "one_sentence", "List management plugin"),
    Question("q12", "What's on my grocery list?", "short", "layer2", "list_plugin",
             "one_sentence", "List read"),

    # ── Layer 3: LLM — Short Factual ──────────────────────────
    Question("q13", "When did the movie Inception come out?", "short", "layer3",
             "llm_factual", "one_sentence"),
    Question("q14", "What year was the Eiffel Tower built?", "short", "layer3",
             "llm_factual", "one_sentence"),
    Question("q15", "Who painted the Mona Lisa?", "short", "layer3",
             "llm_factual", "one_word"),
    Question("q16", "What's the capital of Australia?", "short", "layer3",
             "llm_factual", "one_word"),
    Question("q17", "How many planets are in our solar system?", "short", "layer3",
             "llm_factual", "one_word"),
    Question("q18", "What's the boiling point of water in Fahrenheit?", "short", "layer3",
             "llm_factual", "one_sentence"),

    # ── Layer 3: LLM — Medium Conversational ──────────────────
    Question("q19", "What's a good recipe for banana bread?", "medium", "layer3",
             "llm_conversational", "paragraph"),
    Question("q20", "Can you explain what machine learning is in simple terms?", "medium",
             "layer3", "llm_conversational", "paragraph"),
    Question("q21", "What's the difference between a hurricane and a tornado?", "medium",
             "layer3", "llm_conversational", "paragraph"),
    Question("q22", "Tell me a joke", "short", "layer3", "llm_creative", "one_sentence"),
    Question("q23", "What should I do if I can't sleep at night?", "medium", "layer3",
             "llm_advice", "paragraph"),
    Question("q24", "How do I change a flat tire?", "medium", "layer3",
             "llm_instructional", "paragraph"),
    Question("q25", "What are some fun things to do with kids on a rainy day?", "medium",
             "layer3", "llm_creative", "paragraph"),

    # ── Layer 3: LLM — Complex / Long ────────────────────────
    Question("q26", "Explain how a nuclear reactor works", "complex", "layer3",
             "llm_educational", "multi_paragraph"),
    Question("q27", "What caused the fall of the Roman Empire?", "complex", "layer3",
             "llm_educational", "multi_paragraph"),
    Question("q28", "Compare the pros and cons of electric vehicles versus gas cars",
             "complex", "layer3", "llm_analysis", "multi_paragraph"),
    Question("q29", "Can you help me plan a week-long trip to Japan?", "complex",
             "layer3", "llm_planning", "multi_paragraph"),
    Question("q30", "Write me a short bedtime story about a brave little robot",
             "complex", "layer3", "llm_creative", "multi_paragraph"),

    # ── Not-yet-built functions (should fall to LLM) ──────────
    Question("q31", "Set a timer for ten minutes", "short", "layer3",
             "unbuilt_timer", "one_sentence", "Timer plugin not implemented yet"),
    Question("q32", "What's the weather going to be like tomorrow?", "short", "layer3",
             "unbuilt_weather", "one_sentence", "Weather plugin not implemented yet"),
    Question("q33", "Play some jazz music in the living room", "short", "layer3",
             "unbuilt_media", "one_sentence", "Media plugin not implemented yet"),
    Question("q34", "Remind me to take my medicine at three PM", "short", "layer3",
             "unbuilt_reminder", "one_sentence", "Reminder plugin not implemented yet"),
    Question("q35", "What's the score of the Lakers game?", "short", "layer3",
             "unbuilt_sports", "one_sentence", "Sports plugin not implemented yet"),
]


@dataclass
class BenchmarkResult:
    """Full timing capture for one question."""
    question_id: str
    question_text: str
    category: str
    expected_layer: str
    target_function: str

    # Pipeline timing (real, from server)
    layer_hit: str = ""                 # Which layer actually handled it
    layer0_ms: float = 0               # Context assembly
    layer1_ms: float = 0               # Instant answer attempt
    layer2_ms: float = 0               # Plugin dispatch
    layer3_ms: float = 0               # LLM total time
    ttft_ms: float = 0                 # Time to first token (LLM)
    pipeline_total_ms: float = 0       # Full pipeline

    # Response data
    response_text: str = ""
    response_char_count: int = 0
    response_word_count: int = 0
    filler_text: str = ""

    # TTS timing (real, from server)
    tts_synthesis_ms: float = 0        # Time to synthesize response audio
    tts_audio_duration_ms: float = 0   # Playback duration of response audio

    # STT timing (estimated for voice, N/A for text)
    estimated_utterance_duration_ms: float = 0  # How long user speaks
    estimated_stt_ms: float = 0        # Whisper transcription time

    # Satellite overhead (estimated from logs)
    estimated_wake_word_ms: float = 0
    estimated_vad_onset_ms: float = 0
    estimated_vad_silence_ms: float = 0
    estimated_network_ms: float = 0

    # Totals
    total_voice_pipeline_ms: float = 0  # End-to-end voice path
    total_text_pipeline_ms: float = 0   # End-to-end text path
    dead_space_ms: float = 0           # Time user hears nothing

    # Wall clock (real)
    wall_clock_ms: float = 0           # curl-style total time
    timestamp: str = ""


async def warm_up_model(client: httpx.AsyncClient, server: str) -> None:
    """Send a throwaway query to load the LLM model into GPU memory."""
    logger.info("Warming up LLM model...")
    try:
        resp = await client.post(
            f"{server}/v1/chat/completions",
            json={
                "model": "atlas-cortex",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
            timeout=120.0,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("Warm-up complete: %s", content[:60])
    except Exception as e:
        logger.warning("Warm-up failed: %s", e)


async def send_question(
    client: httpx.AsyncClient,
    server: str,
    question: Question,
) -> BenchmarkResult:
    """Send a question and capture timing."""
    result = BenchmarkResult(
        question_id=question.id,
        question_text=question.text,
        category=question.category,
        expected_layer=question.expected_layer,
        target_function=question.target_function,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    t_start = time.monotonic()
    try:
        resp = await client.post(
            f"{server}/v1/chat/completions",
            json={
                "model": "atlas-cortex",
                "messages": [{"role": "user", "content": question.text}],
                "stream": False,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # Separate filler from response (filler ends before \n\n)
        if "\n\n" in content and content.index("\n\n") < 80:
            filler, response = content.split("\n\n", 1)
            result.filler_text = filler.strip()
            result.response_text = response.strip()
        else:
            result.response_text = content.strip()

        result.response_char_count = len(result.response_text)
        result.response_word_count = len(result.response_text.split())

    except Exception as e:
        result.response_text = f"ERROR: {e}"
        logger.error("Question %s failed: %s", question.id, e)

    result.wall_clock_ms = (time.monotonic() - t_start) * 1000

    # Estimate utterance duration: ~130ms per word for natural speech
    word_count = len(question.text.split())
    result.estimated_utterance_duration_ms = word_count * 130

    # Estimate STT time: Whisper on Intel Arc B580 ≈ 200-500ms for short audio
    # Scale with utterance length: base 200ms + 50ms per second of audio
    utterance_s = result.estimated_utterance_duration_ms / 1000
    result.estimated_stt_ms = 200 + utterance_s * 50

    # Satellite overhead
    result.estimated_wake_word_ms = SATELLITE_ESTIMATES["wake_word_detection_ms"]
    result.estimated_vad_onset_ms = SATELLITE_ESTIMATES["vad_speech_start_ms"]
    result.estimated_vad_silence_ms = SATELLITE_ESTIMATES["vad_silence_detect_ms"]
    result.estimated_network_ms = SATELLITE_ESTIMATES["audio_network_overhead_ms"]

    return result


async def capture_server_timing(
    server_host: str,
    ssh_key: str | None,
    results: list[BenchmarkResult],
) -> None:
    """Parse server logs to extract per-layer timing for each question."""
    if not ssh_key:
        logger.warning("No SSH key — skipping server log capture")
        return

    try:
        cmd = [
            "ssh", "-i", ssh_key, f"root@{server_host}",
            "docker logs atlas-cortex --since 10m 2>&1 | grep -E 'Layer [0-3]'"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = proc.stdout.strip().split("\n")
    except Exception as e:
        logger.warning("Could not fetch server logs: %s", e)
        return

    # Parse lines like: Layer 1 hit (0.0ms): "It's 11:55 PM." [total 0ms]
    # or: Layer 3 (LLM): 5432ms (TTFT 234ms) [total 5433ms] L0=0 L1=0 L2=0
    layer1_pat = re.compile(
        r'Layer 1 hit \(([0-9.]+)ms\).*\[total (\d+)ms\]'
    )
    layer3_pat = re.compile(
        r'Layer 3 \(LLM\): (\d+)ms \(TTFT (\d+)ms\) '
        r'\[total (\d+)ms\] L0=([0-9.]+) L1=([0-9.]+) L2=([0-9.]+)'
    )

    # Match log lines to results by order (they execute sequentially)
    log_idx = 0
    for result in results:
        if log_idx >= len(lines):
            break
        line = lines[log_idx]

        m1 = layer1_pat.search(line)
        m3 = layer3_pat.search(line)

        if m1:
            result.layer_hit = "layer1"
            result.layer1_ms = float(m1.group(1))
            result.pipeline_total_ms = float(m1.group(2))
            log_idx += 1
        elif m3:
            result.layer_hit = "layer3"
            result.layer3_ms = float(m3.group(1))
            result.ttft_ms = float(m3.group(2))
            result.pipeline_total_ms = float(m3.group(3))
            result.layer0_ms = float(m3.group(4))
            result.layer1_ms = float(m3.group(5))
            result.layer2_ms = float(m3.group(6))
            log_idx += 1
        else:
            log_idx += 1  # skip unparseable line


async def measure_tts_timing(
    client: httpx.AsyncClient,
    server: str,
    results: list[BenchmarkResult],
) -> None:
    """Measure TTS synthesis time for each response by calling the TTS endpoint."""
    logger.info("Measuring TTS synthesis times...")
    for result in results:
        if not result.response_text or result.response_text.startswith("ERROR"):
            continue

        text = result.response_text[:500]  # Cap at 500 chars for TTS
        t_start = time.monotonic()
        try:
            resp = await client.post(
                f"{server}/v1/audio/speech",
                json={"input": text, "model": "tts-1", "voice": "default"},
                timeout=60.0,
            )
            audio_bytes = resp.content
            result.tts_synthesis_ms = (time.monotonic() - t_start) * 1000

            # Calculate audio duration (PCM 24kHz 16-bit mono = 48000 bytes/sec)
            if len(audio_bytes) > 0:
                result.tts_audio_duration_ms = len(audio_bytes) / 48.0  # bytes / (24000*2/1000)

        except Exception as e:
            logger.warning("TTS measurement failed for %s: %s", result.question_id, e)
            result.tts_synthesis_ms = 0
            result.tts_audio_duration_ms = 0


def compute_totals(results: list[BenchmarkResult]) -> None:
    """Compute end-to-end totals and dead space."""
    for r in results:
        # Text pipeline: just the pipeline time
        r.total_text_pipeline_ms = r.pipeline_total_ms

        # Voice pipeline: satellite overhead + STT + pipeline + TTS synthesis
        r.total_voice_pipeline_ms = (
            r.estimated_wake_word_ms
            + r.estimated_vad_onset_ms
            + r.estimated_utterance_duration_ms
            + r.estimated_vad_silence_ms
            + r.estimated_network_ms
            + r.estimated_stt_ms
            + r.pipeline_total_ms
            + r.tts_synthesis_ms
        )

        # Dead space: time between user finishing speaking and hearing response
        # = network + STT + pipeline + TTS synthesis (user waits during all of these)
        r.dead_space_ms = (
            r.estimated_network_ms
            + r.estimated_stt_ms
            + r.pipeline_total_ms
            + r.tts_synthesis_ms
        )


def build_timing_profiles(results: list[BenchmarkResult]) -> dict:
    """Aggregate results into category profiles for mock generation."""
    profiles = {}

    for category in ("short", "medium", "complex"):
        cat_results = [r for r in results if r.category == category]
        if not cat_results:
            continue

        profiles[category] = {
            "count": len(cat_results),
            "pipeline_ms": {
                "min": min(r.pipeline_total_ms for r in cat_results),
                "max": max(r.pipeline_total_ms for r in cat_results),
                "avg": sum(r.pipeline_total_ms for r in cat_results) / len(cat_results),
            },
            "ttft_ms": {
                "min": min(r.ttft_ms for r in cat_results),
                "max": max(r.ttft_ms for r in cat_results),
                "avg": sum(r.ttft_ms for r in cat_results) / len(cat_results),
            },
            "response_words": {
                "min": min(r.response_word_count for r in cat_results),
                "max": max(r.response_word_count for r in cat_results),
                "avg": sum(r.response_word_count for r in cat_results) / len(cat_results),
            },
            "dead_space_ms": {
                "min": min(r.dead_space_ms for r in cat_results),
                "max": max(r.dead_space_ms for r in cat_results),
                "avg": sum(r.dead_space_ms for r in cat_results) / len(cat_results),
            },
            "tts_synthesis_ms": {
                "min": min(r.tts_synthesis_ms for r in cat_results),
                "max": max(r.tts_synthesis_ms for r in cat_results),
                "avg": sum(r.tts_synthesis_ms for r in cat_results) / len(cat_results),
            },
        }

    # Layer-specific profiles
    for layer in ("layer1", "layer3"):
        layer_results = [r for r in results if r.layer_hit == layer]
        if not layer_results:
            continue

        profiles[f"by_{layer}"] = {
            "count": len(layer_results),
            "pipeline_ms": {
                "avg": sum(r.pipeline_total_ms for r in layer_results) / len(layer_results),
            },
            "response_words": {
                "avg": sum(r.response_word_count for r in layer_results) / len(layer_results),
            },
        }

    profiles["satellite_overhead"] = SATELLITE_ESTIMATES
    return profiles


async def run_benchmark(server: str, ssh_key: str | None, ssh_host: str | None) -> None:
    """Run the full benchmark."""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Warm up the model
        await warm_up_model(client, server)
        await asyncio.sleep(2)

        # 2. Clear server logs marker
        logger.info("Running %d questions...", len(CORPUS))

        # 3. Send each question sequentially
        results: list[BenchmarkResult] = []
        for i, question in enumerate(CORPUS):
            logger.info("[%d/%d] %s: %s", i + 1, len(CORPUS), question.id, question.text)
            result = await send_question(client, server, question)
            results.append(result)
            logger.info(
                "  → %dms wall, %d words: %s",
                int(result.wall_clock_ms),
                result.response_word_count,
                result.response_text[:80],
            )
            # Small delay to keep logs ordered
            await asyncio.sleep(0.5)

        # 4. Parse server logs for per-layer timing
        if ssh_key and ssh_host:
            await capture_server_timing(ssh_host, ssh_key, results)

        # 5. Measure TTS timing for each response
        await measure_tts_timing(client, server, results)

        # 6. Compute totals
        compute_totals(results)

    # 7. Save results
    results_path = data_dir / "benchmark_results.json"
    with open(results_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    logger.info("Results saved to %s", results_path)

    # 8. Build and save timing profiles
    profiles = build_timing_profiles(results)
    profiles_path = data_dir / "timing_profiles.json"
    with open(profiles_path, "w") as f:
        json.dump(profiles, f, indent=2)
    logger.info("Profiles saved to %s", profiles_path)

    # 9. Print summary
    print("\n" + "=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)
    for r in results:
        layer = r.layer_hit or "?"
        print(
            f"{r.question_id} [{layer:6s}] {r.pipeline_total_ms:7.0f}ms pipeline | "
            f"{r.dead_space_ms:7.0f}ms dead | {r.response_word_count:3d} words | "
            f"{r.question_text[:45]}"
        )
    print("=" * 70)

    # Category averages
    for cat in ("short", "medium", "complex"):
        cat_r = [r for r in results if r.category == cat]
        if cat_r:
            avg_pipe = sum(r.pipeline_total_ms for r in cat_r) / len(cat_r)
            avg_dead = sum(r.dead_space_ms for r in cat_r) / len(cat_r)
            avg_words = sum(r.response_word_count for r in cat_r) / len(cat_r)
            print(f"{cat:8s}: avg pipeline={avg_pipe:.0f}ms, dead={avg_dead:.0f}ms, words={avg_words:.0f}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Atlas Cortex pipeline")
    parser.add_argument("--server", default="http://192.168.3.8:5100",
                        help="Atlas Cortex server URL")
    parser.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/unraid_hive_key"),
                        help="SSH key for server log access")
    parser.add_argument("--ssh-host", default="192.168.3.8",
                        help="Server hostname for SSH")
    args = parser.parse_args()

    ssh_key = args.ssh_key if os.path.exists(args.ssh_key) else None
    asyncio.run(run_benchmark(args.server, ssh_key, args.ssh_host))


if __name__ == "__main__":
    main()
