# Atlas Cortex — Installation & Backend Abstraction

## Design Principles

1. **No hardcoded backends** — HuggingFace Transformers is the default, with Ollama and others as fallbacks
2. **Discovery-first** — the installer finds what's already running before suggesting new installs
3. **Two-stage setup** — deterministic installer first (no LLM), then LLM-assisted refinement
4. **Voice routes through Cortex** — HA voice pipeline → Atlas Cortex → LLM, never HA → LLM directly

---

## LLM Backend Abstraction

Atlas Cortex talks to LLMs through a **provider interface**, not directly to Ollama.

### Supported Providers

| Provider | API Style | How Detected | Notes |
|----------|-----------|-------------|-------|
| **HuggingFace Transformers** (default) | In-process Python | Always available | KV cache injection (CAG), no external server needed |
| **Ollama** (legacy fallback) | `/api/chat`, `/api/embeddings` | Probe `localhost:11434/api/tags` | Set `LLM_PROVIDER=ollama` |
| **llama.cpp server** | OpenAI-compatible `/v1/chat/completions` | Probe common ports (8080, 8000) | Lightweight, no container needed |
| **vLLM** | OpenAI-compatible | Probe `/v1/models` | High throughput, production-grade |
| **LocalAI** | OpenAI-compatible | Probe `/v1/models` | Multi-backend, batteries included |
| **LM Studio** | OpenAI-compatible | Probe `localhost:1234/v1/models` | GUI-friendly, popular on desktop |
| **koboldcpp** | Kobold API + OpenAI-compatible | Probe `/api/v1/model` | Popular for creative/RP |
| **text-generation-webui** | OpenAI-compatible extension | Probe `/v1/models` | Oobabooga, lots of model formats |
| **Existing OpenAI-compatible** | Any `/v1/chat/completions` | User provides URL | Self-hosted or cloud |

### Provider Interface

```python
class LLMProvider:
    """Abstract interface for any LLM backend."""
    
    async def chat(self, messages, model=None, stream=True, temperature=0.7, 
                   max_tokens=None, **kwargs):
        """Send a chat completion request. Returns async generator if stream=True."""
        raise NotImplementedError
    
    async def embed(self, text, model=None):
        """Generate embeddings for text. Returns list of floats."""
        raise NotImplementedError
    
    async def list_models(self):
        """List available models. Returns list of {name, size_bytes, supports_thinking}."""
        raise NotImplementedError
    
    async def health(self):
        """Check if the backend is reachable. Returns bool."""
        raise NotImplementedError
    
    def supports_embeddings(self):
        """Whether this provider can generate embeddings."""
        return False
    
    def supports_thinking(self):
        """Whether models on this provider support extended thinking."""
        return False


class TransformersProvider(LLMProvider):
    """HuggingFace Transformers: in-process inference with KV cache injection (CAG)."""
    ...

class OllamaProvider(LLMProvider):
    """Ollama-specific (legacy fallback): /api/chat, /api/embeddings, /api/tags"""
    ...

class OpenAICompatibleProvider(LLMProvider):
    """Works with vLLM, LocalAI, LM Studio, llama.cpp, etc."""
    ...
```

### Embedding Provider (Separate)

Embeddings might come from a different source than the chat LLM:

| Provider | How | Notes |
|----------|-----|-------|
| **Sentence-transformers** (default) | Python in-process | `EMBED_MODEL=all-MiniLM-L6-v2`, no external dependency, CPU |
| Fastembed (qdrant) | Python in-process | ONNX optimized, very fast |
| Ollama `nomic-embed-text` | `/api/embeddings` | Legacy, if Ollama is the LLM provider |
| OpenAI-compatible `/v1/embeddings` | HTTP | vLLM, LocalAI, etc. |

Sentence-transformers is the default embedding provider. No external server required.

---

## Chat UI Abstraction

Atlas isn't hardcoded to Open WebUI either:

| Chat UI | Integration Method | Notes |
|---------|-------------------|-------|
| **Open WebUI** (default) | Pipe function (Python plugin) | Deepest integration, runs inside the UI |
| **Any OpenAI-compatible client** | Atlas exposes its own `/v1/chat/completions` endpoint | Cortex acts as a proxy |
| **HA voice pipeline** | Wyoming protocol or custom conversation agent | Voice → Cortex → LLM |
| **REST API** | Direct HTTP to Atlas Cortex server | For custom frontends |
| **CLI** | Terminal client | For power users / debugging |

### Atlas as an OpenAI-Compatible Server

For maximum compatibility, Atlas Cortex can run as a standalone server that speaks the OpenAI API:

```
Any client that supports OpenAI API
    │
    ▼
Atlas Cortex Server (:5100)
    /v1/chat/completions  → full pipeline (sentiment, memory, plugins, LLM)
    /v1/models            → lists "atlas-cortex" as available model
    /v1/embeddings        → proxies to embedding provider
    │
    ▼
LLM Backend (HuggingFace Transformers, Ollama, vLLM, llama.cpp, etc.)
```

This means:
- Open WebUI can point to Atlas as an "OpenAI-compatible" provider
- HA conversation agent can point to Atlas as an "OpenAI-compatible" provider
- Any chat app, script, or tool that supports OpenAI API works with Atlas
- The Pipe function mode (inside Open WebUI) is still an option for tighter integration

---

## Voice Pipeline Routing

### The Problem

If HA's voice pipeline connects directly to Ollama, it bypasses everything Atlas provides: no memory, no personality, no device commands, no sentiment, no filler streaming, no user profiles.

### The Solution

HA voice pipeline → Atlas Cortex → LLM backend. Atlas is the conversation agent, not Ollama.

```
┌──────────────────────────────────────────────────────────────────┐
│                   Voice Pipeline Flow                              │
│                                                                    │
│  Satellite Mic                                                    │
│       │                                                            │
│       ▼                                                            │
│  HA Wyoming Protocol                                              │
│       │                                                            │
│       ├── STT: faster-whisper (audio → text)                      │
│       │                                                            │
│       ├── Audio → Speaker ID sidecar (→ user identity)            │
│       │                                                            │
│       ▼                                                            │
│  HA Conversation Agent: Atlas Cortex                              │
│  (NOT Ollama directly)                                            │
│       │                                                            │
│       │  HA sends: { text, satellite_id, [speaker_id] }          │
│       │  Atlas receives it as a normal request with metadata      │
│       │                                                            │
│       ▼                                                            │
│  Atlas Cortex Pipeline (full processing)                          │
│       │  Layer 0: context (user + room + sentiment)               │
│       │  Layer 1: instant answers                                 │
│       │  Layer 2: plugin commands (HA, lists, etc.)               │
│       │  Layer 3: LLM (via provider interface)                    │
│       │                                                            │
│       ▼                                                            │
│  Response → HA Conversation Agent                                 │
│       │                                                            │
│       ├── TTS: piper (text → audio + phonemes)                    │
│       │                                                            │
│       ├── Avatar: viseme stream to satellite display              │
│       │                                                            │
│       └── Audio → satellite speaker                               │
└──────────────────────────────────────────────────────────────────┘
```

### HA Integration Options

Atlas can integrate with HA as a conversation agent in two ways:

**Option A: OpenAI-compatible agent (recommended)**
- HA has a built-in "OpenAI-compatible" conversation agent integration
- Point it to Atlas Cortex server URL (e.g., `http://192.168.3.8:5100/v1`)
- Zero custom code in HA — just configure the URL + API key
- Atlas receives the text, processes it, returns the response
- Satellite metadata (device_id) passed via headers or request context

**Option B: Custom HA integration**
- Custom `conversation` platform for HA
- Richer metadata: satellite_id, audio for speaker-id, presence state
- More work to set up, but deeper integration
- Could be a HACS component (installable via HA UI)

**Recommendation**: Start with Option A (works immediately), build Option B later for deeper spatial awareness.

---

## Installation Flow

### Stage 1: Deterministic Installer (No LLM)

The installer is a Python CLI tool that runs entirely without an LLM. It uses rule-based logic, hardware detection, and network probing.

