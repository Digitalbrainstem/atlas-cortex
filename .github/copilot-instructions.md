# Atlas Cortex — Copilot Instructions

## Novel Ideas → Blog Drafts

When a conversation produces a novel idea, architecture pattern, or technique that
hasn't been widely documented in the industry, **save it as a self-contained blog
article draft** in the private repo:

**Repo:** `Betanu701/atlas-blog-drafts` (private, GitHub)
**Local clone:** `~/atlas-blog-drafts/`

### What qualifies as "novel"
- New architectural patterns (e.g., GPU choreography, two-phase self-evolution)
- Creative applications of existing techniques to new domains
- Compound approaches that combine known techniques in undocumented ways
- Insights from Atlas development that others could learn from

### How to save
1. Create a numbered directory: `articles/NN-short-slug/article.md`
2. Write a **self-contained** draft — someone should be able to construct a full blog
   post from it without needing any other context
3. Include: TL;DR, problem statement, technical details (code/diagrams), "what makes
   this novel" section, and references
4. Update `README.md` with the new article in the table
5. Commit and push to `Betanu701/atlas-blog-drafts`

### Existing articles
- 01: Application-Aware MoE Surgery
- 02: GPU Choreography for Self-Evolving AI
- 03: Two-Phase Self-Evolution on 8GB
- 04: Universal Distillation Philosophy
- 05: Model Scout with Safety Gates
- 06: The Atlas Body (Bio-Inspired Architecture)
- 07: Breaking the I/O Wall (Zero-I/O Pipeline)
- 08: Universal Skill Packages (Coordinated LoRA)
- 09: Autonomous LoRA Training on Consumer Hardware

Always check the repo for the current highest number before creating a new article.

## Research Threads

### Orpheus LoRA for TTS/STT Vocabulary

**Research location:** `Betanu701/atlas-blog-drafts` → `research/orpheus-lora-vocabulary/`
**Agent instructions:** `research/orpheus-lora-vocabulary/AGENT_INSTRUCTIONS.md`

This research thread is kept private in the blog repo, NOT in atlas-cortex.
It explores domain-specific LoRA adapters for TTS pronunciation and STT recognition
(medication names, technical terms). When continuing this research, clone/open the
blog repo and read the agent instructions there.

### Bio-Inspired Architecture

**Implementation plan:** `docs/bio-architecture-plan.md`

Focused plan for implementing bio-inspired patterns: parallel pipeline, muscle
memory cache, thalamus triage, hormonal engine, sleep architecture, immune vaccine.
This is SEPARATE from the LLM optimization strategy and should be treated as its
own work stream.

## Build, Test & Run

```bash
# Install Python dependencies (requires Python 3.11+)
pip install -r requirements.txt

# Run all tests
python -m pytest tests/ -q

# Run a single test file
python -m pytest tests/test_pipeline.py -v

# Run a single test by name
python -m pytest tests/test_pipeline.py -k "test_instant_answer" -v

# Start the server (OpenAI-compatible API on :5100)
python -m cortex.server

# Start mock servers for GPU-free development (LLM + STT + TTS)
python -m mocks.run

# Build the admin panel (Vue 3 SPA, requires Node.js 18+)
cd admin && npm install && npx vite build && cd ..
```

There is no linter configured. pytest uses `asyncio_mode = auto` (see `pytest.ini`).

**For new sessions**: Read `LLM_BOOTSTRAP_PROMPT.md` first — it has the full project
context, mock setup, current state, pending todos, and directory structure.

**Test count**: 2,825+ tests passing (`python -m pytest tests/ -q`).

## Architecture

Atlas Cortex is a self-evolving personal AI with an OpenAI-compatible API. It has completed Parts 1–12 (16 of 18 phases). Two entry points share the same pipeline:

- **`cortex/server.py`** — Standalone FastAPI server on port 5100
- **`cortex/pipe.py`** — Open WebUI Pipe function (drop-in, no separate server)

### 4-Layer Pipeline (`cortex/pipeline/`)

Every message flows through layers sequentially; **first match wins**:

