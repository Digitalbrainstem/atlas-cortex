# Atlas Cortex — Intercom & Broadcasting (Part 7)

Room-to-room communication and whole-house announcements via the satellite speaker network.

## Overview

Atlas turns the satellite network into a whole-house intercom system with targeted messaging, broadcast announcements, and two-way communication.

## Capabilities

### Targeted Messages
```
User (in kitchen): "Tell the kids dinner is ready"
Atlas: "Which room are the kids in?"
User: "Their bedrooms"
Atlas: *plays on bedroom satellites* "Hey! Dinner's ready — come to the kitchen!"
```

### Broadcast Announcements
```
User: "Announce family meeting in 5 minutes"
Atlas: *plays on ALL satellites* "Attention everyone — family meeting in 5 minutes in the living room."
```

### Room-to-Room Call
```
User: "Call the garage"
Atlas: *opens two-way audio between kitchen and garage satellites*
Atlas: "Connected to the garage. Go ahead."
User: "Hey, are you almost done out there?"
Garage person: "Five more minutes!"
Atlas: *ends call after silence timeout*
```

### Group Pages
```
User: "Page upstairs"
Atlas: *plays on all upstairs satellites* "Message from the kitchen..."
```

## Message Types

| Type | Delivery | Two-Way | Timeout |
|------|----------|---------|---------|
| **Announce** | TTS on target satellites | No | N/A |
| **Broadcast** | TTS on ALL satellites | No | N/A |
| **Call** | Two-way audio stream | Yes | 60s silence |
| **Page** | TTS on zone satellites | No | N/A |
| **Emergency** | Max volume ALL satellites | No | N/A |

## Room Zones

```sql
CREATE TABLE IF NOT EXISTS satellite_zones (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,            -- "upstairs", "downstairs", "bedrooms"
    satellite_ids TEXT NOT NULL             -- JSON: ["bedroom-01", "bedroom-02", "office-01"]
);
```

Zones allow grouping satellites for targeted announcements: "Page upstairs", "Announce in the bedrooms".

## Message Personalization

Atlas uses the voice engine (C11) to generate appropriate TTS:
- **To children**: Friendly, warm tone — "Hey buddy! Mom says dinner's ready!"
- **To teens**: Casual, brief — "Dinner time."
- **Emergency**: Urgent, clear — "ATTENTION: Smoke detected in the kitchen. Please evacuate."

## Architecture

```
User: "Tell the kids dinner is ready"
         │
         ▼
┌────────────────────────────┐
│  Intent Parser (Layer 2)   │
│  intent: "intercom"        │
│  target: ["bedroom-01",    │
│           "bedroom-02"]    │
│  message: "Dinner is ready"│
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  Message Personalizer      │
│  • Look up target users    │
│  • Adapt tone for age      │
│  • Choose voice/emotion    │
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  TTS Engine (C11)          │
│  Generate audio            │
└────────────┬───────────────┘
             │
             ▼
┌────────────────────────────┐
│  Satellite Router          │
│  • Send to target sats     │
│  • Set volume/priority     │
└────────────────────────────┘
```

## Implementation Tasks

| Task | Description |
|------|-------------|
| P7.1 | Intercom intent parser — extract target rooms, zones, people, message |
| P7.2 | Message personalizer — adapt tone/voice for target audience |
| P7.3 | Satellite router — deliver TTS audio to specific satellites/zones |
| P7.4 | Two-way calling — bidirectional audio stream between satellites |
| P7.5 | Zone management — create/edit/delete satellite groups |
| P7.6 | Emergency broadcast — max-priority, all-satellite override |
| P7.7 | Pipeline integration — Layer 2 plugin for intercom/announce intents |
