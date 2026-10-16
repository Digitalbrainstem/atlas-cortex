# Nexus Academy — Game Design Document

> "The OASIS school from Ready Player One, but real."
> An open-world adaptive learning game for ages 2-24.
> Powered by Atlas. Built by Digital Brainstem.

**Version:** 0.1 (Draft)
**Date:** April 2, 2026
**Author:** Derek Thomas + Atlas Copilot

---

## 1. Vision Statement

Nexus Academy is an open-world game where **playing IS learning**. The player
never takes a test. They never write an essay. They explore, build, craft,
solve, discover, and adventure — and in doing so, they master everything from
counting to calculus, from colors to chemistry, from letters to linear algebra.

The game world adapts to the player's interests. Love space? You'll be
calculating orbital trajectories. Love fantasy? You'll be mixing potions using
stoichiometry. Love building? You'll be designing circuits and structures. The
learning happens because the gameplay **requires** it — not because we bolted
a quiz onto a game.

Atlas doesn't run the game. Atlas is the **architect** — it reshapes the world
between sessions, personalizes quest lines, detects boredom, identifies gaps,
and ensures the player is always in the sweet spot between "I can do this" and
"this is challenging." The game itself runs standalone, no LLM needed.

---

## 2. References & Inspiration

| Source | What We Take From It |
|--------|---------------------|
| **Ready Player One (book)** | The OASIS school — a virtual world IS the classroom. Parzival learns because the world demands it. |
| **Ender's Game** | The Mind Game — adapts psychologically. The "impossible game" that's secretly evaluating you. |
| **Dora the Explorer / Blue's Clues** | For ages 2-5: character talks TO the player, asks for help, celebrates success. Breaks the fourth wall. |
| **Minecraft** | Open world, crafting, building — accidentally teaches geometry, resource management, planning. |
| **Prodigy** | Kids do 50 math problems per session because they're "battling monsters." They BEG to play. |
| **Kerbal Space Program** | Accidentally teaches orbital mechanics because the gameplay demands it. |
| **No Man's Sky** | Procedurally generated worlds — every player's experience is different. |
| **Zelda: Tears of the Kingdom** | Building/crafting system where physics IS the gameplay. |

---

## 3. The World — "The Nexus"

### 3.1 Core Concept

The Nexus is an **infinite, adaptive open world** that reshapes itself around
the player. There is no fixed map. The world generates environments based on:

- **Player's interests** (picked at start, evolves over time)
- **Player's progress** (new areas unlock as skills develop)
- **Player's engagement** (bored? the world changes. In flow? it deepens)
- **Player's choices** (explore a cave → underground theme. Find a telescope → space theme)

### 3.2 Environment Biomes (Learning Contexts)

Every biome is a **learning context** where different subjects emerge naturally:

| Biome | Primary Subjects | Example Gameplay |
|-------|-----------------|------------------|
| **The Workshop** | Engineering, Math, Physics | Build machines, design circuits, calculate loads |
| **The Alchemist's Lab** | Chemistry, Biology | Craft potions (stoichiometry), grow plants (biology), mix compounds |
| **The Observatory** | Astronomy, Physics, Math | Navigate by stars, calculate orbits, understand light/gravity |
| **The Ancient Ruins** | History, Archaeology, Language | Decode inscriptions (language arts), piece together civilizations |
| **The Living Forest** | Biology, Ecology, Earth Science | Ecosystem management, animal classification, weather patterns |
| **The Trading Post** | Economics, Math, Social Studies | Supply/demand, percentages, negotiation, currency |
| **The Architect's Domain** | Geometry, Engineering, Art | Build structures, angles, load-bearing, symmetry, design |
| **The Code Forge** | Computer Science, Logic | Program machines, debug systems, automate the world |
| **The Healer's Sanctuary** | Biology, Chemistry, Medicine | Anatomy, first aid, pharmacology (age-appropriate) |
| **The Explorer's Map** | Geography, Navigation, Culture | World travel, coordinate systems, cultures, languages |
| **The Time Rift** | History, Cause & Effect | Travel to historical periods, witness events, understand consequences |
| **The Crystal Caverns** | Geology, Chemistry, Math | Mineral classification, crystal structures, mining math |
| **The Storm Tower** | Physics, Weather, Energy | Electricity, magnetism, renewable energy, weather systems |
| **The Library of Echoes** | Language Arts, Literature, Writing | Stories, vocabulary, comprehension, creative writing through world-building |
| **The Arena** | Strategy, Game Theory, Logic | Puzzles, competitions, strategic thinking, probability |