1. **Layer 0** (`layer0_context.py`) — Context assembly: speaker ID, room, sentiment, time-of-day (~1ms)
2. **Layer 1** (`layer1_instant.py`) — Instant answers: date, time, math, greetings — no LLM needed (~5ms)
3. **Layer 2** (`layer2_plugins.py`) — Plugin dispatch: 21 built-in plugins + Home Assistant, lists, knowledge search (~100ms)
4. **Layer 3** (`layer3_llm.py`) — Pre-cached filler audio + sentence-level LLM streaming (~500–4000ms)

`run_pipeline()` in `cortex/pipeline/__init__.py` is the orchestrator. It returns an async generator yielding text tokens.

### Voice Engine (`cortex/speech/`)

**TTS hierarchy:** Qwen3-TTS (primary, RTX 4060) → Fish Audio S2 (story character voices, GPU) → Orpheus (backup, GPU) → Kokoro (CPU) → Piper (CPU, fast fallback).

Provider factory: `get_tts_provider()` in `cortex/voice/providers/__init__.py`.
Default voice: `af_bella`. Qwen3-TTS voices include Ryan, Aiden, and others.
Env vars: `TTS_PROVIDER=qwen3_tts`, `QWEN_TTS_HOST`, `QWEN_TTS_PORT=7860`, `KOKORO_HOST`, `KOKORO_PORT`, `KOKORO_VOICE`.

### Plugin System (`cortex/plugins/`)

Layer 2 plugins extend `CortexPlugin` (in `cortex/plugins/base.py`) and register with `PluginRegistry`. Each plugin implements `match()` → `CommandMatch` and `handle()` → `CommandResult`. Dispatch tries plugins in registration order.

**ConfigField system:** Plugins declare `config_fields: list[ConfigField]` for schema-driven admin forms. Field types: `text`, `password`, `url`, `toggle`, `select`, `number`. The `health_message` property provides human-readable status. The admin panel renders configuration forms automatically from the schema.

**21 built-in plugins:** weather, dictionary, wikipedia, conversions, movie, cooking, news, translation, stocks, sports, sound library, scheduling, routines, daily briefing, STEM games (Number Quest, Science Safari, Word Wizard), stories, intercom, media, + core 3 (HA, lists, knowledge).

### LLM Providers (`cortex/providers/`)

`LLMProvider` is the abstract base class. Concrete implementations: `TransformersProvider` (default), `OllamaProvider` (legacy fallback), `OpenAICompatibleProvider`. Use `get_provider()` factory which reads `LLM_PROVIDER` env var (default: `transformers`). Transformers provider uses `CAG_MODEL` env var (default: `Qwen/Qwen3-4B`). Embeddings use sentence-transformers via `EMBED_MODEL` (default: `all-MiniLM-L6-v2`). Ollama/OpenAI providers use `MODEL_FAST` and `MODEL_THINKING` env vars.

### Safety (`cortex/safety/`)

Two-checkpoint model: `InputGuardrails` run before the pipeline (can block/warn), `OutputGuardrails` buffer the full LLM response before yielding to the user (validate-then-yield). Content tiers are age-based: child/teen/adult/unknown. Jailbreak defense is in `jailbreak.py` (5 layers: regex, semantic, deobfuscation, output analysis, drift monitoring).

### Memory (`cortex/memory/`)

HOT/COLD architecture: HOT path is synchronous read (BM25 via SQLite FTS5 + optional ChromaDB vector search, fused with RRF). COLD path is async write (PII redaction → dedup → embed → store). Use `_fts_query()` to sanitize free text into safe FTS5 MATCH expressions.

### Key Modules

