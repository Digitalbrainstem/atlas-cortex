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

### Part 2.7: Fast-Path Plugins
*Layer 2 plugins that bypass the LLM for common queries — instant responses, lower latency, reduced GPU load.*

These plugins intercept frequent request types at Layer 2, hitting external APIs or local logic directly instead of routing through the LLM. Each one follows the existing `CortexPlugin` interface (`match` → `handle`). The nightly evolution job already identifies LLM fallthrough patterns that could become fast-path plugins, but these are proactively built for the most common cases.

**All plugins are opt-in.** Users enable what they want from the Admin Panel → Plugins page. Disabled plugins are never loaded — queries fall through to the LLM as normal.

#### Information & Lookup

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Weather** | *"What's the weather?"*, *"Will it rain tomorrow?"* | OpenWeatherMap / NWS API | ~1s |
| **News Headlines** | *"What's in the news?"*, *"Tech news today"* | RSS feeds / NewsAPI | ~1s |
| **Dictionary** | *"Define serendipity"*, *"How do you spell accommodate?"* | Free Dictionary API / local | ~500ms |
| **Translation** | *"How do you say hello in Spanish?"* | LibreTranslate (self-hosted) | ~500ms |
| **Wikipedia Summary** | *"Tell me about the Eiffel Tower"*, *"Who was Tesla?"* | Wikipedia API (first paragraph) | ~1s |
| **This Day in History** | *"What happened today in history?"* | History API / local DB | ~500ms |

#### Time, Math & Utilities

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Timers & Alarms** | *"Set a 10-minute timer"*, *"Cancel my alarm"* | Local scheduler (no API) | <200ms |
| **Unit Conversions** | *"Convert 5 miles to km"*, *"Cups in a liter?"* | Local math (no API) | <100ms |
| **Calculator** | *"What's 15% tip on $84?"*, *"Mortgage on $300k at 6.5%?"* | Local math (no API) | <100ms |
| **Timezone** | *"What time is it in Tokyo?"*, *"Time difference to London?"* | Local tz database | <100ms |
| **Calendar** | *"What's on my schedule today?"*, *"Next meeting?"* | CalDAV (already in Part 2) | ~500ms |
| **Countdown** | *"How many days until Christmas?"*, *"Days until my birthday?"* | Local date math | <100ms |

#### Finance & Shopping

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Stock Prices** | *"What's AAPL at?"*, *"How's the market?"* | Yahoo Finance / Alpha Vantage | ~1s |
| **Crypto Prices** | *"What's Bitcoin at?"*, *"ETH price?"* | CoinGecko API (free) | ~1s |
| **Package Tracking** | *"Where's my package?"*, *"Any deliveries today?"* | 17track / carrier APIs | ~1-2s |
| **Gas Prices** | *"What's gas near me?"*, *"Cheapest gas?"* | GasBuddy API / local | ~1s |

#### Sports & Entertainment

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Sports Scores** | *"Did the Lakers win?"*, *"NFL scores?"* | ESPN / SportsData API | ~1s |
| **Movie/TV Lookup** | *"What's playing at the movies?"*, *"Rate for Inception?"* | TMDb API | ~1s |
| **Jokes** | *"Tell me a joke"*, *"Dad joke"* | Local joke DB (no API) | <100ms |
| **Quotes** | *"Give me a motivational quote"*, *"Quote of the day"* | Local quotes DB (no API) | <100ms |

#### Environment & Outdoors

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Sun & Moon** | *"When does the sun set?"*, *"Moon phase tonight?"* | Sunrise-Sunset API / local calc | ~500ms |
| **Air Quality** | *"What's the air quality?"*, *"Is it safe to run outside?"* | AirNow / PurpleAir API | ~1s |
| **Pollen & Allergies** | *"What's the pollen count?"*, *"Bad allergy day?"* | Pollen.com / Ambee API | ~1s |
| **UV Index** | *"Should I wear sunscreen?"*, *"UV level today?"* | OpenUV API | ~1s |
| **Tides** | *"What's the tide schedule?"*, *"High tide today?"* | NOAA Tides API | ~1s |

#### Home & Personal

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Home Energy** | *"How much power am I using?"*, *"Energy today?"* | HA energy integration | ~500ms |
| **System Status** | *"Is the server healthy?"*, *"GPU temperature?"* | Internal health checks | <200ms |
| **Contacts** | *"What's John's phone number?"*, *"Email for Dr. Smith?"* | Local contacts DB | <200ms |
| **Household Log** | *"When was the dog fed?"*, *"Did I take my meds?"* | Local tracking DB | <200ms |
| **Commute & Traffic** | *"How's traffic to work?"*, *"ETA to the office?"* | Google Maps / HERE API | ~1-2s |
| **Flight Status** | *"Is flight AA123 on time?"*, *"Gate for UA456?"* | FlightAware / AviationStack | ~1s |

