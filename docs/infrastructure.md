# Atlas Cortex — Infrastructure

## Server Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                        Home Network                               │
│                                                                    │
│  192.168.1.x — Main LAN                                          │
│    .3  NGINX Proxy Manager (Proxmox LXC 101)                     │
│    .5  MariaDB                                                    │
│    .6  Nextcloud                                                  │
│    .8  HIVE (Unraid — primary NAS)                               │
│    .12 AdGuard Home (Proxmox LXC 100)                            │
│                                                                    │
│  192.168.3.x — Compute                                            │
│    .8  Overwatch (Unraid — LLM server) ◀── Atlas Cortex lives here│
│                                                                    │
│  192.168.4.x — Management                                         │
│    .8  Observer (Proxmox — hypervisor)                            │
│         └── VM 103: Home Assistant OS                             │
└──────────────────────────────────────────────────────────────────┘
```

## Overwatch (192.168.3.8) — Container Stack

| Container | Image | Port | GPU | Status |
|-----------|-------|------|-----|--------|
| `ollama` | ollama/ollama:rocm | 11434 | RX 7900 XT | ✅ Running |
| `open-webui` | ghcr.io/open-webui/open-webui:main | 8080 | — | ✅ Running |
| `searxng` | searxng/searxng | 8888 | — | ✅ Running |
| `faster-whisper` | faster-whisper | 10300 | — | ✅ Running |
| `piper` | piper | 10200 | — | ✅ Running |
| `atlas-evolution` | *custom* (Python + cron) | — | — | 🔲 Phase C2 |
| `atlas-speaker-id` | *custom* (resemblyzer) | 8890 | — | 🔲 Phase C3 |

## Hardware: Overwatch

| Component | Spec |
|-----------|------|
| CPU | AMD Ryzen 7 5700G (8c/16t, 3.8GHz) |
| RAM | 128GB DDR4 |
| GPU (discrete) | AMD Radeon RX 7900 XT (20GB GDDR6, RDNA3) |
| GPU (integrated) | AMD Cezanne iGPU (not used) |
| Storage | 450GB cache + 7.4TB fast pool + NVMe boot |
| OS | Unraid 7.1.4 |

## Models on Ollama

| Model | Size | Speed | Used By |
|-------|------|-------|---------|
| `qwen3:30b-a3b` | 18.6GB | 75 tok/s | Atlas, Atlas Deep Thought |
| `huihui_ai/qwen2.5-abliterate:14b` | 9.0GB | 55 tok/s | Atlas Turbo |
| `qwen3-nothink:30b-a3b` | 18.6GB | 75 tok/s | *(deprecated, can delete)* |

**After Cortex deployment:**
- Atlas Turbo / Atlas / Atlas Deep Thought → replaced by Atlas Cortex
- Cortex auto-selects between qwen2.5 (fast) and qwen3 (thinking) internally

## Open WebUI Custom Models (current)

| Model | Base | Temperature | Context | Role |
|-------|------|-------------|---------|------|
| Atlas Turbo | qwen2.5-abliterate:14b | 0.7 | 8K | Quick answers (default) |
| Atlas | qwen3:30b-a3b | 0.2 | 8K | Complex questions |
| Atlas Deep Thought | qwen3:30b-a3b | 0.6 | 8K | Hard reasoning |

**After Cortex:** Single "Atlas Cortex" model replaces all three.

## Open WebUI Tools (7 custom + built-in)

| Tool | ID | Function |
|------|----|----------|
| DateTime | datetime | Current date/time (fixes "thinks it's 2023") |
| Calculator | calculator | Math with trig, stats |
| Web Scraper | web_scraper | Fetch and extract URL text |
| YouTube Transcriber | youtube_transcript | Extract video captions |
| Run Python Code | run_code | Execute Python in sandbox |
| Home Assistant | home_assistant | Control smart home (needs HA token) |
| Memory Manager | memory_manager | Persistent user memory |

## DNS & Proxy

| Domain | Target |
|--------|--------|
| chat.digitalbrainstem.com | Open WebUI (192.168.3.8:8080) via NGINX |

## Access

| Service | Auth |
|---------|------|
| SSH (all servers) | Key-based auth (ed25519) |
| Open WebUI | Admin account (see private credentials store) |
| NPM | Admin account (Proxmox LXC) |
| AdGuard | Admin account (Proxmox LXC) |
| Home Assistant | VM on Proxmox (needs long-lived access token for Cortex) |

## Atlas Cortex Docker Stack (192.168.3.8)

The Cortex server runs as a Docker stack on Overwatch. Critical config:

| Container | Port | GPU | Notes |
|-----------|------|-----|-------|
| `atlas-cortex` | 5100 | — | FastAPI server, pipeline, admin API |
| `atlas-ollama` | 11434 | Intel Arc B580 | LLM (qwen2.5:7b) |
| `atlas-whisper` | — | Intel Arc B580 (Vulkan) | faster-whisper STT |
| `atlas-piper` | — | — | Piper TTS (CPU fallback) |

**CRITICAL:** The `.env` file at the Docker compose directory must contain:
```
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu-intel.yml
```
Without this, Whisper falls back to CPU and is extremely slow.

**Environment:**
- `TZ=America/New_York` — set in docker-compose.yml for correct time answers

### Deploy Server

```bash
# From development machine (needs ~/.ssh/unraid_hive_key):
rsync -az -e "ssh -i ~/.ssh/unraid_hive_key" \
  --exclude='.git' --exclude='node_modules' --exclude='__pycache__' \
  /path/to/atlas-cortex/ root@192.168.3.8:/tmp/atlas-cortex-build/