### 3.3 World Morphing

The world is not static. It transforms based on player actions:

**Example Flow:**
1. Player is exploring a dungeon (math/logic puzzles to unlock doors)
2. They find an **ancient, broken spaceship** in a cavern
3. Game: "This ship looks ancient... but the controls still glow. Want to try to get it running?"
4. Player says yes → world gradually shifts to **space exploration**
5. Fixing the ship requires: physics (thrust), chemistry (fuel), math (trajectory)
6. Once flying, they discover a space station → new biome unlocks
7. If they get bored of space → they find a portal back, or discover a planet with forests (biology biome)

**Boredom Detection:**
- Repeated failed attempts without trying different approaches → offer hints, shift scene
- Rapid random tapping/clicking → engagement drop → introduce a surprising event
- Long idle → companion character checks in: "Hey, want to try something different?"
- Completing tasks too fast without challenge → escalate immediately

### 3.4 Gender-Neutral Design

- No "boy" or "girl" paths — all content available to all players
- Avatar is fully customizable (any appearance, any style)
- Characters in the world are diverse in every way
- Interests are tracked by behavior, never by demographic assumptions
- Combat is optional — building, exploration, and puzzle-solving are equally valid paths

---

## 4. Age Tiers & Gameplay Modes

### 4.1 Little Learner Mode (Ages 2-5)

**Inspiration:** Dora the Explorer, Blue's Clues, Sesame Street

**Interface:** Voice-driven, large touch targets, avatar companion

**The Companion:** A friendly character (customizable — bear, bunny, robot, dragon)
who talks directly to the child:

- "I see three butterflies! Can you count them with me? One... two..."
- "Oh no! The bridge needs to be the RED one! Can you tap the red bridge?"
- "We need to collect 3 stars to open the door! How many do we have? How many more?"
- "What sound does a cow make? MOO! That's right!"

**Mechanics:**
- Tap to select, drag to place, shake to activate
- Voice responses accepted (fuzzy matching for toddler speech)
- Every correct action → celebration (confetti, music, companion dances)
- Wrong answer → gentle redirect, never negative ("Hmm, let's try again! I think it's...")
- Automatic difficulty based on response patterns

**Subjects at this tier:**
- Counting (1-100, introducing place value)
- Colors, shapes (including 3D), patterns
- Letters, phonics, first words
- Animals, body parts, food, nature
- Music (rhythm, pitch, simple instruments)
- **Radians AND degrees** (angles as "turns" — full turn, half turn, quarter turn)
- Basic spatial reasoning (left/right, big/small, over/under)

### 4.2 Explorer Mode (Ages 6-10)

**Interface:** Touchscreen or mouse/keyboard, text + images + voice

**The World Opens:** Player creates their avatar, picks a starting biome,
and begins exploring. The companion evolves from "talking to you" to
"adventuring with you."

**Core Loop:**
1. **Discover** a location, puzzle, or character
2. **Understand** the challenge (presented as a game problem, not a "question")
3. **Solve** using skills (build, craft, calculate, decode, experiment)
4. **Unlock** new areas, tools, abilities, story chapters
5. **Return** to mastered areas periodically (companion: "Remember the bridge? Someone built a bigger one — want to help?")

