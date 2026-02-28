<div align="center">

# 🧠 Atlas Cortex

**A self-evolving AI assistant that learns, adapts, and grows with your household.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-554%20passing-brightgreen.svg)](#testing)
[![Open WebUI](https://img.shields.io/badge/Open%20WebUI-compatible-orange.svg)](https://github.com/open-webui/open-webui)

*Hardware-agnostic · Privacy-first · Family-safe · Self-learning*

</div>

---

Atlas Cortex transforms a local LLM into an intelligent home assistant that understands who's speaking, adapts to each family member, controls your smart home, and gets smarter every day — all running on **your hardware**, with **zero cloud dependencies**.

## ✨ Key Features

### 🚀 Intelligent Response Pipeline
| Layer | Latency | What Happens |
|-------|---------|--------------|
| **Context Assembly** | ~1ms | Identifies speaker, room, sentiment, time-of-day |
| **Instant Answers** | ~5ms | Date, time, math, greetings — no LLM needed |
| **Plugin Dispatch** | ~100ms | Smart home control, lists, knowledge search |
| **LLM Generation** | ~500–4000ms | Full reasoning with filler streaming for zero perceived wait |

### 🏠 Smart Home Integration
- **Natural language control** — *"Turn off the bedroom lights"* executes directly via Home Assistant API
- **Spatial awareness** — knows which room you're in via satellite mics and presence sensors
- **Scene automation** — *"Good night"* triggers your bedtime routine
- **Self-learning** — commands that go to the LLM are analyzed nightly and converted into fast regex patterns

### 🗣️ Voice & Speech Engine
- **Emotional TTS** — Orpheus speech synthesis with natural pauses, breathing, laughter, and emotion tags
- **Voice identification** — recognizes family members by voice, personalizes responses per person
- **Multiple providers** — Orpheus (emotional, GPU), Piper (fast, CPU fallback), extensible provider interface
- **Sentence-boundary streaming** — starts speaking before the full response is generated

### 🛡️ Safety & Content Policy
- **Age-appropriate responses** — automatically adapts vocabulary and content for toddlers, children, teens, and adults
- **Educational mode** — uses scientific terminology for biology/anatomy at all ages — never evasive
- **5-layer jailbreak defense** — regex patterns, semantic analysis, system prompt hardening, output monitoring, adaptive learning
- **PII protection** — SSN, credit card, phone, email auto-redacted from logs and memory
- **Crisis detection** — recognizes self-harm/emergency language and responds with appropriate resources

### 🧠 Memory & Learning
- **HOT/COLD architecture** — ChromaDB vector search + SQLite FTS5 with reciprocal rank fusion
- **Persistent memory** — remembers conversations, preferences, and facts across sessions
- **Nightly evolution** — analyzes patterns, learns from mistakes, evolves personality profiles
- **Anti-hallucination** — confidence scoring, grounding loops, and mistake tracking

### 👤 User Profiles & Personality
- **Per-user adaptation** — vocabulary level, preferred tone, communication style
- **Honest personality** — pushes back on bad ideas, challenges in tutoring mode, never sycophantic
- **Emotional evolution** — builds unique rapport with each household member over time
- **Parental controls** — content filtering, allowed hours, restricted actions per child

### 🖥️ Admin Web Panel
- **Dark-themed dashboard** — real-time stats, recent activity, system health at a glance
- **User management** — profiles, age settings, vocabulary levels, parental controls
- **Safety monitoring** — guardrail event log, jailbreak pattern management, content tier overrides
- **Voice enrollment** — view and manage speaker profiles, confidence thresholds
- **Device management** — Home Assistant devices, aliases, command patterns
- **Evolution tracking** — rapport scores, emotional profiles, nightly evolution logs
- **System overview** — hardware info, GPU assignment, model configs, discovered services

## 🏗️ Architecture

```
                        ┌───────────────────────┐
                        │    User Interface      │
                        │  Open WebUI / Voice    │
                        │  / Satellite Mics      │
                        └───────────┬───────────┘
                                    │
                        ┌───────────▼───────────┐
                        │    Atlas Cortex        │
                        │    Server (:5100)      │
                        │  OpenAI-compatible     │
                        └───────────┬───────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                       │
  ┌─────────▼──────────┐ ┌─────────▼──────────┐ ┌─────────▼──────────┐
  │   Input Pipeline    │ │  Safety Guardrails  │ │   Voice Engine      │
  │                     │ │                     │ │                     │
  │ L0: Context  (1ms)  │ │ • Content tiers     │ │ • Orpheus TTS       │
  │ L1: Instant  (5ms)  │ │ • Jailbreak defense │ │ • Piper fallback    │
  │ L2: Plugins (100ms) │ │ • PII redaction     │ │ • Emotion tags      │
  │ L3: LLM   (500ms+)  │ │ • Crisis detection  │ │ • Voice streaming   │
  └─────────┬──────────┘ └─────────────────────┘ └─────────────────────┘
            │
  ┌─────────▼───────────────────────────────────────────────────────┐
  │                         Integrations                           │
  │                                                                │
  │  🏠 Home Assistant  📋 Lists  📚 Knowledge  🔍 Memory        │
  │  🔧 Service Discovery  📦 Backup  🎓 Learning                │
  └───────────────────────────┬───────────────────────────────────┘
                                │
                     ┌──────────▼───────────┐
                     │  SQLite + ChromaDB    │
                     │  (WAL mode, 50+ tbl)  │
                     └──────────┬───────────┘
                                │
                     ┌──────────▼───────────┐
                     │  Nightly Evolution    │
                     │  Pattern learning     │
                     │  Profile evolution    │
                     │  Device discovery     │
                     └──────────────────────┘
```

### Multi-GPU Support

Atlas detects all GPUs at startup and assigns optimal roles:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GPU 0 (Largest)│     │  GPU 1 (Second) │     │  iGPU (Fallback)│
│  ═══════════════│     │  ═══════════════│     │  ═══════════════│
│  LLM Inference  │     │  Voice / TTS    │     │  Lightweight    │
│  Ollama :11434  │     │  Ollama :11435  │     │  tasks only     │
│  20GB+ VRAM     │     │  8-12GB VRAM    │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

- **Supported GPUs**: AMD (ROCm), NVIDIA (CUDA), Intel (oneAPI/IPEX), Apple (Metal)
- **Auto-sizing**: Models selected based on available VRAM — from 1.7B (4GB) to 72B (48GB+)
- **Mixed vendors**: Run AMD + Intel GPUs in the same system via separate containers

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.com)** — any GPU or CPU-only
- **[Open WebUI](https://github.com/open-webui/open-webui) v0.8.5+** (recommended) or any OpenAI-compatible client

### Quick Start (Docker)

```bash
# Clone the repository
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex

# Start with Docker Compose
docker compose -f docker/docker-compose.yml up -d

# Atlas is now running at http://localhost:5100
```

### Quick Start (Manual)

```bash
# Clone and set up
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the interactive installer
python -m cortex.install

# Or start the server directly
python -m cortex.server
```

### Connect to Open WebUI

1. Open your Open WebUI instance
2. Go to **Admin → Settings → Connections**
3. Add a new OpenAI-compatible connection:
   - **URL**: `http://<atlas-host>:5100/v1`
   - **Model**: `atlas-cortex`
4. Start chatting — Atlas handles the rest

### Admin Panel Setup

The admin panel is a Vue 3 SPA served directly from the Atlas Cortex server.

```bash
# Build the admin panel (requires Node.js 18+)
cd admin
npm install
npx vite build
cd ..

# Start the server (admin panel is now available)
python -m cortex.server
```

Open **`http://localhost:5100/admin/`** in your browser.

| | |
|---|---|
| **Default username** | `admin` |
| **Default password** | `atlas-admin` |

> ⚠️ **Change the default password immediately** via the admin panel (click your username → Change Password).

#### What you can do in the admin panel:

- **Dashboard** — view interaction counts, safety events, layer distribution, recent activity
- **Users** — edit profiles, set ages (birth year/month), manage vocabulary and tone preferences
- **Parental Controls** — set content filter levels, allowed hours, restricted topics per child
- **Safety** — browse guardrail events with filters, manage jailbreak detection patterns
- **Voice** — view enrolled speakers, adjust confidence thresholds, remove enrollments
- **Devices** — browse Home Assistant devices and command patterns, manage aliases
- **Evolution** — monitor rapport scores, review emotional profiles, view nightly evolution logs
- **System** — check hardware/GPU status, model configs, discovered services, backup history

#### Development mode (hot reload):

```bash
# Terminal 1: Start the API server
python -m cortex.server

# Terminal 2: Start the Vite dev server with hot reload
cd admin
npm run dev
# Dev server runs at http://localhost:5173/admin/
# API calls are proxied to http://localhost:5100
```

### Discover Your Services

```bash
# Scan your network for Home Assistant, Nextcloud, MQTT, etc.
python -m cortex.discover
```

Atlas finds available services on your network and configures integrations automatically.

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CORTEX_HOST` | `0.0.0.0` | Server bind address |
| `CORTEX_PORT` | `5100` | Server port |
| `CORTEX_DATA_DIR` | `./data` | Database and state directory |
| `LLM_PROVIDER` | `ollama` | LLM backend (`ollama`, `openai_compatible`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `OPENAI_BASE_URL` | — | Custom OpenAI-compatible endpoint |
| `OPENAI_API_KEY` | — | API key for OpenAI-compatible backends |
| `MODEL_FAST` | `qwen2.5:14b` | Model for quick factual answers |
| `MODEL_THINKING` | `qwen3:30b-a3b` | Model for complex reasoning |
| `HA_URL` | — | Home Assistant URL (e.g., `http://192.168.1.100:8123`) |
| `HA_TOKEN` | — | Home Assistant long-lived access token |
| `CORTEX_JWT_SECRET` | `atlas-cortex-change-me` | Secret key for admin JWT tokens (change in production!) |
| `CORTEX_JWT_EXPIRY` | `86400` | Admin session duration in seconds (default: 24 hours) |

## 📡 API Reference

Atlas exposes an **OpenAI-compatible API** so any client that works with OpenAI/Ollama works with Atlas.

### Chat Completions

```bash
# Streaming
curl http://localhost:5100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "atlas-cortex",
    "messages": [{"role": "user", "content": "Turn off the living room lights"}],
    "stream": true
  }'
```

### Text-to-Speech

```bash
# Generate speech with emotion
curl http://localhost:5100/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "orpheus",
    "input": "Good morning! The weather looks beautiful today.",
    "voice": "tara",
    "emotion": "happy"
  }' --output speech.wav

# List available voices
curl http://localhost:5100/v1/audio/voices
```

### Health Check

```bash
curl http://localhost:5100/health
```

### Admin API

All admin endpoints require a JWT token obtained via login.

```bash
# Login and get token
TOKEN=$(curl -s http://localhost:5100/admin/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "atlas-admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# Dashboard stats
curl http://localhost:5100/admin/dashboard \
  -H "Authorization: Bearer $TOKEN"

# List users
curl http://localhost:5100/admin/users \
  -H "Authorization: Bearer $TOKEN"

# Set a user's age
curl -X POST http://localhost:5100/admin/users/derek/age \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"birth_year": 1990, "birth_month": 6}'

# View safety events
curl "http://localhost:5100/admin/safety/events?page=1&per_page=20" \
  -H "Authorization: Bearer $TOKEN"

# Change admin password
curl -X POST http://localhost:5100/admin/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "atlas-admin", "new_password": "my-new-secure-password"}'
```

<details>
<summary>Full Admin Endpoint List</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/admin/auth/login` | Authenticate and get JWT token |
| GET | `/admin/auth/me` | Get current admin info |
| POST | `/admin/auth/change-password` | Change admin password |
| GET | `/admin/dashboard` | Aggregate stats and recent activity |
| GET | `/admin/users` | List user profiles (paginated) |
| GET | `/admin/users/:id` | Get user detail with emotional profile, topics |
| PATCH | `/admin/users/:id` | Update user profile fields |
| POST | `/admin/users/:id/age` | Set user age (birth_year, birth_month) |
| DELETE | `/admin/users/:id` | Delete a user |
| GET | `/admin/users/:id/parental` | Get parental controls for a child |
| POST | `/admin/users/:id/parental` | Set parental controls |
| DELETE | `/admin/users/:id/parental` | Remove parental controls |
| GET | `/admin/safety/events` | List guardrail events (filterable, paginated) |
| GET | `/admin/safety/patterns` | List jailbreak detection patterns |
| POST | `/admin/safety/patterns` | Add a jailbreak pattern |
| DELETE | `/admin/safety/patterns/:id` | Delete a jailbreak pattern |
| GET | `/admin/voice/speakers` | List enrolled speakers |
| PATCH | `/admin/voice/speakers/:id` | Update speaker (name, threshold) |
| DELETE | `/admin/voice/speakers/:id` | Remove speaker enrollment |
| GET | `/admin/devices` | List HA devices with aliases (paginated) |
| GET | `/admin/devices/patterns` | List command patterns (paginated) |
| PATCH | `/admin/devices/patterns/:id` | Update a command pattern |
| DELETE | `/admin/devices/patterns/:id` | Delete a command pattern |
| GET | `/admin/evolution/profiles` | List emotional profiles with top topics |
| GET | `/admin/evolution/logs` | List nightly evolution run logs |
| GET | `/admin/evolution/mistakes` | List mistake log (filterable) |
| PATCH | `/admin/evolution/mistakes/:id` | Mark a mistake as resolved |
| GET | `/admin/system/hardware` | Hardware profile and GPU info |
| GET | `/admin/system/models` | Model configuration |
| GET | `/admin/system/services` | Discovered services |
| GET | `/admin/system/backups` | Backup history |
| GET | `/admin/system/interactions` | Browse interactions (filterable, paginated) |

</details>

## 📁 Project Structure

```
atlas-cortex/
├── admin/                         # Vue 3 Admin Panel (SPA)
│   ├── src/
│   │   ├── views/                 #   10 admin views (dashboard, users, safety, etc.)
│   │   ├── components/            #   Reusable components (NavBar, DataTable, etc.)
│   │   ├── router/                #   Vue Router with auth guards
│   │   ├── stores/                #   Pinia auth store
│   │   └── api.js                 #   API client with JWT handling
│   ├── package.json
│   └── vite.config.js
├── cortex/                        # Core Python package
│   ├── server.py                  # OpenAI-compatible FastAPI server
│   ├── auth.py                    # JWT authentication (bcrypt + PyJWT)
│   ├── admin_api.py               # Admin REST API (30+ endpoints)
│   ├── pipe.py                    # Open WebUI Pipe function
│   ├── db.py                      # SQLite schema (50+ tables, WAL mode)
│   ├── pipeline/                  # 4-layer processing pipeline
│   │   ├── layer0_context.py      #   Context assembly, sentiment, spatial
│   │   ├── layer1_instant.py      #   Instant answers (math, date, identity)
│   │   ├── layer2_plugins.py      #   Plugin dispatch (HA, lists, knowledge)
│   │   └── layer3_llm.py          #   Filler streaming + LLM generation
│   ├── providers/                 # LLM backend abstraction
│   │   ├── ollama.py              #   Ollama provider
│   │   └── openai_compat.py       #   Any OpenAI-compatible backend
│   ├── voice/                     # Voice & TTS engine
│   │   ├── providers/orpheus.py   #   Orpheus emotional TTS
│   │   ├── providers/piper.py     #   Piper CPU fallback
│   │   ├── composer.py            #   Emotion composition
│   │   ├── streaming.py           #   Sentence-boundary streaming
│   │   ├── spatial.py             #   Room resolution & spatial awareness
│   │   ├── multiroom.py           #   Multi-room command expansion
│   │   ├── wyoming.py             #   Wyoming protocol STT/TTS client
│   │   └── registry.py            #   Voice registry & selection
│   ├── safety/                    # Safety guardrails
│   │   ├── __init__.py            #   Content tiers, input/output guards
│   │   └── jailbreak.py           #   5-layer jailbreak defense
│   ├── plugins/                   # Plugin framework
│   ├── integrations/              # Part 2 integrations
│   │   ├── ha/                    #   Home Assistant (client, bootstrap, websocket)
│   │   ├── knowledge/             #   WebDAV, CalDAV, sync scheduler
│   │   ├── lists/                 #   Smart lists (multi-backend, HA discovery)
│   │   └── learning/              #   Nightly self-learning engine
│   ├── memory/                    # HOT/COLD memory architecture
│   ├── profiles/                  # User profiles & age-awareness
│   ├── context/                   # Context window management
│   ├── filler/                    # Sentiment-aware filler streaming
│   ├── grounding/                 # Anti-hallucination engine
│   ├── backup/                    # Automated backup/restore
│   │   ├── __init__.py            #   Nightly backups, one-command restore
│   │   ├── offsite.py             #   NAS rsync/SMB offsite sync
│   │   └── voice_commands.py      #   Voice backup commands
│   ├── install/                   # Hardware detection & installer
│   └── discovery/                 # Network service discovery
├── docker/
│   ├── Dockerfile                 # Production container
│   └── docker-compose.yml         # Full stack deployment
├── docs/                          # Design documentation (20+ files)
├── seeds/
│   └── command_patterns.sql       # Initial HA command patterns
├── tests/                         # 554 tests
├── requirements.txt
└── pytest.ini
```

## 🧪 Testing

```bash
# Run all tests
source .venv/bin/activate
python -m pytest tests/ -q

# Run specific module
python -m pytest tests/test_pipeline.py -v
python -m pytest tests/test_safety.py -v
python -m pytest tests/test_voice.py -v
```

**Current status: 554 tests passing** across pipeline, providers, safety, voice, discovery, integrations, filler, memory, learning, evolution, avatar, admin, spatial, Wyoming, WebDAV, CalDAV, backup, and multi-room modules.

## 📊 Implementation Status

### Part 1: Core Engine

| Phase | Module | Status | Description |
|-------|--------|--------|-------------|
| C0 | Installer & Backend | ✅ Complete | LLM provider abstraction, GPU detection, CLI installer |
| C1 | Core Pipeline | ✅ Complete | 4-layer pipeline, sentiment, instant answers, filler streaming |
| C3a | Voice Identity | ✅ Complete | Speaker recognition, enrollment, hybrid age estimation |
| C4 | Emotional Evolution | ✅ Complete | Rapport tracking, personality drift, proactive suggestions |
| C5 | Memory System | ✅ Complete | HOT/COLD paths, vector search, BM25, RRF fusion |
| C6 | User Profiles | ✅ Complete | Age-awareness, onboarding, parental controls |
| C7 | Avatar System | ✅ Complete | Phoneme-to-viseme lip-sync, emotion expressions |
| C9 | Backup & Restore | ✅ Complete | Automated nightly backups, one-command restore |
| C10 | Context Management | ✅ Complete | Context windows, compaction, overflow recovery |
| C11 | Voice & Speech | ✅ Complete | TTS providers (Orpheus + Piper), emotion, streaming |
| C12 | Safety Guardrails | ✅ Complete | Content tiers, jailbreak defense, PII redaction |
| — | Admin Web Panel | ✅ Complete | Vue 3 dashboard, JWT auth, 30+ REST endpoints |

### Part 2: Integration Layer

| Phase | Module | Status | Description |
|-------|--------|--------|-------------|
| I1 | Service Discovery | ✅ Complete | HTTP-probe scanner, service registry, config wizard |
| I2 | Home Assistant | ✅ Complete | REST + WebSocket client, device bootstrap, patterns |
| I3 | Voice Pipeline | ✅ Complete | Wyoming STT/TTS, spatial awareness, multi-room commands |
| I4 | Self-Learning | ✅ Complete | Fallthrough analysis, pattern lifecycle, nightly evolution |
| I5 | Knowledge Sources | ✅ Complete | WebDAV/Nextcloud, CalDAV calendars, sync scheduler |
| I6 | List Management | ✅ Complete | Multi-backend lists, HA to-do discovery, permissions |
| I7 | Offsite Backup | ✅ Complete | NAS rsync/SMB, voice commands, retention policy |

### Coming Next

| Part | Name | Description |
|------|------|-------------|
| 2.5 | **Satellite System** | Distributed speakers/mics — ESP32-S3, RPi, any Linux device |
| 3 | **Alarms, Timers & Reminders** | Wake alarms, cooking timers, location-aware reminders |
| 4 | **Routines & Automations** | "Good morning"/"Good night" routines, custom triggers |
| 5 | **Proactive Intelligence** | Weather actions, energy optimization, anomaly detection |
| 6 | **Learning & Education** | Homework help, quizzes, science mode, language learning |
| 7 | **Intercom & Broadcasting** | Room-to-room, whole-house announcements, emergency alerts |
| 8 | **Media & Entertainment** | Local files, YouTube Music, Spotify, multi-room audio |

> 📋 **Full roadmap with detailed plans:** [docs/roadmap.md](docs/roadmap.md)

## 📖 Documentation

Comprehensive design documentation lives in the [`docs/`](docs/) directory:

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, pipeline layers, evolution engine |
| [Data Model](docs/data-model.md) | 50+ SQLite tables, normalized schema, relationships |
| [Voice Engine](docs/voice-engine.md) | TTS providers, emotion composition, streaming |
| [Safety Guardrails](docs/safety-guardrails.md) | Content tiers, jailbreak defense, crisis protocol |
| [Context Management](docs/context-management.md) | Context windows, compaction, multi-GPU detection |
| [Memory System](docs/memory-system.md) | HOT/COLD architecture, vector search, RRF fusion |
| [User Profiles](docs/user-profiles.md) | Age-awareness, onboarding, parental controls |
| [Personality](docs/personality.md) | Honesty system, pushback, tutoring mode |
| [Grounding](docs/grounding.md) | Anti-hallucination, confidence scoring |
| [Knowledge Access](docs/knowledge-access.md) | Document indexing, privacy gates |
| [Lists](docs/lists.md) | Multi-backend lists, permissions |
| [Avatar System](docs/avatar-system.md) | Lip-sync, visemes, emotion expressions |
| [Backup & Restore](docs/backup-restore.md) | Automated backups, one-command restore |
| [Satellite System](docs/satellite-system.md) | Satellite speaker/mic architecture |
| [Roadmap](docs/roadmap.md) | Future features and implementation plan |
| [Installation](docs/installation.md) | Installer flow, backend abstraction |
| [Phases](docs/phases.md) | Implementation roadmap and dependency graph |

## 🤝 Contributing

Atlas Cortex is open source and welcomes contributions!

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Make your changes** and add tests
4. **Run the test suite**: `python -m pytest tests/ -q`
5. **Submit a Pull Request**

### Development Setup

```bash
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q  # verify everything works
```

## 📄 License

[MIT](LICENSE) — use it, modify it, build on it.
