# Atlas Cortex — Implementation Phases

## Part 1 vs Part 2

Atlas Cortex is split into multiple parts so that the core engine is **portable and reusable** by anyone, while extended features adapt to available infrastructure.

| | Part 1: Core Engine | Part 2: Integration | Part 2.5: Satellites | Parts 3–8: Extended |
|---|---|---|---|---|
| **What** | The brain — personality, memory, context, avatar, safety | Connects to real world (HA, files, network) | Distributed speakers/mics in every room | Alarms, routines, media, education, intercom |
| **Requires** | Any LLM backend + Python | Discovered services (HA, etc.) | Satellite hardware (Pi, ESP32) | Satellites + integrations |
| **Portable?** | Yes — any machine | Adapts to found services | Hardware-agnostic | Builds on Parts 1–2.5 |

### How It Works for Others

When someone installs Atlas Cortex on their own system:

1. **Installer runs** — detects hardware, finds existing LLM backends (or offers to install one)
2. **Selects models** — recommends best models for detected GPU/RAM, pulls them
3. **Core starts** — Atlas Cortex server (:5100) + optional Open WebUI Pipe function
4. **Service discovery** — scans network for HA, Nextcloud, CalDAV, IMAP, NAS, etc.
5. **User configures** — confirms services, provides credentials (CLI or via conversation with Atlas)
6. **Plugins activate** — integrations register into Layer 2
7. **LLM-assisted refinement** — once running, Atlas helps configure the rest conversationally

See [installation.md](installation.md) for the full installer design.

---

## Phase Overview

### Part 1: Core Engine (no infrastructure knowledge needed)

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| C0 | Installer & Backend Abstraction | 🔲 Planned | None |
| C1 | Core Pipe & Logging | 🔲 Planned | C0 |
| C3a | Voice Identity (generic) | 🔲 Planned | None |
| C4 | Emotional Evolution | 🔲 Planned | C3a + C5 + C6 |
| C5 | Memory System (HOT/COLD) | 🔲 Planned | None |
| C6 | User Profiles & Age-Awareness | 🔲 Planned | C3a + C5 |
| C7 | Avatar System | 🔲 Planned | None |
| C9 | Backup & Restore | 🔲 Planned | None |
| C10 | Context Management & Hardware | 🔲 Planned | C0 |
| C11 | Voice & Speech Engine | 🔲 Planned | C0 |
| C12 | Safety Guardrails & Content Policy | 🔲 Planned | C6 |

### Part 2: Integration Layer (discovered at install)

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| I1 | Service Discovery & Setup | 🔲 Planned | Part 1 C1 operational |
| I2 | Home Assistant Integration | 🔲 Planned | I1 + HA discovered |
| I3 | Voice Pipeline & Spatial | 🔲 Planned | I1 + I2 + C3a |
| I4 | Self-Learning Engine | 🔲 Planned | I2 + C1 logging |
| I5 | Knowledge Source Connectors | 🔲 Planned | I1 + C5 memory + C6 profiles |
| I6 | List Management | 🔲 Planned | I1 + I5 |
| I7 | Offsite Backup | 🔲 Planned | I1 + C9 |

### Part 2.5: Satellite System

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| S2.5 | Satellite Speaker/Mic System | 🔲 Planned | C11 (TTS) + C3a (Voice ID) |

### Part 3–8: Extended Features

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P3 | Alarms, Timers & Reminders | 🔲 Planned | S2.5 + I2 |
| P4 | Routines & Automations | 🔲 Planned | I2 + P3 |
| P5 | Proactive Intelligence | 🔲 Planned | I2 + S2.5 + C5 |
| P6 | Learning & Education | 🔲 Planned | C6 + C12 |
| P7 | Intercom & Broadcasting | 🔲 Planned | S2.5 |
| P8 | Media & Entertainment | 🔲 Planned | S2.5 + I2 |

---

# Part 1: Core Engine

Everything below works with any LLM backend. No Home Assistant, no specific servers, no network knowledge.

## Phase C0: Installer & Backend Abstraction

See [installation.md](installation.md) for full design.

### C0.1 — LLM Provider Interface
- Abstract `LLMProvider` class: `chat()`, `embed()`, `list_models()`, `health()`
- `OllamaProvider` — talks to Ollama's `/api/chat`, `/api/embeddings`
- `OpenAICompatibleProvider` — works with vLLM, LocalAI, LM Studio, llama.cpp, etc.
- Provider selected at install time, configurable in `cortex.env`

### C0.2 — Embedding Provider Interface
- Separate from LLM provider (can be different backends)
- Options: Ollama, OpenAI-compatible, sentence-transformers (in-process), fastembed
- Fallback: if LLM provider has no embedding support, use in-process sentence-transformers

### C0.3 — Hardware Detection & GPU Assignment
- GPU detection (AMD/NVIDIA/Intel/Apple/CPU-only)
- Multi-GPU discovery: enumerate all discrete GPUs, rank by VRAM
- GPU role assignment: largest → LLM, second → voice (TTS/STT), third+ → overflow
- Mixed-vendor support: generate per-GPU isolation env vars (HIP/CUDA/oneAPI)
- VRAM/RAM budgets, context window limits
- Model recommendations based on hardware tier
- Store per-GPU profiles in `hardware_gpu` table
- Already designed in C10.1 — shared implementation

### C0.4 — LLM Backend Discovery
- Probe localhost + local network for running LLM backends
- Support: Ollama, LM Studio, vLLM, LocalAI, llama.cpp, koboldcpp, text-gen-webui
- Offer to install if nothing found (default: Ollama)
- Validate connectivity before saving

### C0.5 — Chat UI Detection & Integration
- Detect Open WebUI → offer Pipe function mode
- Always start standalone server (:5100) with OpenAI-compatible API
- This makes Atlas work with ANY client that supports OpenAI API
- HA conversation agent can point to Atlas server directly

