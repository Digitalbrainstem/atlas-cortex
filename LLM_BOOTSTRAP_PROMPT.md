# Atlas Cortex — LLM Development Bootstrap

> **Give this file to your AI coding assistant when starting a new development session.**
> It tells the LLM everything it needs to understand the project, run tests, and
> continue development — even without access to the production hardware.

---

## Step 1: Understand the Project

Read these files in order before making any changes:

1. **`.github/copilot-instructions.md`** — Build, test, architecture, conventions
2. **`docs/roadmap.md`** — What's done, what's next
3. **`docs/architecture.md`** — 4-layer pipeline, processing flow
4. **`docs/voice-engine.md`** — TTS providers, voice pipeline, timing data
5. **`docs/development-mocks.md`** — How to develop without GPU/servers
6. **`docs/infrastructure.md`** — Production deployment, Docker stack, satellite hardware
7. **`docs/configuration.md`** — All environment variables

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
LLM_PROVIDER=ollama MODEL_FAST=qwen2.5:7b MODEL_THINKING=qwen2.5:7b \
python -m cortex.server
```

## Step 3: Run Tests

```bash
# All tests (3,660+ pass)
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

### Completed Parts (16 of 18 phases)
- **Part 1**: Core Engine — 4-layer pipeline, providers, memory (HOT/COLD), safety, avatar, profiles, context, backup
- **Part 2**: Integration — HA, discovery, voice pipeline, self-learning, knowledge (WebDAV/CalDAV), lists, offsite backup
- **Part 2.5**: Satellite system (wake word deferred)
- **Part 2.7**: Plugin infrastructure — 21 built-in plugins, ConfigField schema-driven admin UI
- **Part 3**: Alarms, timers, reminders — NL time parser, notification routing
- **Part 4**: Routines & automations — triggers (voice/cron/HA events), templates
- **Part 5**: Proactive intelligence — rule engine, weather/energy/anomaly/calendar providers, daily briefing
- **Part 6**: Learning & education — Socratic tutoring, quiz gen (through Calc III), 3 STEM games
- **Part 7**: Intercom — announce, broadcast, zones, two-way calling, drop-in
- **Part 8**: Media — YouTube Music, Plex, Audiobookshelf, podcasts, local library, playback router
- **Part 9**: Self-evolution — conversation analysis, LoRA discovery & registration (discover_and_register, model_registry DB), model scout, A/B testing, drift monitor
- **Part 10**: Story time — generator, character voices (Fish Audio S2), TTS hot-swap, interactive stories
- **Part 11**: Atlas CLI — REPL, 31 agent tools, ReAct loop, context management, sessions
- **Part 12**: Standalone web app — browser chat, WebSocket streaming, voice I/O, avatar, dashboard, public `/chat` page with user profiles (PIN/passkey auth)

### Admin Panel Views (20 total)
Chat, Dashboard, Users, UserDetail, Parental, Safety, Voice, Avatar, Devices, Satellites, SatelliteDetail, Plugins, Scheduling, Routines, Learning, Proactive, Media, Intercom, Evolution, Stories, System

### Hardware Architecture (Production)
- RX 7900 XT (20GB, ROCm) — LLM inference + LoRA training at night
- RTX 4060 (8GB, CUDA) — TTS (Qwen3-TTS), specialist models (vision, embeddings)
- TTS hierarchy: Qwen3-TTS → Fish Audio S2 (stories) → Orpheus → Kokoro → Piper

### Remaining Parts (Planned)
- P13: Legacy Protocol
- P14: Household Management
- P15: Security & Monitoring
- P16: Health & Wellness
- P17: Multi-Language Support
- P18: Visual Media & Casting

## Key Technical Facts

### TTS Provider Stack
- **Primary: Qwen3-TTS** — GPU (RTX 4060), high quality
- **Character voices: Fish Audio S2** — GPU, TTS hot-swap for stories
- **Alternate: Orpheus** — GPU-based, supports emotion tags (`<laugh>`, `<sigh>`)
- **CPU fallback: Kokoro** — port 8880, voice `af_bella`
- **Last resort: Piper** — Ultra-fast CPU, basic quality

### LLM Configuration
- `LLM_PROVIDER=transformers` — default; uses HuggingFace Transformers with KV cache injection (CAG)
- `CAG_MODEL=Qwen/Qwen3-4B` — HuggingFace model for inference
- `EMBED_MODEL=all-MiniLM-L6-v2` — sentence-transformers embedding model
- `LLM_PROVIDER=ollama` — legacy fallback (set `OLLAMA_BASE_URL`)
- `MODEL_FAST=qwen2.5:14b` — factual questions (Ollama/OpenAI providers; production uses `qwen2.5:7b`)
- `MODEL_THINKING=qwen3:30b-a3b` — reasoning tasks (Ollama/OpenAI providers; production uses `qwen2.5:7b`)

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

