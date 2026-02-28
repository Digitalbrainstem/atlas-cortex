# Atlas Cortex — Satellite System (Part 2.5)

The satellite system enables Atlas to be present in every room through distributed speaker/microphone devices. Each satellite is a lightweight agent that handles local audio I/O and streams to the Atlas Cortex server for processing.

## Overview

```
                    ┌─────────────────────┐
                    │    Atlas Cortex      │
                    │    Server (:5100)    │
                    │    Admin Panel       │
                    │  ┌───────────────┐  │
                    │  │ Satellite Mgr │  │
                    │  │ • Discovery   │  │
                    │  │ • Provision   │  │
                    │  │ • Configure   │  │
                    │  └───────────────┘  │
                    └──────────┬──────────┘
                               │ WebSocket / SSH (provisioning)
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼────────┐ ┌────▼────────┐ ┌─────▼────────────┐
    │  Kitchen Sat.    │ │ Bedroom Sat.│ │ Living Room Sat. │
    │  Pi 4 + ReSpeaker│ │ ESP32-S3    │ │ FPH Satellite1   │
    │  Speaker + Mic   │ │ I2S Mic/Amp │ │ XMOS + ESP32-S3  │
    └──────────────────┘ └─────────────┘ └──────────────────┘
```

## Design Principles

1. **Hardware-agnostic** — works on Raspberry Pi, ESP32-S3, x86 mini-PCs, Orange Pi, BeagleBone, FutureProofHomes Satellite1, or any Linux device with audio I/O
2. **Plug-and-play provisioning** — flash an OS, set a hostname, boot → Atlas auto-discovers and configures
3. **Zero-config discovery** — satellites use a standard hostname pattern (`atlas-sat-XXXX`) so the server can find them via network scan
4. **Admin panel managed** — add, configure, assign rooms, enable features, and reconfigure satellites from the web UI
5. **Local wake word** — wake word runs on-device for privacy and low latency
6. **Thin client** — satellites only capture/play audio; all intelligence lives on the Atlas server
7. **Wyoming-compatible** — integrates with Home Assistant Wyoming protocol for HA voice pipelines
8. **Graceful offline** — satellites cache essential TTS (e.g., "I can't reach Atlas right now") for server outages

## Hardware Support

### Supported Platforms

| Platform | Type | Notes |
|----------|------|-------|
| **Raspberry Pi 3B+/4/5** | SBC (Linux) | Best general choice, full Python satellite agent |
| **Raspberry Pi Zero 2W** | SBC (Linux) | Budget option, runs full agent |
| **Orange Pi, BeagleBone** | SBC (Linux) | Any ARM/x86 Linux SBC works |
| **ESP32-S3** | Microcontroller | Ultra-low power, ESPHome-based agent |
| **FutureProofHomes Satellite1** | Purpose-built | ESP32-S3 + XMOS audio, premium audio quality |
| **Any Linux x86 box** | PC/NUC | Repurpose old hardware |

### Recommended Configurations

| Budget | Hardware | Notes |
|--------|----------|-------|
| **$15** | ESP32-S3 + INMP441 mic + MAX98357A amp | Ultra-low power, ESPHome firmware |
| **$40** | Pi Zero 2W + USB mic/speaker combo | Good balance, runs full satellite agent |
| **$70** | FutureProofHomes Satellite1 Dev Kit | Premium audio (XMOS), sensors, 25W amp, HA-native |
| **$75** | Pi 4 + ReSpeaker 2-Mic HAT | Good quality, AEC support |
| **$120** | Pi 4 + ReSpeaker 4-Mic Array + quality speaker | 360° pickup, noise cancellation |

---

## Provisioning System

The provisioning system is designed so that setting up a new satellite is as simple as:
1. Flash an OS image to an SD card
2. Set the hostname to `atlas-sat-XXXX` (where XXXX is auto-generated from MAC)
3. Boot the device and connect it to the network
4. Atlas discovers it automatically (or you enter the IP in the admin panel)
5. Atlas SSHes in, detects hardware, installs the satellite agent, and configures everything

### Hostname Convention

All satellites boot with the **same default hostname** for zero-config discovery:

```
atlas-satellite
```

- Every new satellite uses `atlas-satellite` as its hostname during setup
- Atlas scans the network and finds all devices with this hostname
- When multiple `atlas-satellite` hosts exist, Atlas distinguishes them by MAC/IP
- **During provisioning**, Atlas renames each satellite to a unique hostname:

```
atlas-sat-{room}       e.g., atlas-sat-kitchen, atlas-sat-bedroom
```

- The naming pattern is configurable in the admin panel (default: `atlas-sat-{room}`)
- Users can disable auto-rename and set hostnames manually if preferred

### Default Credentials

To enable fully headless setup, all satellites use standard default credentials:

| Setting | Default Value |
|---------|---------------|
| **Hostname** | `atlas-satellite` |
| **Username** | `atlas` |
| **Password** | `atlas-setup` |
| **SSH** | Enabled (password auth) |

> ⚠️ **During provisioning**, Atlas:
> 1. Installs its SSH public key on the satellite
> 2. Disables password authentication (key-only from that point)
> 3. All future management uses SSH key auth — no passwords stored or transmitted
>
> The default password is only used for the initial connection.
> If a satellite needs to be re-provisioned, re-flash the SD card to restore defaults.

### LED Identification

When Atlas discovers multiple new satellites, it can blink an LED to identify which physical device you're configuring:

```
Admin clicks "Identify" on a satellite →
  Atlas SSHes in and triggers LED blink →
    Device flashes rapidly for 10 seconds →
      User confirms "Yes, that's the kitchen one"
```

**LED detection priority:**
1. **ReSpeaker HAT** — APA102 LEDs via SPI
2. **NeoPixel/WS2812B** — GPIO pin (configurable)
3. **GPIO LED** — built-in activity LED (/sys/class/leds/)
4. **Screen flash** — if HDMI connected, flash the display
5. **Audio beep** — play a distinctive tone through the speaker as fallback

### Provisioning Flow

