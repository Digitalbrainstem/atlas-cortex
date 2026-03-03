# Atlas Cortex — System Architecture

## Overview

Atlas Cortex is an Open WebUI **Pipe function** that intercepts all user messages and processes them through a multi-layer pipeline. Each layer is progressively more expensive — simple queries are answered instantly without touching the LLM, while complex queries get routed to the appropriate model with perceived-zero-latency filler streaming.

## Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     Atlas Cortex Pipe                            │
│                                                                  │
│  Input Sources:                                                  │
│    • Web UI (user_id known from session)                        │
│    • Voice via HA (speaker identified by embedding)             │
│                                                                  │
│  Processing Layers (executed sequentially, first match wins):   │
│                                                                  │
│  Layer 0: Context Assembly                          ~1ms        │
│    • Identify user (session or voice embedding)                 │
│    • Load emotional profile (rapport, tone, filler pool)        │
│    • Run VADER sentiment analysis on input                      │
│    • Check time-of-day for tone adjustment                      │
│                                                                  │
│  Layer 1: Instant Answers                           ~5ms        │
│    • Date/time/day-of-week                                      │
│    • Basic math (eval with safety sandbox)                      │
│    • Identity questions ("who are you", "what can you do")      │
│    • Greetings (time-of-day + user-name aware)                  │
│    • Recent memory recall (from interaction log)                │
│                                                                  │
│  Layer 2: Learned Device Commands                   ~100-200ms  │
│    • Pattern-match against command_patterns DB                  │
│    • Execute HA REST API directly (no LLM round trip)           │
│    • Patterns auto-generated from HA device discovery           │
│    • Patterns learned from LLM fallthrough analysis             │
│    • Returns natural response: "Done — bedroom lights off"      │
│                                                                  │
│  Layer 3: Filler + LLM Streaming                    ~500-4000ms │
│    • Select pre-cached filler audio (0ms latency)              │
│    • Stream filler TTS to satellite immediately                │
│    • Fire Ollama API request in background                     │
│    • Sentence-level TTS streaming as LLM tokens arrive         │
│    • Confidence assessment on response (see grounding.md)       │
│    • If confidence < 0.5 → grounding loop (search/verify)      │
│    • Mistake history injected into prompt for known-weak topics │
│                                                                  │
│  Always Running: Interaction Logger                             │
│    • Log every interaction: user, message, layer hit,           │
│      sentiment, entities, response time, tool calls             │
│    • If Layer 3 used HA tools → flag for pattern learning       │
│    • Feeds the nightly evolution job                            │
└─────────────────────────────────────────────────────────────────┘
```

## Nightly Evolution Job

Runs as a cron container at 3 AM daily. Uses the existing Qwen3 30B model for intelligent tasks.

```
┌─────────────────────────────────────────────────────────────────┐
│                   Nightly Evolution Job (cron)                   │
│                                                                  │
│  1. Device Discovery                                            │
│     • GET /api/states from HA → compare with ha_devices table   │
│     • New devices → LLM generates friendly names + aliases      │
│     • LLM generates command patterns (regex) for each device    │
│     • Insert into command_patterns DB (source: 'nightly')       │
│                                                                  │
│  2. Fallthrough Analysis                                        │
│     • Query interactions where matched_layer = 'llm'            │
│       AND llm_tool_calls includes HA actions                    │
│     • For each: LLM generates a regex pattern that would have   │
│       caught this at Layer 2                                    │
│     • Insert into command_patterns DB (source: 'learned')       │
│     • Over time, fewer queries fall through to LLM              │
│                                                                  │
│  3. Emotional Profile Evolution                                 │
│     • Per user: analyze day's interactions                      │
│     • Update rapport_score (interaction count, sentiment trend) │
│     • LLM generates updated relationship_notes                  │
│     • LLM generates new personalized filler phrases             │
│     • Adjust preferred_tone based on communication patterns     │
│     • Time-of-day patterns: morning greetings vs late-night     │
│                                                                  │
│  4. Pattern Optimization                                        │
│     • Prune patterns with 0 hits after 30 days                  │
│     • Boost confidence on frequently-hit patterns               │
│     • Merge similar patterns into more general ones             │
│     • Report: "Learned 12 new patterns, 94% of HA commands     │
│       now handled at Layer 2 (up from 87%)"                     │
└─────────────────────────────────────────────────────────────────┘
```

## Speaker Identification Sidecar

Lightweight CPU-based container using [resemblyzer](https://github.com/resemble-ai/Resemblyzer) for speaker embeddings.

```
┌─────────────────────────────────────────────────────────────────┐
│                  Speaker Identification Sidecar                  │
│                                                                  │
│  Container: atlas-speaker-id (CPU-based, ~200MB RAM)            │
│  Library: resemblyzer (d-vector embeddings, 256-dim)            │
│                                                                  │
│  Enrollment Flow:                                               │
│    User: "Hey Atlas, remember my voice"                         │
│    Atlas: "Sure! Say a few sentences so I can learn your voice" │
│    → Record 10-15 seconds → Generate embedding → Store          │
│    Atlas: "Got it, Derek. I'll know it's you next time."        │
│                                                                  │
│  Identification Flow:                                           │
│    Audio arrives → Extract embedding → Cosine similarity match  │
│    Threshold > 0.85 → Identified (inject user context)          │
│    Threshold < 0.85 → Unknown → "I don't recognize that voice.  │
│                        What's your name?"                       │
│                                                                  │
│  API: POST /identify { audio_base64 } → { user_id, confidence }│
│  API: POST /enroll   { audio_base64, user_id } → { success }   │
└─────────────────────────────────────────────────────────────────┘
```

## Spatial Awareness — Room/Area Context

Atlas Cortex knows *where* you are, not just *who* you are. This transforms ambiguous commands like "turn off the lights" into precise actions targeting the correct room.

### Signal Sources (combined for confidence)

```
┌─────────────────────────────────────────────────────────────────┐
│                   Spatial Context Resolution                     │
│                                                                  │
│  Signal 1: Satellite ID (primary)                    ~0ms       │
│    • Each voice satellite is assigned to an HA area             │
│    • Wyoming protocol includes device_id in metadata            │
│    • "Voice came from kitchen satellite" → room = kitchen       │
│    • Simplest, most reliable signal                             │
│                                                                  │
│  Signal 2: Multi-Mic Proximity                       ~50ms      │
│    • If multiple satellites hear the same utterance,            │
│      compare audio energy / SNR across them                     │
│    • Loudest/clearest mic = closest to speaker                  │
│    • Resolves ambiguity when rooms are adjacent                 │
│    • Optional: rough triangulation with 3+ mics                 │
│                                                                  │
│  Signal 3: Presence Sensors                          ~0ms       │
│    • Motion sensors (PIR): "motion detected in office"          │
│    • mmWave radar: "person present in bedroom" (even still)     │
│    • BLE beacons / phone tracking: per-person room location     │
│    • Door contact sensors: infer room transitions               │
│    • HA provides this via entity states in real-time            │
│                                                                  │
│  Signal 4: Speaker + Presence Fusion                 ~10ms      │
│    • "Derek's voice" + "person in office" + "office mic"        │
│    • All three agree → confidence = very high                   │
│    • Disagreement → fallback to satellite ID (most reliable)    │
│                                                                  │
│  Output: { room: "office", area: "upstairs",                    │
│            confidence: 0.95, signals: [...] }                   │
└─────────────────────────────────────────────────────────────────┘
```

### How It Affects Command Resolution

```
Without spatial awareness:
  User: "Turn off the lights"
  Atlas: "Which lights? You have 8 light groups."