#### Cooking & Recipes

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Quick Cooking Facts** | *"How long to cook chicken at 350?"*, *"Internal temp for steak?"* | Local cooking reference DB | <200ms |
| **Recipe Lookup** | *"Recipe for banana bread"*, *"How to make pasta?"* | Spoonacular / Edamam API | ~1s |
| **Measurement Conversion** | *"Tablespoons in a cup?"*, *"Grams to ounces?"* | Local math (cooking-specific) | <100ms |

---

**Total: 35 fast-path plugins** across 7 categories.

#### Plugin Management (Admin Panel)

All plugins are managed from **Admin → Plugins**:

```
┌──────────────────────────────────────────────────────────────────┐
│ 🔌 Plugins                                        [ + Custom ]   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ ── Information & Lookup ─────────────────────────────────────── │
│ ☑ Weather             OpenWeatherMap    API Key: ••••••ok  [⚙]  │
│ ☑ News Headlines      RSS + NewsAPI     API Key: ••••••ok  [⚙]  │
│ ☐ Dictionary          Free Dictionary   No key needed      [⚙]  │
│ ☐ Translation         LibreTranslate    URL: localhost:5000 [⚙] │
│ ☐ Wikipedia Summary   Wikipedia API     No key needed      [⚙]  │
│ ☐ This Day in History Local DB          No key needed      [⚙]  │
│                                                                  │
│ ── Time, Math & Utilities ───────────────────────────────────── │
│ ☑ Timers & Alarms     Local             No key needed      [⚙]  │
│ ☑ Unit Conversions    Local             No key needed      [⚙]  │
│ ☑ Calculator          Local             No key needed      [⚙]  │
│ ...                                                              │
│                                                                  │
│ ── Finance & Shopping ───────────────────────────────────────── │
│ ☐ Stock Prices        Yahoo Finance     No key needed      [⚙]  │
│ ☐ Crypto Prices       CoinGecko         No key needed      [⚙]  │
│ ...                                                              │
│                                                                  │
│ Stats: 12 enabled / 35 available    LLM bypass rate: 47%        │
│        Last 24h: 142 fast-path hits, 158 LLM fallthrough        │
└──────────────────────────────────────────────────────────────────┘
```

**Per-plugin settings (⚙):**
- API key / URL / credentials
- Response preferences (units: °F/°C, currency: USD/EUR, language)
- Custom regex patterns (power users can add their own trigger phrases)
- Priority (higher priority plugins match first)
- Cache TTL (how long to cache API responses — e.g., weather: 30 min)
- Fallback behavior: if API is down, fall through to LLM or return error

**Plugin stats dashboard:**
- Hit count per plugin (last 24h, 7d, 30d)
- Average response time per plugin
- LLM bypass rate (% of queries handled without LLM)
- Suggested new plugins from evolution job analysis
- Most common LLM fallthrough queries (candidates for new fast-path rules)

**Architecture per plugin:**
```python
class WeatherPlugin(CortexPlugin):
    plugin_id = "weather"
    plugin_type = "query"

    async def match(self, message, context) -> CommandMatch:
        # Regex patterns: "weather", "temperature", "rain", "forecast", etc.
        # Return confidence score based on match quality

    async def handle(self, message, match, context) -> CommandResult:
        # 1. Extract location (from message, user profile, or HA zone)
        # 2. Hit weather API directly
        # 3. Format natural-language response from template
        # 4. Return — never touches LLM
```

**Key design points:**
- Each plugin is opt-in — disabled plugins are never loaded
- Plugins with no API key requirement work out of the box (math, jokes, timers, etc.)
- API keys configured per-plugin in admin settings
- Response templates are natural language, not robotic ("It's 72°F and sunny in Austin — perfect day to be outside")
- Plugins can optionally enrich LLM context (e.g., weather data included when LLM handles a complex weather question)
- Evolution job tracks which queries still fall through to LLM and suggests new patterns or new plugins
- Users can write custom plugins following the `CortexPlugin` interface and drop them in

> **Implementation note:** Timers/Alarms and Calendar overlap with Part 3. The fast-path plugin handles the voice interface; Part 3 adds the full scheduler, recurrence, and multi-room delivery.

---

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
         ├─────────────────────┬──────────────────┐
         ▼                     ▼                  ▼
Part 2.5: Satellites     Part 2.7: Fast-Path   Part 4: Routines
         │               (independent)          │
         ├───────────┐                           │
         ▼           ▼                           ▼
Part 3: Alarms  Part 7: Intercom
         │           │
         ▼           ▼
Part 5: Proactive Intelligence
         │
         ▼
Part 8: Media & Entertainment

Part 6: Learning & Education ◀── Part 1 (C6 + C12)
    (can start independently after Part 1)

Part 2.7: Fast-Path Plugins ◀── Part 2 (plugin system)
    (can start independently after Part 2, no other deps)
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
