# Atlas Cortex — Routines & Automations (Part 4)

User-defined routines that combine multiple actions into single voice triggers, built conversationally through Atlas.

## Overview

Routines are named sequences of actions that trigger from voice commands, schedules, or events. Unlike Home Assistant automations (YAML/UI-driven), Atlas routines are **built through conversation** and execute across both Atlas and HA.

## Routine Types

| Type | Trigger | Examples |
|------|---------|----------|
| **Voice** | Spoken phrase | "Good morning", "Movie time", "I'm leaving" |
| **Schedule** | Time/cron | Every weekday at 6:30 AM |
| **Event** | HA state change | Front door unlocked, motion detected |
| **Condition** | Contextual | Sunrise, sunset, temperature threshold |
| **Composite** | Multiple | Weekdays at 6:30 AM AND only if I'm home |

## Conversational Routine Builder

```
User: "When I say 'movie time', dim the living room to 20% and turn on the TV"
Atlas: "Got it — 'Movie Time' routine created:
  1. Dim living room lights to 20%
  2. Turn on living room TV
  Want me to add anything else to it?"

User: "Also close the blinds"
Atlas: "Added. Movie Time now has 3 actions. Should I run it now to test?"

User: "Yeah, test it"
Atlas: *executes routine* "Done — lights dimmed, TV on, blinds closing. How's that?"
```

## Routine Schema

```sql
CREATE TABLE IF NOT EXISTS routines (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,
    trigger_type  TEXT NOT NULL,            -- "voice", "schedule", "event", "condition"
    trigger_value TEXT NOT NULL,            -- phrase, cron, HA entity/state, expression
    is_active     BOOLEAN DEFAULT TRUE,
    run_count     INTEGER DEFAULT 0,
    last_run      TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS routine_actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id    TEXT REFERENCES routines(id) ON DELETE CASCADE,
    seq_order     INTEGER NOT NULL,
    action_type   TEXT NOT NULL,            -- "ha_service", "tts_speak", "delay", "condition"
    action_config TEXT NOT NULL,            -- JSON: {domain, service, entity_id, data}
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS routine_conditions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id    TEXT REFERENCES routines(id) ON DELETE CASCADE,
    condition_type TEXT NOT NULL,           -- "time_range", "presence", "state", "weather"
    condition_config TEXT NOT NULL,         -- JSON: {start: "06:00", end: "22:00"}
    is_and        BOOLEAN DEFAULT TRUE     -- AND vs OR with other conditions
);
```

## Built-in Routine Templates

| Routine | Default Actions |
|---------|----------------|
| **Good Morning** | Lights on (gradual), weather briefing, calendar summary, coffee maker on |
| **Good Night** | Lights off, doors locked, alarm set, thermostat night mode |
| **I'm Leaving** | Lights off, thermostat away, security armed, garage closed |
| **I'm Home** | Lights on (welcome), thermostat comfort, disarm security, play music |
| **Movie Time** | Dim lights, TV on, blinds closed, volume set |
| **Dinner Time** | Kitchen lights bright, dining lights warm, announce to family |

## Execution Engine

```python
class RoutineEngine:
    async def execute(self, routine_id: str, context: dict) -> RoutineResult:
        """Execute routine actions sequentially with condition checks."""
        routine = self.get_routine(routine_id)

        # Check conditions first
        if not await self.check_conditions(routine):
            return RoutineResult(skipped=True, reason="Conditions not met")

        results = []
        for action in self.get_actions(routine_id):
            match action.action_type:
                case "ha_service":
                    result = await self.ha_client.call_service(**action.config)
                case "tts_speak":
                    result = await self.tts_speak(action.config["text"])
                case "delay":
                    await asyncio.sleep(action.config["seconds"])
                case "condition":
                    if not await self.evaluate(action.config):
                        break  # Stop execution
            results.append(result)

        return RoutineResult(success=True, actions_executed=len(results))
```

## Implementation Tasks

| Task | Description |
|------|-------------|
| P4.1 | Routine engine — sequential action execution with conditions |
| P4.2 | Conversational builder — NLP to create/edit routines via chat |
| P4.3 | Built-in templates — seed common routines with customizable defaults |
| P4.4 | Schedule triggers — cron-based routine scheduling |
| P4.5 | Event triggers — HA state change subscription for auto-trigger |
| P4.6 | Pipeline integration — Layer 2 plugin for routine voice triggers |
| P4.7 | Routine management — list, edit, delete, enable/disable routines |