### Plugin System
- **ConfigField** (`cortex/plugins/base.py`) — schema-driven config for admin forms
- `config_fields: list[ConfigField]` on each plugin declares typed form fields
- Field types: `text`, `password`, `url`, `toggle`, `select`, `number`
- `health_message` property returns human-readable status for admin panel
- Plugins discovered automatically; admin UI renders forms from schema

### LoRA System
- LoRAs are **discovered** at startup via `discover_and_register()` — not composed automatically
- `model_registry` DB table tracks all known models and LoRA adapters
- LoRA manager uses the `peft` library for adapter loading/composition
- Admin API (`/admin/loras/compose`) to compose a LoRA on demand
- Model scout scans local HuggingFace cache for available models
- Groups: `ultra-9b-v2/` (11 domains), `core-4b-h100/` (11 domains), `focused-9b/` (specialty)

### Public Chat
- `/chat` serves a public-facing chat SPA (from `admin/dist/chat.html`)
- User profiles with PIN, password, or passkey authentication
- `/api/chat/users`, `/api/chat/auth`, `/api/chat/session` endpoints
- Not behind admin auth — designed for household members

## Directory Structure (Key Modules)

```
cortex/
├── server.py                    # FastAPI server (:5100)
├── pipe.py                      # Open WebUI Pipe function
├── db.py                        # SQLite schema (50+ tables)
├── auth.py                      # JWT authentication
├── admin/                       # Admin API domain routers (19 sub-routers, 144+ endpoints)
├── orchestrator/                # Request coordination (STT→pipeline→TTS)
│   ├── voice.py                 # STT → pipeline → TTS flow
│   ├── text.py                  # Sentence splitting, auto-listen
│   └── filler.py                # Filler dispatch (cache → live)
├── speech/                      # All audio synthesis/transcription
│   ├── tts.py                   # Multi-provider TTS with hot-swap
│   ├── stt.py                   # Whisper + Wyoming STT
│   ├── voices.py                # Voice resolution (satellite→user→system)
│   ├── cache.py                 # Unified audio cache (data/tts_cache/)
│   ├── fish_audio.py            # Fish Audio S2 character voices
│   └── hotswap.py               # Runtime TTS model swapping
├── pipeline/
│   ├── __init__.py              # Pipeline orchestrator (run_pipeline)
│   ├── events.py                # Typed events (TextToken, ExpressionEvent, etc.)
│   ├── layer0_context.py        # Context assembly
│   ├── layer1_instant.py        # Instant answers (no LLM)
│   ├── layer2_plugins.py        # Learned patterns + plugin dispatch
│   └── layer3_llm.py            # Filler + LLM streaming
├── avatar/                      # Avatar state: face, mouth, skin
├── memory/                      # HOT/COLD memory system
├── safety/                      # Input/Output guardrails, jailbreak defense
├── plugins/                     # CortexPlugin ABC + PluginRegistry
├── providers/                   # LLM providers (Transformers, Ollama, OpenAI)
├── profiles/                    # User profiles, parental controls
├── context/                     # Token budgeting
├── scheduling/                  # Alarms, timers, reminders
├── routines/                    # Routine automations, triggers, templates
├── proactive/                   # Proactive intelligence, daily briefing
├── intercom/                    # Announce, broadcast, two-way calling
├── media/                       # YouTube Music, Plex, ABS, podcasts, router
├── stories/                     # Story generator, character voices, library
├── evolution/                   # LoRA training, model scout, drift
├── cli/                         # Atlas CLI agent (REPL, 31 tools, ReAct)
├── learning/                    # Self-learning (re-exports)
├── notifications/               # Alert and notification routing
├── selfmod/                     # Self-evolution security gates
├── content/                     # Pre-cached content (jokes)
├── scheduler/                   # Background task management
├── filler/                      # Pre-generated filler audio cache
├── grounding/                   # Anti-hallucination (confidence scoring)
├── backup/                      # Automated backup/restore + offsite
├── satellite/                   # WebSocket handler + provisioning
├── install/                     # Hardware detection & installer
├── discovery/                   # Network service discovery
├── integrity/                   # Data integrity checks
├── integrations/                # Plugin impls (HA, knowledge, lists)
│   └── learning/                # Fallthrough analysis, pattern lifecycle
└── voice/                       # DEPRECATED → use cortex.speech

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

admin/                           # Vue 3 + Vite SPA (20 views)
docs/                            # Architecture, roadmap, guides (35+ files)
```