**Example Quests:**
- "The farmer needs a fence around a 12m × 8m field. How much fencing?" (perimeter)
- "These ruins have inscriptions in a pattern. What comes next?" (language patterns, sequences)
- "The recipe needs 3/4 cup of this but we only have 1/2 cup measures" (fractions)
- "Build a catapult to launch supplies across the river. Adjust the angle!" (physics, angles)

**Subjects expand to:**
- All arithmetic operations, fractions, decimals, percentages
- Pre-algebra (variables as unknowns in crafting recipes)
- Basic science (forces, energy, living systems, materials)
- World geography and history through exploration
- Reading comprehension (journals, notes, maps found in the world)
- Creative problem-solving (multiple valid solutions to challenges)

### 4.3 Adventurer Mode (Ages 11-14)

**Interface:** Full game UI with inventory, maps, crafting system, quest log

**The World Deepens:** More complex mechanics, longer quest chains,
multi-step problems, real consequences for decisions.

**Mechanics:**
- Crafting system with real chemical formulas (inputs → outputs)
- Building system with structural physics (bridges that collapse if engineered wrong)
- Economy system with supply/demand, trade, investment
- Code Forge: write simple programs to automate machines in the game
- Time Rift missions: visit historical periods, make decisions, see outcomes

**Example Quests:**
- "Design a water purification system for the village" (chemistry, biology, engineering)
- "Navigate to the island using only stars and a compass" (trig, astronomy)
- "The ancient code is encrypted — crack it" (CS, math, pattern recognition)
- "Build a wind turbine to power the workshop" (physics, engineering, math)
- "The kingdom's economy is crashing — figure out why" (economics, data analysis)

**Subjects expand to:**
- Algebra I & II, geometry with proofs
- Biology (cells, genetics, ecology)
- Chemistry (elements, reactions, conservation of mass)
- Physics (mechanics, energy, waves)
- World history (connecting events, cause and effect)
- Computer science (algorithms, data structures as game puzzles)
- Electrical engineering basics (circuits in the game world)

### 4.4 Scholar Mode (Ages 15-18)

**Interface:** Sophisticated UI, equation rendering, graphing tools, simulation environments

**The World Challenges:** Problems require real academic knowledge. The game
world IS the textbook — but interactive, explorable, testable.

**Mechanics:**
- Physics simulations (launch rockets, design pendulums, model waves)
- Chemistry lab (virtual experiments with real reaction outcomes)
- Program full automation systems in the Code Forge
- Historical debates (take positions, argue with evidence)
- Creative engineering (design, test, iterate)

**Subjects:**
- Trigonometry, pre-calc, intro calculus
- Physics (mechanics, E&M, optics, thermo)
- Chemistry (stoichiometry, equilibrium, acids/bases, organic intro)
- AP-level history, government, economics
- Literature analysis through the Library of Echoes
- CS: algorithms, data structures, basic ML concepts

### 4.5 Master Mode (Ages 18-24)

**Interface:** Full simulation environment, research tools, collaboration features

**The World Is Yours:** The player becomes a creator in the world —
designing systems, running experiments, solving unsolved problems.

**Subjects:**
- Calculus 1 → 2 → 3, Linear Algebra, Differential Equations
- Organic Chemistry, Biochemistry
- Quantum Mechanics, Thermodynamics, Statistical Mechanics
- Advanced CS (distributed systems, ML, compilers)
- Electrical Engineering (signal processing, control systems)
- Research methodology, technical writing (building documentation in-game)

---

## 5. Mastery System — The Ender Protocol

Named after Ender's Game. The game never tells the player their "level."
They just notice the world getting more interesting.

### 5.1 Principles

1. **Mastery ≠ Getting 5 Right.** True mastery means:
   - Can solve it days later without prompting
   - Can solve variations they haven't seen
   - Can explain it (teach a companion character)
   - Can apply it in a different context (cross-subject transfer)

