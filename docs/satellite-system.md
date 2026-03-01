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
2. **Plug-and-play provisioning** — flash an OS, set a hostname, boot → satellite announces itself, admin provisions from the panel
3. **Self-announcing** — satellites broadcast their presence via mDNS on boot; Atlas passively listens — no network scanning
4. **Shared mode** — install the satellite service on an existing machine without taking over the system; Atlas connects only to the satellite service, not SSH
5. **Admin panel managed** — add, configure, assign rooms, enable features, and reconfigure satellites from the web UI
6. **Local wake word** — wake word runs on-device for privacy and low latency
7. **Thin client** — satellites only capture/play audio; all intelligence lives on the Atlas server
8. **Wyoming-compatible** — integrates with Home Assistant Wyoming protocol for HA voice pipelines
9. **Graceful offline** — satellites cache essential TTS (e.g., "I can't reach Atlas right now") for server outages

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
2. Set the hostname to `atlas-satellite` (same for all, no per-device setup)
3. Boot the device and connect it to the network
4. The satellite announces itself — a notification appears in the Admin Panel
5. Admin clicks the notification and assigns a room, selects features, clicks "Provision"
6. Atlas SSHes in, detects hardware, installs the satellite agent, and configures everything

### Satellite Modes

Atlas supports two distinct satellite modes, depending on whether the device is dedicated to Atlas or shared with other workloads:

#### Dedicated Mode (default)

A device that exists solely as an Atlas satellite. Atlas has full control:

- **Discovery:** Self-announces via mDNS → appears in Admin Panel automatically
- **Provisioning:** Atlas SSHes in, installs agent, renames hostname, configures everything
- **Security:** SSH key installed, password auth disabled — Atlas manages the device
- **Management:** Full control from Admin Panel (restart, reconfigure, update, remove)
- **Examples:** Raspberry Pi with a mic/speaker HAT in the kitchen, ESP32-S3 on a shelf

#### Shared Mode

A device that's already running other services (media server, NAS, desktop, etc.) where the user wants to **add** the Atlas satellite service alongside their existing workload. Atlas does **not** take over the system:

- **Discovery:** No auto-discovery — admin manually enters IP/hostname + credentials in the Admin Panel
- **Provisioning:** User runs the install script themselves (or Atlas installs just the satellite service via SSH)
- **Security:** **No SSH key installed**, no password auth changes — the user manages their own system. Atlas communicates exclusively through the satellite service WebSocket port (default `:5110`), never SSH after initial setup
- **Hostname:** Not renamed — the device keeps its existing hostname
- **Management:** Atlas can restart/reconfigure the satellite service, but cannot modify the host system
- **Updates:** The satellite service self-updates or the user runs the update script manually
- **Examples:** Home server also running Plex, dev laptop, NAS box, office workstation with a USB mic

```
Dedicated Mode                          Shared Mode
┌─────────────────────┐                 ┌─────────────────────┐
│   Atlas Satellite   │                 │  Existing System    │
│   (full control)    │                 │  ┌───────────────┐  │
│                     │                 │  │ Plex, NAS,    │  │
│  mDNS announce      │                 │  │ desktop, etc. │  │
│  SSH key auth       │                 │  └───────────────┘  │
│  hostname: atlas-*  │                 │  ┌───────────────┐  │
│  agent + OS managed │                 │  │ Atlas Sat Svc │◄─── Atlas connects
│                     │                 │  │ (port 5110)   │    via WebSocket only
└─────────────────────┘                 │  └───────────────┘  │
      ▲ Atlas manages                   │  hostname: unchanged│
        everything                      └─────────────────────┘
                                              ▲ User manages
                                                the system
```

### Hostname Convention

All satellites boot with the **same default hostname** for zero-config discovery:

```
atlas-satellite
```

- Every new satellite uses `atlas-satellite` as its hostname during setup
- On boot, a lightweight `atlas-announce` service broadcasts via mDNS:
  - Service: `_atlas-satellite._tcp.local`
  - TXT record: `status=new`, `mac=XX:XX:XX:XX:XX:XX`