```
┌──────────────────────────────────────────────────────────┐
│                    USER SETUP                             │
│                                                          │
│  1. Flash OS to SD card (Raspberry Pi OS Lite / DietPi)  │
│  2. In Raspberry Pi Imager settings (gear icon):         │
│     • Hostname: atlas-satellite                          │
│     • Enable SSH: yes                                    │
│     • Username: atlas                                    │
│     • Password: atlas-setup                              │
│     • Configure Wi-Fi: enter SSID + password             │
│  3. Insert SD card, power on — done!                     │
│                                                          │
│  (That's it. No terminal, no config files.)              │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│                  ATLAS DISCOVERY                          │
│                                                          │
│  Atlas server periodically scans for new satellites:     │
│                                                          │
│  Method 1: Hostname scan (automatic, every 5 min)        │
│    • mDNS lookup: atlas-satellite.local                  │
│    • ARP table scan for new devices                      │
│    • Distinguishes multiple by MAC address               │
│                                                          │
│  Method 2: Manual add (Admin Panel)                      │
│    • User enters IP address                              │
│    • Atlas tries default credentials (atlas/atlas-setup) │
│    • Or user enters custom username/password             │
│                                                          │
│  Method 3: Satellite self-announce (already provisioned) │
│    • mDNS: _atlas-satellite._tcp.local                   │
│    • Atlas picks it up immediately                       │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│               HARDWARE DETECTION (via SSH)                │
│                                                          │
│  Atlas SSHes in with default credentials and detects:    │
│                                                          │
│  • Platform: RPi model, ESP32, x86, ARM variant          │
│  • Audio: list input/output devices (arecord -l, etc.)   │
│  • Mic type: USB, I2S, ReSpeaker HAT, XMOS              │
│  • Speaker type: USB, 3.5mm, I2S, Bluetooth, HDMI       │
│  • Sensors: I2C scan (temp, humidity, light, mmWave)     │
│  • LEDs: GPIO, NeoPixel, ReSpeaker LEDs                  │
│  • Resources: CPU cores, RAM, storage                    │
│  • Network: Wi-Fi signal, Ethernet, IP, MAC address      │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│            ADMIN PANEL — NEW SATELLITE                    │
│                                                          │
│  Satellite appears in Admin → Satellites page:           │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ 🆕 New Satellite Detected            [Identify 💡] │  │
│  │                                                    │  │
│  │ IP: 192.168.3.42     MAC: dc:a6:32:xx:xx:a3       │  │
│  │ Hardware: Raspberry Pi 4 Model B (4GB RAM)         │  │
│  │ Audio In: ReSpeaker 2-Mic HAT                      │  │
│  │ Audio Out: 3.5mm analog                            │  │
│  │ LEDs: ReSpeaker LEDs (12x APA102)                  │  │
│  │ Sensors: None detected                             │  │
│  │                                                    │  │
│  │ Room: [  Kitchen          ▾]                       │  │
│  │ Display Name: [  Kitchen Speaker  ]                │  │
│  │                                                    │  │
│  │ Features to enable:                                │  │
│  │ ☑ Wake word detection (openWakeWord)               │  │
│  │ ☑ LED feedback                                     │  │
│  │ ☑ Acoustic echo cancellation                       │  │
│  │ ☐ Wyoming protocol (HA voice pipeline)             │  │
│  │ ☐ Presence detection (no sensor found)             │  │
│  │ ☐ Temperature/humidity reporting                   │  │
│  │                                                    │  │
│  │ [  Provision & Configure  ]  [ Skip for now ]      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────┘
                           │ User clicks "Provision"
                           ▼
┌──────────────────────────────────────────────────────────┐
│            AUTOMATED PROVISIONING (via SSH)                │
│                                                          │
│  Atlas SSHes into satellite and:                         │
│                                                          │
│  1. Installs Atlas SSH public key                      │
│  2. Disables password authentication (key-only)        │
│  3. Sets hostname → atlas-sat-kitchen                  │
│  3. Installs system dependencies (apt packages)          │
│  4. Creates systemd service for atlas-satellite           │
│  5. Installs atlas-satellite Python package               │
│  6. Writes satellite config:                             │
│     - Server URL: ws://atlas-server:5100/ws/satellite    │
│     - Room: Kitchen                                      │
│     - Satellite ID: sat-{MAC}-kitchen                    │
│     - Audio device selection (mic + speaker)             │
│     - Feature flags                                      │
│  7. Installs Atlas SSH key for future management         │
│  8. Starts the satellite agent service                   │
│  9. Verifies WebSocket connection to Atlas server        │
│ 10. Runs audio test (play tone, verify mic picks it up)  │
│                                                          │
│  Admin panel shows live progress:                        │
│  ✅ Connected via SSH                                    │
│  ✅ SSH key installed, password auth disabled             │
│  ✅ Hostname set to atlas-sat-kitchen                    │
│  ✅ System packages installed                            │
│  ✅ Satellite agent installed                            │
│  ✅ Configuration written                                │
│  ✅ Service started                                      │
│  ✅ Connected to Atlas server                            │
│  ✅ Audio test passed                                    │
│                                                          │
│  🎉 Kitchen Speaker is ready!                            │
└──────────────────────────────────────────────────────────┘
```

### SD Card Setup Instructions

**Using Raspberry Pi Imager (recommended — no script needed):**