```
$ python -m cortex.install

╔══════════════════════════════════════════════╗
║         Atlas Cortex — Installation          ║
╚══════════════════════════════════════════════╝

[1/6] Checking Python environment...
  ✓ Python 3.12.3
  ✓ pip available
  ✓ Docker available (for optional sidecars)

[2/6] Detecting hardware...
  CPU: AMD Ryzen 7 5700G (8c/16t)
  RAM: 128 GB DDR4
  GPU: AMD Radeon RX 7900 XT (20 GB GDDR6, ROCm)
  Disk: 347 GB free on /data

[3/6] Scanning for existing LLM backends...
  Probing localhost and local network...
  ✓ HuggingFace Transformers available (in-process, no server needed)
  ✓ Ollama found at localhost:11434 (3 models loaded)
  ✗ vLLM not found
  ✗ llama.cpp not found
  ✓ LM Studio found at 192.168.3.15:1234 (1 model)

  Which LLM backend should Atlas use?
  > 1. HuggingFace Transformers (recommended — no external server needed)
    2. Ollama at localhost:11434
    3. LM Studio at 192.168.3.15:1234
    4. Install Ollama fresh
    5. Other (provide URL)

  Selected: HuggingFace Transformers

[4/6] Selecting models for your hardware...
  Based on: 20 GB VRAM (AMD ROCm), 128 GB RAM

  Role          │ Recommended           │ Size    │ Why
  ──────────────┼───────────────────────┼─────────┼────────────────────
  Fast/CAG      │ Qwen/Qwen3-4B        │ ~8 GB   │ In-process, KV cache injection
  Embedding     │ all-MiniLM-L6-v2     │ ~80 MB  │ CPU, 384-dim, <5ms

  Models download automatically from HuggingFace on first use.

[5/6] Scanning for chat interfaces...
  ✓ Open WebUI found at localhost:8080
  
  Atlas can integrate as:
  > 1. Open WebUI Pipe function (recommended — tightest integration)
    2. Standalone server (:5100) — Open WebUI connects as OpenAI provider
    3. Both (Pipe for web, standalone for voice/API)

  Selected: Both

[6/6] Setting up Atlas Cortex...
  ✓ Created /data/cortex.db (schema initialized)
  ✓ ChromaDB initialized at /data/cortex_chroma/
  ✓ Atlas Cortex server starting on :5100
  ✓ Open WebUI Pipe function installed
  ✓ Hardware profile saved
  ✓ Model config saved

╔══════════════════════════════════════════════╗
║  ✓ Atlas Cortex is running!                  ║
║                                              ║
║  Web UI: Open WebUI → select "Atlas Cortex"  ║
║  API:    http://localhost:5100/v1             ║
║                                              ║
║  Next: say "Hey Atlas" or run service        ║
║  discovery to find Home Assistant, etc.       ║
║                                              ║
║  $ python -m cortex.discover                  ║
╚══════════════════════════════════════════════╝
```

### Stage 2: LLM-Assisted Refinement (Optional)

Once the LLM is running, Atlas can help configure the rest via conversation:

```
User: "Atlas, discover my network"

Atlas: "Scanning your local network... Found 4 services:
  1. Home Assistant at 192.168.4.8:8123 — smart home platform
  2. Nextcloud at 192.168.1.6:443 — file storage
  3. MariaDB at 192.168.1.5:3306 — database
  4. MQTT broker at 192.168.1.8:1883 — message broker

  Want me to connect to any of these? I'll need credentials 
  for each one."

User: "Connect to Home Assistant"

Atlas: "To connect to Home Assistant, I need a long-lived access token.
  Here's how to create one:
  1. Go to http://192.168.4.8:8123/profile
  2. Scroll to 'Long-Lived Access Tokens'
  3. Click 'Create Token', name it 'Atlas Cortex'
  4. Copy the token and paste it here

  I'll wait."

User: [pastes token]

Atlas: "Got it. Connecting... ✓ Connected!
  Found 47 devices across 8 rooms. I can now control your 
  lights, switches, climate, locks, and media players.
  
  Try: 'turn off the living room lights'"
```

### Fallback: Full CLI Discovery (No LLM)