- Atlas passively listens for these announcements — **no network scanning**
- When multiple `atlas-satellite` hosts announce, Atlas distinguishes them by MAC/IP
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
│  1. Flash the pre-built Atlas Satellite image to an SD   │
│     card using ANY imaging tool (balenaEtcher, RPi       │
│     Imager, Rufus, dd — whatever you prefer).            │
│  2. Insert SD card, power on.                            │
│  3. Connect your phone to the "Atlas-Setup" WiFi.        │
│  4. A setup page opens — pick your network, enter your   │
│     password, tap Connect.  Done!                        │
│                                                          │
│  Power users: edit atlas-wifi.txt on the boot partition  │
│  before first boot to skip the captive portal.           │
│                                                          │
│  (No terminal, no SSH, no config files needed.)          │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│               SATELLITE SELF-ANNOUNCEMENT                 │
│                                                          │
│  Atlas never actively scans the network. Instead,        │
│  satellites announce themselves and Atlas listens:        │
│                                                          │
│  How it works:                                           │
│  1. On boot, a tiny `atlas-announce` service starts      │
│  2. It broadcasts via mDNS:                              │
│       _atlas-satellite._tcp.local                        │
│       TXT: status=new, mac=XX:XX:XX:XX:XX:XX            │
│  3. Atlas server runs a passive mDNS listener            │
│  4. New satellite appears in Admin Panel with a 🔔        │
│       notification: "New satellite found"                │
│  5. Admin provisions when ready (no time pressure)       │
│                                                          │
│  Fallback: Manual add (Admin Panel)                      │
│    • User enters IP address manually                     │
│    • Atlas tries default credentials (atlas/atlas-setup) │
│    • Or user enters custom username/password             │
│    • Useful for networks where mDNS is blocked           │
│                                                          │
│  After provisioning, the satellite upgrades to full      │
│  mDNS with room/status info for ongoing monitoring.      │
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

#### Option A: Pre-built Atlas Satellite Image (Recommended — works on Windows/Mac/Linux)

The easiest method — everything is baked in. A GitHub Actions pipeline automatically builds
fresh images from the latest Raspberry Pi OS each month.

