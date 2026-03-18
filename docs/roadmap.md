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

### Part 2.7: Fast-Path Plugins
21 built-in Layer 2 plugins for instant responses without LLM.

| Category | Plugins |
|----------|---------|
| Information & Lookup | Weather, News Headlines, Dictionary, Translation, Wikipedia Summary |
| Utilities | Unit Conversions, Calculator |
| Finance | Stock Prices |
| Sports & Entertainment | Sports Scores, Movie/TV Lookup |
| Kids & Family | Sound Library, STEM Games (Number Quest, Science Safari, Word Wizard) |
| Cooking | Quick Cooking Facts |
| Home & Scheduling | Scheduling, Routines, Daily Briefing |
| Media & Stories | Media, Intercom, Stories |

### Part 3: Alarms, Timers & Reminders
| Phase | Module | Description |
|-------|--------|-------------|
| P3 | Scheduling | NL time parser, RRULE recurrence, notification routing, snooze/dismiss |

### Part 4: Routines & Automations
| Phase | Module | Description |
|-------|--------|-------------|
| P4 | Routines | Voice/cron/HA event triggers, templates, conversational builder |

### Part 5: Proactive Intelligence
| Phase | Module | Description |
|-------|--------|-------------|
| P5 | Proactive | Rule engine, notification throttle, weather/energy/anomaly/calendar providers, daily briefing |

### Part 6: Learning & Education
| Phase | Module | Description |
|-------|--------|-------------|
| P6 | Learning | Socratic tutoring, quiz generator (through Calculus III), 3 STEM games, progress tracking, parent reports |

### Part 7: Intercom & Broadcasting
| Phase | Module | Description |
|-------|--------|-------------|
| P7 | Intercom | Announce, broadcast, zones, two-way calling, drop-in monitoring |

### Part 8: Media & Entertainment
| Phase | Module | Description |
|-------|--------|-------------|
| P8 | Media | YouTube Music, Plex, Audiobookshelf, podcasts, local library, playback router |

### Part 9: Self-Evolution
| Phase | Module | Description |
|-------|--------|-------------|
| P9 | Evolution | Conversation analysis, model registry, LoRA training (ROCm/AMD), model scout, A/B testing, drift monitor |

### Part 10: Story Time
| Phase | Module | Description |
|-------|--------|-------------|
| P10 | Stories | Generator, character voices (Fish Audio S2), TTS hot-swap, interactive branching stories, library |

### Part 11: Atlas CLI Agent
| Phase | Module | Description |
|-------|--------|-------------|
| P11 | CLI | REPL, 31 agent tools, ReAct reasoning loop, context management, sessions |

### Part 12: Standalone Web App
| Phase | Module | Description |
|-------|--------|-------------|
| P12 | Web App | Browser chat with WebSocket streaming, voice input/output, avatar, unified dashboard |

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

**Status:** ⏸️ Core satellite functionality works; wake word reliability deferred.

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

#### MVP Blockers (must complete before release)

| Item | Description | Status |
|------|-------------|--------|
| **Wake Word Reliability** | Cold-start detection needs 2-3 tries on ReSpeaker 2-mic (scores 0.26-0.38 vs threshold 0.25). Investigate model fine-tuning, mic AGC, or alternative engine. | 🔴 Pending |
| **Button Config in Admin UI** | Add button_mode dropdown (toggle/press/hold) to satellite detail view. Backend API field exists. | 🟡 Pending |

#### Conversational Engine (pre-MVP)

Transform the voice pipeline from a rigid state machine (wake → listen → process → speak → idle) into a continuous, natural conversational flow.