With spatial awareness:
  User (from office satellite): "Turn off the lights"
  Atlas knows: room=office, user=Derek
  Atlas: "Done — office lights off."

Smarter scenarios:
  User (from kitchen, 10 PM): "I'm heading to bed"
  Atlas knows: room=kitchen, user=Derek, time=late
  Atlas: "Night, Derek. Locking up — kitchen and living room
          lights off, bedroom set to 20%."
  → Triggers HA "goodnight" scene scoped to context
```

### Room Context Data Flow

```
Voice Satellite (kitchen)
    │
    ├── device_id: "satellite_kitchen"
    ├── audio → faster-whisper → text
    └── audio → speaker-id → "Derek"
                    │
                    ▼
          Atlas Cortex Layer 0
                    │
    ┌───────────────┼────────────────────┐
    │               │                    │
    ▼               ▼                    ▼
satellite_rooms    ha_presence       speaker_profiles
table lookup       sensor query      voice match
    │               │                    │
    │  room:kitchen │  motion:kitchen    │  user:Derek
    └───────────────┼────────────────────┘
                    │
                    ▼
            Spatial Context:
            { room: "kitchen",
              area: "downstairs",
              user: "Derek",
              confidence: 0.98,
              nearby_entities: [
                "light.kitchen",
                "switch.kitchen_fan",
                "sensor.kitchen_temp"
              ]}
                    │
                    ▼
             Layer 2: "turn off the lights"
             → matches light.kitchen (room-scoped)
             → POST /api/services/light/turn_off