2. **Spaced Repetition Built Into the World.** Old challenges return naturally:
   - "The bridge you built last week? A storm damaged it. But now it needs to be longer."
   - Player uses the same skills at higher difficulty without realizing it's review.

3. **The Impossible Challenge.** Periodically, the game presents something
   slightly above the player's level. If they solve it → big reward, world
   expands dramatically. If they don't → no penalty, no mention of failure.
   Atlas logs the attempt for future calibration.

4. **Gap Detection.** When a player struggles, Atlas doesn't just repeat.
   It traces WHY:
   - Wrong calculation → is it the operation or the numbers?
   - Wrong chemistry → do they know the elements or just the process?
   - Find the prerequisite gap and create a quest that fills it.

5. **Never Stunt Progression.** If a 7-year-old is ready for algebra, give them
   algebra (wrapped in age-appropriate gameplay). The game content goes 3-4
   years ahead of where the player currently is, always pre-loaded.

### 5.2 Engagement Psychology

| Technique | Implementation |
|-----------|---------------|
| **Variable Reward Schedule** | Sometimes quests give rare items, sometimes common. Unpredictability drives engagement. |
| **Flow State** | Challenge matches skill level. Too easy → escalate. Too hard → scaffold. |
| **Curiosity Gap** | "What's behind that locked door?" — player must learn something to find out. |
| **Social Proof** | Companion mentions what "other explorers" have discovered (anonymized community data). |
| **Ownership** | Player builds things that persist. Their workshop. Their garden. Their spaceship. |
| **Identity** | Player becomes "The Architect" or "The Alchemist" based on their path. |
| **Narrative Drive** | Overarching mystery that unfolds as player progresses. |
| **Meaningful Choice** | Multiple paths to every solution. No single "right answer" to how to play. |

---

## 6. Atlas Integration — The Architect

Atlas is the **invisible game designer**. The player never interacts with Atlas
during gameplay. Atlas works between sessions.

### 6.1 What Atlas Does

| Role | When | How |
|------|------|-----|
| **World Shaping** | Between sessions | Analyzes play data, generates new quests, modifies biomes |
| **Difficulty Tuning** | Between sessions | Adjusts challenge levels, inserts impossible challenges |
| **Interest Tracking** | Continuous (logged) | Tracks time spent, biome preferences, quest types chosen |
| **Gap Analysis** | Nightly | Reviews mistakes, identifies prerequisite gaps |
| **Content Generation** | Batch (pre-computed) | Generates 3-4 years of content ahead of player's level |
| **Boredom Response** | Flagged in-session, acted on next session | Detects engagement drops, plans scene changes |
| **Parent Reports** | Weekly + on-demand | Progress summaries, strength/weakness analysis |
| **Curriculum Alignment** | Setup | Maps game progression to real educational standards (Common Core, AP, etc.) |

### 6.2 What Atlas Does NOT Do

- Does NOT generate content in real-time during gameplay
- Does NOT interrupt the player
- Does NOT make the game feel like school
- Does NOT judge or grade — the world simply responds

### 6.3 The Game Runs Standalone

The game engine must work WITHOUT Atlas connected:
- All mechanics, physics, crafting systems built-in
- Pre-generated content cached 3-4 years ahead
- Offline-capable (satellite mode)
- When Atlas reconnects, it syncs progress and refreshes content

---

## 7. Technical Architecture

### 7.1 Stack

