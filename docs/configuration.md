# ⚙️ Atlas Cortex — Configuration Reference

All configuration is via environment variables. Set them in your shell, a `.env` file, or in `docker-compose.yml`.

---

## Core

| Variable | Default | Description |
|----------|---------|-------------|
| `CORTEX_HOST` | `0.0.0.0` | Server bind address |
| `CORTEX_PORT` | `5100` | Server port |
| `CORTEX_DATA_DIR` | `./data` | Database, cache, and state directory |
| `CORTEX_JWT_SECRET` | *(generated)* | JWT signing secret — set in production |
| `CORTEX_JWT_EXPIRY` | `86400` | Admin session duration in seconds (24 h) |

## LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | Backend type: `ollama`, `openai_compatible` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `LLM_URL` | — | Override URL for any LLM backend |
| `LLM_API_KEY` | — | API key (required for OpenAI-compatible) |
| `OPENAI_BASE_URL` | — | Custom OpenAI-compatible endpoint |
| `OPENAI_API_KEY` | — | API key for OpenAI-compatible backends |
| `MODEL_FAST` | `qwen2.5:14b` | Model for quick factual answers |
| `MODEL_THINKING` | `qwen3:30b-a3b` | Model for complex reasoning |
| `MODEL_EMBEDDING` | — | Embedding model (Docker only) |

## TTS (Text-to-Speech)

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_PROVIDER` | `orpheus` | Primary provider: `orpheus`, `kokoro`, `piper` |
| `ORPHEUS_FASTAPI_URL` | `http://localhost:5005` | Orpheus TTS server URL |
| `KOKORO_HOST` | `localhost` | Kokoro TTS host |
| `KOKORO_PORT` | `8880` | Kokoro TTS port |
| `KOKORO_VOICE` | `af_bella` | Default Kokoro voice |
| `PIPER_HOST` | `localhost` | Piper TTS host (falls back to `TTS_HOST`) |
| `PIPER_PORT` | `10200` | Piper TTS port (falls back to `TTS_PORT`) |
| `TTS_HOST` | `localhost` | Generic TTS host fallback |
| `TTS_PORT` | `10200` | Generic TTS port fallback |
| `TTS_GPU_ID` | `cuda:0` | GPU device for TTS hot-swap |
| `TTS_VRAM_MB` | `8192` | VRAM budget for TTS in MB |
| `FISH_AUDIO_HOST` | `localhost` | Fish Audio S2 host (story voices) |
| `FISH_AUDIO_PORT` | `8860` | Fish Audio S2 port |
| `FISH_AUDIO_API_KEY` | — | Fish Audio API key |

## STT (Speech-to-Text)

| Variable | Default | Description |
|----------|---------|-------------|
| `STT_BACKEND` | `whisper_cpp` | STT backend type |
| `STT_HOST` | `localhost` | STT server host |
| `STT_PORT` | `10300` | STT server port |
| `STT_URL` | `http://localhost:8178` | STT URL (CLI tools) |

## Home Assistant

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_URL` | — | Home Assistant URL (e.g. `http://192.168.1.100:8123`) |
| `HA_TOKEN` | — | Long-lived access token |

## Media Services

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_URL` | — | Plex server URL |
| `PLEX_TOKEN` | — | Plex authentication token |
| `ABS_URL` | — | Audiobookshelf server URL |
| `ABS_TOKEN` | — | Audiobookshelf API token |

## Weather & Calendar

| Variable | Default | Description |
|----------|---------|-------------|
| `WEATHER_API_KEY` | — | OpenWeatherMap API key |
| `WEATHER_LOCATION` | — | Location for weather queries |
| `WEATHER_API_URL` | `https://api.openweathermap.org/data/2.5` | Weather API base URL |
| `CALDAV_URL` | — | CalDAV server URL |
| `CALDAV_USERNAME` | — | CalDAV username |
| `CALDAV_PASSWORD` | — | CalDAV password |

## Self-Evolution

| Variable | Default | Description |
|----------|---------|-------------|
| `LORA_BASE_MODEL` | `Qwen/Qwen2.5-7B` | Base model for LoRA fine-tuning |
| `VISION_MODEL_URL` | `http://localhost:11434` | Vision model endpoint |
| `VISION_MODEL` | `llava` | Vision model name |
| `IMAGE_GEN_URL` | — | Image generation endpoint |
| `SEARXNG_URL` | — | SearXNG search engine URL |

## Stories

| Variable | Default | Description |
|----------|---------|-------------|
| `STORY_AUDIO_CACHE` | system temp dir | Story audio cache directory |

## Docker-Only Variables

These are typically set in `docker-compose.yml`, not in application code:

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `America/New_York` | Container timezone |
| `PUID` / `PGID` | `99` / `100` | Container user/group IDs |
| `OLLAMA_HOST` | `0.0.0.0` | Ollama bind address |
| `OLLAMA_KEEP_ALIVE` | `0` | Model keep-alive duration |
| `WHISPER_MODEL` | `large-v3-turbo-q5_0` | Whisper model variant |
| `WHISPER_BEAM` | `5` | Whisper beam search width |
| `WHISPER_LANG` | `en` | Whisper language |
| `PIPER_VOICE` | `en_US-lessac-medium` | Piper voice model |

---

## Minimal Configuration

For a basic setup with Ollama running locally, no environment variables are required — defaults work out of the box:

```bash
python -m cortex.server
```

For Home Assistant integration, set just two variables:

```bash
export HA_URL=http://192.168.1.100:8123
export HA_TOKEN=your_long_lived_access_token
python -m cortex.server
```
