# 💾 Atlas Cortex — Persistent Storage Reference

All persistent data paths for Docker and bare-metal deployments.

---

## Directory Layout

```
/mnt/user/ai-core-files/                (or any host path)
├── cortex-data/          → /data in container (CORTEX_DATA_DIR)
│   ├── cortex.db         — Main SQLite database (50+ tables)
│   ├── tts_cache/        — Pre-generated TTS audio cache
│   └── models/
│       └── atlas.onnx    — Wake word detection model
├── models/
│   ├── base/             — Base LLM models (HuggingFace format)
│   └── loras/            — LoRA adapters by group
│       ├── ultra-9b-v2/  — 11 domain LoRAs for 9B model
│       ├── core-4b-h100/ — 11 domain LoRAs for 4B model
│       └── focused-9b/   — Specialty LoRAs (music, history, etc.)
├── ollama/               — Ollama model storage (.ollama/models)
├── tts-cache/            — External TTS audio cache
├── piper/                — Piper TTS voice models and config
├── whisper/              — Whisper STT model files (.bin)
├── fish-speech/          — Fish Speech voice cloning checkpoints
└── wake-word/            — Wake word ONNX models
```

## Docker Volume Mappings

From `docker/docker-compose.yml`:

| Container | Host Path | Container Path | Purpose |
|-----------|-----------|----------------|---------|
| `atlas-cortex` | `${ATLAS_DATA_BASE}/data` | `/data` | Main database, state, cache |
| `atlas-cortex` | `${ATLAS_DATA_BASE}/models/loras` | `/loras` (read-only) | LoRA adapters |
| `atlas-ollama` | `${ATLAS_DATA_BASE}/models/ollama` | `/root/.ollama` | Ollama model storage |
| `atlas-piper` | `atlas-piper-data` (named volume) | `/config` | Piper voice models |
| `atlas-whisper` | `atlas-whisper-data` (named volume) | `/models` | Whisper STT models |
| `atlas-orpheus` | `atlas-orpheus-models` (named volume) | `/models` | Orpheus TTS GGUF model |

**Default base path:** `ATLAS_DATA_BASE=/mnt/fastpool/atlas`

## Environment Variables for Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `CORTEX_DATA_DIR` | `./data` (bare metal) or `/data` (Docker) | Main data directory — SQLite DB, cache, state |
| `LORA_DIR` | `~/.cortex/loras` (bare metal) or `/loras` (Docker) | Path to LoRA adapter directories |
| `STORY_AUDIO_CACHE` | System temp dir | Story audio cache directory |

## What Lives Where

### `/data` (CORTEX_DATA_DIR)

The main data directory. **Back this up regularly.**

| Path | Description |
|------|-------------|
| `cortex.db` | Main SQLite database — users, conversations, memory, safety events, evolution logs, 50+ tables |
| `tts_cache/` | Pre-generated TTS audio (filler phrases, cached responses) |
| `models/atlas.onnx` | Wake word detection model (OpenWakeWord format) |
| `backups/` | Automated nightly backup archives |

### `/loras` (LORA_DIR)

LoRA adapters organized by group. Each group contains domain-specific adapters:

```
loras/
├── ultra-9b-v2/
│   ├── home-automation/   — Smart home commands
│   ├── cooking/           — Recipes and food
│   ├── education/         — Teaching and tutoring
│   ├── medical/           — Health information
│   ├── music/             — Music knowledge
│   ├── science/           — Scientific topics
│   ├── sports/            — Sports data
│   ├── stories/           — Story generation
│   ├── tech/              — Technology
│   ├── weather/           — Weather patterns
│   └── general/           — General conversation
├── core-4b-h100/
│   └── (same domain structure)
└── focused-9b/
    ├── music-deep/        — Deep music knowledge
    ├── history/           — Historical events
    └── (specialty adapters)
```

LoRAs are **discovered** at startup via `discover_and_register()` and registered in the `model_registry` database table. They are **not composed** automatically — use the admin panel or API to compose them into Ollama models.

### Ollama Storage

Ollama stores models in its own directory structure:

```
ollama/
└── models/
    ├── blobs/         — Model weight files
    └── manifests/     — Model metadata
```

### Named Docker Volumes

These are managed by Docker and don't need manual host paths:

| Volume | Used By | Content |
|--------|---------|---------|
| `atlas-piper-data` | atlas-piper | Piper voice models (auto-downloaded) |
| `atlas-whisper-data` | atlas-whisper | Whisper STT models (downloaded by init container) |
| `atlas-orpheus-models` | atlas-orpheus | Orpheus GGUF model (downloaded by init container) |

## Docker Compose Services

Full service stack from `docker/docker-compose.yml`:

| Service | Image | Port | GPU | Purpose |
|---------|-------|------|-----|---------|
| `atlas-cortex` | `atlas-cortex:latest` | 5100 (host net) | — | Main server + admin UI |
| `atlas-ollama` | `ollama/ollama:latest` | 11434 | Primary (LLM) | LLM inference |
| `atlas-qwen-tts` | `atlas-qwen-tts:latest` | 7860 | NVIDIA (TTS) | Primary TTS (Qwen3-TTS) |
| `atlas-fish-tts` | `fishaudio/fish-speech:latest` | 8860 | NVIDIA | Story character voices |
| `atlas-orpheus` | `atlas-orpheus-vllm:latest` | 5005 | NVIDIA | Backup TTS (Orpheus) |
| `atlas-kokoro` | `ghcr.io/remsky/kokoro-fastapi-cpu:latest` | 8880 | — (CPU) | Fast CPU TTS |
| `atlas-piper` | `lscr.io/linuxserver/piper:latest` | 10200 | — (CPU) | Ultra-fast fallback TTS |
| `atlas-whisper` | `ghcr.io/ggml-org/whisper.cpp:main-vulkan` | 10300 | Vulkan | Speech-to-text |

GPU override files for vendor-specific configuration:
- `docker-compose.gpu-nvidia.yml` — NVIDIA CUDA devices
- `docker-compose.gpu-amd.yml` — AMD ROCm devices
- `docker-compose.gpu-intel.yml` — Intel oneAPI/IPEX devices

## Backup Strategy

- **Database:** Nightly automated backups of `cortex.db` (see `cortex/backup/`)
- **LoRAs:** Read-only mount — source of truth is the host directory
- **Ollama models:** Can be re-pulled from registries; backup optional
- **TTS cache:** Regenerable — backup not required
- **Whisper/Piper models:** Auto-downloaded on first run; backup not required

## Bare-Metal Paths

When running without Docker, default paths are relative to the working directory:

| What | Default Path | Override |
|------|-------------|----------|
| Database | `./data/cortex.db` | `CORTEX_DATA_DIR` |
| TTS cache | `./data/tts_cache/` | `CORTEX_DATA_DIR` |
| LoRA adapters | `~/.cortex/loras/` | `LORA_DIR` |
| Wake word model | `./data/models/atlas.onnx` | `CORTEX_DATA_DIR` |
