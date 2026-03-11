# Atlas Cortex — LLM Development Bootstrap

> **Give this file to your AI coding assistant when starting a new development session.**
> It tells the LLM everything it needs to understand the project, run tests, and
> continue development — even without access to the production hardware.

---

## Step 1: Understand the Project

Read these files in order before making any changes:

1. **`.github/copilot-instructions.md`** — Build, test, architecture, conventions
2. **`docs/roadmap.md`** — What's done, what's next, MVP blockers
3. **`docs/architecture.md`** — 4-layer pipeline, processing flow
4. **`docs/voice-engine.md`** — TTS providers (Kokoro primary), voice pipeline, timing data
5. **`docs/development-mocks.md`** — How to develop without GPU/servers
6. **`docs/infrastructure.md`** — Production deployment, Docker stack, satellite hardware

## Step 2: Set Up Mock Development Environment

Atlas Cortex requires an LLM, STT, and TTS server. The mock infrastructure
simulates all three with realistic timing captured from production hardware.

```bash
# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Start mock servers (LLM + STT + TTS)
python -m mocks.run
# This prints env vars — the server reads them automatically

# In another terminal, start Atlas Cortex:
LLM_URL=http://localhost:11434 \
STT_HOST=localhost STT_PORT=10300 \
TTS_PROVIDER=kokoro KOKORO_HOST=localhost KOKORO_PORT=8880 \
MODEL_FAST=qwen2.5:7b MODEL_THINKING=qwen2.5:7b \
python -m cortex.server
```

## Step 3: Run Tests

```bash
# All tests (625+ pass, ~3min)
python -m pytest tests/ -q

# Specific module
python -m pytest tests/test_pipeline.py -v

# Specific test
python -m pytest tests/test_pipeline.py -k "test_instant_answer" -v
```

Known pre-existing failures (not regressions):
- `tests/test_wyoming.py` — 4-9 failures (Wyoming protocol attribute issues)
- `tests/test_voice.py::TestOrpheusTTSProvider::test_init_defaults` — port mismatch
- `tests/test_voice.py::TestSpeechEndpoint::test_list_voices` — needs live Kokoro
- `tests/test_satellite.py::TestProvisioning::test_provision_config` — SSID mismatch

## Step 4: Understand Current State

### What Works (Deployed to Production)
- 4-layer pipeline (context → instant → plugins → LLM)
- Kokoro TTS (primary, CPU, sub-2s synthesis, port 8880)
- Sentence-level TTS streaming (audio starts before LLM finishes)
- Pre-generated filler cache (0ms lookup during pipeline wait)
- Hallucination detection for Whisper noise patterns
- Auto-listen after questions (conversational continuity)
- ReSpeaker button handler (gpiod, GPIO 17, toggle/press/hold modes)
- Safety guardrails (input + output, content tiers, jailbreak defense)
- Memory system (HOT/COLD, BM25 + optional vector search)
- Admin panel (Vue 3 SPA)

### Voice Pipeline Timing (Real Measurements)
| Component | Average | Range |
|-----------|---------|-------|
| STT (Whisper.cpp Vulkan) | 190ms | 176-251ms |
| LLM (qwen2.5:7b) | 4371ms | 1768-6672ms |
| Time to first TTS audio | 4620ms | 2259-5191ms |
| Total end-to-end | 8567ms | 6518-11291ms |

### Pending Todos (Priority Order)

#### 🔴 MVP Blockers
- **wake-word-reliability** — openwakeword needs 2-3 tries from cold start. Investigate model fine-tuning, mic AGC, or alternative wake word engine.

#### 🟡 Near-term
- **admin-ui-button** — Add button_mode dropdown to `admin/src/views/SatelliteDetailView.vue`

#### 🟢 Conversational Engine (CE-1 through CE-5)
These are the next major architectural phase — see `docs/roadmap.md` Part 2.5:

1. **CE-1: Streaming STT** — Extended listening with local phrase boundary detection
2. **CE-2: Multi-question queuing** — Queue multiple phrases, process in order
3. **CE-3: Barge-in** — Hot mics during TTS, interrupt detection
4. **CE-4: Pause/pivot** — Pause TTS on interruption, decide to pivot or resume
5. **CE-5: Adaptive LEDs** — Dual-state LED patterns by satellite hardware

## Key Technical Facts

### TTS Provider Stack
- **Primary: Kokoro** — `TTS_PROVIDER=kokoro`, port 8880, voice `af_bella`
- **Alternate: Orpheus** — GPU-based, supports emotion tags (`<laugh>`, `<sigh>`)
- **Fallback: Piper** — Ultra-fast CPU, basic quality
- Kokoro is used via `KokoroClient` in `cortex/voice/kokoro.py` AND registered as `KokoroTTSProvider` in the provider registry

### LLM Configuration
- `MODEL_FAST=qwen2.5:7b` — factual questions (default)
- `MODEL_THINKING=qwen2.5:7b` — reasoning tasks (same model in current setup)
- Both must be set via env vars — defaults in code are `qwen2.5:14b` / `qwen3:30b-a3b`

### Satellite Hardware (Reference)
- Pi Zero 2W + ReSpeaker 2-mic HAT
- GPIO 17 button (active LOW, gpiod not RPi.GPIO — Bookworm kernel bug)
- 16kHz 16-bit mono audio over WebSocket
- Deploy via SSH hop through Unraid server (see `docs/infrastructure.md`)

### Code Conventions
- `from __future__ import annotations` in every module
- `async/await` native, `pytest-asyncio` auto mode
- `@dataclass` (not Pydantic, except API models in `server.py`)
- Optional deps: `try/except` import with `_HAS_*` flags
- Branch protection on `main` — all changes via PR

### Mock Infrastructure
- `mocks/run.py` — starts all 3 mock servers
- `mocks/benchmark.py` — 35-question corpus with timing data
- `mocks/benchmark_voice.py` — WebSocket voice pipeline benchmark
- `mocks/data/benchmark_results.json` — text API timing (35 questions)
- `mocks/data/voice_benchmark_results.json` — voice pipeline timing (35 questions)
- `mocks/conftest.py` — pytest fixtures for auto-starting mocks

## Directory Structure (Key Files)

