# Atlas Cortex — Satellite System (Part 2.5)

The satellite system enables Atlas to be present in every room through distributed speaker/microphone devices. Each satellite is a lightweight agent that handles local audio I/O and streams to the Atlas Cortex server for processing.

## Overview

```
                    ┌─────────────────────┐
                    │    Atlas Cortex      │
                    │    Server (:5100)    │
                    └──────────┬──────────┘
                               │ WebSocket / gRPC
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼────────┐ ┌────▼────────┐ ┌─────▼────────────┐
    │  Kitchen Sat.    │ │ Bedroom Sat.│ │ Living Room Sat. │
    │  Pi 4 + ReSpeaker│ │ ESP32-S3    │ │ Pi Zero 2W       │
    │  Speaker + Mic   │ │ I2S Mic/Amp │ │ USB Speaker      │
    └──────────────────┘ └─────────────┘ └──────────────────┘
```

## Design Principles

1. **Hardware-agnostic** — works on Raspberry Pi, ESP32-S3, x86 mini-PCs, or any Linux device with audio I/O
2. **Zero-config discovery** — satellites announce themselves via mDNS/Zeroconf; Atlas auto-detects
3. **Local wake word** — wake word runs on-device for privacy and low latency
4. **Thin client** — satellites only capture/play audio; all intelligence lives on the Atlas server
5. **Wyoming-compatible** — integrates with Home Assistant Wyoming protocol for HA voice pipelines
6. **Graceful offline** — satellites cache essential TTS (e.g., "I can't reach Atlas right now") for server outages

## Hardware Requirements

### Minimum (any ONE of these)
| Component | Requirement |
|-----------|-------------|
| **SBC** | Raspberry Pi 3B+ / Pi Zero 2W / Orange Pi / ESP32-S3 |
| **Microphone** | Any USB mic, I2S MEMS mic (INMP441), or ReSpeaker array |
| **Speaker** | Any 3.5mm, USB, I2S (MAX98357A), or Bluetooth speaker |
| **Network** | Wi-Fi or Ethernet |
| **Storage** | 4GB+ SD card / flash |

### Recommended Configurations

| Budget | Hardware | Notes |
|--------|----------|-------|
| **$15** | ESP32-S3 + INMP441 mic + MAX98357A amp | Ultra-low power, limited wake word |
| **$40** | Pi Zero 2W + USB mic/speaker combo | Good balance, runs full satellite agent |
| **$75** | Pi 4 + ReSpeaker 2-Mic HAT | Best quality, supports AEC and beamforming |
| **$120** | Pi 4 + ReSpeaker 4-Mic Array + quality speaker | Premium — 360° pickup, noise cancellation |

## Architecture

### Satellite Agent

```python
class SatelliteAgent:
    """Lightweight agent running on each satellite device."""

    # Core components
    audio_input: AudioCapture        # Microphone capture (16kHz, 16-bit mono)
    audio_output: AudioPlayback      # Speaker output
    wake_word: WakeWordDetector      # Local wake word detection
    vad: VoiceActivityDetector       # Detect speech start/end
    connection: ServerConnection     # WebSocket to Atlas server

    # State
    satellite_id: str                # Unique device ID (from MAC or config)
    room: str                        # Room name (configured or discovered)
    is_listening: bool               # Currently capturing audio
    is_speaking: bool                # Currently playing audio
```

### Communication Protocol

```
Satellite → Server (WebSocket):
  1. ANNOUNCE    {satellite_id, room, capabilities, hw_info}
  2. WAKE        {satellite_id, wake_word_confidence}
  3. AUDIO_START {satellite_id, format: "pcm_16k_16bit_mono"}
  4. AUDIO_CHUNK {satellite_id, audio: bytes}
  5. AUDIO_END   {satellite_id, reason: "vad_silence" | "timeout" | "interrupt"}
  6. STATUS      {satellite_id, status: "idle" | "listening" | "speaking"}

Server → Satellite (WebSocket):
  1. ACCEPTED    {satellite_id, session_id}
  2. TTS_START   {session_id, format: "pcm_22k_16bit_mono"}
  3. TTS_CHUNK   {session_id, audio: bytes}
  4. TTS_END     {session_id}
  5. COMMAND     {action: "listen" | "stop" | "volume" | "led", params: {}}
  6. CONFIG      {wake_word, volume, led_brightness, vad_sensitivity}
```

### Audio Pipeline

```
┌──────────┐     ┌─────────┐     ┌─────────────┐     ┌───────────┐
│ Mic      │────▶│ AEC     │────▶│ Wake Word   │────▶│ VAD       │
│ Capture  │     │ (echo   │     │ (openWakeWord│     │ (Silero)  │
│ 16kHz    │     │ cancel) │     │ or Porcupine)│     │           │
└──────────┘     └─────────┘     └──────┬──────┘     └─────┬─────┘
                                        │ triggered         │ speech detected
                                        ▼                   ▼
                                ┌──────────────────────────────────┐
                                │   Stream audio → Atlas Server     │
                                │   via WebSocket (opus or raw PCM) │
                                └──────────────────────────────────┘
```