| Phase | Feature | Description | Depends On |
|-------|---------|-------------|------------|
| CE-1 | **Streaming STT + Extended Listening** | Keep mic active longer after wake word. Run local phrase/sentence boundary detection (not LLM) to segment speech in real-time. Only send complete phrases to pipeline. Don't cut off slow speakers. | — |
| CE-2 | **Multi-Question Queuing** | Queue multiple detected phrases and process in order. If user asks 3 questions before first answer, queue all 3. Filler may be unnecessary if TTS finishes before user stops talking. | CE-1 |
| CE-3 | **Barge-In / Interruption** | Keep mics hot during TTS playback. Detect "stop", "nevermind", or user speaking over Atlas. Kill current TTS stream immediately. Natural conversation: if someone talks over you, you stop. | — |
| CE-4 | **Conversational Pause & Pivot** | When user interrupts Atlas mid-speech, pause TTS (don't kill). Listen to what they say. If it's a correction or new question, pivot to new response. If it's just "uh huh", resume after a few seconds. | CE-3 |
| CE-5 | **Adaptive Dual-State LEDs** | Outer LEDs show listening state, inner LEDs show activity/processing state. Auto-configure layout by satellite hardware (ReSpeaker 2-mic has 3 LEDs, 4-mic has 12, etc). Satellite reports LED count in capabilities. | — |

**Architecture notes:**
- CE-1 and CE-3 are independent — can be built in parallel
- CE-2 builds on CE-1's phrase detection; CE-4 builds on CE-3's barge-in detection
- CE-5 (LEDs) is independent and can ship with any phase
- Filler cache (18 pre-generated phrases, already implemented) becomes less important as CE-1/CE-2 reduce dead time

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

#### Kids & Family

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Sound Library** | *"What does a dolphin sound like?"*, *"Play a train sound"*, *"What does a cricket sound like?"* | Local audio clips (pre-downloaded, kid-safe) | <200ms |

**Sound Library categories:**
- **Nature — Animals:** dog, cat, cow, horse, pig, chicken, rooster, sheep, goat, duck, frog, owl, eagle, wolf, whale, dolphin, elephant, lion, monkey, parrot
- **Nature — Insects & Small Creatures:** cricket, bee, cicada, mosquito, grasshopper
- **Nature — Environment:** rain, thunder, ocean waves, river, wind, waterfall, campfire crackling
- **Human-Made World:** train, airplane, fire truck siren, submarine sonar, clock ticking, doorbell, church bells, steam engine whistle, spaceship launch countdown, old car horn (ahooga)

All sounds are kid-friendly, pre-downloaded audio clips served locally — no API needed. Plays through the nearest satellite speaker or HA media player. Content-tier safe for all ages.

**Follow-up learning:** After playing a sound, follow-up questions like *"Why do dolphins make that sound?"*, *"Tell me more about elephants"*, or *"Where do crickets live?"* are detected as probing questions. The plugin injects the sound context (animal/object just played) into the LLM request so the response is age-appropriate and anchored to what the child just heard. This bridges the fast-path plugin with the LLM — the clip is instant, the curiosity follow-up gets a rich answer.

**Guided discovery mode:** When a sound triggers curiosity (either via follow-up or proactively after the clip), Atlas can initiate a short conversational learning session. For example: *🐬 [dolphin click plays]* → *"Did you know dolphins use those clicks like sonar to find fish in the dark? Can you think of another animal that uses sound to see?"* Atlas asks age-appropriate questions, gives encouraging feedback, and can chain to related sounds (*"Want to hear what a bat sounds like?"*). The session stays conversational — the child leads, Atlas follows their curiosity. Ties into Part 6 (Learning & Education) and the user's content tier for age-appropriate depth.

**Speculative response caching:** When Atlas asks a question (e.g., *"Can you think of another animal that uses sound to see?"*), it already knows the likely answers. While the child is thinking:
1. **Pre-generate TTS** for the expected correct answers (e.g., "A bat! That's exactly right!" / "A whale! Great thinking!") and cache the audio
2. **Pre-generate encouragement paths** for common wrong-but-close answers
3. **Pre-generate a gentle nudge** if they need a hint (*"Here's a clue — it flies at night!"*)

When the child responds, a **lightweight answer evaluator** (not full LLM) classifies the response:
- ✅ **Expected correct** — play pre-cached response instantly (<200ms). E.g., *"A bat! That's exactly right!"*
- ✅ **Partial / nuanced** — answer is in the right direction but needs context (<200ms, also pre-cached). E.g., *"A whale!"* → *"Great answer! Some whales like sperm whales do use clicks to find things — they're cousins of dolphins. Want to hear what a sperm whale sounds like?"* Chains to related sounds and teaches the nuance without making them feel wrong.
- ✅ **Unexpected but valid** (e.g., *"a submarine!"* — not an animal, but genuinely uses sonar) — LLM evaluates with the question context, gives credit: *"That's really creative! Submarines do use sonar. Can you think of an animal that does it?"*
- ❌ **Wrong answer** — gentle encouragement + hint, never "wrong": *"Hmm, cats are amazing but they use their whiskers more than sound. Think about an animal that flies at night..."*
- 🤷 **Off-topic / unclear** — re-engage: *"That's interesting! But back to our sound question..."*

Each question's answer set is structured with tiers: `exact` (bat), `partial` (whale — correct idea, needs nuance), `common_wrong` (cat — predictable miss), all with pre-generated TTS. The evaluator uses: (1) pre-built answer sets per question (fast-path match for exact/partial/wrong), (2) semantic similarity for unexpected-but-valid detection, and (3) LLM fallback only when the fast checks are inconclusive. Goal: most responses are instant from cache, LLM only fires for surprising answers worth exploring.

#### Cooking & Recipes

| Plugin | Example Queries | Data Source | Expected Latency |
|--------|----------------|-------------|------------------|
| **Quick Cooking Facts** | *"How long to cook chicken at 350?"*, *"Internal temp for steak?"* | Local cooking reference DB | <200ms |
| **Recipe Lookup** | *"Recipe for banana bread"*, *"How to make pasta?"* | Spoonacular / Edamam API | ~1s |
| **Measurement Conversion** | *"Tablespoons in a cup?"*, *"Grams to ounces?"* | Local math (cooking-specific) | <100ms |

---

**Total: 36 fast-path plugins** across 8 categories.

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
    supports_learning = True  # opt into guided discovery for "why" questions

    async def match(self, message, context) -> CommandMatch:
        # Regex patterns: "weather", "temperature", "rain", "forecast", etc.
        # Return confidence score based on match quality

    async def handle(self, message, match, context) -> CommandResult:
        intent = classify_intent(message)  # inform / learn / explore
        if intent == "inform":
            # 1. Extract location, hit weather API, format response
            # 2. Return — done, no follow-up
        elif intent == "learn":
            # 1. Answer the question ("Rain happens because...")
            # 2. Inject guided discovery: ask a question, pre-cache answer branches
            # 3. Enter learning mode for this topic
        else:  # explore
            # Enrich context with weather data, fall through to LLM
```

**Key design points:**
- Each plugin is opt-in — disabled plugins are never loaded
- Plugins with no API key requirement work out of the box (math, jokes, timers, etc.)
- API keys configured per-plugin in admin settings
- Response templates are natural language, not robotic ("It's 72°F and sunny in Austin — perfect day to be outside")
- Plugins can optionally enrich LLM context (e.g., weather data included when LLM handles a complex weather question)
- Evolution job tracks which queries still fall through to LLM and suggests new patterns or new plugins
- Users can write custom plugins following the `CortexPlugin` interface and drop them in

**Query intent detection (inform vs. learn):**

Every fast-path plugin classifies the query intent *before* responding. This is a lightweight, fast-path check (regex + keyword signals, no LLM) that determines how to handle the response:

| Intent | Signal Words | Example | Behavior |
|--------|-------------|---------|----------|
| **Inform** | direct questions, "what is", "how much", "when" | *"What's the weather?"*, *"Recipe for pasta"*, *"AAPL stock price?"* | Answer directly, done. No follow-up. |
| **Learn** | "why", "how does", "explain", "tell me about", "what makes" | *"Why is the sky blue?"*, *"How does sonar work?"*, *"What makes thunder?"* | Answer + guided discovery (ask questions, pre-cache responses, teach) |
| **Explore** | "what if", "could", "imagine", follow-up after a learn | *"What if dolphins lived on land?"*, *"Could a bat find a submarine?"* | Creative LLM response, open-ended conversation |

**How it works:**
1. Plugin `match()` fires as normal and identifies the topic (weather, animal, recipe, etc.)
2. A shared `classify_intent()` utility inspects the query for learn signals — this runs in the same fast-path, no extra latency
3. **Inform intent** → plugin returns the answer directly (current behavior)
4. **Learn intent** → plugin returns the answer AND injects a guided discovery prompt into the conversation context. Atlas asks a follow-up question, pre-caches likely answer branches (exact/partial/wrong), and enters learning mode
5. **Explore intent** → falls through to LLM with the plugin's topic context for rich, creative responses

**Examples across plugins:**
- Weather: *"What's the forecast?"* → inform (just answer). *"Why does it rain?"* → learn (guided discovery about the water cycle)
- Stocks: *"What's AAPL at?"* → inform. *"Why do stock prices go up and down?"* → learn
- Cooking: *"Recipe for banana bread"* → inform. *"Why does bread rise?"* → learn (yeast, CO2, guided questions)
- Sound Library: *"Play a dolphin sound"* → inform (play clip). *"How do dolphins talk to each other?"* → learn

This is a **cross-cutting concern** — the `classify_intent()` function and guided discovery framework are shared infrastructure that all plugins opt into. Plugins that are purely action-based (timers, alarms, unit conversions) skip learning mode since there's no knowledge to explore. Each plugin declares whether it supports learning mode in its registration.

> **Implementation note:** Timers/Alarms and Calendar overlap with Part 3. The fast-path plugin handles the voice interface; Part 3 adds the full scheduler, recurrence, and multi-room delivery.

---

### Part 3: Alarms, Timers & Reminders ✅

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

### Part 4: Routines & Automations ✅

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

### Part 5: Proactive Intelligence ✅

> **Design doc:** [proactive-intelligence.md](proactive-intelligence.md)

| Feature | Example |
|---------|---------|
| **Weather awareness** | *"It's going to rain — should I close the garage?"* |
| **Energy optimization** | *"AC has been on 8 hours — house is at 68°F"* |
| **Anomaly detection** | *"Basement humidity is unusually high"* |
| **Package tracking** | *"Your Amazon order arrives tomorrow 2-6pm"* |
| **Activity patterns** | *"You usually start the coffee maker by now — want me to?"* |

---

### Part 6: Learning & Education ✅

> **Design doc:** [learning-education.md](learning-education.md)

| Feature | Example |
|---------|---------|
| **Homework help** | Age-appropriate explanations, never gives direct answers |
| **Interactive quizzes** | *"Quiz me on state capitals"* with score tracking |
| **Science mode** | *"What happens if we mix baking soda and vinegar?"* |
| **Language learning** | Vocabulary drills, pronunciation practice, flashcards |
| **Reading companion** | Read-along mode for young children |

---

### Part 7: Intercom & Broadcasting ✅

> **Design doc:** [intercom-broadcasting.md](intercom-broadcasting.md)

| Feature | Example |
|---------|---------|
| **Targeted broadcast** | *"Tell the kids dinner is ready"* → children's rooms |
| **Whole-house** | *"Announce: family meeting in 5 minutes"* → all satellites |
| **Room-to-room** | *"Atlas, talk to the garage"* → opens intercom channel |
| **Emergency alert** | Smoke/CO detection → all speakers: *"Fire alarm triggered"* |

---

### Part 8: Media & Entertainment ✅

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

## 🔮 Future — Parts 13–18

### Part 13: Legacy Protocol 🔲
- Backward compatibility for older integrations and protocols

### Part 14: Household Management 🔲
- Pet care reminders: feeding schedules, vet appointments, medication
- Cooking assistant: step-by-step recipes with integrated timers
- Inventory tracking: *"We're running low on milk"* → auto-add to grocery list
- Chore assignments: fair rotation tracking for household members

### Part 15: Security & Monitoring 🔲
- Camera feed summaries: *"Who was at the front door?"*
- Motion alert intelligence: distinguishes pets, packages, people
- Door/window status: *"Is the garage door open?"*
- Visitor history: *"When did the kids get home from school?"*

### Part 16: Health & Wellness 🔲
- Medication reminders with confirmation tracking
- Sleep pattern analysis from presence sensors
- Air quality monitoring and ventilation suggestions
- Exercise reminders based on activity patterns

### Part 17: Multi-Language Support 🔲
- Real-time language detection and switching
- Per-user language preferences
- Translation assistance between household members

### Part 18: Visual Media & Casting 🔲
- Screen casting and photo display
- Visual content on smart displays
- Camera integration

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
Part 2.5: Satellites     Part 2.7: Plugins   Part 4: Routines
  ⏸️ Wake word deferred   ✅ COMPLETE (21)     ✅ COMPLETE
         │                                        │
         ├───────────┐                             │
         ▼           ▼                             ▼
Part 3: Alarms  Part 7: Intercom          Part 5: Proactive
  ✅ COMPLETE     ✅ COMPLETE               ✅ COMPLETE
         │           │
         ▼           ▼
Part 8: Media & Entertainment ──────────────────────── ✅ COMPLETE

Part 6: Learning & Education ◀── Part 1 ──────────── ✅ COMPLETE
Part 9: Self-Evolution ◀── Part 1 + Part 2 ───────── ✅ COMPLETE
Part 10: Story Time ◀── Part 1 ───────────────────── ✅ COMPLETE
Part 11: Atlas CLI ◀── Part 1 ────────────────────── ✅ COMPLETE
Part 12: Standalone Web App ◀── Part 11 ──────────── ✅ COMPLETE

Part 13: Legacy Protocol ──────────────────────────── 🔲 Planned
Part 14: Household Management ─────────────────────── 🔲 Planned
Part 15: Security & Monitoring ────────────────────── 🔲 Planned
Part 16: Health & Wellness ────────────────────────── 🔲 Planned
Part 17: Multi-Language Support ───────────────────── 🔲 Planned
Part 18: Visual Media & Casting ───────────────────── 🔲 Planned
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
