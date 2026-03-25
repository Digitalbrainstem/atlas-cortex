# 🔌 Atlas Cortex — Plugin Guide

## How Plugins Work

Layer 2 of the 4-layer pipeline handles plugin dispatch. When a message arrives:

1. **Layer 0** assembles context (speaker, room, time)
2. **Layer 1** checks for instant answers (date, math, greetings)
3. **Layer 2** tries each registered plugin in order:
   - Calls `match(message, context)` → returns `CommandMatch`
   - If `matched=True`, calls `handle(message, match, context)` → returns `CommandResult`
   - **First match wins** — no further plugins are tried
4. **Layer 3** falls through to the LLM if no plugin matches

## Plugin Categories

### 🏠 Home

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Home Assistant | `home_assistant` | HA_URL + HA_TOKEN | Smart home device control |

### 💬 Chat

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Dictionary | `dictionary` | No | Word definitions, synonyms, etymology |
| Wikipedia | `wikipedia` | No | Quick encyclopedia lookups |
| Conversions | `conversions` | No | Unit/currency conversions |
| Cooking | `cooking` | No | Recipes, substitutions, meal planning |
| Translation | `translation` | No | Language translation |

### 📡 Integration

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Weather | `weather` | `WEATHER_API_KEY` | Forecasts and conditions |
| News | `news` | API key | Headlines and summaries |
| Stocks | `stocks` | API key | Market data and quotes |
| Sports | `sports` | API key | Scores and schedules |
| Movie | `movie` | API key | Movie info, ratings, recommendations |

### ⏰ Automation

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Scheduling | `scheduling` | No | Alarms, timers, reminders |
| Routines | `routines` | No | Automation routines |
| Daily Briefing | `daily_briefing` | No | Personalized morning summary |

### 🎵 Media

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Media | `media` | Provider auth | YouTube Music, Plex, Audiobookshelf |
| Stories | `stories` | No | Interactive story generator |
| Sound Library | `sound_library` | No | Sound effects and ambient sounds |

### 🎓 Activity

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Number Quest | `number_quest` | No | Math STEM game |
| Science Safari | `science_safari` | No | Science STEM game |
| Word Wizard | `word_wizard` | No | Language STEM game |
| Intercom | `intercom` | Satellites | Announce, broadcast, calling |

### 📚 Knowledge

| Plugin | ID | Needs Setup | Description |
|--------|----|-------------|-------------|
| Knowledge | `knowledge` | Optional (WebDAV) | Document search, knowledge base |
| Lists | `lists` | Optional (HA) | Shopping lists, to-do lists |

## Enabling & Disabling Plugins

In the admin panel (**Plugins** page):
- Each plugin shows a toggle to enable/disable
- Changes take effect on the next server restart
- Disabled plugins are skipped during Layer 2 dispatch

Via API:
```bash
# Enable
curl -X POST http://localhost:5100/admin/plugins/weather/enable \
  -H "Authorization: Bearer $TOKEN"

# Disable
curl -X POST http://localhost:5100/admin/plugins/weather/disable \
  -H "Authorization: Bearer $TOKEN"
```

## Configuring Plugins

The admin panel provides **form-based configuration** for each plugin using the `ConfigField` system. Each plugin declares its config fields with types, labels, and validation.

Supported field types:
| Type | Renders As |
|------|------------|
| `text` | Text input |
| `password` | Password input (masked) |
| `url` | URL input with validation |
| `toggle` | On/off switch |
| `select` | Dropdown with options |
| `number` | Numeric input |

Via API:
```bash
curl -X PATCH http://localhost:5100/admin/plugins/weather/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-openweathermap-key", "location": "New York"}'
```

## API Key Requirements

Plugins that need external API keys:

| Plugin | Key Variable | Free Tier |
|--------|-------------|-----------|
| Weather | `WEATHER_API_KEY` (OpenWeatherMap) | Yes — 1,000 calls/day |
| Movie | TMDB or OMDB API key | Yes |
| News | NewsAPI key | Yes — 100 req/day |
| Stocks | Alpha Vantage or similar | Yes — 5 req/min |
| Sports | Sports API key | Varies |