## Core Components

### 1. Audio Capture (`audio_capture.py`)

```python
class AudioCapture:
    """Cross-platform microphone capture."""

    def __init__(self, device: str = "default", sample_rate: int = 16000):
        ...

    async def start(self) -> AsyncGenerator[bytes, None]:
        """Yield audio chunks (20ms frames)."""

    def stop(self):
        """Stop capture."""

    @staticmethod
    def list_devices() -> list[dict]:
        """List available audio input devices."""
```

**Backends** (auto-detected):
- `pyaudio` — works everywhere, ALSA/PulseAudio/CoreAudio
- `sounddevice` — PortAudio wrapper, better API
- `alsaaudio` — direct ALSA for headless Pi
- ESP32: `machine.I2S` (MicroPython) or `esp-idf` I2S driver

### 2. Wake Word Detection (`wake_word.py`)

```python
class WakeWordDetector:
    """Local wake word detection — runs entirely on-device."""

    def __init__(self, wake_words: list[str] = ["hey atlas", "atlas"]):
        ...

    async def detect(self, audio_chunk: bytes) -> WakeWordResult | None:
        """Returns result if wake word detected in audio chunk."""

    def set_sensitivity(self, value: float):
        """0.0 (loose) to 1.0 (strict)."""
```

**Engines** (user-selectable):
| Engine | License | Size | Accuracy | Platforms |
|--------|---------|------|----------|-----------|
| **openWakeWord** (default) | Apache 2.0 | ~5MB | Good | Pi, x86, ARM |
| **Porcupine** | Free tier | ~2MB | Excellent | Pi, ESP32, x86 |
| **Snowboy** | Apache 2.0 | ~3MB | Good | Pi, x86 |
| **microWakeWord** | Apache 2.0 | <1MB | Good | ESP32-S3, Pi |

### 3. Voice Activity Detection (`vad.py`)

```python
class VoiceActivityDetector:
    """Detect speech boundaries in audio stream."""

    def __init__(self, sensitivity: float = 0.5, silence_ms: int = 800):
        ...

    def process(self, audio_chunk: bytes) -> VADEvent | None:
        """Returns SPEECH_START, SPEECH_CONTINUE, or SPEECH_END."""
```

**Default**: Silero VAD (Apache 2.0, ONNX, runs on any device, 16kHz)

### 4. Acoustic Echo Cancellation (`aec.py`)

```python
class AcousticEchoCanceller:
    """Remove speaker output from microphone input (barge-in support)."""

    def __init__(self, frame_size: int = 320):
        ...

    def process(self, mic_frame: bytes, speaker_frame: bytes) -> bytes:
        """Returns echo-cancelled audio."""
```

**Engines**: `speexdsp` (default), `webrtc-audio-processing`, hardware AEC (ReSpeaker)

### 5. Server Connection (`connection.py`)

```python
class ServerConnection:
    """WebSocket connection to Atlas Cortex server."""

    def __init__(self, server_url: str, satellite_id: str):
        ...

    async def connect(self):
        """Connect and announce satellite."""

    async def send_audio(self, chunk: bytes):
        """Stream audio to server."""

    async def receive(self) -> ServerMessage:
        """Receive TTS audio or commands."""

    async def reconnect(self):
        """Auto-reconnect with exponential backoff."""
```

**Transport options**:
- **WebSocket** (default) — simple, works through firewalls
- **gRPC** — lower overhead for high-frequency audio streaming
- **Wyoming protocol** — for direct HA voice pipeline integration

### 6. LED / Visual Feedback (`feedback.py`)

```python
class FeedbackController:
    """Visual and audio feedback for satellite state."""

    async def set_state(self, state: SatelliteState):
        """Update LEDs/display based on state."""
        # IDLE     → dim white pulse
        # WAKE     → blue ring
        # LISTEN   → blue pulse
        # THINK    → spinning blue
        # SPEAK    → green pulse
        # ERROR    → red flash
        # MUTED    → solid red
```

**Hardware support**: NeoPixel/WS2812B, ReSpeaker LEDs, GPIO LEDs, OLED display

## Wyoming Protocol Integration

Atlas satellites are compatible with Home Assistant's [Wyoming protocol](https://www.home-assistant.io/integrations/wyoming/), enabling:

1. **HA discovers satellites** automatically via Zeroconf
2. **HA voice pipeline** can route through Atlas satellites
3. Satellites appear as **voice assistants** in HA UI
4. Users can assign satellites to HA areas for spatial awareness

```yaml
# satellite_config.yaml
wyoming:
  enabled: true
  port: 10400
  # Exposes: wake-word, stt (proxied to Atlas), tts (proxied to Atlas)
```

## Discovery & Registration

