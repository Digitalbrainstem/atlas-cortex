# 🔌 Atlas Cortex — API Reference

Atlas Cortex exposes an **OpenAI-compatible API** on port 5100. Any client that works with OpenAI or Ollama works with Atlas.

> **Auto-generated docs** — FastAPI generates interactive API docs at:
> - **Swagger UI**: `http://localhost:5100/docs`
> - **ReDoc**: `http://localhost:5100/redoc`

---

## Public API

No authentication required. These endpoints are compatible with any OpenAI client library.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check — returns `{"status": "ok"}` |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completions (streaming SSE or JSON) |
| `GET` | `/v1/models` | List available models (returns `atlas-cortex`) |
| `GET` | `/v1/audio/voices` | List available TTS voices across all providers |
| `POST` | `/v1/audio/speech` | Text-to-speech synthesis — returns WAV audio |

### Examples

#### Health Check

```bash
curl http://localhost:5100/health
```

#### Streaming Chat Completion

```bash
curl http://localhost:5100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "atlas-cortex",
    "stream": true,
    "messages": [
      {"role": "user", "content": "What time is it?"}
    ]
  }'
```

#### Non-Streaming Chat Completion

```bash
curl http://localhost:5100/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "atlas-cortex",
    "stream": false,
    "messages": [
      {"role": "user", "content": "Tell me a joke."}
    ]
  }'
```

#### List Voices

```bash
curl http://localhost:5100/v1/audio/voices
```

#### Text-to-Speech Synthesis

```bash
curl http://localhost:5100/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Hello, welcome to Atlas.",
    "voice": "af_bella"
  }' \
  --output speech.wav
```

---

## Chat API

User-facing endpoints for the chat SPA. No admin authentication required.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/chat/users` | List chat user profiles (no secrets exposed) |
| `POST` | `/api/chat/auth` | Authenticate user (PIN, password, passkey, or none) |
| `GET` | `/api/chat/session` | Validate an existing session token |
| `GET` | `/chat` | Public chat SPA page (served from `admin/dist/chat.html`) |

---

## WebSocket Endpoints

Real-time communication channels for voice, chat, and satellite devices.

| Path | Description |
|------|-------------|
| `/ws/chat` | Browser chat — streams pipeline responses + voice I/O |
| `/ws/satellite` | Satellite discovery and communication |
| `/ws/avatar` | Avatar display control (expressions, lip-sync) |

---

## Avatar API

Public endpoints for the avatar display system.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/avatar/config` | Avatar feature flags |
| `GET` | `/avatar` | Fullscreen avatar display page |
| `GET` | `/avatar/skin/{skin_id}.svg` | Serve avatar skin SVG file |
| `GET` | `/avatar/skin/expressions.json` | Shared expression mouth library |
| `GET` | `/avatar/web-satellite.js` | Web satellite overlay JavaScript |

---

## Admin API

All admin endpoints are under `/admin` and require **JWT authentication**.

### Authentication

Get a token via `/admin/auth/login`, then pass it as a Bearer token:

```bash
TOKEN=$(curl -s http://localhost:5100/admin/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "atlas-admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl http://localhost:5100/admin/dashboard \
  -H "Authorization: Bearer $TOKEN"
```

---

### Auth (`/admin/auth/`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/auth/login` | Login with username/password, returns JWT |
| `GET` | `/admin/auth/me` | Get current admin user info |
| `POST` | `/admin/auth/change-password` | Change admin password |

