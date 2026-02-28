# 🗺️ Atlas Cortex — Roadmap

> **For current implementation status, see the main [README](../README.md#-implementation-status).**
>
> **For detailed phase designs, see [phases.md](phases.md).**

This document outlines planned future features organized by implementation part. Each part builds on previous ones — see the [dependency graph](#dependency-graph) at the bottom.

---

## ✅ Completed

### Part 1: Core Engine
Everything in Part 1 is **complete** — the brain of Atlas works standalone with any LLM backend.

| Phase | Module | Description |
|-------|--------|-------------|
| C0 | Installer & Backend | LLM provider abstraction, GPU detection, CLI installer |
| C1 | Core Pipeline | 4-layer pipeline, sentiment, instant answers, filler streaming |
| C3a | Voice Identity | Speaker recognition, enrollment, hybrid age estimation |
| C4 | Emotional Evolution | Rapport tracking, personality drift, proactive suggestions |
| C5 | Memory System | HOT/COLD paths, vector search, BM25, RRF fusion |
| C6 | User Profiles | Age-awareness, onboarding, parental controls |
| C7 | Avatar System | Phoneme-to-viseme lip-sync, emotion expressions |
| C9 | Backup & Restore | Automated nightly backups, one-command restore |
| C10 | Context Management | Context windows, compaction, overflow recovery |
| C11 | Voice & Speech | TTS providers (Orpheus + Piper), emotion, streaming |
| C12 | Safety Guardrails | Content tiers, jailbreak defense, PII redaction |
| — | Admin Web Panel | Vue 3 dashboard, JWT auth, 30+ REST endpoints |

### Part 2: Integration Layer
Connects Atlas to real-world services discovered on your network.

| Phase | Module | Description |
|-------|--------|-------------|
| I1 | Service Discovery | HTTP-probe scanner, service registry, config wizard |
| I2 | Home Assistant | REST client + WebSocket listener, device bootstrap, patterns |
| I3 | Voice Pipeline | Wyoming STT/TTS client, spatial awareness, multi-room commands |
| I4 | Self-Learning | Fallthrough analysis, pattern lifecycle, nightly evolution |
| I5 | Knowledge Sources | WebDAV/Nextcloud connector, CalDAV calendars, sync scheduler |
| I6 | List Management | Multi-backend lists, HA to-do discovery, permissions |
| I7 | Offsite Backup | NAS rsync/SMB sync, voice commands, retention policy |

---

## 🔜 Up Next

### Part 2.5: Satellite System
*Distributed speakers and microphones in every room.*

> **Design doc:** [satellite-system.md](satellite-system.md)

| Component | Description |
|-----------|-------------|
| **Satellite Agent** | Lightweight daemon for ESP32-S3, Raspberry Pi, or any Linux device |
| **Audio Streaming** | Continuous wake-word detection → stream audio to central server |
| **Speaker Output** | Receive TTS audio from server, play through local speaker |
| **Registration** | Auto-discover and register with Atlas Cortex server |
| **Room Assignment** | Admin assigns satellite to room/area for spatial awareness |
| **Hardware Agnostic** | Core protocol works on any device with mic + speaker + network |

**Supported Hardware:**
- ESP32-S3 (INMP441 mic + MAX98357A speaker) — $15 per room
- Raspberry Pi Zero 2W with USB mic + speaker — $25 per room
- Raspberry Pi 3/4/5 with ReSpeaker HAT — $50+ per room
- Any Linux box with ALSA audio — repurpose old hardware

**Key Design Goals:**
- Satellite is a thin client — all processing happens on the server
- Wyoming protocol for STT/TTS communication
- Wake word detection runs locally (openWakeWord) to minimize network traffic
- Graceful degradation: if server is unreachable, satellite announces "Atlas is offline"

---

### Part 3: Alarms, Timers & Reminders

> **Design doc:** [alarms-timers-reminders.md](alarms-timers-reminders.md)

| Feature | Example |
|---------|---------|
| **Alarms** | *"Wake me up at 7am"* — plays on bedroom satellite or HA media player |
| **Timers** | *"Set a 15-minute timer"* — cooking timers with voice countdown |
| **Reminders** | *"Remind me to take medicine at 3pm"* — recurring with snooze |
| **Location triggers** | *"Remind me when I get home to check the mail"* — presence-based |
| **Multi-user** | Each family member has their own alarms, announced in their room |

**Architecture:**
- Timer/alarm scheduler runs as background asyncio task
- Alarms stored in SQLite with recurrence rules (RRULE format)
- Delivery via satellite speakers (nearest to user) or HA media players
- Snooze/dismiss via voice: *"Snooze for 5 minutes"* / *"Stop"*

---

### Part 4: Routines & Automations

> **Design doc:** [routines-automations.md](routines-automations.md)

| Feature | Example |
|---------|---------|
| **Built-in routines** | *"Good morning"* → lights, coffee, weather, calendar |
| **Bedtime** | *"Good night"* → locks, lights off, alarm set, sleep sounds |
| **Departure** | *"I'm leaving"* → security arm, thermostat eco, non-essentials off |
| **Custom routines** | *"When I say 'movie time', dim lights to 20% and turn on TV"* |
| **Conditional** | *"If temperature drops below 60, turn on the heater"* |

**Architecture:**
- Routines are stored as ordered action sequences in SQLite
- Each action maps to an HA service call, Atlas command, or delay
- Conversational builder: Atlas asks clarifying questions to build routines
- Trigger types: voice command, schedule, sensor threshold, presence change

---

### Part 5: Proactive Intelligence

> **Design doc:** [proactive-intelligence.md](proactive-intelligence.md)

| Feature | Example |
|---------|---------|
| **Weather awareness** | *"It's going to rain — should I close the garage?"* |
| **Energy optimization** | *"AC has been on 8 hours — house is at 68°F"* |
| **Anomaly detection** | *"Basement humidity is unusually high"* |
| **Package tracking** | *"Your Amazon order arrives tomorrow 2-6pm"* |
| **Activity patterns** | *"You usually start the coffee maker by now — want me to?"* |

---

### Part 6: Learning & Education

> **Design doc:** [learning-education.md](learning-education.md)

| Feature | Example |
|---------|---------|
| **Homework help** | Age-appropriate explanations, never gives direct answers |
| **Interactive quizzes** | *"Quiz me on state capitals"* with score tracking |
| **Science mode** | *"What happens if we mix baking soda and vinegar?"* |
| **Language learning** | Vocabulary drills, pronunciation practice, flashcards |
| **Reading companion** | Read-along mode for young children |

---

### Part 7: Intercom & Broadcasting

> **Design doc:** [intercom-broadcasting.md](intercom-broadcasting.md)

| Feature | Example |
|---------|---------|
| **Targeted broadcast** | *"Tell the kids dinner is ready"* → children's rooms |
| **Whole-house** | *"Announce: family meeting in 5 minutes"* → all satellites |
| **Room-to-room** | *"Atlas, talk to the garage"* → opens intercom channel |
| **Emergency alert** | Smoke/CO detection → all speakers: *"Fire alarm triggered"* |

---

### Part 8: Media & Entertainment

> **Design doc:** [media-entertainment.md](media-entertainment.md)

| Feature | Example |
|---------|---------|
| **Music playback** | *"Play jazz in the living room"* |
| **Multi-room audio** | Synchronized playback across satellites |
| **Audio recognition** | *"What song is this?"* |
| **Recommendations** | *"Recommend a movie for family night"* |
| **Podcast/audiobook** | *"Resume my audiobook in the kitchen"* |

**Music Sources (priority order):**
1. **Local files** — NAS/server music library (FLAC, MP3, etc.)
2. **YouTube Music** — via yt-dlp for audio streaming
3. **Spotify** — via Spotify Connect / librespot
4. **Internet radio** — TuneIn, IceCast streams
5. **Podcasts** — RSS feed aggregation

---

## 🔮 Future Explorations

These are ideas that may become their own parts:

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

### 🏥 Health & Wellness
- Medication reminders with confirmation tracking
- Sleep pattern analysis from presence sensors
- Air quality monitoring and ventilation suggestions
- Exercise reminders based on activity patterns

---

## Dependency Graph

```
Part 1: Core Engine ──────────────────────────────────── ✅ COMPLETE
         │
         ▼
Part 2: Integration Layer ────────────────────────────── ✅ COMPLETE
         │
         ├─────────────────────┐
         ▼                     ▼
Part 2.5: Satellites     Part 4: Routines
         │                     │
         ├───────────┐         │
         ▼           ▼         ▼
Part 3: Alarms  Part 7: Intercom
         │           │
         ▼           ▼
Part 5: Proactive Intelligence
         │
         ▼
Part 8: Media & Entertainment

Part 6: Learning & Education ◀── Part 1 (C6 + C12)
    (can start independently after Part 1)
```

---

## Contributing a New Feature

Want to implement one of these parts? Here's how:

1. Check the design doc in `docs/` for architecture details
2. Create a feature branch: `git checkout -b feature/part-N-name`
3. Implement with tests (see existing modules for patterns)
4. Update `docs/phases.md` status
5. Submit a PR

See the main [README](../README.md#-contributing) for full contributing guidelines.