ssh -i ~/.ssh/unraid_hive_key root@192.168.3.8 \
  "cd /tmp/atlas-cortex-build/docker && docker compose build atlas-cortex && docker compose up -d atlas-cortex"
```

## Satellite: Pi Zero 2W (192.168.16.1)

| Component | Detail |
|-----------|--------|
| Hardware | Raspberry Pi Zero 2W + ReSpeaker 2-mic HAT |
| OS | Raspberry Pi OS Bookworm (64-bit) |
| User | `atlas` |
| Install path | `/opt/atlas-satellite/` |
| Service | `systemctl status atlas-satellite` |
| Config | `/opt/atlas-satellite/config.json` |

### ReSpeaker 2-mic HAT

| Feature | Detail |
|---------|--------|
| Codec | WM8960 |
| LEDs | 3× APA102 on SPI |
| Button | GPIO 17, active LOW with pull-up |
| Mic noise floor | Raw RMS ~17k (webrtcvad useless, use energy VAD) |

**ALSA Volume:** Must max all controls AND enable amplifier:
```bash
amixer -c wm8960soundcard sset Speaker 100%
amixer -c wm8960soundcard sset Playback 100%
amixer -c wm8960soundcard sset 'Speaker AC' 5
amixer -c wm8960soundcard sset 'Speaker DC' 5
sudo alsactl store
```

### Current Satellite Config
```json
{
  "satellite_id": "sat-atlas-satellite",
  "server_url": "ws://192.168.3.8:5100/ws/satellite",
  "wake_word_threshold": 0.25,
  "mic_gain": 0.6,
  "vad_sensitivity": 1,
  "silence_threshold_frames": 15,
  "vad_speech_energy_ratio": 2.2,
  "button_enabled": true,
  "button_mode": "press"
}
```

### Deploy Satellite

SSH to satellite goes through the server (SSH hop) using a key stored in a Docker volume:

```bash
# From development machine:
# 1. rsync code to server
rsync -az -e "ssh -i ~/.ssh/unraid_hive_key" \
  --exclude='.git' --exclude='__pycache__' \
  /path/to/atlas-cortex/satellite/atlas_satellite/ \
  root@192.168.3.8:/tmp/sat-deploy/

# 2. SSH to server, then rsync to satellite
ssh -i ~/.ssh/unraid_hive_key root@192.168.3.8 "
  SATKEY=/var/lib/docker/volumes/docker_atlas-data/_data/ssh/atlas_satellite
  rsync -az -e 'ssh -o StrictHostKeyChecking=no -i \$SATKEY' \
    /tmp/sat-deploy/ atlas@192.168.16.1:/opt/atlas-satellite/atlas_satellite/
  ssh -o StrictHostKeyChecking=no -i \$SATKEY atlas@192.168.16.1 \
    'sudo systemctl restart atlas-satellite'
"
```

### Satellite Logs
```bash
# Via SSH hop:
ssh -i ~/.ssh/unraid_hive_key root@192.168.3.8 "
  SATKEY=/var/lib/docker/volumes/docker_atlas-data/_data/ssh/atlas_satellite
  ssh -o StrictHostKeyChecking=no -i \$SATKEY atlas@192.168.16.1 \
    'sudo journalctl -u atlas-satellite -f --no-pager'
"
```

### Key Dependencies (satellite venv)
- `gpiod` — GPIO button handler (RPi.GPIO edge detection broken on Bookworm)
- `openwakeword` — wake word detection (models: hey_jarvis, alexa, hey_mycroft)
- `webrtcvad-wheels` — Python 3.13 compatible webrtcvad