### Dashboard

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/dashboard` | Dashboard stats (users, interactions, safety, devices) |

### Users (`/admin/users/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/users` | List all users with pagination |
| `GET` | `/admin/users/{user_id}` | Get user details (profile, parental, topics) |
| `POST` | `/admin/users` | Create a new user profile |
| `PATCH` | `/admin/users/{user_id}` | Update user profile fields |
| `POST` | `/admin/users/{user_id}/age` | Set user age by birth year/month |
| `DELETE` | `/admin/users/{user_id}` | Delete user and associated data |
| `GET` | `/admin/users/{user_id}/parental` | Get parental controls |
| `POST` | `/admin/users/{user_id}/parental` | Set/update parental controls |
| `DELETE` | `/admin/users/{user_id}/parental` | Remove parental controls |
| `GET` | `/admin/users/{user_id}/auth` | Get user chat auth configuration |
| `POST` | `/admin/users/{user_id}/auth` | Set user auth method (PIN/password/passkey) |
| `GET` | `/admin/users/{user_id}/trusted-devices` | List trusted devices |
| `DELETE` | `/admin/users/{user_id}/trusted-devices/{fingerprint}` | Remove a trusted device |

### Safety (`/admin/safety/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/safety/events` | List safety guardrail events |
| `GET` | `/admin/safety/patterns` | List known jailbreak patterns |
| `POST` | `/admin/safety/patterns` | Add a jailbreak pattern |
| `DELETE` | `/admin/safety/patterns/{pattern_id}` | Delete a jailbreak pattern |

### Devices & Voice (`/admin/devices/`, `/admin/voice/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/voice/speakers` | List speaker profiles |
| `DELETE` | `/admin/voice/speakers/{speaker_id}` | Delete speaker profile |
| `PATCH` | `/admin/voice/speakers/{speaker_id}` | Update speaker profile |
| `GET` | `/admin/devices` | List HA devices (filterable by domain) |
| `GET` | `/admin/devices/patterns` | List command patterns |
| `PATCH` | `/admin/devices/patterns/{pattern_id}` | Update command pattern |
| `DELETE` | `/admin/devices/patterns/{pattern_id}` | Delete command pattern |

### System (`/admin/system/`, `/admin/settings/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/system/hardware` | Hardware profile and GPU details |
| `GET` | `/admin/system/models` | Model configuration by role |
| `GET` | `/admin/system/services` | Discovered services |
| `GET` | `/admin/system/backups` | Backup log entries |
| `GET` | `/admin/system/interactions` | Interactions with user/layer filtering |
| `GET` | `/admin/settings` | Get all system settings |
| `PUT` | `/admin/settings/{key}` | Set a system setting |

### TTS (`/admin/tts/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/tts/voices` | List TTS voices from all providers |
| `PUT` | `/admin/tts/default_voice` | Set system-wide default voice |
| `POST` | `/admin/tts/regenerate` | Regenerate pre-cached TTS audio |
| `POST` | `/admin/tts/preview` | Preview TTS synthesis |
| `POST` | `/admin/tts/filler_preview` | Preview filler phrase |

### Avatar (`/admin/avatar/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/avatar/skins` | List avatar skins |
| `POST` | `/admin/avatar/skins` | Create avatar skin |
| `GET` | `/admin/avatar/skins/{skin_id}` | Get avatar skin |
| `DELETE` | `/admin/avatar/skins/{skin_id}` | Delete avatar skin |
| `GET` | `/admin/avatar/default` | Get default avatar skin |
| `PUT` | `/admin/avatar/default/{skin_id}` | Set default avatar skin |
| `GET` | `/admin/avatar/assignments` | List user→skin assignments |
| `PUT` | `/admin/avatar/assignments/{user_id}` | Assign skin to user |
| `DELETE` | `/admin/avatar/assignments/{user_id}` | Remove user skin assignment |
| `GET` | `/admin/avatar/audio-route/{room}` | Get audio routing for room |
| `PUT` | `/admin/avatar/audio-route/{room}` | Set audio routing for room |
| `GET` | `/admin/avatar/flags` | List feature flags |
| `PATCH` | `/admin/avatar/flags` | Update a feature flag |
| `POST` | `/admin/avatar/flags/dev-mode` | Toggle dev mode |
| `POST` | `/admin/avatar/flags/reset` | Reset flags to defaults |

### Plugins (`/admin/plugins/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/plugins` | List all plugins with status |
| `POST` | `/admin/plugins/{plugin_id}/enable` | Enable a plugin |
| `POST` | `/admin/plugins/{plugin_id}/disable` | Disable a plugin |
| `PATCH` | `/admin/plugins/{plugin_id}/config` | Update plugin configuration |
| `POST` | `/admin/plugins/{plugin_id}/health` | Force health check |

### Satellites (`/admin/satellites/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/satellites` | List all satellites |
| `GET` | `/admin/satellites/announced` | List self-announced satellites |
| `GET` | `/admin/satellites/{satellite_id}` | Get satellite details |
| `POST` | `/admin/satellites/discover` | Trigger network scan |
| `POST` | `/admin/satellites/add` | Manually add satellite by IP |
| `POST` | `/admin/satellites/{satellite_id}/detect` | Detect satellite hardware |
| `POST` | `/admin/satellites/{satellite_id}/provision` | Start provisioning |
| `PATCH` | `/admin/satellites/{satellite_id}` | Update satellite config |
| `POST` | `/admin/satellites/{satellite_id}/restart` | Restart satellite agent |
| `POST` | `/admin/satellites/{satellite_id}/identify` | Blink LEDs for identification |
| `POST` | `/admin/satellites/{satellite_id}/test` | Run audio test |
| `POST` | `/admin/satellites/{satellite_id}/command` | Send command |
| `PATCH` | `/admin/satellites/{satellite_id}/led_config` | Update LED patterns |
| `GET` | `/admin/satellites/{satellite_id}/led_config` | Get LED configuration |
| `DELETE` | `/admin/satellites/{satellite_id}` | Remove satellite |

### Scheduling (`/admin/scheduling/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/scheduling/alarms` | List all alarms |
| `POST` | `/admin/scheduling/alarms` | Create alarm |
| `DELETE` | `/admin/scheduling/alarms/{alarm_id}` | Delete alarm |
| `POST` | `/admin/scheduling/alarms/{alarm_id}/enable` | Enable alarm |
| `POST` | `/admin/scheduling/alarms/{alarm_id}/disable` | Disable alarm |
| `GET` | `/admin/scheduling/timers` | List active timers |
| `DELETE` | `/admin/scheduling/timers/{timer_id}` | Cancel timer |
| `GET` | `/admin/scheduling/reminders` | List reminders |
| `DELETE` | `/admin/scheduling/reminders/{reminder_id}` | Delete reminder |

### Routines (`/admin/routines/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/routines` | List routines with steps, triggers, runs |
| `POST` | `/admin/routines` | Create routine |
| `GET` | `/admin/routines/templates` | List routine templates |
| `POST` | `/admin/routines/templates/{template_id}/instantiate` | Instantiate from template |
| `GET` | `/admin/routines/{routine_id}` | Get routine details |
| `DELETE` | `/admin/routines/{routine_id}` | Delete routine |
| `POST` | `/admin/routines/{routine_id}/enable` | Enable routine |
| `POST` | `/admin/routines/{routine_id}/disable` | Disable routine |
| `POST` | `/admin/routines/{routine_id}/run` | Execute routine immediately |
| `POST` | `/admin/routines/{routine_id}/steps` | Add step |
| `DELETE` | `/admin/routines/{routine_id}/steps/{step_id}` | Remove step |
| `POST` | `/admin/routines/{routine_id}/triggers` | Add trigger |
| `DELETE` | `/admin/routines/{routine_id}/triggers/{trigger_id}` | Remove trigger |

### Evolution (`/admin/evolution/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/evolution/profiles` | Emotional profiles with rapport scores |
| `GET` | `/admin/evolution/logs` | Evolution logs |
| `GET` | `/admin/evolution/mistakes` | Recorded mistakes |
| `PATCH` | `/admin/evolution/mistakes/{mistake_id}` | Update mistake status |
| `GET` | `/admin/evolution/runs` | List evolution runs |
| `GET` | `/admin/evolution/runs/{run_id}` | Get evolution run details |
| `POST` | `/admin/evolution/analyze` | Trigger analysis run |
| `GET` | `/admin/evolution/models` | List model registry |
| `POST` | `/admin/evolution/models/{model_id}/promote` | Promote model |
| `POST` | `/admin/evolution/models/{model_id}/retire` | Retire model |
| `GET` | `/admin/evolution/drift` | Personality drift report |
| `GET` | `/admin/evolution/metrics` | Quality metrics over time |

### LoRAs (`/admin/loras/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/loras` | List discovered LoRAs |
| `POST` | `/admin/loras/compose` | Compose a LoRA into Ollama model |
| `POST` | `/admin/loras/compose-all` | Compose all LoRAs |
| `DELETE` | `/admin/loras/{domain}` | Remove composed LoRA model |
| `GET` | `/admin/loras/domains` | List available domains |

### Stories (`/admin/stories/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/stories` | List stories (filterable by genre/age) |
| `POST` | `/admin/stories` | Create story |
| `GET` | `/admin/stories/progress` | All users' story progress |
| `GET` | `/admin/stories/characters/{story_id}` | List characters/voices |
| `POST` | `/admin/stories/characters/{story_id}` | Assign character voice |
| `GET` | `/admin/stories/{story_id}` | Get story details |
| `DELETE` | `/admin/stories/{story_id}` | Delete story |
| `POST` | `/admin/stories/{story_id}/approve` | Approve story |

### Learning (`/admin/learning/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/learning/progress` | All users' learning progress |
| `GET` | `/admin/learning/progress/{user_id}` | User learning progress |
| `GET` | `/admin/learning/sessions` | Recent learning sessions |
| `GET` | `/admin/learning/report/{user_id}` | Parent-friendly report |
| `GET` | `/admin/learning/leaderboard` | Top streaks and scores |

### Proactive (`/admin/proactive/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/proactive/rules` | List proactive rules |
| `POST` | `/admin/proactive/rules` | Create rule |
| `DELETE` | `/admin/proactive/rules/{rule_id}` | Delete rule |
| `POST` | `/admin/proactive/rules/{rule_id}/enable` | Enable rule |
| `POST` | `/admin/proactive/rules/{rule_id}/disable` | Disable rule |
| `GET` | `/admin/proactive/events` | Recent proactive events |
| `GET` | `/admin/proactive/briefing` | Daily briefing preview |
| `GET` | `/admin/proactive/preferences/{user_id}` | User notification preferences |
| `PATCH` | `/admin/proactive/preferences/{user_id}` | Update notification preferences |

### Intercom (`/admin/intercom/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/intercom/zones` | List intercom zones |
| `POST` | `/admin/intercom/zones` | Create zone |
| `DELETE` | `/admin/intercom/zones/{zone_id}` | Delete zone |
| `PATCH` | `/admin/intercom/zones/{zone_id}` | Update zone |
| `GET` | `/admin/intercom/calls` | List active calls |
| `POST` | `/admin/intercom/calls/{call_id}/end` | End call |
| `GET` | `/admin/intercom/log` | Intercom event log |
| `POST` | `/admin/intercom/broadcast` | Send broadcast |

### Media (`/admin/media/`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/media/providers` | List media providers with health |
| `GET` | `/admin/media/now-playing` | Current playback state |
| `GET` | `/admin/media/history` | Playback history |
| `GET` | `/admin/media/preferences/{user_id}` | User genre preferences |
| `GET` | `/admin/media/targets` | Available playback targets |
| `GET` | `/admin/media/podcasts` | List podcast subscriptions |
| `POST` | `/admin/media/podcasts/subscribe` | Subscribe to podcast |
| `POST` | `/admin/media/library/scan` | Trigger library scan |
| `GET` | `/admin/media/auth` | List media auth (no secrets) |
| `POST` | `/admin/media/auth/{provider}` | Store media auth |
| `DELETE` | `/admin/media/auth/{provider}` | Remove media auth |
| `POST` | `/admin/media/auth/{provider}/set-global` | Set global default |
| `POST` | `/admin/media/auth/youtube/start` | Start YouTube OAuth |
| `POST` | `/admin/media/auth/youtube/complete` | Complete YouTube OAuth |

---

> **Total: 144+ endpoints** across 19 admin routers + public API.
>
> For interactive exploration, visit `http://localhost:5100/docs` (Swagger UI).