The `cortex.discover` CLI can do everything the LLM-assisted mode does, just less conversationally:

```bash
$ python -m cortex.discover

Scanning network...
Found services:
  [1] Home Assistant   192.168.4.8:8123    (mDNS: _home-assistant._tcp)
  [2] Nextcloud        192.168.1.6:443     (WebDAV probe)
  [3] MQTT Broker      192.168.1.8:1883    (mDNS: _mqtt._tcp)

Configure a service? [1/2/3/skip] > 1

Home Assistant setup:
  URL: http://192.168.4.8:8123
  Token: [paste long-lived access token] > eyJ...
  
  Testing connection... ✓ Connected (47 devices, 8 areas)
  Registering HA plugin... ✓ 142 command patterns loaded
  
Configure another? [2/3/skip] > skip

Done. Atlas now has Home Assistant integration active.
```

---

## What Gets Installed

### Minimal (just the brain)

```
atlas-cortex/
├── cortex/                    # Python package
│   ├── __init__.py
│   ├── server.py              # Standalone OpenAI-compatible server
│   ├── pipe.py                # Open WebUI Pipe function (optional)
│   ├── providers/             # LLM backend providers
│   │   ├── base.py            # LLMProvider interface
│   │   ├── transformers.py    # HuggingFace Transformers provider (default)
│   │   ├── ollama.py          # Ollama provider (legacy fallback)
│   │   └── openai_compat.py   # Any OpenAI-compatible backend
│   ├── pipeline/              # Processing layers
│   │   ├── layer0_context.py
│   │   ├── layer1_instant.py
│   │   ├── layer2_plugins.py  # Plugin dispatch
│   │   └── layer3_llm.py
│   ├── plugins/               # Integration plugins (Part 2)
│   │   ├── base.py            # Plugin interface
│   │   └── (installed by discovery)
│   ├── memory/                # HOT/COLD memory engine
│   ├── profiles/              # User profiles, age, parental
│   ├── context/               # Context compaction, checkpoints
│   ├── filler/                # Sentiment + confidence fillers
│   ├── grounding/             # Confidence scoring, grounding loop
│   ├── backup/                # Backup/restore CLI
│   └── install/               # Installer + discovery
│       ├── __main__.py        # python -m cortex.install
│       ├── hardware.py        # GPU/CPU/RAM detection
│       ├── providers.py       # Find running LLM backends
│       ├── discovery.py       # Network service discovery
│       └── wizard.py          # Interactive config wizard
├── data/
│   ├── cortex.db              # SQLite database
│   ├── cortex_chroma/         # ChromaDB vectors
│   └── cortex.env             # Config (provider URLs, API keys)
└── docker/                    # Optional container configs
    ├── docker-compose.yml
    └── Dockerfile
```

### With Sidecars (optional)

```
docker-compose.yml deploys:
  atlas-cortex       — main server (:5100)
  atlas-speaker-id   — voice identification (:8890)
  atlas-avatar       — avatar WebSocket server (:8891)
  atlas-evolution    — nightly cron job
```

---

## Configuration File

```env
# cortex.env — generated by installer, editable

# LLM Provider
LLM_PROVIDER=transformers               # transformers | ollama | openai_compatible
CAG_MODEL=Qwen/Qwen3-4B                 # HuggingFace model (Transformers provider)
CAG_DEVICE=auto                          # auto | cuda | cpu
CAG_DTYPE=auto                           # auto | float16 | bfloat16
LLM_API_KEY=                             # optional, for authenticated providers

# Legacy Ollama config (set LLM_PROVIDER=ollama to use)
# LLM_URL=http://localhost:11434
# MODEL_FAST=qwen2.5:14b
# MODEL_THINKING=qwen3:30b-a3b

# Embedding Provider (sentence-transformers is default)
EMBED_MODEL=all-MiniLM-L6-v2            # sentence-transformers model name

# Atlas Server
CORTEX_HOST=0.0.0.0
CORTEX_PORT=5100
CORTEX_DATA_DIR=/data

# Open WebUI Integration (optional)
OPENWEBUI_PIPE_ENABLED=true
OPENWEBUI_URL=http://localhost:8080

# Context Limits (auto-computed from hardware, override here)
# CONTEXT_DEFAULT=16384
# CONTEXT_THINKING=32768
# MAX_MODEL_SIZE_MB=12000
```