```
┌─────────────────────────────────────────────┐
│              Nexus Academy Client            │
│  ┌─────────┐  ┌──────────┐  ┌────────────┐ │
│  │ Browser  │  │ Surface  │  │ Satellite  │ │
│  │ (any)    │  │ (kiosk)  │  │ (audio)    │ │
│  └────┬─────┘  └────┬─────┘  └─────┬──────┘ │
│       └──────────────┼──────────────┘        │
│              Game Engine (JS/TS)              │
│  ┌──────────────────────────────────────┐    │
│  │ World Engine │ Craft │ Physics │ UI  │    │
│  │ Quest System │ Audio │ Speech  │ VFX │    │
│  └──────────────────────────────────────┘    │
└──────────────────┬──────────────────────────┘
                   │ REST API (sync)
┌──────────────────┴──────────────────────────┐
│             Nexus Academy Server             │
│  ┌──────────────────────────────────────┐    │
│  │ FastAPI │ Profiles │ Progress │ Sync │    │
│  │ Content │ Mastery  │ Assets   │ Auth │    │
│  └──────────────────────────────────────┘    │
└──────────────────┬──────────────────────────┘
                   │ Atlas Module API
┌──────────────────┴──────────────────────────┐
│              Atlas Cortex                    │
│  ┌──────────────────────────────────────┐    │
│  │ LLM │ Content Gen │ Gap Analysis     │    │
│  │ TTS │ Difficulty  │ Parent Reports   │    │
│  └──────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

### 7.2 Game Engine

**Client:** HTML5 Canvas or WebGL (Phaser.js / PixiJS / Three.js)
- Runs in Chromium (Surface kiosk), any browser, or headless (audio-only satellite)
- Touch input, keyboard/mouse, voice input
- 2D for ages 2-10, optional 2.5D/3D for ages 11+
- Offline-capable with service worker + cached content

**Server:** FastAPI (Python) — same stack as Atlas Cortex
- Player profiles, progress, mastery tracking
- Content serving, asset management
- Sync endpoint for offline satellites
- Auth (child profiles, parent PIN)

### 7.3 Content Pipeline

```
Atlas LLM (nightly batch)
    │
    ▼
Quest Generator → validates → stores in Content DB
    │
    ▼
Asset Generator (Fish Audio TTS, SVG graphics, procedural)
    │
    ▼
Content Cache (3-4 years ahead per player)
    │
    ▼