### C0.6 — CLI Installer
- `python -m cortex.install` — interactive CLI wizard
- Two-stage: deterministic setup first (no LLM), then LLM-assisted refinement
- Generates `cortex.env` with all configuration
- Creates database, pulls models, starts server
- Offers to run Part 2 discovery immediately or later

---

## Phase C1: Core Pipe & Logging

The foundational pipe function — an intelligent router that processes every message through layered analysis.

### C1.1 — Core Cortex Pipe Function
Create the Open WebUI Pipe function:
- VADER sentiment analysis (installed in pipe's `__init__`)
- Layer 0: context assembly (user identification, sentiment, time-of-day)
- Layer 1: instant answers (date, time, math, identity, greetings)
- Layer 2: **plugin-based action layer** — dispatches to registered integration plugins (initially empty; Part 2 adds HA, lists, etc.)
- Layer 3: filler streaming + Ollama API background call
- Auto-select model based on query complexity
- **No hardcoded infrastructure** — Layer 2 is a registry that plugins populate

### C1.2 — Interaction Logging System
- Create cortex.db SQLite database (mounted volume)
- Create all tables from [data-model.md](data-model.md)
- Log every interaction with full metadata
- Flag LLM fallthrough events that triggered plugins (for learning)

### C1.3 — Filler Streaming Engine
- Default filler pools per sentiment category
- Time-of-day aware fillers (morning, afternoon, late night)
- Confidence-aware fillers (see [grounding.md](grounding.md))
- Background thread for Ollama streaming
- Smooth transition: inject filler context into LLM system prompt

### C1.4 — Register Atlas Cortex Model
- Register Cortex as a model in Open WebUI
- Set as default model
- If prior models exist (Turbo/Atlas/Deep), retire them (Cortex replaces all)

### C1.5 — Plugin Registry System
- Layer 2 action registry: plugins register command patterns + handlers
- Plugin lifecycle: discover → configure → activate → health check
- Plugin API: `register_patterns()`, `handle_command()`, `discover_entities()`
- Built-in plugins: none (Part 2 provides HA, lists, etc.)
- Plugin health monitoring: disable unhealthy plugins gracefully

---

## Phase C3a: Voice Identity (Generic)

Speaker recognition — no infrastructure dependencies. Works with any audio source.

### C3a.1 — Speaker ID Sidecar Container
- Docker container with resemblyzer library (CPU-based, ~200MB RAM)
- REST API:
  - `POST /enroll` — audio + user_id → store embedding
  - `POST /identify` — audio → user_id + confidence
- Cosine similarity matching against stored embeddings

### C3a.2 — Voice Enrollment Flow
- Voice command trigger: "Hey Atlas, remember my voice"
- Multi-sample enrollment (3-5 utterances for accuracy)
- Link voice profile to Open WebUI user account
- Average embeddings across samples for robustness

### C3a.3 — Cortex Pipe Integration
- Voice requests include speaker embedding in metadata
- Pipe calls speaker-id sidecar for identification
- Inject identified user context into all processing layers
- Unknown speaker handling: prompt for name, offer enrollment

### C3a.4 — Voice-Based Age Estimation
- Extract pitch, cadence, speech rate from speaker-id audio
- Vocabulary complexity analysis from transcript
- Low-confidence heuristic (used as initial hint only, refined through interaction)
- Never tell a user their estimated age — only use internally for tone

---

## Phase C4: Emotional Evolution

The personality layer that makes Atlas feel human.

### C4.1 — Emotional Profile Engine
- Initialize profile on first interaction
- Track `rapport_score`: +0.01 per positive, -0.02 per frustrated
- Detect communication style from message patterns
- Store time-of-day activity patterns
- Decay rapport by 0.005/day of no interaction

### C4.2 — Nightly Personality Evolution
- LLM reviews day's conversations per user
- Generates updated `relationship_notes`
- Creates new personalized filler phrases matching user's style
- Adjusts `preferred_tone` based on communication patterns
- "Personality drift" — Atlas slowly develops unique traits per relationship

### C4.3 — Contextual Response Personalization
- Morning: "Good morning, Derek. Coffee's probably brewing?"
- Late night: "Still at it? Here's what I found..."
- After absence: "Hey, haven't seen you in a couple days!"
- User frustrated: tone shifts to calm, direct, solution-focused
- User excited: matches energy, uses exclamation marks

### C4.4 — Memory and Proactive Suggestions
- Remember user preferences ("Derek likes lights at 40% in the evening")
- Proactive suggestions ("It's 10 PM — want me to set evening mode?")
- Conversation callbacks ("How'd that Docker fix work out?")

---

## Phase C5: Memory System (HOT/COLD Architecture)

Adapted from [agentic-memory-quest](https://github.com/Betanu701/agentic-memory-quest). See [memory-system.md](memory-system.md) for full design.

### C5.1 — Embedding Model Setup
- Pull `nomic-embed-text` into Ollama (274MB, CPU-friendly)
- Verify embedding API: `POST /api/embeddings` returns 768-dim vectors
- Benchmark: target <10ms per embedding on CPU

### C5.2 — ChromaDB Integration
- Deploy ChromaDB in embedded mode (inside Cortex pipe or sidecar)
- Create `cortex_memory` collection with HNSW index
- Persistent storage on mounted volume
- Metadata schema: user_id, type, source, tags, supersedes, ttl, confidence

### C5.3 — HOT Path (Read)
- Compute query embedding via Ollama
- Sparse search: SQLite FTS5 (BM25 scoring)
- Dense search: ChromaDB vector similarity (cosine)
- RRF Fusion (k=60) to merge ranked lists
- Optional cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`)
- Return top-K (default 8) MemoryHits, sub-50ms target

### C5.4 — COLD Path (Write)
- asyncio.Queue for non-blocking writes
- PII redactor (regex-based: emails, phones, SSN, CC numbers)
- Memory decider: heuristics for keep/drop/dedup (preference, fact, chit-chat)
- Embed via Ollama, upsert to ChromaDB + FTS5 mirror
- Append-only: corrections link to originals, never overwrite
- Content-hash dedup for idempotency

### C5.5 — Memory Integration with Pipe Layers
- Layer 0: HOT query to retrieve user memories on every request
- Layer 1: Memory-powered instant answers ("what's my daughter's name?")
- Layer 2: Memory-powered personalized defaults (via plugins)
- Layer 3: Inject memory context into LLM system prompt
- COLD path fires after every interaction to capture new memories

---

## Phase C6: User Profiles & Age-Awareness

See [user-profiles.md](user-profiles.md) for full design.

### C6.1 — User Profile Engine
- SQLite `user_profiles` table for fast structured queries
- Profile fields: age, age_group, vocabulary_level, preferred_tone, communication_style
- Append-only profile evolution with confidence scoring
- Parent-child relationships (`parent_user_id` foreign key)

### C6.2 — Conversational Onboarding
- First encounter detection (new user_id or unknown voice)
- Natural "meeting someone new" dialogue flow
- Gradual profile building through conversation (not interrogation)
- "We've talked before" handling — search memory, re-link profiles

### C6.3 — Age-Appropriate Response Adaptation
- Response profiles: toddler, child, teen, adult, unknown/neutral
- Vocabulary filtering by age group
- Content safety filtering for children
- Tone adaptation: warm+simple (toddler) → casual+respectful (teen) → personalized (adult)
- System prompt modifier injected based on detected age group

### C6.4 — Parental Controls
- `parental_controls` table: content filter level, allowed devices, allowed hours
- Children can only trigger actions on their allowed list
- Time-based restrictions (e.g., no actions after 9 PM for kids)
- Sensitive commands require parent confirmation

---

## Phase C7: Avatar System (Future)

Visual face for Atlas displayed on screens. See [avatar-system.md](avatar-system.md) for full design.

### C7.1 — Avatar Server Container
- FastAPI + WebSocket server (atlas-avatar, port 8891)
- Receives TTS audio + phoneme timing from any TTS engine
- Receives emotion state from Cortex pipe
- Routes viseme + emotion frames to displays via WebSocket
- Serves the avatar web page (HTML/CSS/JS/SVG)

### C7.2 — Phoneme Extraction
- Integrate with Piper TTS phoneme output or espeak-ng
- Generate timed phoneme sequences from TTS text
- Handle streaming chunks (sentence-boundary splitting)

### C7.3 — Viseme Mapping & Sequencing
- Map ~40 IPA phonemes → 13 viseme mouth shapes (Preston Blair simplified)
- Generate timed viseme sequences synced to audio timestamps
- Smooth transitions between visemes (interpolation, not snapping)

### C7.4 — Browser-Based Avatar Renderer (Tier 2: SVG)
- SVG/Canvas2D face with eyes, mouth, eyebrows
- Mouth morphs between viseme shapes via CSS/JS animation
- Idle behaviors: blinking (3-6s random), breathing bob, eye drift
- Responsive — works on tablets, phones, wall displays

### C7.5 — Emotion Integration
- Drive eye shape, eyebrow position, mouth modifier from sentiment engine
- Emotional transitions blend over 300-500ms (ease-in-out)
- Time-of-day expressions (sleepy at night, bright in morning)
- Background color/mood tinting based on emotional state

### C7.6 — Audio-Viseme Synchronization
- Audio and viseme stream start at same timestamp
- 100ms buffer for network jitter absorption
- Client uses shared clock for playback + animation sync
- Incremental streaming: animate while LLM is still generating

### C7.7 — ASCII Avatar (Tier 1)
- Text-based faces for ESP32 OLED and terminal displays
- Viseme + emotion combinations as ASCII art strings
- MQTT or WebSocket delivery to tiny displays
- Minimal resource usage

### C7.8 — Multi-Skin System
- Skin manifest format (JSON): colors, animation FPS, display requirements
- Skin directory structure: face, eyes, mouths (per viseme), brows (per emotion)
- Built-in skins: Orb (default), Bot, Buddy, Minimal, Classic (ASCII)
- Per-display or per-user skin selection

### C7.9 — ComfyUI Asset Generation (Optional)
- Use ComfyUI to generate consistent avatar art for custom skins
- img2img for viseme × emotion combination sheets
- Store generated assets as skin packs

---

## Phase C9: Backup & Restore

See [backup-restore.md](backup-restore.md) for full design.

### C9.1 — Backup/Restore CLI Tool
- `python -m cortex.backup create` — manual snapshot
- `python -m cortex.backup restore --latest daily` — one-command restore
- SQLite online backup (no locks, consistent snapshot)
- ChromaDB directory copy
- Config and avatar skins included
- Compressed tar.gz archives

### C9.2 — Automated Nightly Backups
- Integrated into nightly evolution job (runs first, before any changes)
- Retention: 7 daily, 4 weekly, 12 monthly
- Pre-operation safety snapshots (before migrations, bulk imports, upgrades)
- Disk space monitoring and backup health checks

### C9.3 — Voice-Accessible Backup Management
- "Atlas, back yourself up" → manual backup
- "Atlas, restore from yesterday" → restore with safety backup first
- "Atlas, when was your last backup?" → query backup_log
- Proactive warnings if backup health degrades

---

## Phase C10: Context Management & Hardware Abstraction

See [context-management.md](context-management.md) for full design.

### C10.1 — Hardware Auto-Detection
- GPU detection: AMD (ROCm), NVIDIA (CUDA), Intel (oneAPI), Apple (Metal), CPU-only
- Auto-compute VRAM budget, KV cache limits, max context window, model size cap
- Store in `hardware_profile` table, re-detect on demand or after OOM
- First-run installation wizard with recommended models

### C10.2 — Dynamic Context Sizing
- Per-request context window based on task complexity (512 for commands, 16K+ for reasoning)
- Token budget allocation: system → memory → active messages → checkpoints → generation reserve
- Thinking mode gets expanded context with pre-think compaction
- GPU memory monitoring to prevent OOM (reduce context or skip thinking when constrained)

### C10.3 — Context Compaction & Overflow Recovery
- Tiered summarization: checkpoint summaries (oldest) → recent summary → active messages (verbatim)
- Compaction triggers at 60% and 80% of context budget
- LLM-generated checkpoint summaries preserving decisions, entities, unresolved items
- Checkpoint expansion on demand if LLM needs detail from old segment
- **Transparent overflow recovery**: if output exceeds generation reserve, capture partial output, compact, re-send with continuation prompt — user never sees the seam
- **Chunked generation**: proactive splitting for long outputs (code, plans, detailed explanations)
- **Output deduplication**: sentence-level fuzzy matching to remove overlap across chunks, with coherence smoothing pass
- **Continuation fillers**: natural bridging phrases ("Bear with me...", "...and continuing with that...") streamed during recovery latency

### C10.4 — Hardware-Agnostic Model Selection
- Auto-recommend fast/standard/thinking/embedding models based on VRAM tier
- User overridable ("Atlas, use qwen3:30b for everything")
- Model config stored in `model_config` table
- Fallback chains: if preferred model doesn't fit, downgrade gracefully

### C10.5 — Context Observability
- `context_metrics` table tracking token budgets, utilization, compactions per request
- `context_checkpoints` table for conversation history compression
- Nightly evolution reviews metrics to tune default windows and thresholds

### C10.6 — User Interruption Handling
- Detect incoming messages during active generation (non-blocking poll)
- Classify interrupt type: stop, redirect, clarify, refine (pattern-based, no LLM)
- Stop: halt immediately, save partial output, natural acknowledgment
- Redirect: halt, checkpoint partial, begin new request with prior context
- Clarify: pause, answer inline, offer to resume
- Refine: halt, re-generate with refinement instruction
- Voice interruption: echo cancellation, listen-during-playback, wake word detection mid-output

---

## Phase C11: Voice & Speech Engine

See [voice-engine.md](voice-engine.md) for full design.

### C11.1 — TTS Provider Interface
- Abstract `TTSProvider`: `synthesize()`, `list_voices()`, `supports_emotion()`
- Implementations: Orpheus (Ollama), Piper (CPU fallback), Parler, Coqui
- Provider discovered at install (C0), configurable in `cortex.env`

### C11.2 — Orpheus TTS Integration
- Pull `legraphista/Orpheus` Q4 GGUF into Ollama (or Orpheus-FastAPI with ROCm)
- Verify audio generation, streaming, emotion tags
- VRAM management: time-multiplexed with LLM (Ollama model switching)
- 8 built-in voices with emotion support

### C11.3 — Emotion Composer
- Map VADER sentiment → Orpheus/Parler emotion format
- Paralingual injection: `<laugh>`, `<sigh>`, `<chuckle>`, `whisper:` based on context
- Age-appropriate emotion filtering (gentler for kids)
- Night mode / quiet hours: automatic pace, volume, energy reduction
- Never repeat same paralingual consecutively

### C11.4 — Voice Registry & Selection
- `tts_voices` table with provider, gender, style, language
- Per-user voice preference (stored in user profile)
- Voice preview/audition: "Atlas, try a different voice"
- Seed voices for each installed provider

### C11.5 — Sentence-Boundary Streaming
- Detect sentence boundaries in LLM token stream
- Pipeline: sentence complete → emotion tag → TTS → audio chunk
- Overlap: sentence N plays while sentence N+1 generates
- Fast path: Layer 1/2 → Piper CPU → <200ms total

### C11.6 — Atlas TTS API Endpoint
- `POST /v1/audio/speech` (OpenAI-compatible)
- Extensions: `emotion`, `include_phonemes` for avatar sync
- Wyoming TTS adapter for HA integration
- HA uses Atlas as both conversation agent AND TTS engine

### C11.7 — Avatar Phoneme Bridge
- Extract phoneme timing from Orpheus/Piper output
- Feed to avatar server (C7) for viseme animation
- Synchronized: audio playback + lip movement + emotion expression

---

## Phase C12: Safety Guardrails & Content Policy

See [safety-guardrails.md](safety-guardrails.md).

### C12.1 — Content Tier Resolution
- Resolve content tier from user profile (age_group + age_confidence)
- Default to strict when age unknown (confidence < 0.6)
- Parental control override support
- Store tier in pipeline context for all downstream layers

### C12.2 — Input Guardrails
- Pre-pipeline checks: self-harm detection, illegal content, PII detection, prompt injection
- GuardrailResult severity levels: PASS, WARN, SOFT_BLOCK, HARD_BLOCK
- PII redaction before logging
- Crisis response protocol with pre-written empathetic responses + resources
- Input deobfuscation: decode base64, leetspeak, Unicode homoglyphs, ROT13, zero-width chars before analysis

### C12.3 — Output Guardrails
- Post-LLM checks: explicit content scan, language appropriateness, harmful instructions, data leakage
- Content tier enforcement on vocabulary and tone
- Response replacement/rewriting when guardrails trigger
- Cross-user data isolation verification
- Output behavioral analysis: persona break, system prompt leak, tone shift, instruction echo

### C12.4 — Safety System Prompt Injection
- Build age-appropriate system prompt prefix per content tier
- Educational mode: scientific terminology for bodies/biology at all tiers
- Profanity handling rules per tier
- Honest challenge mode: push back on bad ideas, admit uncertainty
- Anti-jailbreak instructions hardened into system prompt

### C12.5 — Guardrail Event Logging & Review
- `guardrail_events` table for all triggers
- Severity-based alerting (parent notification on crisis for minors)
- Nightly evolution review of guardrail patterns to reduce false positives
- Hard limits that cannot be overridden (explicit content, CSAM, self-harm methods)

### C12.6 — Adaptive Jailbreak Defense
- 5-layer defense: static regex, semantic intent, system prompt, output analysis, adaptive learning
- `jailbreak_patterns` table: learned regex patterns from blocked attempts
- `jailbreak_exemplars` table: semantic embeddings of novel attacks
- Auto-extract patterns from blocked attacks, validate against known-good messages (<1% FPR)
- Hot-reload detectors when new patterns are learned
- Conversation drift monitor: track safety temperature across multi-turn escalation attempts
- Nightly clustering of attack families, meta-pattern generation, stale pattern pruning
- Attack taxonomy classification: direct override, persona swap, roleplay wrap, encoding, gradual escalation

---

# Part 2: Integration Layer

Everything below connects Atlas to the outside world. Designed as **discovery-based plugins** so anyone can install Atlas and it adapts to whatever services are available.

## Phase I1: Service Discovery & Setup

The installer that finds what's on the network and configures integrations.

### I1.1 — Network Service Discovery
- mDNS/Zeroconf scan for common services:
  - Home Assistant (`_home-assistant._tcp`)
  - Nextcloud (WebDAV probing on common ports/paths)
  - MQTT brokers (`_mqtt._tcp`)
  - CalDAV/CardDAV servers
  - NAS shares (SMB/NFS discovery)
  - IMAP/SMTP email servers
- Manual fallback: user provides URLs/IPs for anything not auto-discovered
- Store discovered services in `discovered_services` table

### I1.2 — Service Configuration Wizard
- Interactive setup for each discovered service:
  - Home Assistant: guide user to create long-lived access token
  - Nextcloud: OAuth or app password flow
  - Email: IMAP credentials
  - NAS: mount path or SMB credentials
- Validate connectivity before saving
- Store configs in `service_config` table (encrypted credentials)

### I1.3 — Plugin Activation
- Map discovered services → available plugins
- Auto-activate plugins for confirmed services
- Register plugin command patterns into Layer 2
- Health check each plugin on startup
- Graceful degradation: if a service goes down, plugin disables itself and re-checks periodically

### I1.4 — Re-Discovery
- User-triggered: "Atlas, scan for new services"
- Nightly: lightweight re-scan for new/removed services
- After network change (new IP, new subnet)
- Detect when a previously-unavailable service comes online

---

## Phase I2: Home Assistant Integration

The HA plugin — registers command patterns, discovers devices, executes actions.

### I2.1 — HA Device Bootstrap
- Fetch all entities from HA REST API (`/api/states`)
- Populate `ha_devices` table
- Fetch HA areas (`/api/config/area_registry/list`) and map entities to rooms
- Generate initial command patterns for common device types (lights, switches, climate, locks, covers, fans, media, sensors)
- Map friendly names → entity IDs with alias support
- Identify and register presence sensors per area into `presence_sensors` table
- Register all patterns into Layer 2 plugin registry

### I2.2 — HA Command Execution
- Pattern-matched commands → direct HA REST API calls (no LLM)
- Room-scoped entity filtering when spatial context is available
- Response generation: "Done — bedroom lights off"
- Error handling: HA unreachable → graceful fallback to LLM (which may also fail, but at least explains)

### I2.3 — HA WebSocket Listener (Real-Time)
- Subscribe to HA state change events
- Update `ha_devices.state` in real-time
- Detect new devices added to HA between nightly scans
- Feed real-time events to proactive suggestion engine (C4.4)

---

## Phase I3: Voice Pipeline & Spatial Awareness

Connects speaker identification to HA's voice infrastructure for room-aware commands.

### I3.1 — HA Voice Pipeline Integration
- Modify Wyoming STT pipeline to pass audio to speaker-id sidecar (C3a)
- Return identified user with transcribed text
- HA automation context: "Derek said turn off lights" vs "Guest said..."

### I3.2 — Spatial Awareness Engine
- Map voice satellites to HA areas (`satellite_rooms` table)
- Query HA presence sensors in real-time during Layer 0
- Combine satellite ID + presence + speaker identity for room resolution
- Multi-mic proximity: compare audio energy across satellites for same utterance
- Ambiguity resolution: satellite+presence > satellite-only > presence-only > ask user
- Room-scoped entity filtering: "the lights" → only entities in resolved room
- Log all spatial resolutions to `room_context_log` for tuning

### I3.3 — Contextual Multi-Room Commands
- "Goodnight" triggers floor/house-scoped scenes based on location
- "Turn off everything downstairs" uses floor mapping
- User's current area informs default command scope

---

## Phase I4: Self-Learning Engine

The system that makes Cortex smarter every day — learns from HA interactions.

### I4.1 — Nightly Evolution Cron Job
- Lightweight Python container with cron
- Schedule: run at 3 AM daily
- HA device discovery diff (new devices, removed devices, renamed)
- LLM-powered pattern generation for new devices
- Write results to `evolution_log`

### I4.2 — Fallthrough Analyzer
- Query interactions where `matched_layer = 'llm'` AND tool calls contain integration actions
- Use LLM to generate regex patterns from the natural language that triggered fallthrough
- Insert learned patterns into `command_patterns` with source `'learned'`
- Confidence scoring and deduplication
- Works for ANY plugin (HA, lists, knowledge queries — not just HA)

### I4.3 — Pattern Lifecycle Management
- Track `hit_count` per pattern
- Prune zero-hit patterns after 30 days
- Boost frequently-hit patterns
- Merge similar patterns into generalized forms
- Weekly report: "X% of device commands now handled without LLM"

---

## Phase I5: Knowledge Source Connectors

Connect Atlas's knowledge/privacy system (C8 framework in Part 1) to actual data sources.

### I5.1 — Knowledge Index Infrastructure
- ChromaDB `cortex_knowledge` collection (separate from memory)
- SQLite `knowledge_docs` metadata table + FTS5 mirror
- Access gate: filter all queries by owner_id + access_level
- Identity confidence determines access tier (private/shared/household/public)

### I5.2 — Source Connector Plugins
Each connector is a plugin discovered via I1:
- **Nextcloud** (WebDAV): files, photos (EXIF), notes
- **Email** (IMAP): subject, body, attachments
- **Calendar** (CalDAV): events, shared calendars
- **NAS** (SMB/NFS): documents on file shares
- **HA history**: device states, automation logs
- **Chat history**: prior Atlas conversations (always available)

### I5.3 — Document Processing Pipeline
- Text extraction: PDF, DOCX, XLSX, CSV, Markdown, plain text
- Chunking for large documents
- Owner assignment from source path / account
- Access level assignment (private default, shared/household by path convention)
- PII tagging (tag, don't redact — it's the user's own data)
- Embed via Ollama, upsert to ChromaDB + FTS5

### I5.4 — Privacy Enforcement
- User-scoped queries: owner_id filter on all retrievals
- Unknown speaker: household + public data only
- Low-confidence speaker: shared + household + public only
- Cross-user data requests blocked with natural explanation
- Children's data visible to their parent (parental_controls)
- Children cannot access parent's private data
- Exclusion list: passwords, alarm codes, SSH keys, .env files, medical, financial

### I5.5 — Sync & Freshness
- Nightly full scan for all connected sources
- Real-time: HA states (WebSocket), chat history (interaction logger)
- Frequent: calendar (15min), email (30min)
- On-demand reindex triggered by user request
- Change detection via content hash (only re-embed modified docs)

---

## Phase I6: List Management

Multi-backend lists with per-list permissions. See [lists.md](lists.md).

### I6.1 — List Management System
- List registry table with backend, permissions, aliases
- Backend adapters (plugins from I1): HA to-do, Nextcloud CalDAV, file-based, Grocy, Todoist
- List resolution: explicit name → category inference → conversation context → memory → ask
- Permission enforcement: public lists allow anyone, private/shared respect access control
- Auto-discovery of lists from connected services during nightly job
- Remember routing preferences so user never repeats a clarification

---

## Phase I7: Offsite Backup

Extends C9 backup to push copies to discovered NAS/storage.

### I7.1 — NAS Offsite Sync
- rsync to NAS share after each backup
- Configurable remote path via cortex.env or discovered NAS
- Ensures recovery even if the Atlas server fails completely

---

## Dependency Graph

```
PART 1 (Core Engine):

C0.1 (LLM Provider) ──┬──▶ C0.4 (Backend Discovery) ──▶ C0.5 (UI Detection)
C0.2 (Embed Provider) ─┤                                        │
C0.3 (Hardware) ────────┘                                        ▼
                                                            C0.6 (Installer)
                                                                 │
                        ┌────────────────────────────────────────┘
                        ▼
C1.1 (Core Pipe) ──┬──▶ C1.3 (Filler Engine) ──▶ C1.4 (Register Model)
                    └──▶ C1.5 (Plugin Registry)
C1.2 (Logging) ────────────────────────────────────────────────────────

C0.3 (Hardware) ──▶ C10.1 ──▶ C10.2 (Context) ──▶ C10.3 (Compaction)
                          │                               │
                          └──▶ C10.4 (Model Selection)    ├──▶ C10.5
                                                          └──▶ C10.6

C3a.1 (Speaker Sidecar) ──▶ C3a.2 (Enrollment) ──▶ C3a.3 (Pipe Integration)
                                                          └──▶ C3a.4 (Age Est.)

C5.1 (Embedding) ──▶ C5.2 (ChromaDB) ──▶ C5.3 (HOT) ──▶ C5.4 (COLD) ──▶ C5.5

C5.5 + C3a.3 ──▶ C6.1 (Profiles) ──▶ C6.2 ──▶ C6.3 ──▶ C6.4 (Parental)
                                                                │
                   C4.1 (Emotion) ◀────────────────────────────┘
                        └──▶ C4.2 ──▶ C4.3 ──▶ C4.4

C6.4 (Parental) ──▶ C12.1 (Content Tier) ──▶ C12.2 (Input Guards)
                                                      │
                          C12.4 (Safety Prompt) ◀─────┤
                                                      ▼
                    C12.3 (Output Guards) ──▶ C12.5 (Logging & Review)
                                                      │
                                                      ▼
                                               C12.6 (Adaptive Jailbreak)

C0.1 (LLM Provider) ──▶ C11.1 (TTS Provider) ──▶ C11.2 (Orpheus) ──▶ C11.3 (Emotion)
                                                        │
                    C11.4 (Voice Registry) ◀────────────┘
                        └──▶ C11.5 (Streaming) ──▶ C11.6 (TTS API)
                                                        └──▶ C11.7 (Phoneme Bridge) ──▶ C7.1 (Avatar Server)

C7.1 (Avatar Server) ──▶ C7.2 → C7.3 → C7.4 → C7.5/C7.6/C7.7/C7.8 → C7.9

C9.1 (Backup CLI) ──▶ C9.2 (Nightly) ──▶ C9.3 (Voice Backup)


PART 2 (Integration Layer):

I1.1 (Discovery) ──▶ I1.2 (Config Wizard) ──▶ I1.3 (Plugin Activation)
       │                                              │
       │  (or via conversation with Atlas)             │
       │                                     ┌────────┤────────┬────────┐
       │                                     ▼        ▼        ▼        ▼
       │                              I2.1 (HA)   I5.1 (Know) I6.1    I7.1
       │                                │             │
       │                                ▼             ▼
       │                          I2.2 → I2.3    I5.2 → I5.3 → I5.4 → I5.5
       │                                │
       │                     ┌──────────┤
       │                     ▼          ▼
       │               I3.1 → I3.2   I4.1 → I4.2 → I4.3
       │                  └──▶ I3.3
```

## What Can Start Now (No Dependencies)

### Part 1 — Start immediately:

| Task | Description |
|------|-------------|
| C0.1 | LLM provider interface (abstract class + Ollama + OpenAI-compat) |
| C0.2 | Embedding provider interface |
| C0.3 | Hardware detection (shared with C10.1) |
| C1.2 | Create database schema and logging infrastructure |
| C3a.1 | Build speaker ID sidecar container |
| C5.1 | Pull embedding model, verify API |
| C7.1 | Avatar server container skeleton |
| C9.1 | Build backup/restore CLI tool |

### Part 2 — Start after C0.6 + C1.1 are operational:

| Task | Description |
|------|-------------|
| I1.1 | Network service discovery (mDNS/Zeroconf scan) |

## Blockers

### Part 1:
- **C1.1** requires C0 (installer/provider interface) to know which LLM to talk to
- **C3a.2+** requires speaker-id sidecar deployed
- **C4.x** requires profiles + memory + voice identity
- **C5.2+** requires embedding model operational
- **C6.x** requires both memory (C5) and speaker-id (C3a)

### Part 2:
- **I2.x** requires Home Assistant discovered + access token provided
- **I3.x** requires HA voice pipeline + speaker-id sidecar
- **I5.x** requires at least one knowledge source discovered
- **All of Part 2** requires core pipe + plugin registry operational

---

## External Projects (separate repos)

- [ ] **Document Classification System** — standalone service that classifies documents by type, sensitivity, and access level. Consumed by Atlas Cortex (I5) for automatic `access_level` assignment, PII detection, and content categorization. Should support: file type detection, content analysis, sensitivity scoring, category tagging (financial, medical, personal, work, household). Could use a fine-tuned small model or rule-based engine. Lives outside this project as a general-purpose utility.

---

# Part 2.5: Satellite System

Distributed speaker/microphone devices for whole-house Atlas presence. See [satellite-system.md](satellite-system.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| S2.5 | Satellite System | 🔲 Planned | Part 1 C11 (TTS) + C3a (Voice ID) |

### S2.5.1 — Satellite Agent Core
- Audio capture (16kHz mono), playback, agent loop
- Cross-platform: Raspberry Pi, ESP32-S3, generic Linux

### S2.5.2 — Wake Word Detection
- openWakeWord (default), pluggable engine interface
- Local-only processing for privacy

### S2.5.3 — VAD + Acoustic Echo Cancellation
- Silero VAD for speech boundaries
- speexdsp AEC for barge-in support

### S2.5.4 — Server Connection
- WebSocket client with auto-reconnect
- Audio streaming (PCM or Opus)
- Protocol: ANNOUNCE → WAKE → AUDIO_CHUNK → AUDIO_END

### S2.5.5 — Atlas WebSocket Endpoint
- Server-side `/ws/satellite` handler
- STT → pipeline → TTS → stream back to satellite

### S2.5.6 — Discovery & Registration
- mDNS/Zeroconf announcement from satellites
- Atlas auto-detection and DB registration

### S2.5.7 — Wyoming Protocol Compatibility
- Integrate with Home Assistant voice pipeline
- Satellites appear as HA voice assistants

### S2.5.8 — LED / Visual Feedback
- State-based LED control (idle, listening, thinking, speaking)
- NeoPixel, GPIO, OLED support

### S2.5.9 — Platform Abstraction
- Raspberry Pi GPIO/I2S, ESP32 I2S, generic ALSA/PulseAudio

### S2.5.10 — Installer & Docker
- One-line install script for Pi
- Docker image for any Linux device
- ESP32 firmware flash tool

### S2.5.11 — Offline Fallback
- Cached error TTS for server outages
- Automatic reconnection with exponential backoff

---

# Part 3: Alarms, Timers & Reminders

See [alarms-timers-reminders.md](alarms-timers-reminders.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P3 | Alarms, Timers & Reminders | 🔲 Planned | S2.5 (satellites) + I2 (HA) |

### P3.1 — Alarm Engine
- Cron-like scheduler, DB persistence, recurring (weekday/weekend/daily)
- Sound selection or TTS message

### P3.2 — Timer Engine
- In-memory countdown, multiple concurrent timers
- Pause, resume, cancel, label ("pasta timer")

### P3.3 — Reminder Engine
- Time-based, location-based (geofence via HA), event-based
- Recurring reminders with cron expressions

### P3.4 — Notification Router
- Route to satellite in user's room, escalate to all, push to phone
- Priority-based delivery strategy

### P3.5 — Natural Language Parser
- Extract time, duration, recurrence from user speech
- "Every weekday at 7am", "In 15 minutes", "When I get home"

### P3.6 — Snooze / Dismiss Handling
- Voice commands during active alarm: "Snooze", "Stop", "5 more minutes"

### P3.7 — Pipeline Integration
- Layer 2 plugin for alarm/timer/reminder intents

---

# Part 4: Routines & Automations

See [routines-automations.md](routines-automations.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P4 | Routines & Automations | 🔲 Planned | I2 (HA) + P3 (timers for delays) |

### P4.1 — Routine Engine
- Sequential action execution with condition checks
- Support for delays, conditional branching, error handling

### P4.2 — Conversational Builder
- Create and edit routines through natural conversation
- "When I say X, do Y" pattern recognition

### P4.3 — Built-in Templates
- Good Morning, Good Night, I'm Leaving, I'm Home, Movie Time, Dinner Time
- Customizable per user

### P4.4 — Schedule Triggers
- Cron-based routine execution

### P4.5 — Event Triggers
- HA state change subscription (door opened, motion detected, etc.)

### P4.6 — Pipeline Integration
- Layer 2 plugin matching voice trigger phrases to routines

### P4.7 — Routine Management
- List, edit, delete, enable/disable via voice or API

---

# Part 5: Proactive Intelligence

See [proactive-intelligence.md](proactive-intelligence.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P5 | Proactive Intelligence | 🔲 Planned | I2 (HA) + S2.5 (satellites) + C5 (memory) |

### P5.1 — Proactive Rule Engine
- Evaluate triggers against HA state + external data sources
- User-configurable rules + built-in defaults

### P5.2 — Notification Priority & Throttle
- Critical/High/Medium/Low/Passive priority levels
- Fatigue prevention: max per hour, cooldown, DND/sleep suppression

### P5.3 — Weather Intelligence
- Storm/rain/temperature/UV alerts from HA weather entities or direct API

### P5.4 — Energy Monitoring
- Usage anomalies, cost optimization, solar awareness

### P5.5 — Anomaly Detection
- Pattern-based unusual activity alerts (unusual door open, device malfunction)

### P5.6 — Package Tracking
- Email parsing for tracking numbers, delivery status updates

### P5.7 — Calendar Awareness
- Meeting prep, travel time calculation, birthday/event reminders

### P5.8 — Daily Briefing
- Morning summary: weather, calendar, reminders, energy, packages

---

# Part 6: Learning & Education

See [learning-education.md](learning-education.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P6 | Learning & Education | 🔲 Planned | C6 (profiles) + C12 (safety) |

### P6.1 — Tutoring Engine
- Socratic method, never gives direct homework answers
- Age-adapted explanations and examples

### P6.2 — Quiz Generator
- Topic-based questions with adaptive difficulty
- Scoring, streaks, encouragement

### P6.3 — Homework Helper
- Guide through problem-solving steps
- Show-your-work mode

### P6.4 — Science Experiments
- Safe, age-appropriate, step-by-step instructions
- Integrated timers for experiments

### P6.5 — Language Learning
- Vocabulary drills, pronunciation practice via TTS
- Conversational language practice

### P6.6 — Progress Tracking
- Per-subject proficiency scoring
- Spaced repetition scheduling

### P6.7 — Parent Reporting
- Summary of what child learned, time spent, areas needing help

---

# Part 7: Intercom & Broadcasting

See [intercom-broadcasting.md](intercom-broadcasting.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P7 | Intercom & Broadcasting | 🔲 Planned | S2.5 (satellites) |

### P7.1 — Intercom Intent Parser
- Extract target rooms, zones, people, and message from voice

### P7.2 — Message Personalizer
- Adapt tone and voice for target audience (child vs adult)

### P7.3 — Satellite Router
- Deliver TTS audio to specific satellites or zones

### P7.4 — Two-Way Calling
- Bidirectional audio stream between two satellites

### P7.5 — Zone Management
- Create/edit/delete satellite groups (upstairs, bedrooms, etc.)

### P7.6 — Emergency Broadcast
- Max-priority, all-satellite, max-volume override

### P7.7 — Pipeline Integration
- Layer 2 plugin for intercom/announce/call intents

---

# Part 8: Media & Entertainment

See [media-entertainment.md](media-entertainment.md) for full design.

| Phase | Name | Status | Prerequisites |
|-------|------|--------|---------------|
| P8 | Media & Entertainment | 🔲 Planned | S2.5 (satellites) + I2 (HA media players) |

### P8.1 — Media Provider Interface
- Abstract source with search, stream, health check

### P8.2 — Local Library Provider
- File scanning (FLAC, MP3, OGG, WAV), ID3 tags, search index
- Optional Jellyfin/Plex/Navidrome integration

### P8.3 — Spotify Provider
- Spotify Connect API via HA or `spotipy`

### P8.4 — YouTube Music Provider
- `ytmusicapi` search and streaming

### P8.5 — HA Media Provider
- Wrap HA `media_player.*` entities

### P8.6 — Podcast Provider
- RSS parsing, auto-download, episode tracking, resume position

### P8.7 — Playback Controller
- Play/pause/skip/volume/transfer between rooms

### P8.8 — Multi-Room Sync
- HA media groups, Snapcast, or satellite-based sync

### P8.9 — Smart Playlists
- Learn preferences from listening patterns
- Contextual auto-generation (morning, focus, cooking, bedtime)

### P8.10 — Pipeline Integration
- Layer 2 plugin for media voice commands

### P8.11 — Source Priority
- Multi-source resolution: local → preferred service → first available

---

## Extended Dependency Graph

```
PART 2.5 → PART 3 → PART 4 (sequential foundation)
                 │
PART 1 (C5+C6+C11+C12) ──▶ PART 5 (proactive, needs memory + HA)
                 │
PART 1 (C6+C12) ──────────▶ PART 6 (education, needs profiles + safety)
                 │
PART 2.5 ──────────────────▶ PART 7 (intercom, needs satellites)
                 │
PART 2.5 + I2 ─────────────▶ PART 8 (media, needs satellites + HA)
```

```
S2.5.1-S2.5.3 ──▶ S2.5.4 ──▶ S2.5.5 ──▶ S2.5.6 ──▶ S2.5.7
S2.5.8-S2.5.9 ──────────────────────────────────────▶ S2.5.10
                                                      S2.5.11

P3.1-P3.3 ──▶ P3.4 ──▶ P3.5 ──▶ P3.7
P3.6 ──────────────────────────────┘

P4.1 ──▶ P4.2 ──▶ P4.3
P4.4 ──┐
P4.5 ──┼──▶ P4.6 ──▶ P4.7
       │
P5.1 ──▶ P5.2 ──▶ P5.3-P5.7 ──▶ P5.8

P6.1 ──▶ P6.2-P6.5 ──▶ P6.6 ──▶ P6.7

P7.1 ──▶ P7.3 ──▶ P7.7
P7.2 ──────┘
P7.4 ──────────────┘
P7.5 ──────────────┘
P7.6 ──────────────┘

P8.1 ──▶ P8.2-P8.6 ──▶ P8.7 ──▶ P8.8 ──▶ P8.10
                                   │
                        P8.9 ◀────┘──▶ P8.11
```
