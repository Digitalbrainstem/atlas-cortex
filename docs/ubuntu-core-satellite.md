# Ubuntu Core Satellite Image

Atlas tablet satellites can run on **Ubuntu Core**, a snap-based immutable OS.
This replaces the Xubuntu live-ISO approach with a smaller (~600 MB vs ~2 GB),
auto-updating system.

## Why Ubuntu Core

| Feature | Xubuntu ISO | Ubuntu Core |
|---------|-------------|-------------|
| Image size | ~2 GB | ~600 MB |
| Updates | Manual apt + git pull | Automatic snap refresh |
| Rollback | None | Automatic snap rollback |
| Security | Standard Linux | Strict snap confinement |
| Display | X11 + XFCE + Chromium | Wayland + ubuntu-frame + WPE |
| Startup | ~30 s (desktop + browser) | ~10 s (compositor + kiosk) |

### Snap Stack

- **Ubuntu Core 24** — Minimal immutable OS
- **ubuntu-frame** — Wayland kiosk compositor (fullscreen, touch, OSK)
- **wpe-webkit-mir-kiosk** — Lightweight web renderer (no Chromium)
- **network-manager** — WiFi management
- **atlas-satellite** — Our agent, TUI, and audio handling

## Building the Image

### Prerequisites

```bash
sudo snap install snapcraft --classic
sudo snap install ubuntu-image --classic
```

### Build

```bash
# From the repository root
sudo bash satellite/tablet/build-core-image.sh
```

This runs four phases:

1. **Build snap** — `snapcraft` packages `atlas-satellite` from `satellite/snap/snapcraft.yaml`
2. **Model assertion** — Prepares the model (signed or `--dangerous` for development)
3. **Build image** — `ubuntu-image snap` assembles the `.img` file
4. **Checksum** — Generates `.sha256` alongside the image

Output: `build/ubuntu-core/atlas-tablet-core-v0.1.0-{TIMESTAMP}.img`

Use `--skip-snap` to reuse an existing `.snap` file during iteration.

## Flashing

Write the image to a USB drive or SD card:

```bash
sudo dd if=build/ubuntu-core/atlas-tablet-core-v0.1.0-*.img \
       of=/dev/sdX bs=32M status=progress
sync
```

For USB-C/Surface-style tablets, use a USB-A adapter or USB hub.

## First-Boot Configuration

On first boot, Ubuntu Core runs the console-conf wizard for initial user
setup. After that, configure the satellite with `snap set`:

```bash
# Connect to WiFi
snap set atlas-satellite wifi-ssid=MyNetwork wifi-password=secret

# Or use the helper
atlas-satellite.configure-wifi MyNetwork secret

# Point at the Atlas server
snap set atlas-satellite server-url=ws://192.168.3.8:5100/ws/satellite

# Set the room name
snap set atlas-satellite room="Living Room"
```

The configure hook writes `/var/snap/atlas-satellite/current/config/config.json`
and updates the kiosk URL to point at the Atlas avatar display.

## WiFi Setup

Two options:

1. **snap set** — `snap set atlas-satellite wifi-ssid=... wifi-password=...`
2. **CLI helper** — `atlas-satellite.configure-wifi SSID [PASSWORD]`

The CLI helper also lists available networks when called without arguments.

## Updating the Satellite

Ubuntu Core snaps auto-update by default. To manually refresh:

```bash
sudo snap refresh atlas-satellite
```

To pin a specific channel:

```bash
sudo snap refresh atlas-satellite --channel=0.1/stable
```

## SSH Access

The Atlas server can push its SSH key to the satellite and rotate the
password remotely. From the admin panel:

1. Open the satellite detail view
2. In the **SSH Access** card, click **Push SSH Key**
3. The server installs its public key and generates a random password
4. Use the displayed `ssh` command to connect

Password rotation is available via **Rotate Password** — the new password
is stored in the Atlas database and visible in the admin panel.

## Snap Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `server-url` | `ws://atlas-cortex.local:5100/ws/satellite` | WebSocket URL for Atlas server |
| `room` | `Default` | Room name for this satellite |
| `wifi-ssid` | *(none)* | WiFi network to connect to |
| `wifi-password` | *(none)* | WiFi password |

## Snap Interfaces

The `atlas-satellite` snap requires these interfaces:

| Interface | Purpose |
|-----------|---------|
| `network` | Outbound network (WebSocket to server) |
| `network-bind` | Inbound connections (service port 5110) |
| `audio-playback` | Speaker output (TTS, fillers) |
| `audio-record` | Microphone input (wake word, VAD) |
| `pulseaudio` | PulseAudio/PipeWire access |
| `alsa` | Direct ALSA access (fallback) |
| `wayland` | Wayland display (ubuntu-frame) |
| `opengl` | GPU acceleration |
| `network-manager` | WiFi configuration (setup-tui, configure-wifi) |
| `network-control` | Network management (setup-tui) |

## Directory Structure

```
satellite/
├── snap/
│   ├── snapcraft.yaml          # Snap build definition
│   ├── hooks/
│   │   └── configure           # Runs on 'snap set'
│   └── scripts/
│       └── configure-wifi.sh   # WiFi helper
└── tablet/
    ├── build-core-image.sh     # Image builder script
    ├── build-image.sh          # Legacy Xubuntu ISO builder
    └── ubuntu-core/
        └── model.json          # Ubuntu Core model assertion
```

## Troubleshooting

### Satellite not connecting

```bash
# Check agent status
sudo snap logs atlas-satellite -n 50

# Verify config
cat /var/snap/atlas-satellite/current/config/config.json

# Test network
ping atlas-cortex.local
```

### No audio

```bash
# Check audio devices
aplay -l
arecord -l

# Verify snap audio interfaces are connected
snap connections atlas-satellite | grep audio
```

### Display not showing

```bash
# Check ubuntu-frame status
sudo snap logs ubuntu-frame -n 20

# Check kiosk browser
sudo snap logs wpe-webkit-mir-kiosk -n 20

# Manually set kiosk URL
snap set wpe-webkit-mir-kiosk url="http://192.168.3.8:5100/avatar#skin=nick"
```

### WiFi issues

```bash
# List available networks
nmcli device wifi list

# Reconnect
atlas-satellite.configure-wifi MyNetwork secret

# Check connection
nmcli connection show --active
```