```
1. Satellite boots → announces via mDNS:
   _atlas-satellite._tcp.local. → {satellite_id, room, capabilities}

2. Atlas server discovers satellite → sends CONFIG message

3. Satellite registers in Atlas DB:
   INSERT INTO satellites (id, room, capabilities, last_seen)

4. Home Assistant discovers via Wyoming:
   _wyoming._tcp.local. → {satellite_id, services}
```

### Database Schema

```sql
CREATE TABLE IF NOT EXISTS satellites (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    room          TEXT,
    hardware      TEXT,              -- "pi4", "esp32s3", "x86"
    capabilities  TEXT,              -- JSON: {mic: true, speaker: true, led: true, display: false}
    wake_word     TEXT DEFAULT 'hey atlas',
    volume        REAL DEFAULT 0.7,
    is_active     BOOLEAN DEFAULT TRUE,
    last_seen     TIMESTAMP,
    ip_address    TEXT,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS satellite_audio_sessions (
    id            TEXT PRIMARY KEY,
    satellite_id  TEXT REFERENCES satellites(id),
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at      TIMESTAMP,
    audio_length_ms INTEGER,
    transcription TEXT,
    response_text TEXT,
    latency_ms    INTEGER
);
```

## Installation

### Raspberry Pi (recommended)

```bash
# On the satellite device:
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash

# Or manually:
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex/satellite
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m atlas_satellite --room "Kitchen" --server "ws://atlas-server:5100/ws/satellite"
```

### ESP32-S3

```bash
# Flash MicroPython firmware with atlas_satellite module
esptool.py --chip esp32s3 write_flash 0x0 firmware-atlas-satellite.bin

# Configure via serial or captive portal:
# - Wi-Fi SSID/password
# - Atlas server URL
# - Room name
```

### Docker (any Linux device)

```bash
docker run -d \
  --name atlas-satellite \
  --device /dev/snd \
  -e ATLAS_SERVER=ws://atlas-server:5100/ws/satellite \
  -e ROOM=Kitchen \
  -e SATELLITE_ID=kitchen-01 \
  ghcr.io/betanu701/atlas-satellite:latest
```

## Satellite Package Structure

```
satellite/
├── atlas_satellite/
│   ├── __init__.py
│   ├── __main__.py              # Entry point
│   ├── agent.py                 # SatelliteAgent orchestrator
│   ├── audio_capture.py         # Microphone input
│   ├── audio_playback.py        # Speaker output
│   ├── wake_word.py             # Wake word detection
│   ├── vad.py                   # Voice activity detection
│   ├── aec.py                   # Acoustic echo cancellation
│   ├── connection.py            # WebSocket to Atlas server
│   ├── feedback.py              # LED / visual feedback
│   ├── wyoming.py               # Wyoming protocol server
│   ├── config.py                # Configuration management
│   └── platforms/
│       ├── __init__.py
│       ├── raspberry_pi.py      # Pi-specific GPIO, I2S, LED
│       ├── esp32.py             # ESP32-S3 specifics
│       └── generic_linux.py     # PulseAudio / ALSA fallback
├── requirements.txt
├── install.sh                   # One-line installer
├── Dockerfile
└── tests/
```

## Server-Side Integration

Atlas Cortex server needs a WebSocket endpoint for satellite connections:

```python
# cortex/satellite/__init__.py
@app.websocket("/ws/satellite")
async def satellite_ws(websocket: WebSocket):
    await websocket.accept()
    satellite_id = await handle_announce(websocket)

    async for message in websocket.iter_json():
        match message["type"]:
            case "WAKE":
                await handle_wake(satellite_id, message)
            case "AUDIO_CHUNK":
                await handle_audio(satellite_id, message)
            case "AUDIO_END":
                response = await process_utterance(satellite_id)
                await stream_tts_response(websocket, response)
```

## Implementation Phases

| Task | Description |
|------|-------------|
| S2.5.1 | Satellite agent core — audio capture, playback, agent loop |
| S2.5.2 | Wake word integration — openWakeWord default, pluggable |
| S2.5.3 | VAD + AEC — Silero VAD, speexdsp echo cancellation |
| S2.5.4 | Server connection — WebSocket client, auto-reconnect, audio streaming |
| S2.5.5 | Atlas WebSocket endpoint — server-side satellite handler |
| S2.5.6 | Discovery — mDNS announcement + Atlas auto-detection |
| S2.5.7 | Wyoming protocol — HA voice pipeline compatibility |
| S2.5.8 | LED/feedback — visual state indicators |
| S2.5.9 | Platform abstraction — Pi, ESP32, generic Linux |
| S2.5.10 | Installer — one-line install script, Docker image |
| S2.5.11 | Offline fallback — cached error TTS, reconnection logic |

### Dependencies

```
S2.5.1 ──┐
S2.5.2 ──┤
S2.5.3 ──┼──▶ S2.5.4 ──▶ S2.5.5 ──▶ S2.5.6 ──▶ S2.5.7
S2.5.8 ──┘                            │
S2.5.9 ─────────────────────────────────▶ S2.5.10
                                       └──▶ S2.5.11
```
