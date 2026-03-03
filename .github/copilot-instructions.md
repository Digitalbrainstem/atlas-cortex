# Atlas Cortex — Copilot Instructions

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

## Architecture

Atlas Cortex is a self-evolving AI home assistant with an OpenAI-compatible API. It has two entry points that share the same pipeline:

- **`cortex/server.py`** — Standalone FastAPI server on port 5100
- **`cortex/pipe.py`** — Open WebUI Pipe function (drop-in, no separate server)

### 4-Layer Pipeline (`cortex/pipeline/`)

Every message flows through layers sequentially; **first match wins**:

1. **Layer 0** (`layer0_context.py`) — Context assembly: speaker ID, room, sentiment, time-of-day (~1ms)
2. **Layer 1** (`layer1_instant.py`) — Instant answers: date, time, math, greetings — no LLM needed (~5ms)
3. **Layer 2** (`layer2_plugins.py`) — Plugin dispatch: Home Assistant, lists, knowledge search (~100ms)
4. **Layer 3** (`layer3_llm.py`) — Pre-cached filler audio + sentence-level LLM streaming (~500–4000ms)

`run_pipeline()` in `cortex/pipeline/__init__.py` is the orchestrator. It returns an async generator yielding text tokens.

### Voice Engine (`cortex/voice/`)

**Primary TTS: Kokoro** (82M params, CPU, sub-2s synthesis, port 8880).
Alternate: Orpheus (GPU, emotion tags). Fallback: Piper (CPU, fast).

Provider factory: `get_tts_provider()` in `cortex/voice/providers/__init__.py`.
Default voice: `af_bella`. Env vars: `TTS_PROVIDER`, `KOKORO_HOST`, `KOKORO_PORT`, `KOKORO_VOICE`.

### Plugin System (`cortex/plugins/`)

Layer 2 plugins extend `CortexPlugin` (in `cortex/plugins/base.py`) and register with `PluginRegistry`. Each plugin implements `match()` → `CommandMatch` and `handle()` → `CommandResult`. Dispatch tries plugins in registration order.

### LLM Providers (`cortex/providers/`)

`LLMProvider` is the abstract base class. Concrete implementations: `OllamaProvider`, `OpenAICompatibleProvider`. Use `get_provider()` factory which reads `LLM_PROVIDER` env var. Models: `MODEL_FAST` and `MODEL_THINKING` env vars (defaults: `qwen2.5:14b` / `qwen3:30b-a3b`, production uses `qwen2.5:7b` for both).

### Safety (`cortex/safety/`)

Two-checkpoint model: `InputGuardrails` run before the pipeline (can block/warn), `OutputGuardrails` buffer the full LLM response before yielding to the user (validate-then-yield). Content tiers are age-based: child/teen/adult/unknown. Jailbreak defense is in `jailbreak.py` (5 layers: regex, semantic, deobfuscation, output analysis, drift monitoring).

### Memory (`cortex/memory/`)

HOT/COLD architecture: HOT path is synchronous read (BM25 via SQLite FTS5 + optional ChromaDB vector search, fused with RRF). COLD path is async write (PII redaction → dedup → embed → store). Use `_fts_query()` to sanitize free text into safe FTS5 MATCH expressions.

### Database (`cortex/db.py`)

SQLite with WAL mode and foreign keys enabled. Schema has 50+ tables. `init_db()` is idempotent. `get_db()` returns per-thread connections. Tests use `set_db_path()` to point at in-memory or temp databases.

### Mock Infrastructure (`mocks/`)

GPU-free development with realistic timing. `python -m mocks.run` starts mock Ollama, Whisper, and Kokoro servers. See `docs/development-mocks.md` for full guide and `mocks/data/` for benchmark timing data.

## Key Conventions

- Every Python module starts with `from __future__ import annotations`.
- All async code uses `async/await` natively (no callback patterns). Tests use `pytest-asyncio` with auto mode.
- Abstract base classes use `abc.ABC` + `@abc.abstractmethod` (see `LLMProvider`, `CortexPlugin`, `TTSProvider`).
- Data classes use `@dataclass` from the standard library (not Pydantic, except for API request/response models in `server.py`).
- Optional heavy dependencies (ChromaDB, zeroconf) use try/except import with graceful fallback — check the `_HAS_*` flags.
- Interaction logging in the pipeline is best-effort (wrapped in try/except, never raises).
- Environment variables drive configuration — see `CORTEX_*`, `LLM_PROVIDER`, `OLLAMA_BASE_URL`, `HA_URL`, `HA_TOKEN` in the README.
- The admin panel (`admin/`) is a separate Vue 3 + Vite + Pinia SPA. Its build output goes to `admin/dist/` and is served as static files by the FastAPI server.
- Branch protection on `main` — all changes must go through pull requests.
