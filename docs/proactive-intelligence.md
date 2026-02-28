# Atlas Cortex — Proactive Intelligence (Part 5)

Atlas doesn't just respond — it anticipates needs and surfaces relevant information before being asked.

## Overview

Proactive intelligence monitors environmental data, patterns, and external sources to deliver timely, relevant notifications without being annoying.

## Proactive Categories

### Weather Intelligence
- **Storm warnings** — "There's a severe thunderstorm warning for your area. Want me to close the garage?"
- **Rain prediction** — "It looks like rain at 3pm. The windows in the bedroom are open."
- **Temperature alerts** — "It's going to drop to 28°F tonight. Should I set the thermostat to keep pipes warm?"
- **UV index** — "UV index is very high today. Don't forget sunscreen if you're going out."

### Energy Monitoring
- **Usage anomalies** — "Your electricity usage is 40% higher than usual today. The AC has been running for 12 hours."
- **Cost optimization** — "Off-peak rates start at 9pm. Want me to delay the dishwasher?"
- **Solar production** — "Solar panels are producing 8kW — great time to run the dryer."
- **Device alerts** — "The basement dehumidifier has been running non-stop for 3 days."

### Anomaly Detection
- **Unusual patterns** — "The front door was opened at 3am but no one is expected."
- **Device malfunctions** — "The kitchen freezer temperature has risen 15°F in the last hour."
- **Water leak** — "The water sensor in the laundry room just triggered."
- **Presence anomalies** — "The kids usually get home by 4pm — it's 4:30 and no one has arrived."

### Package & Delivery Tracking
- **Delivery updates** — "Your Amazon package is out for delivery, expected between 2-6pm."
- **Package arrived** — "Motion at the front door — looks like a package was delivered."
- **Pickup reminders** — "You have a package at the UPS store that expires in 2 days."

### Calendar & Schedule Awareness
- **Meeting prep** — "You have a video call in 15 minutes. Want me to set up the office?"
- **Travel time** — "Based on current traffic, you should leave in 20 minutes for your dentist appointment."
- **Birthday reminders** — "It's Sarah's birthday tomorrow."
- **Recurring events** — "Trash pickup is tomorrow morning."

## Notification Priority System

| Priority | Delivery | Examples |
|----------|----------|---------|
| **Critical** | Immediate TTS + push + HA alert | Water leak, smoke, break-in |
| **High** | TTS if user present, else push | Storm warning, unusual door open |
| **Medium** | Next natural interaction | Package delivered, energy tip |
| **Low** | Daily briefing | Weather forecast, birthday |
| **Passive** | Only if asked | Energy stats, usage patterns |

## Notification Fatigue Prevention

```python
class NotificationThrottle:
    """Prevent notification overload."""

    max_per_hour: int = 3           # Hard cap per user
    cooldown_seconds: int = 300     # Min time between notifications
    suppress_sleeping: bool = True  # No notifications during sleep hours
    suppress_dnd: bool = True       # Respect do-not-disturb mode
    escalation_only: bool = False   # Only notify if priority increased
```

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS proactive_rules (
    id            TEXT PRIMARY KEY,
    category      TEXT NOT NULL,            -- "weather", "energy", "anomaly", "package", "calendar"
    trigger_type  TEXT NOT NULL,            -- "threshold", "pattern", "schedule", "event"
    trigger_config TEXT NOT NULL,           -- JSON: {entity_id, operator, value, ...}
    message_template TEXT NOT NULL,         -- "{{entity_name}} is {{state}} — {{suggestion}}"
    priority      TEXT DEFAULT 'medium',
    cooldown_sec  INTEGER DEFAULT 3600,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS proactive_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id       TEXT REFERENCES proactive_rules(id),
    user_id       TEXT,
    message       TEXT NOT NULL,
    priority      TEXT NOT NULL,
    delivered_via TEXT,                     -- "tts", "push", "briefing", "suppressed"
    acknowledged  BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Data Sources

| Source | How | Data |
|--------|-----|------|
| **Home Assistant** | WebSocket subscription | Entity states, automations, device status |
| **Weather API** | Polling (via HA or direct) | Forecast, alerts, UV, precipitation |
| **Calendar** | CalDAV sync | Events, appointments, birthdays |
| **Email** | IMAP scan (opt-in) | Shipping confirmations, tracking numbers |
| **Energy** | HA energy dashboard entities | Usage, cost, solar production |
| **Presence** | HA person entities + satellites | Who's home, room location |

## Implementation Tasks

| Task | Description |
|------|-------------|
| P5.1 | Proactive rule engine — evaluate triggers against HA state + external data |
| P5.2 | Notification priority + throttle — prevent fatigue, respect DND/sleep |
| P5.3 | Weather intelligence — storm/rain/temperature/UV alerts |
| P5.4 | Energy monitoring — usage anomalies, cost optimization, solar awareness |
| P5.5 | Anomaly detection — pattern-based unusual activity alerts |
| P5.6 | Package tracking — email parsing for tracking numbers, delivery updates |
| P5.7 | Calendar awareness — meeting prep, travel time, birthdays |
| P5.8 | Daily briefing — morning summary of weather, calendar, reminders, news |
