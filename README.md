# Atlas Cortex

A self-evolving AI assistant system built on top of [Open WebUI](https://github.com/open-webui/open-webui) and [Ollama](https://ollama.com). Atlas Cortex transforms a standard LLM chat into a **self-learning, personality-evolving home AI** that gets faster, smarter, and more human over time.

## What It Does

- **Instant answers** (~5ms) — date/time, math, identity questions with zero LLM overhead
- **Direct smart home control** (~100ms) — executes Home Assistant commands via API, no LLM round trip
- **Self-learning patterns** — commands that fall through to the LLM are analyzed nightly and converted into fast regex patterns
- **Sentiment-aware filler streaming** — starts responding immediately with contextual phrases while the LLM generates the real answer
- **Spatial awareness** — knows which room you're in via satellite mics, presence sensors, and speaker identity to scope commands automatically
- **Voice identification** — recognizes household members by voice and personalizes responses
- **Emotional evolution** — builds unique personality traits per user relationship over time
- **Age-appropriate responses** — adapts vocabulary, tone, and content filtering for toddlers, children, teens, and adults
- **Persistent memory** — HOT/COLD architecture with vector search, BM25, and RRF fusion for instant context recall
- **Conversational onboarding** — learns about users naturally through conversation, never overwrites, always builds upon
- **Animated avatar** — lip-synced face on satellite displays using phoneme-to-viseme mapping, emotion-driven expressions
- **Honest personality** — pushes back on bad ideas, challenges users in tutoring mode, never sycophantic
- **Anti-hallucination** — internal confidence scoring, grounding loops, mistake tracking and learning
- **Personal knowledge access** — indexes files, email, messages, calendar with strict user-scoped privacy
- **Smart lists** — grocery, to-do, shopping lists across multiple backends with per-list permissions

## Architecture

```
User Message (typed or voice)
         │
         ▼
┌─────────────────────────────────────┐
│         Atlas Cortex Pipe           │
│                                     │
│  Layer 0: Context    (~1ms)         │
│    User ID, sentiment, time-of-day  │
│                                     │
│  Layer 1: Instant    (~5ms)         │
│    Date, math, greetings, identity  │
│                                     │
│  Layer 2: Plugins    (~100ms)       │
│    Discovered integrations:         │
│    HA, lists, knowledge, etc.       │
│                                     │
│  Layer 3: LLM        (~500-4000ms)  │
│    Filler stream → Ollama API       │
│    Auto-selects model by complexity │
│                                     │
│  Logger: every interaction saved    │
└─────────────────────────────────────┘
         │
         ▼ (nightly)
┌─────────────────────────────────────┐
│       Evolution Engine              │
│  • Discover new devices/services    │
│  • Learn patterns from fallthrough  │
│  • Evolve emotional profiles        │
│  • Optimize pattern database        │
└─────────────────────────────────────┘
```

## Hardware Target (auto-detected)

Atlas auto-detects hardware at install. Works on any system with Ollama:
- **GPU**: AMD (ROCm), NVIDIA (CUDA), Intel (oneAPI), Apple (Metal), or CPU-only
- **Models**: Auto-selected based on VRAM — from 1.7B (4GB GPU) to 72B (48GB+)
- See [docs/context-management.md](docs/context-management.md) for hardware detection details

## Project Structure

```
atlas-cortex/
├── README.md                  # This file
├── docs/
│   ├── architecture.md        # Detailed system architecture
│   ├── data-model.md          # Database schema (v2, normalized)
│   ├── backup-restore.md      # Automated backups, one-command restore
│   ├── context-management.md  # Context windows, compaction, hardware auto-detect
│   ├── installation.md        # Installer, backend abstraction, voice routing
│   ├── memory-system.md       # HOT/COLD memory with vector search
│   ├── user-profiles.md       # Age-awareness, onboarding, profile evolution
│   ├── personality.md         # Honesty system, pushback, tutoring mode
│   ├── grounding.md           # Anti-hallucination, confidence scoring, mistake learning
│   ├── knowledge-access.md    # File/email/message indexing, user-scoped privacy
│   ├── lists.md               # Multi-backend lists, permissions, resolution
│   ├── avatar-system.md       # Lip-sync avatars, visemes, multi-skin
│   ├── voice-engine.md        # TTS providers, emotional speech, Orpheus, voice selection
│   ├── phases.md              # Implementation phases and dependencies
│   └── infrastructure.md      # Reference server topology (Derek's setup)
├── cortex/                    # Python package (future)
│   ├── server.py              # Standalone OpenAI-compatible server (:5100)
│   ├── pipe.py                # Open WebUI Pipe function (optional)
│   ├── providers/             # LLM backend providers (Ollama, OpenAI-compat)
│   ├── pipeline/              # Processing layers (0-3)
│   ├── plugins/               # Integration plugins (Part 2)
│   ├── memory/                # HOT/COLD memory engine
│   ├── profiles/              # User profiles, age, parental
│   ├── context/               # Context compaction, checkpoints
│   ├── filler/                # Sentiment + confidence fillers
│   ├── grounding/             # Confidence scoring, grounding loop
│   ├── backup/                # Backup/restore CLI
│   └── install/               # Installer + discovery
├── seeds/
│   └── command_patterns.sql   # Initial HA command patterns (for I2)
└── tests/
```

## Implementation Phases

### Part 1: Core Engine (portable, no infrastructure needed)

| Phase | Name | Status | Description |
|-------|------|--------|-------------|
| C0 | Installer & Backend | 🔲 Planned | LLM provider abstraction, hardware detection, CLI installer |
| C1 | Core Pipe & Logging | 🔲 Planned | Sentiment, instant answers, plugin registry, filler streaming |
| C3a | Voice Identity | 🔲 Planned | Speaker recognition, enrollment, age estimation |
| C4 | Emotional Evolution | 🔲 Planned | Rapport tracking, personality drift, proactive suggestions |
| C5 | Memory System | 🔲 Planned | HOT/COLD paths, vector search, BM25, RRF fusion, ChromaDB |
| C6 | User Profiles | 🔲 Planned | Age-awareness, onboarding, parental controls, profile evolution |
| C7 | Avatar System | 🔲 Future | Phoneme-to-viseme lip-sync, emotion expressions, multi-skin |
| C9 | Backup & Restore | 🔲 Planned | Automated backups, one-command restore, voice commands |
| C10 | Context & Hardware | 🔲 Planned | Context windows, compaction, overflow recovery, hardware auto-detect |
| C11 | Voice & Speech | 🔲 Planned | TTS provider abstraction, Orpheus emotional speech, voice selection |

### Part 2: Integration Layer (discovered at install)

| Phase | Name | Status | Description |
|-------|------|--------|-------------|
| I1 | Service Discovery | ✅ Complete | HTTP-probe scanner, service registry, config wizard, `python -m cortex.discover` |
| I2 | Home Assistant | ✅ Complete | HA REST client, device bootstrap, pattern generation, HAPlugin, graceful offline fallback |
| I3 | Voice Pipeline & Spatial | 🔲 Future | HA Wyoming integration, room awareness, multi-mic proximity (requires live HA + hardware) |
| I4 | Self-Learning | ✅ Complete | Fallthrough analyzer, pattern lifecycle (prune/boost), nightly evolution orchestrator |
| I5 | Knowledge Sources | ✅ Complete | Document processor, FTS5 index, privacy/access gate, KnowledgePlugin |
| I6 | List Management | ✅ Complete | SQLite backend, list registry, permissions, ListPlugin with add/get/remove intents |
| I7 | Offsite Backup | 🔲 Future | NAS sync for disaster recovery (requires discovered NAS) |

See [docs/phases.md](docs/phases.md) for detailed task breakdown and dependency graph.

## Prerequisites

**Part 1 (Core Engine):**
- [Ollama](https://ollama.com) (any GPU or CPU-only)
- [Open WebUI](https://github.com/open-webui/open-webui) v0.8.5+
- Python 3.11+

**Part 2 (Integration — all optional, discovered at install):**
- [Home Assistant](https://www.home-assistant.io/) with long-lived access token
- Nextcloud, CalDAV, IMAP, NAS shares — whatever you have

## Backup & Restore

Atlas backs itself up nightly (7 daily, 4 weekly, 12 monthly retention). One-command restore:

```bash
python -m cortex.backup restore --latest daily    # restore from latest daily
python -m cortex.backup restore path/to/backup.tar.gz  # restore specific backup
```

Or just ask: *"Atlas, back yourself up"* / *"Atlas, restore from yesterday"*

See [docs/backup-restore.md](docs/backup-restore.md) for full details.

## License

MIT