```

### HA Area Integration

Home Assistant already organizes entities into **Areas** (rooms) and **Floors**. Cortex leverages this:

```sql
-- Satellite-to-room mapping
satellite_rooms (
    satellite_id TEXT PRIMARY KEY,    -- Wyoming device_id
    area_id TEXT NOT NULL,            -- HA area: "kitchen", "office", etc.
    floor TEXT,                       -- "upstairs", "downstairs", "basement"
    mic_position TEXT                 -- JSON: {"x": 3.2, "y": 1.5} for triangulation
)

-- Presence sensor mapping (which sensors indicate presence in which area)
presence_sensors (
    entity_id TEXT PRIMARY KEY,       -- "binary_sensor.office_motion"
    area_id TEXT NOT NULL,            -- "office"
    sensor_type TEXT,                 -- "motion" | "mmwave" | "ble" | "door"
    priority INTEGER DEFAULT 1        -- higher = more trusted (mmwave > motion)
)
```

### Ambiguity Resolution Rules

When a command doesn't specify a room:

1. **Satellite room matches presence** → use that room (highest confidence)
2. **Satellite room only** → use satellite room (good confidence)
3. **Presence only (typed command)** → use room with most recent motion/presence
4. **Multiple presence signals** → use the room matching the identified user
5. **No location data** → ask: "Which room?" (only as last resort)

For commands that affect multiple rooms (e.g., "goodnight"):
- Use spatial context to determine *scope* — "everything downstairs" vs "whole house"
- User's current floor/area informs the default scope

## Filler Streaming — How It Works

The key insight: **start talking before you have the answer**, then seamlessly blend in the real response.

```
User: "Why is the sky blue?"

Timeline:
  0ms    → Sentiment: curious/question
  1ms    → No instant answer match, no HA command match
  2ms    → Select filler: "Good question — "
  3ms    → Start streaming filler tokens to user
  50ms   → Background thread: POST /api/chat to Ollama
  300ms  → Filler complete, user sees: "Good question — "
  800ms  → First real token arrives from Ollama
  801ms  → Continue streaming: "the sky appears blue because..."
  
