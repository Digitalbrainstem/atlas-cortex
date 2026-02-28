# Atlas Cortex — Alarms, Timers & Reminders (Part 3)

Natural language alarm and reminder management through Atlas with multi-device delivery via satellites and Home Assistant.

## Overview

Atlas handles three distinct time-based notification types:

| Type | Trigger | Persistence | Examples |
|------|---------|-------------|----------|
| **Alarm** | Exact time, recurring | Survives reboot | "Wake me at 7am weekdays" |
| **Timer** | Countdown from now | Session-only | "Set a 15-minute timer" |
| **Reminder** | Time, location, or event | Persistent | "Remind me to call Mom at 3pm" |

## Architecture

```
┌──────────────────────────────────────────────────┐
│                Atlas Cortex Server                │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐│
│  │ Alarm Engine │  │ Timer Engine │  │ Reminder ││
│  │             │  │              │  │ Engine   ││
│  │ • Cron-like │  │ • In-memory  │  │ • DB-    ││
│  │ • DB-backed │  │ • Countdown  │  │   backed ││
│  │ • Recurring │  │ • Multi-timer│  │ • Geo    ││
│  └──────┬──────┘  └──────┬───────┘  └────┬─────┘│
│         └────────────────┼────────────────┘      │
│                          ▼                        │
│              ┌───────────────────────┐            │
│              │  Notification Router  │            │
│              │  • Which satellite?   │            │
│              │  • Which HA device?   │            │
│              │  • TTS or push?       │            │
│              └───────────┬───────────┘            │
└──────────────────────────┼───────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
    ┌────▼─────┐    ┌─────▼──────┐    ┌─────▼──────┐
    │ Satellite│    │ HA Media   │    │ Mobile     │
    │ Speaker  │    │ Player     │    │ Push (HA)  │
    └──────────┘    └────────────┘    └────────────┘
```

## Natural Language Interface

```
User: "Wake me up at 7am"
Atlas: "Alarm set for 7:00 AM. Should I play it in the bedroom?"

User: "Set a timer for 15 minutes for the pasta"
Atlas: "15-minute pasta timer started. I'll announce in the kitchen when it's done."

User: "Remind me to take medicine every day at 8am and 8pm"
Atlas: "Done — daily medicine reminders at 8:00 AM and 8:00 PM."

User: "Remind me to check the mail when I get home"
Atlas: "I'll remind you about the mail when you arrive home."

User: "What timers do I have running?"
Atlas: "You have one timer: pasta — 8 minutes remaining."

User: "Snooze for 10 minutes"
Atlas: "Snoozed. I'll wake you at 7:10 AM."

User: "Cancel all alarms"
Atlas: "All 3 alarms cancelled."
```

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS alarms (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    label         TEXT,
    time          TEXT NOT NULL,            -- "07:00" (24h format)
    days          TEXT,                     -- JSON: ["mon","tue","wed","thu","fri"] or null for one-time
    sound         TEXT DEFAULT 'default',   -- alarm sound or TTS message
    satellite_id  TEXT REFERENCES satellites(id),
    is_active     BOOLEAN DEFAULT TRUE,
    snooze_minutes INTEGER DEFAULT 10,
    next_trigger  TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS timers (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    label         TEXT,
    duration_sec  INTEGER NOT NULL,
    remaining_sec INTEGER NOT NULL,
    satellite_id  TEXT REFERENCES satellites(id),
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP NOT NULL,
    status        TEXT DEFAULT 'running'    -- running, paused, expired, cancelled
);

CREATE TABLE IF NOT EXISTS reminders (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    message       TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,            -- "time", "location", "event"
    trigger_value TEXT NOT NULL,            -- ISO timestamp, geofence ID, or event name
    recurrence    TEXT,                     -- cron expression or null
    satellite_id  TEXT REFERENCES satellites(id),
    is_active     BOOLEAN DEFAULT TRUE,
    last_triggered TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Notification Router

Determines where and how to deliver alarm/timer/reminder notifications:

1. **Satellite in user's current room** (preferred) — TTS announcement
2. **All satellites** (escalation) — if user doesn't acknowledge after 30s
3. **HA media player** — play alarm sound on configured device
4. **HA mobile push** — send notification to phone via HA companion app
5. **HA persistent notification** — dashboard notification

## Implementation Tasks

| Task | Description |
|------|-------------|
| P3.1 | Alarm engine — cron scheduler, DB persistence, recurring support |
| P3.2 | Timer engine — in-memory countdown, multi-timer, pause/resume |
| P3.3 | Reminder engine — time/location/event triggers, recurrence |
| P3.4 | Notification router — satellite/HA/push delivery |
| P3.5 | Natural language parser — extract time, duration, recurrence from user speech |
| P3.6 | Snooze/dismiss handling — voice commands during alarm |
| P3.7 | Pipeline integration — Layer 2 plugin for alarm/timer/reminder intents |