1. Download and open [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose OS: **Raspberry Pi OS Lite (64-bit)** or **DietPi**
3. Click the **gear icon ⚙️** (or Ctrl+Shift+X)
4. Set these values:
   - **Hostname:** `atlas-satellite`
   - **Enable SSH:** ✅ Use password authentication
   - **Username:** `atlas`
   - **Password:** `atlas-setup`
   - **Configure Wi-Fi:** enter your SSID and password
   - **Wi-Fi country:** your country code
5. Click **Write** and wait for it to finish
6. Insert SD card into the device and power on

That's it — Atlas will discover the device within 5 minutes.

### ESPHome Satellites (ESP32-S3, FutureProofHomes Satellite1)

ESPHome-based devices use a different provisioning path:

```
1. User flashes ESPHome firmware (via USB or web flasher)
2. Device creates captive portal Wi-Fi: "Atlas-Satellite-Setup"
3. User connects and enters:
   - Wi-Fi SSID/password
   - Atlas server IP (or auto-discover via mDNS)
4. Device reboots, connects to Wi-Fi
5. Announces via mDNS: _atlas-satellite._tcp.local
6. Atlas discovers and shows in admin panel
7. User configures room and features in admin panel
8. Atlas pushes config via ESPHome API (no SSH needed)
```

---

## Admin Panel — Satellites Page

> **This is a section within the main Atlas Cortex admin panel** (`/admin/#/satellites`), not a separate portal. It sits alongside Users, Devices, Safety, etc.

### Satellite Settings (Admin → Satellites → Settings)

| Setting | Default | Description |
|---------|---------|-------------|
| **Auto-discover** | Enabled | Scan network for `atlas-satellite` hosts every 5 min |
| **Hostname pattern** | `atlas-sat-{room}` | Pattern for renaming after provisioning |
| **Default SSH user** | `atlas` | Username for connecting to new satellites |
| **Default SSH password** | `atlas-setup` | Password for initial connection only |
| **SSH key** | *(auto-generated)* | Atlas server key installed on all satellites during provisioning |
| **Auto-provision** | Disabled | Automatically provision discovered satellites (advanced) |

### Satellite List View

```
┌──────────────────────────────────────────────────────────────────┐
│ 📡 Satellites                                    [ + Add Manual ] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ ● Kitchen Speaker         Pi 4 + ReSpeaker    192.168.3.42      │
│   Room: Kitchen           Status: Online       Uptime: 3d 14h   │
│                                                                  │
│ ● Bedroom                 ESP32-S3            192.168.3.55      │
│   Room: Master Bedroom    Status: Online       Uptime: 1d 2h    │
│                                                                  │
│ ● Living Room             FPH Satellite1      192.168.3.60      │
│   Room: Living Room       Status: Online       Uptime: 7d 8h    │
│                                                                  │
│ ○ Garage                  Pi Zero 2W          192.168.3.71      │
│   Room: Garage            Status: Offline      Last: 2h ago     │
│                                                                  │
│ 🆕 atlas-sat-e4d9         Pi 4 (4GB)          192.168.3.78      │
│   Room: Unassigned        Status: New          [Configure →]     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Satellite Detail/Config View

```
┌──────────────────────────────────────────────────────────────────┐
│ 📡 Kitchen Speaker                              [Reconfigure ↻] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ General                                                          │
│ ├─ Display Name:    [ Kitchen Speaker          ]                 │
│ ├─ Room:            [ Kitchen               ▾ ]                 │
│ ├─ Satellite ID:    sat-a3f2-kitchen                            │
│ └─ Hostname:        atlas-kitchen                                │
│                                                                  │
│ Hardware (detected)                                              │
│ ├─ Platform:        Raspberry Pi 4 Model B (4GB RAM)            │
│ ├─ Audio Input:     ReSpeaker 2-Mic HAT                         │
│ ├─ Audio Output:    3.5mm analog (bcm2835 Headphones)           │
│ ├─ LEDs:            APA102 x12 (ReSpeaker)                      │
│ └─ Sensors:         None                                         │
│                                                                  │
│ Features                               │ Audio Settings          │
│ ├─ ☑ Wake word (openWakeWord)          │ ├─ Volume:   [███░░] 70%│
│ ├─ ☑ LED feedback                      │ ├─ Mic gain: [████░] 80%│
│ ├─ ☑ Echo cancellation                 │ └─ VAD sens: [██░░░] 50%│
│ ├─ ☑ Wyoming protocol                  │                         │
│ └─ ☐ Presence reporting                │ Wake Word               │
│                                        │ └─ Phrase: [ hey atlas ]│
│ Connection                                                       │
│ ├─ Status:     🟢 Online                                        │
│ ├─ IP:         192.168.3.42                                      │
│ ├─ Uptime:     3 days, 14 hours                                 │
│ ├─ Last audio: 4 minutes ago                                    │
│ └─ Latency:    12ms avg                                          │
│                                                                  │
│ [Save Changes]  [Restart Agent]  [Test Audio ▶]  [Remove ✕]     │
└──────────────────────────────────────────────────────────────────┘
```

### Manual Add Dialog

```
┌────────────────────────────────────────────────┐
│ Add Satellite Manually                         │
│                                                │
│ IP Address:  [ 192.168.3.78     ]              │
│ Username:    [ pi                ]              │
│ Password:    [ ••••••••          ]              │
│   — or —                                       │
│ SSH Key:     [ Use Atlas server key ]          │
│                                                │
│ [ Connect & Detect Hardware ]                  │
└────────────────────────────────────────────────┘
```

---

## Satellite Agent Architecture

### Communication Protocol

```
Satellite → Server (WebSocket):
  1. ANNOUNCE    {satellite_id, room, capabilities, hw_info}
  2. WAKE        {satellite_id, wake_word_confidence}
  3. AUDIO_START {satellite_id, format: "pcm_16k_16bit_mono"}
  4. AUDIO_CHUNK {satellite_id, audio: bytes}
  5. AUDIO_END   {satellite_id, reason: "vad_silence" | "timeout" | "interrupt"}
  6. STATUS      {satellite_id, status: "idle" | "listening" | "speaking"}
  7. HEARTBEAT   {satellite_id, uptime, cpu_temp, wifi_rssi}

Server → Satellite (WebSocket):
  1. ACCEPTED    {satellite_id, session_id}
  2. TTS_START   {session_id, format: "pcm_22k_16bit_mono"}
  3. TTS_CHUNK   {session_id, audio: bytes}
  4. TTS_END     {session_id}
  5. COMMAND     {action: "listen" | "stop" | "volume" | "led" | "reboot", params: {}}
  6. CONFIG      {wake_word, volume, led_brightness, vad_sensitivity, features}
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

### Core Components

#### 1. Audio Capture (`audio_capture.py`)

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

#### 2. Wake Word Detection (`wake_word.py`)

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
| **microWakeWord** | Apache 2.0 | <1MB | Good | ESP32-S3, Pi |

#### 3. Voice Activity Detection (`vad.py`)

**Default**: Silero VAD (Apache 2.0, ONNX, runs on any device, 16kHz)

#### 4. Acoustic Echo Cancellation (`aec.py`)

**Engines**: `speexdsp` (default), `webrtc-audio-processing`, hardware AEC (ReSpeaker, XMOS)

#### 5. LED / Visual Feedback (`feedback.py`)

```
State         │ LED Pattern
──────────────┼──────────────────────
IDLE          │ dim white pulse
WAKE          │ blue ring
LISTENING     │ blue pulse
THINKING      │ spinning blue
SPEAKING      │ green pulse
ERROR         │ red flash
MUTED         │ solid red
PROVISIONING  │ rainbow sweep
```

**Hardware support**: NeoPixel/WS2812B, APA102 (ReSpeaker), GPIO LEDs, OLED display

---

## Server-Side Components

### Satellite Manager (`cortex/satellite/manager.py`)

```python
class SatelliteManager:
    """Server-side satellite lifecycle management."""

    async def discover_satellites(self) -> list[DiscoveredSatellite]:
        """Scan network for atlas-sat-* hostnames and mDNS announcements."""

    async def detect_hardware(self, host: str, username: str, password: str) -> HardwareProfile:
        """SSH into satellite and detect hardware capabilities."""

    async def provision(self, satellite_id: str, config: SatelliteConfig) -> ProvisionResult:
        """Install and configure satellite agent via SSH."""

    async def reconfigure(self, satellite_id: str, config: SatelliteConfig):
        """Update satellite configuration (pushes new config, restarts agent)."""

    async def restart_agent(self, satellite_id: str):
        """Restart the satellite agent service."""

    async def test_audio(self, satellite_id: str) -> AudioTestResult:
        """Play test tone and verify mic picks it up."""

    async def remove(self, satellite_id: str):
        """Uninstall agent and deregister satellite."""
```

### Discovery Service (`cortex/satellite/discovery.py`)

```python
class SatelliteDiscovery:
    """Find new satellites on the network."""

    async def scan_hostnames(self, subnet: str = "192.168.0.0/16") -> list[str]:
        """Scan for hosts matching atlas-sat-* pattern."""
        # 1. Check mDNS for atlas-sat-*.local
        # 2. Parse ARP table for known prefixes
        # 3. DNS lookup atlas-sat-XXXX.local for common ranges

    async def listen_mdns(self) -> AsyncGenerator[SatelliteAnnouncement, None]:
        """Listen for _atlas-satellite._tcp.local announcements."""

    async def check_host(self, ip: str) -> DiscoveredSatellite | None:
        """Probe a specific IP for satellite indicators."""
```

### Hardware Detector (`cortex/satellite/hardware.py`)

```python
class HardwareDetector:
    """Detect satellite hardware capabilities via SSH."""

    async def detect(self, ssh: SSHConnection) -> HardwareProfile:
        """Run detection commands and return hardware profile."""

    async def detect_platform(self, ssh) -> PlatformInfo:
        """Identify: RPi model, Orange Pi, BeagleBone, x86, etc."""
        # cat /proc/device-tree/model  (RPi, OPi, BB)
        # cat /proc/cpuinfo            (CPU arch, cores)
        # free -m                       (RAM)
        # df -h                         (storage)

    async def detect_audio(self, ssh) -> AudioDevices:
        """Find microphones and speakers."""
        # arecord -l                   (capture devices)
        # aplay -l                     (playback devices)
        # Check for ReSpeaker: lsusb, i2cdetect
        # Check for I2S: /proc/asound/cards

    async def detect_sensors(self, ssh) -> list[Sensor]:
        """Scan I2C bus for sensors."""
        # i2cdetect -y 1
        # Known addresses: 0x38 (AHT20), 0x29 (LTR-303), 0x77 (BME280)

    async def detect_leds(self, ssh) -> LEDInfo | None:
        """Check for addressable LEDs or GPIO."""
        # ReSpeaker: check i2c for APA102 controller
        # GPIO: check /sys/class/gpio
        # NeoPixel: check for known SPI/PWM configurations
```

### WebSocket Endpoint (`cortex/satellite/websocket.py`)

```python
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
            case "HEARTBEAT":
                await update_satellite_status(satellite_id, message)
```

### Admin API Endpoints

```
GET    /admin/satellites              — List all satellites (with status)
GET    /admin/satellites/:id          — Satellite detail + hardware info
POST   /admin/satellites/discover     — Trigger network scan for new satellites
POST   /admin/satellites/add          — Manual add (IP, username, password)
POST   /admin/satellites/:id/provision — Start provisioning with config
PATCH  /admin/satellites/:id          — Update satellite config (room, features, etc.)
POST   /admin/satellites/:id/restart  — Restart satellite agent
POST   /admin/satellites/:id/test     — Run audio test
DELETE /admin/satellites/:id          — Remove and uninstall satellite
GET    /admin/satellites/:id/logs     — Get satellite agent logs
```

---

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS satellites (
    id              TEXT PRIMARY KEY,          -- "sat-a3f2-kitchen"
    display_name    TEXT NOT NULL,             -- "Kitchen Speaker"
    hostname        TEXT,                      -- "atlas-kitchen"
    room            TEXT,                      -- "kitchen"
    ip_address      TEXT,
    mac_address     TEXT,
    platform        TEXT,                      -- "rpi4", "esp32s3", "fph-sat1", "x86"
    hardware_info   TEXT,                      -- JSON: full detection results
    capabilities    TEXT,                      -- JSON: {mic, speaker, led, sensors, aec}
    features        TEXT,                      -- JSON: {wake_word, led, aec, wyoming, presence}
    wake_word       TEXT DEFAULT 'hey atlas',
    volume          REAL DEFAULT 0.7,
    mic_gain        REAL DEFAULT 0.8,
    vad_sensitivity REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'new',        -- new, provisioning, online, offline, error
    provision_state TEXT,                      -- JSON: step-by-step progress
    ssh_username    TEXT,
    ssh_key_installed BOOLEAN DEFAULT FALSE,
    is_active       BOOLEAN DEFAULT TRUE,
    last_seen       TIMESTAMP,
    last_audio      TIMESTAMP,
    uptime_seconds  INTEGER,
    wifi_rssi       INTEGER,
    cpu_temp        REAL,
    registered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provisioned_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS satellite_audio_sessions (
    id              TEXT PRIMARY KEY,
    satellite_id    TEXT REFERENCES satellites(id),
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at        TIMESTAMP,
    audio_length_ms INTEGER,
    transcription   TEXT,
    response_text   TEXT,
    latency_ms      INTEGER
);
```

---

## Wyoming Protocol Integration

Atlas satellites are compatible with Home Assistant's [Wyoming protocol](https://www.home-assistant.io/integrations/wyoming/), enabling:

1. **HA discovers satellites** automatically via Zeroconf
2. **HA voice pipeline** can route through Atlas satellites
3. Satellites appear as **voice assistants** in HA UI
4. Users can assign satellites to HA areas for spatial awareness

```yaml
# Enabled per-satellite in admin panel
wyoming:
  enabled: true
  port: 10400
  # Exposes: wake-word, stt (proxied to Atlas), tts (proxied to Atlas)
```

---

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
│   ├── hw_detect.py             # Local hardware detection
│   └── platforms/
│       ├── __init__.py
│       ├── raspberry_pi.py      # Pi-specific GPIO, I2S, LED
│       ├── esp32.py             # ESP32-S3 / ESPHome specifics
│       ├── fph_satellite1.py    # FutureProofHomes Satellite1
│       └── generic_linux.py     # PulseAudio / ALSA fallback
├── requirements.txt
├── install.sh                   # One-line installer
├── atlas-sat-prepare.sh         # SD card preparation script
├── Dockerfile
└── tests/
```

---

## Installation Methods

### Method 1: Pre-configured SD Card Image (easiest)

Download a pre-built image with the satellite agent already installed:

```bash
# Download and flash
wget https://github.com/Betanu701/atlas-cortex/releases/download/latest/atlas-satellite-rpi.img.gz
gunzip atlas-satellite-rpi.img.gz
# Flash with Raspberry Pi Imager, balenaEtcher, or dd
```

### Method 2: Standard OS + Auto-Provision (recommended)

1. Flash Raspberry Pi OS Lite (or DietPi) to SD card
2. Use Raspberry Pi Imager to set hostname `atlas-sat-XXXX`, enable SSH, configure Wi-Fi
3. Boot and connect to network
4. Atlas discovers and provisions automatically via admin panel

### Method 3: Manual Install Script (any Linux device)

```bash
# On the satellite device:
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash

# Or manually:
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex/satellite
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m atlas_satellite --server ws://atlas-server:5100/ws/satellite
```

### Method 4: Docker (any Linux device)

```bash
docker run -d \
  --name atlas-satellite \
  --device /dev/snd \
  --net host \
  -e ATLAS_SERVER=ws://atlas-server:5100/ws/satellite \
  -e ROOM=Kitchen \
  ghcr.io/betanu701/atlas-satellite:latest
```

### Method 5: ESPHome (ESP32-S3 / FutureProofHomes Satellite1)

Flash via USB or web flasher, configure via captive portal, auto-discovered by Atlas.

---

## Implementation Phases

| Task | Description | Dependencies |
|------|-------------|--------------|
| S2.5.1 | Satellite agent core — audio capture, playback, agent loop | None |
| S2.5.2 | Wake word integration — openWakeWord default, pluggable | S2.5.1 |
| S2.5.3 | VAD + AEC — Silero VAD, speexdsp echo cancellation | S2.5.1 |
| S2.5.4 | Server WebSocket — satellite connection handler, audio streaming | S2.5.1 |
| S2.5.5 | Discovery service — hostname scan, mDNS listen, manual add | None |
| S2.5.6 | Hardware detector — SSH-based platform/audio/sensor detection | S2.5.5 |
| S2.5.7 | Provisioning engine — install agent, configure, start service via SSH | S2.5.4, S2.5.6 |
| S2.5.8 | Admin API — satellite CRUD, discover, provision, reconfigure endpoints | S2.5.5, S2.5.7 |
| S2.5.9 | Admin UI — Satellites page (list, detail, config, provision wizard) | S2.5.8 |
| S2.5.10 | LED/feedback — visual state indicators, platform abstraction | S2.5.1 |
| S2.5.11 | Wyoming protocol — HA voice pipeline compatibility | S2.5.4 |
| S2.5.12 | Platform abstraction — Pi, ESP32, FPH Satellite1, generic Linux | S2.5.1 |
| S2.5.13 | Installer — install.sh, SD card prep script, Docker image | S2.5.7 |
| S2.5.14 | Offline fallback — cached error TTS, reconnection logic | S2.5.4 |
| S2.5.15 | ESPHome integration — firmware, captive portal, OTA updates | S2.5.5 |

### Dependency Graph

```
S2.5.1 (Agent Core) ──┬──▶ S2.5.2 (Wake Word)
                       ├──▶ S2.5.3 (VAD + AEC)
                       ├──▶ S2.5.10 (LED Feedback)
                       ├──▶ S2.5.12 (Platform Abstraction)
                       └──▶ S2.5.4 (Server WebSocket) ──┬──▶ S2.5.11 (Wyoming)
                                                         └──▶ S2.5.14 (Offline)
S2.5.5 (Discovery) ──▶ S2.5.6 (HW Detect) ──▶ S2.5.7 (Provisioning)
                                                     │
                       S2.5.8 (Admin API) ◀──────────┘
                            │
                       S2.5.9 (Admin UI)
                       S2.5.13 (Installer)
                       S2.5.15 (ESPHome)
```