User perceives: continuous response from 3ms
Actual LLM latency: 800ms (hidden behind filler)
```

### Filler Pools (default, personalized over time)

Fillers are selected based on **two dimensions**: sentiment (emotional tone) and confidence (certainty level). Sentiment filler goes first, confidence framing appends if needed.

#### Sentiment Fillers

**Filler selection is randomized with recency tracking** — Atlas never repeats the same filler twice in a row, and weights toward less-recently-used phrases. The pool grows over time as the nightly evolution job generates new phrases matched to each user's style.

| Sentiment | Pool (rotated, never repeated consecutively) |
|-----------|------|
| Greeting | "Hey!", "Morning!", "What's up?", "Yo!", "Hey there." |
| Question | "Hmm — ", "Let me think... ", "So — ", "Alright — ", "Okay, " |
| Frustrated | "I hear you. ", "Yeah, that's annoying. ", "Ugh, let me look at this. " |
| Command | *(no filler — execute directly)* |
| Excited | "Nice! ", "Oh cool — ", "Hell yeah! " |
| Late night | "Still at it? ", "Alright, ", "Late one, huh? " |
| Follow-up | "So — ", "Right, ", "Okay — ", *(often no filler needed)* |
| Casual | *(no filler — just start answering)* |

```python
# Filler selection — never stale, never repetitive
def select_filler(sentiment, confidence, user_id):
    pool = get_filler_pool(sentiment, user_id)  # personalized pool
    
    # Remove last 2 used fillers to prevent repetition
    recent = get_recent_fillers(user_id, count=2)
    candidates = [f for f in pool if f not in recent]
    
    # Weighted random: less-recently-used phrases get higher weight
    filler = weighted_random(candidates, recency_weights)
    
    # Some interactions don't need fillers at all
    if sentiment in ('command', 'casual') or is_follow_up_in_conversation:
        filler = ""
    
    # Append confidence framing if needed
    if confidence < 0.8 and filler:
        filler += select_confidence_filler(confidence)
    
    log_filler_used(user_id, filler)
    return filler
```

**Important**: Many interactions need NO filler at all. If the user is in the middle of a conversation and asks a follow-up, jumping in with "Good question — " every time is robotic. The system tracks conversation flow and often just starts answering directly.

#### Confidence Fillers (appended when confidence < 0.8)

| Confidence | Fillers |
|------------|---------|
| Medium (0.5–0.8) | "I think — ", "If I remember right — ", "Pretty sure — " |
| Low (0.2–0.5) | "I'm not 100% on that — checking now... ", "Let me verify... " |
| None (< 0.2) | "I genuinely don't know this one. ", "I'd rather not guess — " |

See [grounding.md](grounding.md) for the full confidence-aware filler system, including grounding loop integration and combined sentiment+confidence selection.

### LLM System Prompt Injection

To prevent the LLM from repeating the filler sentiment:

```
[injected before real system prompt]
You already started your response with: "Good question — "
Continue naturally from that point. Do NOT repeat the greeting or acknowledgment.
```

## Model Selection (Layer 3)

The pipe auto-selects the LLM based on query analysis:

| Signal | Model | Reasoning |
|--------|-------|-----------|
| Short question, factual | Qwen2.5 14B (Turbo) | Fast, no thinking overhead |
| Code, explanation, multi-step | Qwen3 30B-A3B | Thinking mode beneficial |
| "explain in detail", "analyze", "compare" | Qwen3 30B-A3B (Deep) | Full thinking budget |

Selection is rule-based (no LLM needed for routing):
- Message length, keyword detection, conversation depth, explicit user cues

## Technical Stack

| Component | Technology | Resource |
|-----------|-----------|----------|
| Core Pipe | Atlas Cortex server (Python) + optional Open WebUI Pipe | Standalone :5100 or embedded |
| LLM Backend | **Any** — Ollama, vLLM, LocalAI, LM Studio, llama.cpp, etc. | Via provider interface |
| Sentiment | VADER (vaderSentiment) | CPU, <1ms |
| Speaker ID | resemblyzer | CPU, ~50ms, ~200MB RAM |
| Memory (vector) | ChromaDB (embedded mode) | CPU, ~50MB RAM, persistent SQLite |
| Memory (BM25) | SQLite FTS5 | CPU, sub-ms, built into Python |
| Embeddings | Ollama / sentence-transformers / fastembed | CPU, ~5ms/embed |
| Reranker | cross-encoder/ms-marco-MiniLM (optional) | CPU, ~20ms |
| Smart Home | Home Assistant REST API (plugin, optional) | Discovered at install |
| Search | SearXNG (optional) | Self-hosted |
| STT | faster-whisper, Whisper.cpp, or any Wyoming-compatible | Discovered |
| TTS | Piper, Coqui, or any Wyoming-compatible | Discovered |
| Nightly Job | Python + cron container | CPU only |
| Voice Routing | HA → Atlas Cortex (:5100) → LLM (not HA → LLM directly) | OpenAI-compatible agent |
