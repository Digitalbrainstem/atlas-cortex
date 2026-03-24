# Atlas Tablet Satellite

Turn an old tablet (Surface Go, iPad-alternatives, x86 laptops) into
an Atlas satellite with full avatar display and voice.

## Supported Hardware

- Microsoft Surface Go (1824) — fully tested
- Any x86_64 tablet/laptop with touchscreen, mic, speakers, WiFi
- Minimum: 2GB RAM, 16GB storage, Intel/AMD x86_64

## Quick Install

### Option 1: Install on existing Ubuntu

```bash
curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/tablet/install-tablet.sh | sudo bash
```

### Option 2: Build USB installer image

```bash
# On your build machine:
cd satellite/tablet
./build-image.sh
# Flash the output ISO to a USB drive
# Boot the tablet from USB
```

## What It Does

1. Installs minimal Ubuntu desktop (Openbox window manager)
2. Installs linux-surface kernel (for Surface Go touchscreen/WiFi)
3. Sets up auto-login kiosk (Chromium in fullscreen)
4. Installs Atlas satellite agent (audio streaming)
5. Configures first-boot WiFi setup (captive portal on screen)
6. Auto-discovers Atlas server via mDNS
7. Opens avatar display in fullscreen browser

## First Boot

1. Tablet boots → shows Atlas WiFi setup screen
2. Select your WiFi network, enter password
3. Atlas server auto-discovered → avatar appears
4. Tap the screen to talk, Atlas responds

## Configuration

After setup, SSH in (if enabled) or use the touch menu:

- Long-press top-right corner → opens settings overlay
- Or SSH: `ssh atlas@atlas-tablet.local` (password: `atlas-setup`)

## Network Architecture

```
┌──────────────────┐        WebSocket         ┌──────────────────┐
│  Tablet Satellite │◄──────────────────────►│   Atlas Server    │
│                    │  audio + control msgs  │   (cortex)        │
│  • Chromium kiosk  │                        │                    │
│  • Avatar display  │◄──── HTTP/mDNS ───────│  :5100/avatar      │
│  • Mic/Speaker     │                        │  :5100/ws/satellite│
│  • Touch-to-talk   │                        │                    │
└──────────────────┘                          └──────────────────┘
```

## Differences from Pi Satellite

| Feature          | Pi Satellite        | Tablet Satellite         |
|------------------|---------------------|--------------------------|
| Display          | None (headless)     | Full avatar (Chromium)   |
| Input            | Wake word / button  | Touch-to-talk + wake     |
| Window manager   | None                | Openbox (kiosk)          |
| Kernel           | Stock Raspberry Pi  | linux-surface (if needed)|
| Camera           | Optional            | Optional (presence)      |
| Form factor      | Dedicated mic/spk   | All-in-one tablet        |

## Troubleshooting

### No WiFi on Surface Go

The Surface Go requires the `linux-surface` kernel for WiFi. The
installer handles this automatically. If WiFi still fails:

```bash
sudo apt install linux-image-surface linux-headers-surface iptsd
sudo reboot
```

### Avatar not loading

Check that the Atlas server is reachable:

```bash
avahi-resolve -n atlas-cortex.local
curl http://atlas-cortex.local:5100/health
```

### Touch not working

For Surface Go, ensure `iptsd` is running:

```bash
sudo systemctl status iptsd
sudo systemctl restart iptsd
```

### Audio issues

```bash
# List audio devices
aplay -l
arecord -l

# Test playback
speaker-test -c 2 -t wav

# Test recording
arecord -d 5 test.wav && aplay test.wav
```