| Module | Purpose |
|--------|---------|
| `cortex/scheduling/` | Alarms, timers, reminders with NL time parsing |
| `cortex/routines/` | Routine automations, triggers, templates |
| `cortex/proactive/` | Proactive intelligence, daily briefing |
| `cortex/intercom/` | Announce, broadcast, two-way calling |
| `cortex/media/` | YouTube Music, Plex, Audiobookshelf, podcasts |
| `cortex/stories/` | Story generator, character voices, TTS hot-swap |
| `cortex/evolution/` | LoRA training, model scout, drift monitor |
| `cortex/cli/` | Atlas CLI agent (REPL, 31 tools, ReAct loop) |
| `cortex/admin/` | Admin API domain routers (19 sub-routers, 144+ endpoints) |
| `cortex/orchestrator/` | Request coordination (STT→pipeline→TTS) |
| `cortex/speech/` | Multi-provider TTS with hot-swap, STT |

### Database (`cortex/db.py`)

SQLite with WAL mode and foreign keys enabled. Schema has 50+ tables. `init_db()` is idempotent. `get_db()` returns per-thread connections. Tests use `set_db_path()` to point at in-memory or temp databases.

### LoRA System (`cortex/evolution/`)

LoRAs are **discovered** at startup via `discover_and_register()` — not composed automatically. The `model_registry` DB table tracks all known models and LoRA adapters. LoRA manager uses the `peft` library for adapter loading/composition. Admin API (`/admin/loras/compose`) composes a LoRA on demand. Model scout scans local HuggingFace cache for available models. Groups: `ultra-9b-v2/` (11 domains), `core-4b-h100/`, `focused-9b/` (specialty).

### Public Chat (`/chat`)

The `/chat` endpoint serves a public-facing chat SPA (from `admin/dist/chat.html`). User profiles support PIN, password, or passkey authentication via `/api/chat/auth`. Not behind admin auth — designed for household members.

### Docker Containers

The full Docker stack (`docker/docker-compose.yml`) includes:
- `atlas-cortex` — Main server + admin UI (port 5100, host networking, HF cache volume)
- `atlas-ollama` — LLM inference (port 11434, optional — commented out by default)
- `atlas-qwen-tts` — Primary TTS via Qwen3-TTS (port 7860, NVIDIA GPU)
- `atlas-fish-tts` — Story character voices via Fish Audio S2 (port 8860, NVIDIA GPU)
- `atlas-orpheus` — Backup TTS (port 5005, NVIDIA GPU)
- `atlas-kokoro` — Fast CPU TTS (port 8880)
- `atlas-piper` — Ultra-fast fallback TTS (port 10200)
- `atlas-whisper` — STT via whisper.cpp Vulkan (port 10300)

GPU overrides: `docker-compose.gpu-nvidia.yml`, `docker-compose.gpu-amd.yml`, `docker-compose.gpu-intel.yml`

### Admin Panel (`admin/`)

Vue 3 + Vite + Pinia SPA with 20 views. Build output goes to `admin/dist/` and is served as static files by the FastAPI server.

### Mock Infrastructure (`mocks/`)

GPU-free development with realistic timing. `python -m mocks.run` starts mock LLM, Whisper, and Kokoro servers. See `docs/development-mocks.md` for full guide and `mocks/data/` for benchmark timing data.

## Key Conventions

- Every Python module starts with `from __future__ import annotations`.
- All async code uses `async/await` natively (no callback patterns). Tests use `pytest-asyncio` with auto mode.
- Abstract base classes use `abc.ABC` + `@abc.abstractmethod` (see `LLMProvider`, `CortexPlugin`, `TTSProvider`).
- Data classes use `@dataclass` from the standard library (not Pydantic, except for API request/response models in `server.py`).
- Optional heavy dependencies (ChromaDB, zeroconf) use try/except import with graceful fallback — check the `_HAS_*` flags.
- Interaction logging in the pipeline is best-effort (wrapped in try/except, never raises).
- Environment variables drive configuration — see `CORTEX_*`, `LLM_PROVIDER`, `CAG_MODEL`, `EMBED_MODEL`, `HA_URL`, `HA_TOKEN` in the README.
- The admin panel (`admin/`) is a separate Vue 3 + Vite + Pinia SPA with 20 views. Its build output goes to `admin/dist/` and is served as static files by the FastAPI server.
- Branch protection on `main` — all changes must go through pull requests.