**What happens without an API key?** The plugin reports `health() → False` with status "Needs Setup". Messages that would match the plugin fall through to Layer 3 (LLM), which answers from its training data — less accurate but functional.

## Service Connections

| Plugin | Required Service | Configuration |
|--------|-----------------|---------------|
| Home Assistant | HA instance | `HA_URL` + `HA_TOKEN` env vars |
| Media (YouTube) | YouTube Music | OAuth device flow via admin panel |
| Media (Plex) | Plex server | `PLEX_URL` + `PLEX_TOKEN` env vars |
| Media (ABS) | Audiobookshelf | `ABS_URL` + `ABS_TOKEN` env vars |
| Knowledge | WebDAV/Nextcloud | CalDAV/WebDAV URL + credentials |
| Intercom | Satellite speakers | At least one provisioned satellite |

## Works Out of the Box

These plugins require **no configuration** — they work immediately:
- Dictionary, Wikipedia, Conversions, Cooking, Translation
- Scheduling (alarms, timers, reminders)
- Routines (automation builder)
- Daily Briefing
- Stories (interactive story generator)
- Sound Library
- STEM Games (Number Quest, Science Safari, Word Wizard)
- Lists (local backend; HA integration optional)
- Knowledge (local search; WebDAV optional)

## Health Status

Each plugin reports a health status visible in the admin panel:

| Status | Meaning |
|--------|---------|
| 🟢 **Ready** | Plugin is configured and backend is reachable |
| 🟡 **Needs Setup** | Missing required configuration (API key, URL, etc.) |
| 🔴 **Error** | Configuration exists but backend is unreachable |

The `health_message` property provides a human-readable explanation (e.g., "Weather API key not configured").

## Writing Custom Plugins

### Plugin Base Class

Every plugin extends `CortexPlugin` from `cortex/plugins/base.py`:

```python
from __future__ import annotations

from cortex.plugins.base import CortexPlugin, CommandMatch, CommandResult, ConfigField


class MyPlugin(CortexPlugin):
    plugin_id = "my_plugin"
    display_name = "My Plugin"
    plugin_type = "action"
    version = "1.0.0"
    author = "Your Name"

    config_fields = [
        ConfigField(
            key="api_key",
            label="API Key",
            field_type="password",
            required=True,
            placeholder="Enter your API key",
            help_text="Get a key from https://example.com",
        ),
        ConfigField(
            key="enabled_feature",
            label="Enable Feature X",
            field_type="toggle",
            default=True,
        ),
    ]

    @property
    def health_message(self) -> str:
        if not self._config.get("api_key"):
            return "API key not configured"
        return "Ready"

    async def setup(self, config: dict) -> bool:
        self._config = config
        return bool(config.get("api_key"))

    async def health(self) -> bool:
        return bool(self._config.get("api_key"))

    async def match(self, message: str, context: dict) -> CommandMatch:
        if "my keyword" in message.lower():
            return CommandMatch(matched=True, intent="my_action", confidence=0.9)
        return CommandMatch(matched=False)

    async def handle(self, message: str, match: CommandMatch, context: dict) -> CommandResult:
        # Do your thing
        return CommandResult(success=True, response="Here's your answer!")
```

### ConfigField Reference

```python
@dataclass
class ConfigField:
    key: str                           # Config dict key
    label: str                         # Human-readable label
    field_type: str = "text"           # text | password | url | toggle | select | number
    required: bool = False             # Must be filled before plugin activates
    placeholder: str = ""              # Input placeholder text
    help_text: str = ""                # Help text shown below input
    default: Any = None                # Default value
    options: list[dict[str, str]] = [] # For "select": [{"value": "a", "label": "Option A"}]
```

### Registration

Plugins are discovered automatically by the `PluginRegistry` when placed in the `cortex/plugins/` or `cortex/integrations/` directories.

### Best Practices

1. **Return `CommandMatch(matched=False)` quickly** — `match()` is called for every message
2. **Use `confidence` scores** — helps with disambiguation when multiple plugins could match
3. **Implement `health()` honestly** — users rely on the admin panel status
4. **Declare `config_fields`** — enables the form-based UI in the admin panel
5. **Set `health_message`** — provides actionable feedback when setup is needed
6. **Handle failures gracefully** — return `CommandResult(success=False, response="...")` rather than raising