Satellite Sync (push to offline devices)
```

### 7.4 Audio-Only Mode (Satellite Speakers)

For Pi/ESP32 satellites with no screen:
- Companion character talks through the speaker
- Player responds by voice
- "I'm thinking of a number between 1 and 20. I'll give you hints!"
- "Let's play a word game. What rhymes with 'cat'?"
- "I'll describe an animal. You guess what it is!"
- Full game logic, just no visual rendering

---

## 8. Screen Time

- **Default:** Unlimited
- **Parent configurable:** min/max session length, allowed hours, break reminders
- **Break reminders:** Companion suggests breaks naturally ("I'm thirsty! Let's take a water break and come back!")
- **NOT punitive** — the game doesn't lock out. It just gets gentler about suggesting breaks.

---

## 9. Profiles & Multi-Device

### 9.1 Child Profiles

- Big avatar icon on login screen (tap to select — no typing for little ones)
- Voice greeting: "Hi [name]! Ready to explore?"
- Each child has their own:
  - World state (biome, built structures, inventory)
  - Progress + mastery per subject
  - Interest profile (tracked automatically)
  - Companion character + customization
  - Achievements + unlocks

### 9.2 Multi-Device Sync

- Progress syncs to Atlas server
- Start playing on Surface Go → continue on phone browser → continue on classroom tablet
- Satellite mode: cache locally, sync when connected
- Parent sees aggregated progress across all devices

---

## 10. Subjects → Game Mechanics Mapping

| Subject | Game Mechanic | Example |
|---------|--------------|---------|
| **Counting/Arithmetic** | Resource management, inventory | "You have 47 coins. The sword costs 23. How much left?" |
| **Fractions/Decimals** | Recipe crafting, potion mixing | "The recipe needs 3/4 cup. You have 1/2 cup scoops." |
| **Algebra** | Unknown ingredients, balancing | "The machine needs X power. Each crystal gives 3. How many crystals?" |
| **Geometry** | Architecture, map navigation | "Build a roof with a 45° angle so rain slides off." |
| **Trigonometry** | Navigation, projectiles, orbits | "Calculate the angle to fire the grappling hook across the canyon." |
| **Calculus** | Rate of change, optimization | "The river is rising. How fast? When will it flood the village?" |
| **Linear Algebra** | Transformations, game physics | "Rotate the mirror array to redirect the light beam." |
| **Chemistry** | Potion/compound crafting | "Combine hydrogen and oxygen with heat → water. Now grow the plants." |
| **Physics** | Building, machines, vehicles | "Your bridge collapsed. The load exceeds the tension strength." |
| **Biology** | Ecosystem management, healing | "The forest is dying. What's missing from the food chain?" |
| **CS/Programming** | Automation, robots, Code Forge | "Program the mining robot to collect ore and return to base." |
| **Electrical Engineering** | Power systems, circuits | "Wire the village — series or parallel? How much voltage?" |
| **History** | Time Rift missions | "You're in ancient Rome. A senator asks your advice on the aqueduct." |
| **Geography** | World map exploration | "Navigate from Alexandria to Beijing using only a compass and stars." |
| **Language Arts** | Decoding, communication, storytelling | "The ancient scroll is in a cipher. Decode it to unlock the tomb." |
| **Economics** | Trading, markets, investment | "Wheat prices are dropping. Should you sell now or store for winter?" |
| **Music** | Instrument crafting, rhythm puzzles | "Play the notes in this pattern to open the harmonic gate." |
| **Art/Design** | World decoration, architecture | "Design the banner for your guild. Symmetry and color theory apply." |

---

## 11. The Name System

| Age Range | What They See | Branding |
|-----------|--------------|----------|
| 2-5 | **Little Learner** (friendly, companion-focused) | Bright colors, rounded fonts, avatar-led |
| 6-10 | **Nexus Academy: Explorer** | Adventurous, discovery-themed |
| 11-14 | **Nexus Academy: Adventurer** | Complex, quest-driven |
| 15-18 | **Nexus Academy: Scholar** | Sophisticated, simulation-heavy |
| 18-24 | **Nexus Academy: Master** | Research-grade, creation tools |

The transition between tiers is seamless — the world just grows with the player.

---

## 12. What Needs Building

### Phase 1: Foundation (MVP on Surface Go)
- Game engine (Phaser.js or PixiJS)
- Little Learner mode (ages 2-5): companion, counting, colors, shapes, letters
- Explorer mode (ages 6-10): open world, basic quests, crafting intro
- FastAPI server with profiles, progress, auth
- Fish Audio TTS for companion voice
- Fuzzy speech matching for voice answers
- Touch + visual + audio-only rendering modes
- Atlas module integration (progress sync, content generation hook)

### Phase 2: World Expansion
- Adventurer mode (ages 11-14): full crafting, Code Forge, Time Rift
- All 15+ biomes with subject-specific content
- Atlas nightly content generation pipeline
- Offline cache + sync for satellites
- Parent dashboard in Atlas admin

### Phase 3: Advanced + Polish
- Scholar and Master modes (ages 15-24)
- Equation rendering (KaTeX), graphing, simulations
- Multiplayer/collaborative quests
- Advanced Atlas difficulty tuning (Ender Protocol full implementation)
- Mobile optimization
- Curriculum standards alignment (Common Core, AP, IB mapping)

---

## 13. Open Questions

1. **Art Style:** Pixel art? Low-poly 3D? Hand-drawn? Flat design? Needs to scale from toddler to college.
2. **Multiplayer:** Should siblings be able to play together? Classroom mode?
3. **Companion Voice:** What personality? Enthusiastic friend? Wise mentor? Changes with age tier?
4. **Overarching Story:** What's the mystery that spans all tiers? Why does The Nexus exist?
5. **ESP32 audio-only:** How rich can the experience be without visuals?

---

*This document is a living design. Derek reviews and adjusts before any code is written.*