---

## Voice Pipeline Connection

### For HA Users

After I2 (HA plugin) is configured:

```yaml
# HA configuration.yaml — conversation agent pointing to Atlas
# (or configured via HA UI → Settings → Voice Assistants)

# Option A: OpenAI-compatible (recommended)
# HA Settings → Integrations → Add → OpenAI Conversation
#   API Base URL: http://192.168.3.8:5100/v1
#   API Key: (from cortex.env, or "cortex" as placeholder)
#   Model: atlas-cortex

# Voice pipeline:
#   STT: faster-whisper (Wyoming, port 10300)
#   Conversation Agent: Atlas Cortex (OpenAI-compatible)
#   TTS: Piper (Wyoming, port 10200)
```

### For Non-HA Users

Voice pipeline without Home Assistant:

```
Microphone (local or remote)
    │
    ▼
STT (faster-whisper, Whisper.cpp, or cloud STT)
    │
    ▼
Atlas Cortex API (:5100/v1/chat/completions)
    │  Includes speaker_id header if available
    │
    ▼
Response text
    │
    ▼
TTS (Piper, Coqui, or cloud TTS)
    │
    ▼
Speaker
```

Atlas provides a `/v1/audio/transcriptions` proxy endpoint that chains STT → pipeline → TTS for simple voice-in/voice-out without HA.

---

## Installer Decision Tree

```
Start
 │
 ├─ Hardware detected
 │
 ├─ Scan for existing LLM backends
 │   ├─ Found? → offer to use existing
 │   └─ Not found? → offer to install (Ollama default, others available)
 │
 ├─ Backend selected/installed
 │
 ├─ Scan for existing models on backend
 │   ├─ Suitable models found? → offer to use them
 │   └─ Not found? → recommend based on hardware, pull
 │
 ├─ Scan for chat UIs
 │   ├─ Open WebUI found? → offer Pipe integration
 │   ├─ Other UI found? → offer standalone server mode
 │   └─ Nothing found? → set up standalone server + suggest Open WebUI
 │
 ├─ Core Atlas running ✓
 │
 ├─ Offer service discovery (Part 2)
 │   ├─ Now → run discovery scan
 │   └─ Later → user can run anytime via CLI or conversation
 │
 └─ Done
```

---

## Provider Auto-Detection

The installer probes in this order:

```python
PROBE_TARGETS = [
    # (name, urls_to_try, health_check_path, detect_fn)
    ('Ollama', ['http://localhost:11434', 'http://127.0.0.1:11434'], '/api/tags', detect_ollama),
    ('LM Studio', ['http://localhost:1234'], '/v1/models', detect_openai_compat),
    ('LocalAI', ['http://localhost:8080'], '/v1/models', detect_openai_compat),
    ('vLLM', ['http://localhost:8000'], '/v1/models', detect_openai_compat),
    ('text-gen-webui', ['http://localhost:5000', 'http://localhost:5001'], '/v1/models', detect_openai_compat),
    ('koboldcpp', ['http://localhost:5001'], '/api/v1/model', detect_kobold),
    ('llama.cpp', ['http://localhost:8080'], '/v1/models', detect_openai_compat),
]

# Also scan local network for common ports if nothing found on localhost
NETWORK_SCAN_PORTS = [11434, 1234, 8080, 8000, 5000, 5001]
```

---

## What This Means for the Architecture

### Before (hardcoded)
```
Open WebUI → Pipe function → Ollama API
HA → Ollama directly (bypasses Atlas)
```

### After (abstracted)
```
Any chat UI ──┐
              ├──▶ Atlas Cortex ──▶ Any LLM backend
HA voice ─────┘         │
                        │
                   Plugin system ──▶ HA, Nextcloud, NAS, etc.
```

Atlas Cortex becomes the **single conversation endpoint** for everything — typed, voice, API. All intelligence (memory, personality, grounding, context management) lives in Atlas, not in the backend or the UI.