```
cortex/
├── server.py                    # FastAPI server (:5100)
├── pipe.py                      # Open WebUI Pipe function
├── db.py                        # SQLite schema (50+ tables)
├── auth.py                      # JWT authentication
├── admin_api.py                 # DEPRECATED shim → cortex.admin
├── jokes.py                     # DEPRECATED shim → cortex.content.jokes
├── admin/                       # Admin API domain routers
│   ├── __init__.py              # Assembles 9 sub-routers
│   ├── helpers.py               # Shared _db(), _rows(), _row()
│   ├── auth.py                  # Login, session, password
│   ├── dashboard.py             # Dashboard stats
│   ├── users.py                 # User CRUD, parental controls
│   ├── safety.py                # Safety events, jailbreak patterns
│   ├── devices.py               # Speakers, HA devices
│   ├── system.py                # Settings, evolution, system info
│   ├── satellites.py            # Satellite management
│   ├── tts.py                   # TTS preview, voice management
│   └── avatar.py                # Avatar skins, audio routing
├── orchestrator/                # Request coordination
│   ├── __init__.py              # Entry: process_voice_pipeline()
│   ├── voice.py                 # STT → pipeline → TTS flow
│   ├── text.py                  # Sentence splitting, auto-listen
│   └── filler.py                # Filler dispatch (cache → live)
├── speech/                      # All audio synthesis/transcription
│   ├── __init__.py              # Entry: synthesize_speech, transcribe
│   ├── tts.py                   # Multi-provider TTS (Orpheus→Kokoro→Piper)
│   ├── stt.py                   # Whisper + Wyoming STT
│   ├── voices.py                # Voice resolution (satellite→user→system)
│   └── cache.py                 # Unified audio cache (data/tts_cache/)
├── pipeline/
│   ├── __init__.py              # Pipeline orchestrator (run_pipeline)
│   ├── events.py                # Typed events (TextToken, ExpressionEvent, etc.)
│   ├── layer0_context.py        # Context assembly
│   ├── layer1_instant.py        # Instant answers (no LLM)
│   ├── layer2_plugins.py        # Learned patterns + plugin dispatch
│   └── layer3_llm.py            # Filler + LLM streaming
├── avatar/                      # Avatar state: face, mouth, skin
│   ├── __init__.py              # Public API + backward compat
│   ├── controller.py            # Single entry point for avatar control
│   ├── expressions.py           # Expression mapping (19 emotions)
│   ├── visemes.py               # Lip-sync viseme generation
│   ├── skins/                   # SVG skins (default, nick)
│   ├── broadcast.py             # WS transport to display clients
│   ├── websocket.py             # WS handler (connect, greeting, jokes)
│   └── display.html             # Client-side renderer
├── memory/                      # HOT/COLD memory system
│   ├── __init__.py              # Re-exports + backward compat
│   ├── controller.py            # MemorySystem singleton
│   ├── hot.py                   # HOT recall (BM25 + vector, RRF)
│   ├── cold.py                  # COLD async write queue
│   ├── classification.py        # Memory type classifier
│   ├── pii.py                   # PII redaction
│   ├── vector.py                # ChromaDB wrapper
│   └── types.py                 # MemoryEntry, MemoryHit
├── notifications/               # Alert and notification system
│   ├── __init__.py              # Entry: send_notification()
│   └── channels.py              # NotificationChannel ABC, LogChannel
├── selfmod/                     # Self-evolution with security gates
│   ├── __init__.py              # Entry: validate_change()
│   └── zones.py                 # FROZEN/MUTABLE zone definitions
├── learning/                    # Self-learning (re-exports)
│   └── __init__.py              # FallthroughAnalyzer, NightlyEvolution
├── content/                     # Pre-cached content
│   ├── __init__.py              # Entry: jokes module
│   └── jokes.py                 # Joke bank, rotation, TTS pre-gen
├── scheduler/                   # Background task management
│   └── __init__.py              # register_task, start_all, stop_all
├── voice/                       # DEPRECATED → use cortex.speech
│   ├── providers/               # TTS provider implementations
│   └── ...                      # Legacy voice module
├── satellite/
│   ├── websocket.py             # Thin WS handler → delegates to orchestrator
│   └── provisioning.py          # Satellite setup/config
├── safety/
│   ├── __init__.py              # Input/Output guardrails → notifications
│   └── jailbreak.py             # 5-layer jailbreak defense
├── filler/
│   └── cache.py                 # Pre-generated filler audio cache
├── evolution/                   # Emotional evolution (rapport, mood)
├── grounding/                   # Anti-hallucination (confidence scoring)
├── plugins/
│   ├── base.py                  # CortexPlugin abstract class
│   └── __init__.py              # PluginRegistry
├── providers/                   # LLM providers (Ollama, OpenAI)
├── profiles/                    # User profiles, parental controls
├── context/                     # Token budgeting
└── integrations/                # Plugin impls (HA, knowledge, lists)
    └── learning/                # Fallthrough analysis, pattern lifecycle

satellite/atlas_satellite/
├── agent.py                     # Satellite state machine
├── button.py                    # ReSpeaker button (gpiod, GPIO 17)
└── ws_client.py                 # WebSocket client

mocks/
├── run.py                       # Start all mock servers
├── benchmark.py                 # 35-question corpus
├── benchmark_voice.py           # Voice pipeline benchmark
├── mock_llm_server.py           # Mock Ollama
├── mock_stt_server.py           # Mock Whisper
├── mock_tts_server.py           # Mock Kokoro
└── data/                        # Benchmark results

admin/                           # Vue 3 + Vite SPA
docs/                            # Architecture, roadmap, guides
```