1. Download the latest image from [GitHub Releases](https://github.com/Betanu701/atlas-cortex/releases)
   - `atlas-satellite-pi-armhf-YYYYMMDD.img` — for Pi Zero 2 W, Pi 3 (32-bit)
   - `atlas-satellite-pi-arm64-YYYYMMDD.img` — for Pi 4, Pi 5 (64-bit)
2. Flash to SD card using **any tool** (Raspberry Pi Imager, balenaEtcher, Rufus, dd)
3. Insert SD card, power on — the satellite creates a WiFi hotspot called **"Atlas-Setup"**
4. Connect your phone or laptop to **Atlas-Setup** → a setup page opens automatically
5. Pick your WiFi network, enter the password, tap **Connect**
6. The satellite connects to your WiFi and auto-discovers the Atlas server via mDNS
7. It appears in Atlas Admin → Satellites within 60 seconds

**Power-user alternative (skip the captive portal):**
Before ejecting the SD card, open the boot partition (FAT32 — visible on Windows) and edit
**`atlas-wifi.txt`** with your network name and password. The satellite connects on boot
without needing the captive portal.

**Default credentials:** `atlas` / `atlas-setup` (SSH enabled)

**What the image includes:**
- Atlas satellite agent (pre-installed at `/opt/atlas-satellite/`)
- **Captive portal** — creates a WiFi hotspot for zero-config setup when no WiFi is configured
- `atlas-wifi.txt` on boot partition for power-user WiFi pre-configuration
- `atlas-announce` mDNS service (auto-starts, broadcasts `_atlas-satellite._tcp.local`)
- First-boot service that installs Python dependencies and starts the agent
- SSH enabled with `atlas` user

**Image freshness:** A CI pipeline (`build-satellite-image.yml`) rebuilds from the latest
Raspberry Pi OS Lite monthly, on each Atlas release, or via manual trigger.

#### Option B: Raspberry Pi Imager (manual setup)

If you prefer the official Raspberry Pi OS without pre-built images:

1. Download and open [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose OS: **Raspberry Pi OS Lite (32-bit)** (for Pi Zero 2 W) or **64-bit** (for Pi 4/5)
3. Click the **gear icon ⚙️** (or Ctrl+Shift+X)
4. Set these values:
   - **Hostname:** `atlas-sat-01` (or any name)
   - **Enable SSH:** ✅ Use password authentication
   - **Username:** `atlas`
   - **Password:** `atlas-setup`
   - **Configure Wi-Fi:** enter your SSID and password
   - **Wi-Fi country:** your country code
5. Click **Write** and wait for it to finish
6. Insert SD card into the device and power on
7. In Atlas Admin → Satellites → **Add Manual**, enter the IP address
8. Atlas provisions the satellite via SSH (installs agent + announce service)

#### Option C: Already-running Linux device

If the device is already booted (a NUC, old laptop, Pi with existing OS, etc.), SSH in and run:

```bash
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/atlas-sat-prepare.sh | bash -s -- --live
```

This configures hostname, creates the `atlas` user, installs the `atlas-announce` mDNS service,
and the satellite will appear in your admin panel. Or use Atlas Admin → Satellites → **Add Manual**.

### ESPHome Satellites (ESP32-S3, FutureProofHomes Satellite1)

ESP32-based devices don't use SD cards — they're flashed via USB or over-the-air. Atlas provides a ready-to-use ESPHome firmware config.

**Method A: Web flasher (easiest — no tools needed)**

1. Connect the ESP32-S3 to your computer via USB
2. Open the Atlas web flasher: `https://your-atlas-server:5100/admin/#/flash-esphome`
3. The admin panel builds and flashes the firmware directly from the browser
4. Device reboots into captive portal Wi-Fi: **"Atlas-Satellite-Setup"**
5. Connect to the portal, enter your Wi-Fi credentials
6. Device reboots, connects to Wi-Fi, announces via mDNS
7. Appears in Admin Panel → configure room and features

**Method B: ESPHome CLI / Dashboard**

1. Clone the Atlas ESPHome config:
   ```bash
   curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/esphome/atlas-satellite.yaml -o atlas-satellite.yaml
   ```
2. Edit Wi-Fi credentials in the YAML (or use `!secret`)
3. Flash via ESPHome:
   ```bash
   esphome run atlas-satellite.yaml
   ```
4. Device announces via mDNS, appears in Admin Panel

**Method C: FutureProofHomes Satellite1**

The FPH Satellite1 ships with ESPHome pre-installed. Follow their [getting started guide](https://futureproofhomes.net/), then point it at your Atlas server. It will announce itself automatically.

```
All ESP32 methods result in:
  → mDNS announcement: _atlas-satellite._tcp.local
  → Appears in Admin Panel with "Announced" status
  → Admin configures room/features
  → Atlas pushes config via ESPHome API (no SSH needed)
  → OTA updates managed from Admin Panel
```

---

## Admin Panel — Satellites Page

> **This is a section within the main Atlas Cortex admin panel** (`/admin/#/satellites`), not a separate portal. It sits alongside Users, Devices, Safety, etc.

### Satellite Settings (Admin → Satellites → Settings)

| Setting | Default | Description |
|---------|---------|-------------|
| **mDNS listener** | Enabled | Passively listen for satellite self-announcements (no scanning) |
| **Hostname pattern** | `atlas-sat-{room}` | Pattern for renaming after provisioning |
| **Default SSH user** | `atlas` | Username for connecting to new satellites |
| **Default SSH password** | `atlas-setup` | Password for initial connection only |
| **SSH key** | *(auto-generated)* | Atlas server key installed on all satellites during provisioning |
| **Auto-provision** | Disabled | Automatically provision discovered satellites (advanced) |

### Satellite List View

```
┌──────────────────────────────────────────────────────────────────┐
│ 📡 Satellites                     [ 🔍 Scan Now ] [ + Add Manual ] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ 🔔 New satellite announced! (1 pending)                          │
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
│ ● Office (shared)         x86 Desktop         192.168.3.10      │
│   Room: Office            Status: Online       Uptime: 12d 4h   │
│                                                                  │
│ ○ Garage                  Pi Zero 2W          192.168.3.71      │
│   Room: Garage            Status: Offline      Last: 2h ago     │
│                                                                  │
│ 🆕 atlas-satellite        Unknown             192.168.3.78      │
│   Room: Unassigned        Status: Announced    [Configure →]     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

> **🔍 Scan Now** is a fallback for networks where mDNS is blocked. It performs a one-time subnet scan for `atlas-satellite` hostnames. Normally not needed — satellites announce themselves.

### Satellite Detail/Config View

```
┌──────────────────────────────────────────────────────────────────┐
│ 📡 Kitchen Speaker                              [Reconfigure ↻] │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ General                                                          │
│ ├─ Display Name:    [ Kitchen Speaker          ]                 │
│ ├─ Room:            [ Kitchen               ▾ ]                 │
│ ├─ Mode:            Dedicated                                    │
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

> **Shared mode satellites** show a "Shared" badge next to the mode. The "Restart Agent" button restarts only the satellite service (not the host). "Remove" uninstalls the satellite service but does not touch the host system. Hostname and system config fields are read-only.

### Manual Add Dialog

```
┌────────────────────────────────────────────────────────────┐
│ Add Satellite                                              │
│                                                            │
│ Mode:  (●) Dedicated — Atlas manages the entire device     │
│        (○) Shared — Add satellite service to existing host │
│                                                            │
│ ── Connection ──────────────────────────────────────────── │
│ IP / Hostname: [ 192.168.3.78          ]                   │
│ Username:      [ pi                     ]                  │
│ Password:      [ ••••••••               ]                  │
│   — or —                                                   │
│ SSH Key:       [ Use Atlas server key   ]                  │
│                                                            │
│ ── Shared Mode Only ────────────────────────────────────── │
│ ☐ Install satellite service via SSH (or install manually)  │
│ Satellite port: [ 5110 ]                                   │
│                                                            │
│ [ Connect & Detect Hardware ]                              │
└────────────────────────────────────────────────────────────┘
```

**Dedicated mode flow:** Atlas SSHes in → detects hardware → installs agent → installs SSH key → disables password auth → renames hostname → full management.

**Shared mode flow:** Admin enters IP + creds → Atlas connects via SSH (one-time) to detect hardware and optionally install the satellite service → after setup, Atlas communicates only via the satellite WebSocket port (`:5110`) → SSH credentials are **not stored** and SSH key is **not installed**. The user's system is untouched except for the satellite service.

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
  5. PLAY_FILLER {session_id}   — play a random pre-cached filler phrase
  6. COMMAND     {action: "listen" | "stop" | "volume" | "led" | "reboot", params: {}}
  7. CONFIG      {wake_word, volume, led_brightness, vad_sensitivity, features}
  8. SYNC_FILLERS {fillers: [{id, audio: bytes}]}  — push updated filler cache
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

### Response Latency & Filler Phrase Caching

Voice assistant UX is highly sensitive to perceived latency. The pipeline has variable response times depending on what's being processed:

| Request Type | Path | Expected Latency |
|---|---|---|
| "What time is it?" | Layer 1 (instant) | **< 500ms** |
| "Turn on kitchen lights" | Layer 2 (HA plugin) | **< 1.5s** |
| "What's the weather?" | Layer 2 (weather plugin) | **1-2s** |
| "Tell me about black holes" | Layer 3 (LLM) | **3-7s** |
| Complex multi-step queries | Layer 3 (LLM + tools) | **5-10s** |

For fast-path responses (Layers 1-2), no filler is needed — the answer arrives before any awkward silence. For LLM responses, the delay is noticeable. Rather than always playing a filler (which feels robotic), the **server decides dynamically**:

#### How It Works

```
User: "Hey Atlas, tell me about quantum computing"

  t=0ms    Wake word detected, audio streaming begins
  t=500ms  Audio ends (VAD silence), server starts processing
  t=600ms  Layer 1: no match
  t=650ms  Layer 2: no match → falls through to Layer 3 (LLM)
  t=650ms  Server starts LLM inference + starts a 1.5s timer
  t=1500ms LLM still processing → server sends PLAY_FILLER
  t=1500ms Satellite plays cached filler INSTANTLY (0ms latency)
            "Let me think about that..."
  t=3500ms LLM response + TTS ready → server sends TTS_START
            Satellite transitions smoothly from filler to real response
```

**If the response is ready before the 1.5s threshold, no filler plays at all.** The user just gets the answer.

#### Pre-Cached Filler Phrases

Each satellite stores ~10-15 pre-rendered TTS audio clips locally (~2-5 MB total):

```
Thinking fillers (used when processing takes > 1.5s):
  "Let me think about that..."
  "Good question, one moment..."
  "Hmm, let me look into that..."
  "Working on it..."
  "Give me just a second..."

Acknowledgment fillers (used when action confirmed but response generating):
  "Sure thing..."
  "On it..."
  "Absolutely..."

Context-aware fillers (server specifies which category):
  "Let me check on that..."          (for lookups/queries)
  "Let me crunch those numbers..."   (for math/data)
```

**Cache management:**
- Fillers are generated using the configured TTS voice during provisioning
- Stored locally on the satellite in `/opt/atlas-satellite/cache/fillers/`
- Server pushes updated fillers via `SYNC_FILLERS` message when voice settings change
- Satellite picks randomly within the category to avoid repetition
- Recently-played fillers are deprioritized (weighted random)

#### Filler Threshold Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `filler_threshold_ms` | 1500 | Delay before triggering a filler phrase |
| `filler_enabled` | true | Enable/disable filler phrases globally |
| `filler_category` | auto | Server auto-selects based on intent classification |

The threshold is tunable per-deployment. Faster hardware (dual-GPU) can lower it or disable fillers entirely.

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
        """Return satellites found via passive mDNS listener or on-demand scan."""

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
    """Passive mDNS listener + on-demand scan fallback."""

    async def start_listener(self):
        """Start background mDNS listener for _atlas-satellite._tcp.local.
        Runs passively — zero network traffic. Satellites announce themselves."""

    async def get_announced(self) -> list[DiscoveredSatellite]:
        """Return satellites that have self-announced since last check."""

    async def scan_now(self, subnet: str = "192.168.0.0/24") -> list[str]:
        """Admin-triggered one-time scan. Fallback for networks where mDNS is blocked.
        Scans for atlas-satellite hostnames via mDNS + ARP table."""

    async def check_host(self, ip: str) -> DiscoveredSatellite | None:
        """Probe a specific IP for satellite indicators (manual add)."""
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

The Atlas server accepts WebSocket connections from all satellites (both modes).
For **dedicated** satellites, the satellite connects to the server.
For **shared** satellites, the server connects to the satellite's local WebSocket port (`:5110`).

```python
# Server-side: accepts connections from dedicated satellites
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

# Server-side: connects OUT to shared satellites
async def connect_shared_satellite(ip: str, port: int = 5110):
    """Atlas connects to a shared satellite's local WebSocket."""
    async with websockets.connect(f"ws://{ip}:{port}/satellite") as ws:
        await handle_shared_session(ws)
```

### Admin API Endpoints

```
GET    /admin/satellites              — List all satellites (with status and mode)
GET    /admin/satellites/:id          — Satellite detail + hardware info
POST   /admin/satellites/discover     — One-time network scan (fallback for mDNS-blocked networks)
POST   /admin/satellites/add          — Add satellite (dedicated or shared mode)
POST   /admin/satellites/:id/provision — Start provisioning with config (dedicated only)
PATCH  /admin/satellites/:id          — Update satellite config (room, features, etc.)
POST   /admin/satellites/:id/restart  — Restart satellite agent (service only for shared)
POST   /admin/satellites/:id/test     — Run audio test
DELETE /admin/satellites/:id          — Remove: dedicated=full uninstall, shared=disconnect only
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
    mode            TEXT DEFAULT 'dedicated',  -- "dedicated" or "shared"
    platform        TEXT,                      -- "rpi4", "esp32s3", "fph-sat1", "x86"
    hardware_info   TEXT,                      -- JSON: full detection results
    capabilities    TEXT,                      -- JSON: {mic, speaker, led, sensors, aec}
    features        TEXT,                      -- JSON: {wake_word, led, aec, wyoming, presence}
    wake_word       TEXT DEFAULT 'hey atlas',
    volume          REAL DEFAULT 0.7,
    mic_gain        REAL DEFAULT 0.8,
    vad_sensitivity REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'new',        -- new, announced, provisioning, online, offline, error
    provision_state TEXT,                      -- JSON: step-by-step progress
    ssh_username    TEXT,                      -- NULL for shared mode after setup
    ssh_key_installed BOOLEAN DEFAULT FALSE,   -- always FALSE for shared mode
    service_port    INTEGER DEFAULT 5110,      -- WebSocket port for satellite service
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
├── install.sh                   # One-line installer (any Linux)
├── atlas-sat-prepare.sh         # SD card/live system preparation script
├── atlas-announce.service       # systemd unit for mDNS self-announcement
├── atlas-announce.py            # Lightweight mDNS announcer (runs before provisioning)
├── esphome/
│   ├── atlas-satellite.yaml     # Base ESPHome config for ESP32-S3
│   ├── atlas-satellite-fph.yaml # FutureProofHomes Satellite1 variant
│   └── components/              # Custom ESPHome components
├── Dockerfile
└── tests/
```

---

## Installation Methods

### Method 1: Pre-configured SD Card Image (easiest)

Download a pre-built image with the satellite agent and announce service already installed:

```bash
# Download image
wget https://github.com/Betanu701/atlas-cortex/releases/download/latest/atlas-satellite-rpi.img.gz
gunzip atlas-satellite-rpi.img.gz
# Flash with ANY tool: Raspberry Pi Imager, balenaEtcher, dd, Rufus, etc.
# Image comes pre-configured: hostname=atlas-satellite, user=atlas, SSH enabled
# Just set Wi-Fi credentials (via Imager settings or atlas-sat-prepare.sh)
```

### Method 2: Standard OS + Prepare Script (recommended)

1. Flash **any** Linux OS (Raspberry Pi OS Lite, DietPi, Armbian, Ubuntu Server, etc.) using **any** flasher
2. Configure hostname/SSH/Wi-Fi using your flasher's built-in settings, **or** run the prepare script on the mounted SD card:
   ```bash
   curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/atlas-sat-prepare.sh | bash
   ```
3. Boot the device — it announces itself via mDNS
4. Atlas auto-provisions via the admin panel

### Method 3: Shared Mode — Add to Existing Machine (any Linux device)

For machines already running other services. Installs **only** the satellite service — does not modify hostname, SSH config, or any other system setting.

```bash
# On the existing machine, run the satellite-only installer:
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash -s -- --shared

# This installs:
#   - atlas-satellite service (systemd)
#   - Listens on port 5110 (WebSocket)
#   - Does NOT modify hostname, SSH, or system config
#   - Does NOT announce via mDNS (admin adds it manually)

# Or manually:
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex/satellite
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m atlas_satellite --mode shared --port 5110
```

Then in the Admin Panel: **+ Add Manual** → select **Shared** mode → enter the machine's IP and port → Atlas connects to the satellite service directly (no SSH).

### Method 4: Dedicated Mode — Manual Install Script (any Linux device)

```bash
# On a dedicated satellite device:
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash

# Or manually:
git clone https://github.com/Betanu701/atlas-cortex.git
cd atlas-cortex/satellite
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m atlas_satellite --server ws://atlas-server:5100/ws/satellite
```

### Method 5: Docker (any Linux device)

```bash
docker run -d \
  --name atlas-satellite \
  --device /dev/snd \
  --net host \
  -e ATLAS_SERVER=ws://atlas-server:5100/ws/satellite \
  -e ROOM=Kitchen \
  ghcr.io/betanu701/atlas-satellite:latest
```

### Method 6: ESPHome (ESP32-S3 / FutureProofHomes Satellite1)

```bash
# Option A: Use the Atlas web flasher from your browser (admin panel)
#   Navigate to Admin → Satellites → Flash ESPHome

# Option B: Use the ESPHome CLI
curl -sSL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/esphome/atlas-satellite.yaml -o atlas-satellite.yaml
# Edit Wi-Fi credentials in the YAML
esphome run atlas-satellite.yaml

# Option C: FutureProofHomes Satellite1 — ships with ESPHome pre-installed
#   Follow FPH getting started guide, point at Atlas server
```

All ESP32 devices announce via mDNS and appear in the Admin Panel automatically.

---

## Implementation Phases

| Task | Description | Dependencies |
|------|-------------|--------------|
| S2.5.1 | Satellite agent core — audio capture, playback, agent loop | None |
| S2.5.2 | Wake word integration — openWakeWord default, pluggable | S2.5.1 |
| S2.5.3 | VAD + AEC — Silero VAD, speexdsp echo cancellation | S2.5.1 |
| S2.5.4 | Server WebSocket — satellite connection handler, audio streaming | S2.5.1 |
| S2.5.5 | Discovery service — passive mDNS listener, on-demand scan fallback, manual add | None |
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
