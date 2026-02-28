<div align="center">

# 🧠 Atlas Cortex

**A self-evolving AI assistant that learns, adapts, and grows with your household.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-285%20passing-brightgreen.svg)](#testing)
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

## 🏗️ Architecture

```
                          ┌─────────────────────┐
                          │    User Interface    │
                          │  Open WebUI / Voice  │
                          │  / Satellite Mics    │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │    Atlas Cortex      │
                          │    Server (:5100)    │
                          │  OpenAI-compatible   │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼─────────┐ ┌─────────▼─────────┐ ┌─────────▼─────────┐
    │   Input Pipeline   │ │  Safety Guardrails │ │   Voice Engine    │
    │                    │ │                    │ │                   │
    │ L0: Context  (1ms) │ │ • Content tiers    │ │ • Orpheus TTS     │
    │ L1: Instant  (5ms) │ │ • Jailbreak defense│ │ • Piper fallback  │
    │ L2: Plugins (100ms)│ │ • PII redaction    │ │ • Emotion tags    │
    │ L3: LLM   (500ms+)│ │ • Crisis detection │ │ • Voice streaming │
    └─────────┬─────────┘ └────────────────────┘ └───────────────────┘
              │
    ┌─────────▼─────────────────────────────────────────────┐
    │                    Integrations                        │
    │                                                       │
    │  🏠 Home Assistant   📋 Lists   📚 Knowledge   🔍 Memory  │
    │  🔧 Service Discovery   📦 Backup   🎓 Learning        │
    └───────────────────────────┬───────────────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │  SQLite + ChromaDB   │
                     │  (WAL mode, 50+ tbl) │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │   Nightly Evolution  │
                     │  Pattern learning    │
                     │  Profile evolution   │
                     │  Device discovery    │
                     └─────────────────────┘
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

## 📁 Project Structure

```
atlas-cortex/
├── cortex/                        # Core Python package
│   ├── server.py                  # OpenAI-compatible FastAPI server
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
│   │   └── registry.py            #   Voice registry & selection
│   ├── safety/                    # Safety guardrails
│   │   ├── __init__.py            #   Content tiers, input/output guards
│   │   └── jailbreak.py           #   5-layer jailbreak defense
│   ├── plugins/                   # Plugin framework
│   ├── integrations/              # Part 2 integrations
│   │   ├── ha/                    #   Home Assistant (client, bootstrap, plugin)
│   │   ├── knowledge/             #   Document indexing & search
│   │   ├── lists/                 #   Smart lists (multi-backend)
│   │   └── learning/              #   Nightly self-learning engine
│   ├── memory/                    # HOT/COLD memory architecture
│   ├── profiles/                  # User profiles & age-awareness
│   ├── context/                   # Context window management
│   ├── filler/                    # Sentiment-aware filler streaming
│   ├── grounding/                 # Anti-hallucination engine
│   ├── backup/                    # Automated backup/restore
│   ├── install/                   # Hardware detection & installer
│   └── discovery/                 # Network service discovery
├── docker/
│   ├── Dockerfile                 # Production container
│   └── docker-compose.yml         # Full stack deployment
├── docs/                          # Design documentation (17 files)
├── seeds/
│   └── command_patterns.sql       # Initial HA command patterns
├── tests/                         # 285 tests
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

**Current status: 285 tests passing** across pipeline, providers, safety, voice, discovery, integrations, filler, memory, and learning modules.

## 📊 Implementation Status

### Part 1: Core Engine

| Phase | Module | Status | Description |
|-------|--------|--------|-------------|
| C0 | Installer & Backend | ✅ Complete | LLM provider abstraction, GPU detection, CLI installer |
| C1 | Core Pipeline | ✅ Complete | 4-layer pipeline, sentiment, instant answers, filler streaming |
| C3a | Voice Identity | 🔲 Planned | Speaker recognition, enrollment, age estimation |
| C4 | Emotional Evolution | 🔲 Planned | Rapport tracking, personality drift, proactive suggestions |
| C5 | Memory System | 🔲 Planned | HOT/COLD paths, vector search, BM25, RRF fusion |
| C6 | User Profiles | 🔲 Planned | Age-awareness, onboarding, parental controls |
| C7 | Avatar System | 🔲 Planned | Phoneme-to-viseme lip-sync, emotion expressions |
| C9 | Backup & Restore | ✅ Complete | Automated nightly backups, one-command restore |
| C10 | Context & Hardware | 🔲 Planned | Context windows, compaction, overflow recovery |
| C11 | Voice & Speech | ✅ Complete | TTS providers (Orpheus + Piper), emotion, streaming |
| C12 | Safety Guardrails | ✅ Complete | Content tiers, jailbreak defense, PII redaction |

### Part 2: Integration Layer

| Phase | Module | Status | Description |
|-------|--------|--------|-------------|
| I1 | Service Discovery | ✅ Complete | HTTP-probe scanner, service registry, config wizard |
| I2 | Home Assistant | ✅ Complete | REST client, device bootstrap, pattern matching |
| I3 | Voice Pipeline | 🔲 Planned | Wyoming integration, room awareness, multi-mic |
| I4 | Self-Learning | ✅ Complete | Fallthrough analysis, pattern lifecycle, nightly evolution |
| I5 | Knowledge Sources | ✅ Complete | Document processor, FTS5 index, privacy gates |
| I6 | List Management | ✅ Complete | Multi-backend lists, permissions, natural language |
| I7 | Offsite Backup | 🔲 Planned | NAS sync for disaster recovery |

## 🗺️ Roadmap — Future Features

These are planned enhancements that build on the existing architecture:

### ⏰ Alarms, Timers & Reminders
- *"Wake me up at 7am"* — alarm management via Home Assistant media players
- *"Set a timer for 15 minutes"* — cooking timers with voice notifications
- *"Remind me to take medicine at 3pm"* — recurring reminders with snooze
- *"Remind me when I get home to check the mail"* — location-aware triggers

### 🌅 Routines & Automations
- *"Good morning"* — triggers wake-up routine: lights on, coffee maker, weather briefing, calendar summary
- *"Good night"* — locks doors, turns off lights, sets alarm, plays sleep sounds
- *"I'm leaving"* — arms security, adjusts thermostat, turns off non-essential devices
- Custom routines built conversationally: *"When I say 'movie time', dim the living room to 20% and turn on the TV"*

### 📅 Calendar & Scheduling
- Reads from CalDAV/Google/Outlook calendars
- *"What's on my schedule today?"* — morning briefing
- *"Schedule a dentist appointment for next Thursday at 2pm"*
- Proactive reminders: *"You have a meeting in 15 minutes"*

### 🎵 Media & Entertainment
- *"Play jazz in the living room"* — multi-room audio via HA media players
- *"What song is this?"* — audio recognition
- *"Recommend a movie for family night"* — preference-aware suggestions

### 🌤️ Proactive Intelligence
- Weather-aware actions: *"It's going to rain — should I close the garage?"*
- Energy optimization: *"You've left the AC on for 8 hours — the house is at 68°F"*
- Anomaly detection: *"The basement humidity is unusually high"*
- Package tracking: *"Your Amazon order arrives tomorrow between 2-6pm"*

### 📚 Learning & Education
- Homework help with age-appropriate explanations
- Interactive quizzes: *"Quiz me on state capitals"*
- Science experiments: *"What happens if we mix baking soda and vinegar?"*
- Language learning: vocabulary drills, pronunciation practice

### 🐾 Household Management
- Pet care reminders: feeding schedules, vet appointments, medication
- Cooking assistant: step-by-step recipes with integrated timers
- Inventory tracking: *"We're running low on milk"* → auto-add to grocery list
- Chore assignments: fair rotation tracking for household members

### 🔒 Security & Monitoring
- Camera feed summaries: *"Who was at the front door?"*
- Motion alert intelligence: distinguishes pets, packages, people
- Door/window status: *"Is the garage door open?"*
- Visitor history: *"When did the kids get home from school?"*

### 🌐 Multi-Language Support
- Real-time language detection and switching
- Per-user language preferences
- Translation assistance between household members

### 📢 Intercom & Broadcasting
- *"Tell the kids dinner is ready"* — broadcast to specific rooms
- *"Announce: family meeting in 5 minutes"* — whole-house broadcast
- Room-to-room communication via satellite speakers

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
