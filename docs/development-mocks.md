# Atlas Cortex — Development with Mocks

> **No GPU? No problem.** The mock infrastructure simulates the full Atlas Cortex
> pipeline with realistic timing data captured from real hardware.

## Quick Start

```bash
# From repo root:
pip install -r requirements.txt

# Start all mock servers (LLM + STT + TTS):
python -m mocks.run

# In another terminal, start Atlas Cortex server:
LLM_URL=http://localhost:11434 \
STT_HOST=localhost STT_PORT=10300 \
TTS_PROVIDER=kokoro KOKORO_URL=http://localhost:8880 \
MODEL_FAST=qwen2.5:7b MODEL_THINKING=qwen2.5:7b \
python -m cortex.server
```

The mock runner prints all environment variables you need. Copy/paste or
use `python -m mocks.run` which sets them automatically.

## What Gets Mocked

| Service | Real | Mock | Port |
|---------|------|------|------|
| **LLM** (Ollama) | qwen2.5:7b on GPU | Pre-recorded responses + simulated latency | 11434 |
| **STT** (Whisper) | faster-whisper on Vulkan GPU | Returns transcriptions by audio length | 10300 |
| **TTS** (Kokoro) | Kokoro on GPU | Generates sine-wave PCM with real timing | 8880 |

### Timing Accuracy

All timing is derived from **real benchmark data** captured on the production hardware:

| Component | Real Hardware | Mock Simulation |
|-----------|-------------|-----------------|
| LLM TTFT | ~200ms (warm) | Simulated delay |
| LLM total (short answer) | ~4000ms | Streamed at real token rate |
| LLM total (medium answer) | ~4200ms | Streamed at real token rate |
| LLM total (complex answer) | ~5000ms | Streamed at real token rate |
| Layer 1 (instant) | <1ms | Instant |
| STT (Whisper) | 200-500ms | Simulated delay |
| TTS synthesis (5 words) | ~1100ms | Simulated delay |
| TTS synthesis (40 words) | ~7400ms | Simulated delay |
| Satellite overhead | ~770ms | Estimated from logs |

## Benchmark Data

The `mocks/data/` directory contains timing captured from real hardware:

- **`benchmark_results.json`** — 35 questions with per-layer timing, response text,
  TTS synthesis estimates, satellite overhead estimates
- **`timing_profiles.json`** — Aggregated profiles by category (short/medium/complex)

### Re-running the Benchmark

To capture fresh timing data from your hardware:

```bash
# Requires access to the live server:
python -m mocks.benchmark --server http://192.168.3.8:5100
```

This sends all 35 questions through the real pipeline and captures:
1. Per-layer pipeline timing from server logs (Layer 0-3, TTFT)
2. Wall-clock response time
3. Response text and word counts
4. TTS synthesis estimates (from Kokoro timing model)
5. Satellite overhead estimates (from historical Pi logs)

## Using Mocks in Tests

### Pytest Fixtures

```python
# In your test file:
import pytest
from mocks.conftest import mock_servers, benchmark_data, timing_profiles

@pytest.mark.asyncio
async def test_pipeline_with_mocks(mock_servers):
    # mock_servers auto-starts LLM, STT, TTS on ephemeral ports
    # Environment vars already set for Atlas Cortex
    from cortex.providers import get_provider
    provider = get_provider()
    result = await provider.chat(
        [{"role": "user", "content": "What time is it?"}],
        stream=False,
    )
    assert "12:05" in result["message"]["content"]


def test_timing_data(benchmark_data):
    # Access all 35 benchmark results
    layer1_results = [r for r in benchmark_data if r["layer_hit"] == "layer1"]
    assert all(r["pipeline_total_ms"] < 10 for r in layer1_results)
```

### Manual Server Start

```python
# Start specific mock servers:
python -m mocks.run --only llm        # Just the LLM mock
python -m mocks.run --only llm,tts    # LLM + TTS

# Custom ports:
python -m mocks.run --llm-port 9434 --tts-port 9880
```

## Question Corpus

The 35 test questions cover the full pipeline:

| Category | Count | Description |
|----------|-------|-------------|
| **Layer 1 — Instant** | 8 | Time, date, math, greetings, identity |
| **Layer 2 — Plugins** | 4 | Home Assistant, lists (may fall to LLM) |
| **Layer 3 — Short** | 6 | Factual one-liners (movie dates, capitals) |
| **Layer 3 — Medium** | 7 | Recipes, explanations, advice |
| **Layer 3 — Complex** | 5 | Multi-paragraph: stories, analysis, planning |
| **Unbuilt functions** | 5 | Timer, weather, media, reminders, sports |

Each question has metadata:
- `category`: short / medium / complex
- `expected_layer`: layer1 / layer2 / layer3
- `target_function`: what should handle it (instant_time, ha_plugin, llm_factual, unbuilt_weather...)
- `expected_response_style`: one_word / one_sentence / paragraph / multi_paragraph

## Architecture

```
mocks/
├── __init__.py              # Package marker
├── benchmark.py             # Captures real timing from live hardware
├── conftest.py              # Pytest fixtures (auto-start mock servers)
├── mock_llm_server.py       # Mock Ollama API (/api/chat, /api/tags)
├── mock_stt_server.py       # Mock Whisper API (/inference)
├── mock_tts_server.py       # Mock Kokoro API (/v1/audio/speech)
├── run.py                   # Starts all mocks + sets env vars
└── data/
    ├── benchmark_results.json   # Per-question timing data
    └── timing_profiles.json     # Aggregated category profiles
```

### Mock LLM Server

- Matches Ollama `/api/chat` endpoint (streaming + non-streaming)
- Looks up pre-recorded responses by question text (fuzzy match)
- Unknown questions get a generic mock response with average timing
- Simulates realistic TTFT and per-token streaming delays

### Mock STT Server

- Matches whisper.cpp `/inference` endpoint
- Returns pre-recorded transcriptions matched by audio duration
- Simulates processing delay: 200ms base + 50ms/second of audio

### Mock TTS Server

- Matches OpenAI `/v1/audio/speech` endpoint
- Returns PCM audio (24kHz 16-bit mono) with soft tone
- Simulates synthesis delay: 200ms base + 180ms/word
- Reports audio duration in response headers

## Adding New Questions

Edit `mocks/benchmark.py` → `CORPUS` list. Then re-run:

```bash
# Against live hardware:
python -m mocks.benchmark --server http://YOUR_SERVER:5100

# Or manually add entries to mocks/data/benchmark_results.json
```

## Hardware Profiles

The timing data is specific to this hardware configuration:

| Component | Spec |
|-----------|------|
| Server CPU | AMD Ryzen 7 5700G |
| Server GPU | Intel Arc B580 (12GB, Vulkan) |
| LLM | qwen2.5:7b (Q4_K_M) via Ollama |
| STT | faster-whisper (Vulkan inference) |
| TTS | Kokoro (GPU accelerated) |
| Satellite | Pi Zero 2W + ReSpeaker 2-mic HAT |

If your hardware differs significantly, re-run the benchmark to capture
accurate timing for your setup.
