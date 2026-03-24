# Atlas Tablet OS

Turn an old tablet (Surface Go, iPad-alternatives, x86 laptops) into
an Atlas satellite with full avatar display and voice. Flash the image,
boot, connect to WiFi — done forever.

## Supported Hardware

- Microsoft Surface Go (1824) — fully tested, includes linux-surface kernel
- Any x86_64 tablet/laptop with touchscreen, mic, speakers, WiFi
- Minimum: 2GB RAM, 16GB USB/storage, Intel/AMD x86_64

## Quick Start (Pre-Built Image)

1. Download `atlas-tablet-os-YYYYMMDD.iso` from
   [Releases](https://github.com/Betanu701/atlas-cortex/releases)
2. Flash to USB drive:
   ```bash
   sudo dd if=atlas-tablet-os-20250101.iso of=/dev/sdX bs=4M status=progress
   ```
   Or use [balenaEtcher](https://etcher.balena.io/) / Rufus.
3. Boot the tablet from USB
4. Touchscreen shows WiFi setup — pick your network, enter password
5. Atlas server auto-discovered via mDNS — avatar appears fullscreen

**That's it.** No installer, no terminal, no configuration. The image
IS the OS with everything pre-installed.

## Build Your Own Image

```bash
# On an Ubuntu/Debian build machine (not the tablet):
sudo apt install debootstrap squashfs-tools xorriso \
    grub-pc-bin grub-efi-amd64-bin mtools dosfstools

cd satellite/tablet
sudo ./build-image.sh            # Build ISO (~1.5 GB)
sudo ./build-image.sh --raw      # Build raw disk image
```

## Alternative: Install on Existing Ubuntu

If you already have Ubuntu installed on the tablet:

```bash
curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/tablet/install-tablet.sh | sudo bash
sudo reboot
```

## What's In The Image

Everything is pre-installed — nothing downloads at boot time:

| Component              | Details                                        |
|------------------------|------------------------------------------------|
| Base OS                | Ubuntu 24.04 LTS (Noble) minimal               |
| Window Manager         | Openbox (no full desktop — kiosk only)          |
| Browser                | Chromium in fullscreen kiosk mode               |
| Kernel                 | Generic + linux-surface (for Surface devices)   |
| Audio                  | PulseAudio + ALSA                               |
| Networking             | NetworkManager + WiFi captive portal            |
| Service Discovery      | Avahi (mDNS) for Atlas server auto-discovery    |
| Atlas Satellite Agent  | Python venv with all dependencies               |
| WiFi Setup             | Captive portal (hotspot → touchscreen setup)    |
| Display                | Auto-login → X → Openbox → Chromium kiosk       |

## First Boot Flow

```
Power on → Auto-login → X starts → Openbox → Chromium kiosk
                                                    │
                        ┌───────────────────────────┘
                        ▼
              ┌─── WiFi connected? ───┐
              │                       │
              No                      Yes
              │                       │
    Captive portal starts      mDNS discovers Atlas
    (hotspot + setup page)          │
              │                       ▼
    User picks WiFi          Avatar loads in fullscreen
    on touchscreen                  │
              │                       ▼
    Connects → marker file     Done forever ✓
              │
    Reboot into avatar
```

## Network Architecture

```
┌─────────────────────┐        WebSocket          ┌──────────────────┐
│   Tablet Satellite   │◄──────────────────────►│   Atlas Server    │
│                       │  audio + control msgs  │   (cortex)        │
│  • Chromium kiosk     │                        │                    │
│  • Avatar display     │◄──── HTTP/mDNS ───────│  :5100/avatar      │
│  • Mic/Speaker        │                        │  :5100/ws/satellite│
│  • Touch-to-talk      │                        │                    │
└─────────────────────┘                          └──────────────────┘
```

## Differences from Pi Satellite

| Feature          | Pi Satellite         | Tablet Satellite          |
|------------------|----------------------|---------------------------|
| Display          | None (headless)      | Full avatar (Chromium)    |
| Input            | Wake word / button   | Touch-to-talk + wake      |
| Window manager   | None                 | Openbox (kiosk)           |
| Kernel           | Stock Raspberry Pi   | Generic + linux-surface   |
| Camera           | Optional             | Optional (presence)       |
| Form factor      | Dedicated mic/spk    | All-in-one tablet         |
| Image builder    | Pi OS + modifications| debootstrap from scratch  |

## Configuration

After first boot, you can SSH in to adjust settings:

```bash
ssh atlas@atlas-tablet-XXXX.local   # password: atlas-setup
```

The satellite config is at `/opt/atlas-satellite/config.json`.

## Troubleshooting

### No WiFi on Surface Go

The image includes the `linux-surface` kernel. If WiFi still fails:

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

## Files

| File                   | Purpose                                      |
|------------------------|----------------------------------------------|
| `build-image.sh`      | Builds complete OS image (debootstrap+chroot)|
| `install-tablet.sh`   | Alternative: install on existing Ubuntu       |
| `setup.html`          | Touchscreen WiFi setup (offline fallback)     |
| `atlas-kiosk.service` | systemd unit for Chromium kiosk               |
