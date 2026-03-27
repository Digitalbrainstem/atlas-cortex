# Debian 12 Minimal Kiosk Image — Build Plan

> **Status:** Research & planning complete. Ready to implement.
> **Target:** Surface Go tablets (Gen 1 & 2)
> **Goal:** ≤1 GB ISO, boots to fullscreen Atlas avatar, no desktop environment

## Background & Motivation

We've tried three approaches so far:

| Approach | ISO Size | Result |
|----------|----------|--------|
| **Ubuntu Core + snaps** | ~600 MB | Snap confinement blocked audio, TTY access, no Surface drivers |
| **Xubuntu 24.04 remaster** | ~2 GB | Works but bloated — XFCE, LightDM, X11 stack we don't need |
| **Ubuntu Core + ubuntu-frame** | ~500 MB | WPE renderer too limited, snap audio still broken |

**The new approach:** Build from scratch using Debian 12 debootstrap. No desktop
environment, no display manager, no X11. Just a Wayland kiosk compositor (Cage)
running a single fullscreen Chromium window.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                  GRUB (UEFI)                    │
│         linux-surface 6.18 kernel               │
├─────────────────────────────────────────────────┤
│                   systemd                       │
│  ┌─────────────┐  ┌───────────┐  ┌───────────┐ │
│  │atlas-satellite│  │ PipeWire  │  │NetworkMgr │ │
│  │  .service    │  │ + Wireplmb│  │+ wpasuppl │ │
│  └──────┬───────┘  └───────────┘  └───────────┘ │
│         │                                       │
│         ▼ (after WiFi ready)                    │
│  ┌─────────────────────────────────────────┐    │
│  │        cage.service (tty1)              │    │
│  │  cage -- chromium --kiosk --ozone=wayl  │    │
│  │  → http://ATLAS:5100/avatar#skin=nick   │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  Touch: iptsd (IPTS)   Audio: PipeWire + SOF    │
│  WiFi: iwlwifi         GPU: Intel i915 (KMS)    │
└─────────────────────────────────────────────────┘
```

---

## 1. Base System — Debian 12 debootstrap

### Approach

Use `debootstrap --variant=minbase` to create a minimal Debian 12 (Bookworm)
root filesystem. This gives us ~200 MB base with only essential packages.

### Command

```bash
debootstrap --variant=minbase --arch=amd64 \
    --include=systemd,systemd-sysv,dbus,udev,kmod,iproute2,ca-certificates,apt-transport-https,gnupg,wget \
    bookworm "$ROOTFS" http://deb.debian.org/debian
```

### Sources List

```bash
# /etc/apt/sources.list
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware
```

The `non-free-firmware` component is critical — it contains Intel WiFi and SOF
audio firmware that Surface Go requires.

### Estimated Size

| Component | Installed Size |
|-----------|---------------|
| minbase rootfs | ~200 MB |

---

## 2. Linux-Surface Kernel

### Source

- **Repository:** `https://pkg.surfacelinux.com/debian`
- **Key:** `https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc`
- **Current version:** 6.18.7-surface-1

### Installation in Chroot

```bash
# Import GPG key
wget -qO - https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc \
    | gpg --dearmor > /etc/apt/trusted.gpg.d/linux-surface.gpg

# Add repository
echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" \
    > /etc/apt/sources.list.d/linux-surface.list

apt update

# Install kernel and Surface-specific packages
apt install -y \
    linux-image-surface \
    linux-headers-surface \
    libwacom-surface \
    iptsd
```

### What This Provides

| Package | Purpose |
|---------|---------|
| `linux-image-surface` | Kernel with Surface patches (IPTS touch, cameras, buttons, thermal) |
| `linux-headers-surface` | Headers for DKMS module builds |
| `libwacom-surface` | Wacom tablet definitions for Surface stylus |
| `iptsd` | Intel Precise Touch & Stylus daemon (multitouch, palm rejection, stylus) |

### Kernel Features

- Intel i915 GPU with KMS (direct rendering, no Xorg needed)
- IPTS (Intel Precise Touch & Stylus) — kernel driver + iptsd userspace
- Intel SOF audio support (HDA codec path for Surface Go's Realtek ALC298)
- Intel WiFi (iwlwifi) support
- Surface-specific ACPI/power management patches
- Thermal management fixes for Surface Go

### Secure Boot

```bash
apt install -y linux-surface-secureboot-mok
```

Optional — only needed if Secure Boot is enabled. Enrolls the linux-surface
signing key into the MOK (Machine Owner Key) database.

### Estimated Size

| Component | Installed Size |
|-----------|---------------|
| linux-image-surface | ~80 MB |
| linux-headers-surface | ~60 MB |
| Kernel modules | ~150 MB |
| **Subtotal** | **~290 MB** |

> **Note:** We can omit `linux-headers-surface` for production images if no
> DKMS modules are needed, saving ~60 MB.

---

## 3. Wayland Compositor — Cage

### Why Cage

| Compositor | Purpose-built kiosk | Touch support | Installed size | Config complexity |
|-----------|-------------------|---------------|---------------|------------------|
| **Cage** | ✅ Yes — single-app only | ✅ Native via libinput | **77 KB** | Zero (CLI only) |
| labwc | ❌ Desktop stacking WM | ✅ | ~500 KB | Config file |
| sway | ❌ Tiling WM | ✅ | ~3 MB | Config file |
| weston | ⚠️ Has kiosk plugin | ✅ | ~2 MB | Config file |

**Cage wins decisively.** It's purpose-built for exactly our use case: display a
single maximized application, prevent all interaction outside that application,
support touch input natively, and do nothing else.

### Package Details (Debian 12 Bookworm)

- **Package:** `cage` version 0.1.4-4
- **Installed size:** 77 KB (amd64)
- **Download size:** 21.4 KB
- **Key dependencies:**
  - `libwlroots10` (0.15.1) — 827 KB — Wayland compositor library
  - `libwayland-server0` — Wayland protocol
  - `libxkbcommon0` — Keyboard handling
  - `libinput10` — Touch/pointer input (via libwlroots10)
  - `libseat1` — Session/seat management
  - `libegl1`, `libgles2`, `libgbm1`, `libdrm2` — GPU rendering (via libwlroots10)

### Cage Behavior

- Runs on a TTY using KMS/DRM (no X11 or display manager needed)
- Displays exactly one application fullscreen
- Automatically exits when the application closes
- No window decorations, no task bar, no right-click menu
- Touch events pass through to the application directly
- Press Alt+Esc to quit (in debug builds)

### XWayland

Chromium on Debian 12 supports `--ozone-platform=wayland` natively. However,
some features (like certain extensions) may need XWayland. Install it as a
safety net:

```bash
apt install -y xwayland
```

- **Package:** `xwayland` version 22.1.9
- **Installed size:** 2.3 MB

### Estimated Size

| Component | Installed Size |
|-----------|---------------|
| cage | 77 KB |
| libwlroots10 + deps | ~5 MB |
| xwayland (optional) | ~2.3 MB |
| Mesa/DRI drivers (libgl1-mesa-dri) | ~50 MB |
| **Subtotal** | **~60 MB** |

---

## 4. Chromium Browser

### Debian 12 Ships Real .deb Packages

**Confirmed:** Debian 12 (Bookworm) provides Chromium as native `.deb` packages,
NOT snap redirects. This is a critical advantage over Ubuntu, where `apt install
chromium-browser` silently installs a snap.

### Packages

```bash
apt install -y \
    chromium \
    chromium-common \
    fonts-liberation
```

### Package Details

| Package | Download Size | Installed Size |
|---------|-------------|---------------|
| `chromium` | 71 MB | 259 MB |
| `chromium-common` | 29 MB | 68 MB |
| `fonts-liberation` | ~3 MB | ~5 MB |
| Shared deps (GTK3, NSS, etc.) | ~30 MB | ~80 MB |
| **Subtotal** | **~133 MB download** | **~412 MB installed** |

> **This is the largest single component.** The ~400 MB installed size is
> unavoidable — it's a full web browser. But it compresses well in squashfs
> (~100 MB compressed).

### Kiosk Flags

```bash
chromium \
    --kiosk \
    --no-first-run \
    --no-sandbox \
    --disable-translate \
    --disable-infobars \
    --disable-suggestions-ui \
    --disable-save-password-bubble \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --disable-pinch \
    --noerrdialogs \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --enable-features=OverlayScrollbar \
    --check-for-update-interval=31536000 \
    --ozone-platform=wayland \
    "http://ATLAS_SERVER:5100/avatar#skin=nick"
```

### Key Flags Explained

| Flag | Purpose |
|------|---------|
| `--kiosk` | Fullscreen, no URL bar, no tabs |
| `--no-sandbox` | Required when running as non-root without user namespaces |
| `--ozone-platform=wayland` | Native Wayland rendering (no XWayland needed for basic use) |
| `--autoplay-policy=no-user-gesture-required` | Allow avatar audio without user click |
| `--use-fake-ui-for-media-stream` | Auto-allow microphone (for voice input) |
| `--disable-pinch` | Prevent accidental zoom on touchscreen |
| `--check-for-update-interval=31536000` | Disable update checks (1 year) |

---

## 5. Audio — PipeWire + SOF Firmware

### Package List

```bash
apt install -y \
    pipewire \
    pipewire-pulse \
    pipewire-alsa \
    wireplumber \
    alsa-utils \
    firmware-sof-signed
```

### Package Details

| Package | Download Size | Installed Size |
|---------|-------------|---------------|
| `pipewire` | 78 KB | 97 KB |
| `pipewire-pulse` | 19 KB | 47 KB |
| `pipewire-alsa` | 53 KB | 193 KB |
| `wireplumber` | 79 KB | 502 KB |
| `libpipewire-0.3-modules` | ~1 MB | ~3 MB |
| `libpipewire-0.3-0` | ~200 KB | ~600 KB |
| `libwireplumber-0.4-0` | ~300 KB | ~900 KB |
| `alsa-utils` | ~1 MB | ~2 MB |
| `firmware-sof-signed` | 598 KB | 17.5 MB |
| **Subtotal** | **~3 MB download** | **~25 MB installed** |

### Surface Go Audio Architecture

The Surface Go uses an **Intel HDA** audio controller with a **Realtek ALC298**
codec. The linux-surface kernel includes patches for proper codec initialization.

**Known issue:** PipeWire/WirePlumber may not auto-detect the ALSA sound card on
Surface Go. We need to ensure the ALSA node is created at boot.

### WirePlumber ALSA Configuration

Create a WirePlumber rule to force ALSA device detection:

```lua
-- /etc/wireplumber/main.lua.d/51-surface-alsa.lua
rule = {
  matches = {
    {
      { "device.name", "matches", "alsa_card.*" },
    },
  },
  apply_properties = {
    ["api.alsa.use-acp"] = true,
    ["api.alsa.ignore-dB"] = false,
  },
}
table.insert(alsa_monitor.rules, rule)
```

### Fallback: Explicit ALSA Node Creation

If WirePlumber still doesn't detect the card, create a systemd service:

```ini
# /etc/systemd/system/atlas-audio-setup.service
[Unit]
Description=Atlas Audio Setup (Surface Go ALSA)
After=pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=oneshot
User=atlas
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/pw-cli create-node adapter { \
    factory.name=api.alsa.pcm.sink \
    node.name=alsa-sink \
    node.description="Surface Go Speakers" \
    media.class=Audio/Sink \
    api.alsa.path="hw:0,0" \
}
RemainAfterExit=yes

[Install]
WantedBy=default.target
```

### PipeWire as System Service vs User Service

PipeWire normally runs as a user service. Since our kiosk runs as the `atlas`
user via Cage, PipeWire user services will start in that user's session. We need
to ensure `XDG_RUNTIME_DIR` is set correctly.

```bash
# In the cage.service environment
Environment="XDG_RUNTIME_DIR=/run/user/1000"
```

PipeWire systemd user services (`pipewire.service`, `pipewire-pulse.service`,
`wireplumber.service`) are enabled by default when the packages are installed.
They start automatically when a user session begins.

---

## 6. Touch — IPTS Driver

### How It Works

1. **Kernel driver** (`ipts`) — Included in linux-surface kernel. Handles the
   Intel Precise Touch & Stylus hardware protocol.
2. **Userspace daemon** (`iptsd`) — Processes raw touch data into multitouch
   events, handles palm rejection, and stylus pressure/tilt.
3. **libinput** — Standard Linux input library. Picks up iptsd events and
   delivers them to the Wayland compositor (Cage → Chromium).

### Verification

```bash
# Check IPTS kernel module is loaded
lsmod | grep ipts

# Check iptsd is running
systemctl status iptsd

# See touch events
libinput debug-events
```

### Wayland Compatibility

Touch input works natively with Cage. No special configuration needed:
- Cage uses `libinput` for all input handling
- `libinput` supports multitouch, gestures, and stylus
- The `atlas` user must be in the `input` group for device access

### Package Details

| Package | Source |
|---------|--------|
| `iptsd` | linux-surface repository |
| Kernel IPTS driver | Built into linux-surface kernel |

---

## 7. Networking — NetworkManager + WiFi

### Package List

```bash
apt install -y \
    network-manager \
    wpasupplicant \
    wireless-regdb \
    firmware-iwlwifi \
    dnsmasq-base \
    avahi-daemon
```

### Package Details

| Package | Download Size | Installed Size |
|---------|-------------|---------------|
| `network-manager` | 3 MB | 15 MB |
| `wpasupplicant` | 1.4 MB | 3.9 MB |
| `wireless-regdb` | ~10 KB | ~50 KB |
| `firmware-iwlwifi` | 9.1 MB | 82 MB |
| `dnsmasq-base` | ~300 KB | ~700 KB |
| `avahi-daemon` | ~250 KB | ~600 KB |
| **Subtotal** | **~14 MB download** | **~102 MB installed** |

> **Note:** `firmware-iwlwifi` is large (82 MB installed) because it includes
> firmware for ALL Intel WiFi chipsets. We could trim this to just the Surface
> Go's chipset files post-install to save ~70 MB.

### WiFi Firmware Trimming (Optional)

The Surface Go 1 uses Intel WiFi AX200/AX201 (CNVi). We only need:

```bash
# Keep only the needed firmware files
KEEP=(
    iwlwifi-cc-a0-77.ucode    # AX200
    iwlwifi-QuZ-a0-hr-b0-77.ucode  # AX201
    iwlwifi-ty-a0-gf-a0.pnvm
)
# Remove the rest from /lib/firmware/
```

This could reduce the firmware from 82 MB to ~5 MB.

### First-Boot WiFi Setup

Since this is Debian (not snap-confined Ubuntu Core), we have full TTY access.
The `atlas-satellite` service can directly manage WiFi configuration.

**Flow:**

1. `atlas-satellite.service` starts before `cage.service`
2. Checks if any WiFi connection is configured
3. If no WiFi: takes over tty1 to show a TUI WiFi scanner
4. User selects network, enters password using touch keyboard or USB keyboard
5. NetworkManager connects
6. After connection: `cage.service` starts

### NetworkManager Configuration

```ini
# /etc/NetworkManager/NetworkManager.conf
[main]
plugins=ifupdown,keyfile
dns=default

[ifupdown]
managed=false

[device]
wifi.scan-rand-mac-address=no
wifi.backend=wpa_supplicant
```

### AP Mode for Captive Portal (Alternative)

If touch-based TUI is too difficult for first-boot, the satellite can create a
WiFi hotspot:

```bash
# Create AP
nmcli device wifi hotspot ifname wlan0 ssid "Atlas-Setup" password "atlas123"
```

Users connect from their phone, visit `http://192.168.4.1`, and configure WiFi
through a web interface. This is the approach used in the Xubuntu build's captive
portal (`satellite/captive_portal/`).

---

## 8. Boot Flow

### Exact Sequence

```
 1. UEFI → GRUB (EFI System Partition)
 2. GRUB loads linux-surface kernel + initramfs
 3. initramfs (live-boot) mounts squashfs → overlayfs
 4. systemd starts (default.target = graphical.target)
 │
 5. ├─ iptsd.service starts (touch driver)
 │  ├─ NetworkManager.service starts
 │  ├─ avahi-daemon.service starts (mDNS)
 │  └─ atlas-satellite.service starts
 │
 6. atlas-satellite checks WiFi:
 │  ├─ IF connected → signal ready, continue
 │  └─ IF no WiFi → start AP mode / TUI on tty2
 │     └─ User configures WiFi
 │     └─ Connection established → signal ready
 │
 7. cage@tty1.service starts (After=atlas-satellite-ready.target)
 │  └─ cage -- chromium --kiosk --ozone-platform=wayland \
 │       http://ATLAS_SERVER:5100/avatar#skin=nick
 │
 8. PipeWire + WirePlumber start (user services, triggered by session)
 │
 9. Atlas avatar loads in Chromium
    ├─ WebSocket connects to Atlas server
    ├─ Audio output via PipeWire → ALSA → speakers
    └─ Microphone input via PipeWire → ALSA → server
```

### Service Dependencies

```
                 ┌──────────────┐
                 │ multi-user   │
                 │  .target     │
                 └──────┬───────┘
            ┌───────────┼────────────┐
            ▼           ▼            ▼
    ┌──────────┐  ┌──────────┐  ┌─────────┐
    │NetworkMgr│  │  iptsd   │  │  avahi   │
    └────┬─────┘  └──────────┘  └─────────┘
         ▼
    ┌──────────────────┐
    │ atlas-satellite   │
    │   .service        │
    │ (WiFi check/setup)│
    └────────┬─────────┘
             ▼
    ┌──────────────────┐
    │ cage@tty1.service │
    │ (Wayland kiosk)   │
    └──────────────────┘
```

---

## 9. Systemd Service Configurations

### cage@tty1.service

```ini
# /etc/systemd/system/cage@.service
[Unit]
Description=Cage Wayland Kiosk on %I
After=atlas-satellite.service network-online.target
Wants=network-online.target
ConditionPathExists=/usr/bin/cage

[Service]
Type=simple
User=atlas
Group=atlas
SupplementaryGroups=video input render audio

# Wayland environment
Environment="WLR_LIBINPUT_NO_DEVICES=1"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="XDG_SESSION_TYPE=wayland"
Environment="XDG_CURRENT_DESKTOP=cage"

# TTY allocation
TTYPath=/dev/%I
StandardInput=tty
StandardOutput=journal
StandardError=journal

# Chromium kiosk
ExecStart=/usr/bin/cage -s -- /usr/local/bin/atlas-kiosk

# Restart on crash
Restart=on-failure
RestartSec=5

# Resource limits
MemoryMax=1G
TasksMax=512

[Install]
WantedBy=graphical.target
```

### atlas-satellite.service

```ini
# /etc/systemd/system/atlas-satellite.service
[Unit]
Description=Atlas Satellite Agent
After=network-online.target NetworkManager.service avahi-daemon.service
Wants=network-online.target

[Service]
Type=notify
User=atlas
Group=atlas
WorkingDirectory=/opt/atlas-satellite
ExecStart=/opt/atlas-satellite/venv/bin/python -m atlas_satellite.agent
Restart=always
RestartSec=10
Environment="HOME=/home/atlas"
Environment="XDG_RUNTIME_DIR=/run/user/1000"

# WiFi setup capability
SupplementaryGroups=netdev

# Notify systemd when WiFi is ready
NotifyAccess=main

[Install]
WantedBy=multi-user.target
```

### atlas-first-boot.service

```ini
# /etc/systemd/system/atlas-first-boot.service
[Unit]
Description=Atlas First Boot Setup
ConditionPathExists=!/etc/atlas/.first-boot-done
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/atlas-first-boot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

### getty@tty1.service.d/override.conf (Autologin)

```ini
# /etc/systemd/system/getty@tty1.service.d/override.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin atlas --noclear %I $TERM
Type=idle
```

> **Note:** We may not need getty autologin at all if cage@tty1.service runs
> directly as the atlas user. The cage service approach is cleaner because it
> doesn't require a login session. We should test both approaches.

### logind.conf.d overrides

```ini
# /etc/systemd/logind.conf.d/atlas-kiosk.conf
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
NAutoVTs=2
```

---

## 10. Autologin Strategy

### Recommended: Direct Cage Service (No Login)

The cleanest approach for a kiosk is to run Cage directly as a systemd system
service, without any login manager or getty autologin. The `cage@tty1.service`
above does this — it runs as `User=atlas` with `TTYPath=/dev/tty1`.

This means:
- No LightDM, GDM, or any display manager
- No getty on tty1 (Cage takes it over)
- No PAM login session (but we create XDG_RUNTIME_DIR manually)
- tty2 remains available for emergency console access

### XDG_RUNTIME_DIR Setup

Since there's no login manager to create the runtime directory, we need a tmpfiles rule:

```ini
# /etc/tmpfiles.d/atlas-runtime.conf
d /run/user/1000 0700 atlas atlas -
```

Or a oneshot service:

```ini
# /etc/systemd/system/atlas-runtime-dir.service
[Unit]
Description=Create XDG_RUNTIME_DIR for atlas user
Before=cage@tty1.service atlas-satellite.service

[Service]
Type=oneshot
ExecStart=/bin/mkdir -p /run/user/1000
ExecStart=/bin/chown atlas:atlas /run/user/1000
ExecStart=/bin/chmod 0700 /run/user/1000
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Alternatively, enable `systemd-logind` with autologin via `loginctl enable-linger atlas`,
which creates `/run/user/1000` automatically and enables user services.

### Fallback: getty + .bash_profile

If the direct cage service approach has issues (some Wayland compositors need a
proper login session for seat management), fall back to:

```bash
# /home/atlas/.bash_profile
if [ "$(tty)" = "/dev/tty1" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    exec cage -s -- /usr/local/bin/atlas-kiosk
fi
```

Combined with getty autologin, this gives us a proper login session.

---

## 11. Installer ISO Strategy

### Two-Part ISO

The ISO serves dual purpose:

1. **Live kiosk** — Boot from USB, runs the full kiosk stack from RAM
2. **Installer** — A GRUB menu option to install to internal SSD/eMMC

### ISO Structure (live-boot)

```
atlas-tablet-debian.iso
├── boot/
│   └── grub/
│       ├── grub.cfg
│       └── fonts/
├── EFI/
│   └── BOOT/
│       └── BOOTX64.EFI
├── live/
│   ├── vmlinuz              (linux-surface kernel)
│   ├── initrd.img           (initramfs with live-boot hooks)
│   └── filesystem.squashfs  (compressed rootfs)
└── .disk/
    └── info
```

### live-boot Package

**Package:** `live-boot` version 20230131 (Debian 12)
**Installed size:** 118 KB

live-boot provides initramfs hooks that:
1. Find the squashfs image on the boot medium
2. Mount it read-only
3. Create an overlayfs (tmpfs) on top for writes
4. Pivot root into the overlay

### Kernel Parameters

```
boot=live components quiet splash consoleblank=0 toram
```

| Parameter | Purpose |
|-----------|---------|
| `boot=live` | Enable live-boot system |
| `components` | Required by live-boot |
| `toram` | Copy squashfs to RAM (Surface Go has 4-8 GB, squashfs ~400 MB) |
| `consoleblank=0` | Prevent console blanking |
| `quiet splash` | Clean boot (no kernel messages) |

### GRUB Configuration

```
set default=0
set timeout=3

menuentry "Atlas Tablet OS" {
    linux /live/vmlinuz boot=live components toram quiet splash consoleblank=0
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Safe Mode" {
    linux /live/vmlinuz boot=live components toram nomodeset consoleblank=0
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Install to Disk" {
    linux /live/vmlinuz boot=live components toram quiet consoleblank=0 atlas.install=1
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Debug (console only)" {
    linux /live/vmlinuz boot=live components toram consoleblank=0 systemd.unit=multi-user.target
    initrd /live/initrd.img
}
```

### Install-to-Disk Flow

When booted with `atlas.install=1`, the first-boot service detects this and
runs an installer instead of the kiosk:

1. Detect internal drive (eMMC on Surface Go 1, NVMe on Surface Go 2)
2. Show TUI: confirm target drive, optional WiFi setup
3. Partition: GPT with 512 MB EFI (FAT32) + remainder ext4
4. Copy live rootfs to ext4 via rsync
5. Generate proper `/etc/fstab` with UUIDs
6. `chroot` into installed system, run `grub-install` + `update-grub`
7. Copy GRUB to fallback EFI path (`/EFI/BOOT/BOOTX64.EFI`)
8. Reboot into installed system

---

## 12. Build Script Steps

### Prerequisites (Build Machine)

```bash
sudo apt install -y \
    debootstrap \
    squashfs-tools \
    xorriso \
    grub-efi-amd64-bin \
    grub-pc-bin \
    mtools \
    dosfstools \
    wget \
    gnupg
```

### Build Phases

```
Phase 1: Create rootfs via debootstrap
Phase 2: Mount pseudo-filesystems (/dev, /proc, /sys, /run)
Phase 3: Configure apt sources (main + non-free-firmware + linux-surface)
Phase 4: Install kernel (linux-surface)
Phase 5: Install display stack (cage, mesa, xwayland)
Phase 6: Install Chromium
Phase 7: Install audio stack (pipewire, wireplumber, firmware-sof)
Phase 8: Install networking (NetworkManager, wpasupplicant, firmware-iwlwifi)
Phase 9: Install utilities (avahi, openssh-server, curl, whiptail)
Phase 10: Install live-boot (live-boot, live-boot-initramfs-tools)
Phase 11: Install atlas-satellite agent (Python venv in /opt/)
Phase 12: Create atlas user, configure groups, autologin
Phase 13: Deploy systemd services (cage, satellite, first-boot, audio-setup)
Phase 14: Deploy kiosk launcher script (/usr/local/bin/atlas-kiosk)
Phase 15: Configure system (hostname, locale, timezone, logind, fstab)
Phase 16: Strip unnecessary files (docs, man pages, extra locales)
Phase 17: Trim WiFi firmware (optional — keep only Surface Go chipsets)
Phase 18: Clean apt cache, temp files
Phase 19: Unmount pseudo-filesystems
Phase 20: Generate initramfs (update-initramfs inside chroot)
Phase 21: Copy kernel + initrd to ISO structure
Phase 22: Create squashfs (mksquashfs -comp xz)
Phase 23: Write GRUB config
Phase 24: Build ISO (grub-mkrescue)
Phase 25: Generate SHA256 checksum
```

### Key Script Patterns

```bash
# Chroot helper
chroot_exec() {
    chroot "$ROOTFS" /bin/bash -c "$1"
}

# Mount pseudo-filesystems
mount_chroot() {
    mount --bind /dev     "$ROOTFS/dev"
    mount --bind /dev/pts "$ROOTFS/dev/pts"
    mount -t proc proc    "$ROOTFS/proc"
    mount -t sysfs sys    "$ROOTFS/sys"
    mount -t tmpfs tmpfs  "$ROOTFS/run"
    cp /etc/resolv.conf   "$ROOTFS/etc/resolv.conf"
}

# Unmount (reverse order, lazy)
umount_chroot() {
    umount -lf "$ROOTFS/run"      2>/dev/null || true
    umount -lf "$ROOTFS/sys"      2>/dev/null || true
    umount -lf "$ROOTFS/proc"     2>/dev/null || true
    umount -lf "$ROOTFS/dev/pts"  2>/dev/null || true
    umount -lf "$ROOTFS/dev"      2>/dev/null || true
    rm -f "$ROOTFS/etc/resolv.conf"
}

# Squashfs creation
mksquashfs "$ROOTFS" "$ISO_DIR/live/filesystem.squashfs" \
    -comp xz -b 1M -Xdict-size 100% -no-duplicates -quiet

# ISO creation
grub-mkrescue -o "$OUTPUT_ISO" "$ISO_DIR" -- -volid "ATLAS_TABLET"
```

---

## 13. Atlas Kiosk Launcher Script

```bash
#!/bin/bash
# /usr/local/bin/atlas-kiosk
# Launched by cage.service — runs inside Cage Wayland compositor

set -euo pipefail

# ── Discover Atlas server via mDNS ───────────────────────────────
ATLAS_URL=""
for attempt in $(seq 1 30); do
    ATLAS_HOST=$(avahi-resolve-host-name -4 atlas-cortex.local 2>/dev/null | awk '{print $2}') || true
    if [ -n "$ATLAS_HOST" ]; then
        ATLAS_URL="http://${ATLAS_HOST}:5100"
        break
    fi
    sleep 2
done

# ── Fallback: check config file ─────────────────────────────────
if [ -z "$ATLAS_URL" ] && [ -f /opt/atlas-satellite/config.json ]; then
    ATLAS_URL=$(python3 -c "
import json
with open('/opt/atlas-satellite/config.json') as f:
    print(json.load(f).get('server_url', ''))
" 2>/dev/null) || true
fi

# ── Fallback: local setup page ───────────────────────────────────
if [ -z "$ATLAS_URL" ]; then
    ATLAS_URL="file:///opt/atlas-satellite/setup.html"
fi

# ── Launch Chromium ──────────────────────────────────────────────
exec chromium \
    --kiosk \
    --no-first-run \
    --no-sandbox \
    --disable-translate \
    --disable-infobars \
    --disable-suggestions-ui \
    --disable-save-password-bubble \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --disable-pinch \
    --noerrdialogs \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --enable-features=OverlayScrollbar \
    --check-for-update-interval=31536000 \
    --ozone-platform=wayland \
    "${ATLAS_URL}/avatar#skin=nick"
```

---

## 14. First-Boot Script

```bash
#!/bin/bash
# /usr/local/bin/atlas-first-boot
set -euo pipefail

MARKER="/etc/atlas/.first-boot-done"
[ -f "$MARKER" ] && exit 0

mkdir -p /etc/atlas

# Generate unique hostname
SERIAL=$(cat /sys/class/dmi/id/board_serial 2>/dev/null | tr -dc 'a-zA-Z0-9' | tail -c 6)
HOSTNAME="atlas-tablet-${SERIAL:-$(head -c 3 /dev/urandom | xxd -p)}"
hostnamectl set-hostname "$HOSTNAME"
echo "127.0.1.1 $HOSTNAME" >> /etc/hosts

# Generate machine ID (if not already set by live-boot)
[ -s /etc/machine-id ] || systemd-machine-id-setup

# Save config
cat > /etc/atlas/tablet.conf << EOF
hostname=$HOSTNAME
first_boot=$(date -Iseconds)
EOF

# Mark done
touch "$MARKER"
```

---

## 15. Size Estimates

### Component Breakdown (Installed Sizes)

| Component | Installed Size | Compressed (xz) |
|-----------|---------------|-----------------|
| Debian 12 minbase | 200 MB | ~70 MB |
| linux-surface kernel + modules | 230 MB* | ~50 MB |
| Chromium + deps | 412 MB | ~100 MB |
| Mesa DRI / GPU drivers | 50 MB | ~15 MB |
| Cage + libwlroots | 5 MB | ~2 MB |
| XWayland | 2 MB | ~1 MB |
| PipeWire + WirePlumber | 25 MB | ~8 MB |
| NetworkManager + wpa_supplicant | 19 MB | ~6 MB |
| firmware-iwlwifi | 82 MB** | ~9 MB |
| firmware-sof-signed | 18 MB | ~1 MB |
| avahi + dbus | 5 MB | ~2 MB |
| openssh-server | 5 MB | ~2 MB |
| Atlas satellite agent | 30 MB | ~10 MB |
| live-boot | 1 MB | ~0.5 MB |
| System configs + scripts | 1 MB | ~0.5 MB |
| Shared libraries (deduped) | ~100 MB | ~30 MB |
| **TOTAL (rootfs)** | **~1,185 MB** | — |
| **TOTAL (squashfs.xz)** | — | **~307 MB** |

\* Without headers: ~170 MB
\** After WiFi firmware trimming: ~15 MB

### Final ISO Size

| Component | Size |
|-----------|------|
| filesystem.squashfs (xz) | ~310 MB |
| vmlinuz + initrd.img | ~40 MB |
| GRUB EFI + BIOS | ~15 MB |
| ISO overhead | ~5 MB |
| **Total ISO** | **~370 MB** |

> **This is well under the 1.5 GB target.** Even with generous estimates and
> no firmware trimming, we'd be under 500 MB. Compare this to the Xubuntu
> remaster at ~2 GB.

### Optimizations to Reduce Further

1. **Drop `linux-headers-surface`** — Save ~60 MB (no DKMS needed)
2. **Trim `firmware-iwlwifi`** — Save ~70 MB (keep only Surface Go chipset)
3. **Drop `xwayland`** — Save ~2 MB (only if Chromium Wayland-native is stable)
4. **Strip `/usr/share/doc`, `/usr/share/man`** — Save ~50 MB
5. **Remove extra locales** — Save ~30 MB (keep only en_US)

With all optimizations: **~250 MB ISO**

---

## 16. Known Issues and Workarounds

### Issue 1: PipeWire Doesn't Auto-Detect ALSA on Surface Go

**Problem:** WirePlumber may not enumerate the ALSA sound card automatically,
especially on the Surface Go where the codec initialization sequence is
non-standard.

**Workaround:** WirePlumber ALSA rule (Section 5) + fallback `pw-cli create-node`
service. The Xubuntu build solved this with a kiosk script that calls `pw-cli`
before launching Chromium.

**Verification:**
```bash
wpctl status         # Should show sinks/sources
aplay -l             # Should list ALSA cards
pactl info           # Should show PipeWire as server
```

### Issue 2: Screen Blanking

**Problem:** Linux enables console blanking by default. Three independent
blanking mechanisms can turn off the screen.

**Workaround:** Triple protection:
1. Kernel parameter: `consoleblank=0` in GRUB
2. systemd service: `echo 0 > /sys/module/kernel/parameters/consoleblank`
3. Cage environment: `WLR_NO_HARDWARE_CURSORS=1` (prevents cursor sleep)

### Issue 3: Cage Needs Proper Seat Access

**Problem:** Cage requires access to a seat (via `libseat`) to use KMS/DRM.
Without `systemd-logind` or `seatd`, it can't take over the GPU.

**Workaround:** Either:
- Use `loginctl enable-linger atlas` so systemd-logind creates a session
- Install `seatd` as a standalone seat manager
- Use getty autologin + `.bash_profile` approach (gets a proper session)

**Recommended:** Use `loginctl enable-linger` — simplest, no extra packages.

### Issue 4: Chromium sandbox with --no-sandbox

**Problem:** Running Chromium without `--no-sandbox` requires either root
or user namespaces. In a kiosk, `--no-sandbox` is acceptable.

**Alternative:** If security is a concern, use:
```bash
sysctl -w kernel.unprivileged_userns_clone=1
```
Then remove `--no-sandbox`.

### Issue 5: Surface Go eMMC vs NVMe

**Problem:** Surface Go Gen 1 (4GB model) uses eMMC (`/dev/mmcblk0`), while
Gen 2 and the 8GB Gen 1 use NVMe (`/dev/nvme0n1`). The installer must detect
and handle both.

**Workaround:** Auto-detect in installer:
```bash
if [ -b /dev/nvme0n1 ]; then
    TARGET="/dev/nvme0n1"
elif [ -b /dev/mmcblk0 ]; then
    TARGET="/dev/mmcblk0"
fi
```

### Issue 6: Surface Go UEFI Boot Order

**Problem:** Surface Go UEFI may not recognize GRUB in non-standard EFI paths.

**Workaround:** Install GRUB to both the standard path and fallback:
```bash
# Standard location
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=atlas

# Fallback (UEFI spec requires this)
cp /boot/efi/EFI/atlas/grubx64.efi /boot/efi/EFI/BOOT/BOOTX64.EFI
```

### Issue 7: live-boot vs casper

**Problem:** Debian uses `live-boot`, Ubuntu uses `casper`. They have different
kernel parameters and squashfs locations.

**Clarification:** Since we're building on Debian 12, we use `live-boot`:
- Kernel param: `boot=live` (not `boot=casper`)
- Squashfs location: `/live/filesystem.squashfs` (not `/casper/`)
- Package: `live-boot` + `live-boot-initramfs-tools`

### Issue 8: Firmware in initramfs

**Problem:** The Intel WiFi and GPU firmware must be available during early
boot. The initramfs must include them.

**Workaround:** Ensure `/etc/initramfs-tools/modules` includes:
```
i915
iwlwifi
```

And run `update-initramfs -u` after installing firmware packages.

---

## 17. Complete Package Manifest

### Core System

```
systemd systemd-sysv dbus udev kmod
iproute2 ca-certificates apt-transport-https
gnupg wget curl
locales console-setup kbd
```

### Kernel & Firmware

```
linux-image-surface
iptsd libwacom-surface
firmware-iwlwifi
firmware-sof-signed
intel-microcode
```

### Display Stack

```
cage
libwlroots10 (auto-dep)
libgl1-mesa-dri
xwayland
```

### Browser

```
chromium
chromium-common (auto-dep)
fonts-liberation
```

### Audio

```
pipewire
pipewire-pulse
pipewire-alsa
wireplumber
alsa-utils
```

### Networking

```
network-manager
wpasupplicant
wireless-regdb
dnsmasq-base
avahi-daemon avahi-utils
```

### Boot / Installer

```
live-boot
live-boot-initramfs-tools
initramfs-tools
grub-efi-amd64
efibootmgr
parted gdisk dosfstools e2fsprogs
rsync
```

### Utilities

```
openssh-server
whiptail
python3 python3-venv python3-pip
sudo
less nano
```

### Packages to OMIT (save space)

```
linux-headers-surface (only needed for DKMS)
chromium-driver (WebDriver, not needed)
chromium-l10n (translations)
man-db manpages (documentation)
```

---

## 18. Comparison: New vs Old Approach

| Aspect | Xubuntu Remaster (old) | Debian debootstrap (new) |
|--------|----------------------|-------------------------|
| **Base** | Xubuntu 24.04 Minimal ISO | Debian 12 minbase |
| **Display** | X11 + XFCE + LightDM | Wayland + Cage (no DM) |
| **Browser** | Chromium via xtradeb PPA | Chromium native Debian deb |
| **ISO size** | ~2 GB | ~400 MB (estimated) |
| **Boot time** | ~30 s (desktop + browser) | ~10 s (kernel → cage → chromium) |
| **Attack surface** | XFCE, X11, LightDM, PulseAudio | Cage, Wayland, PipeWire |
| **Updates** | Manual apt | Manual apt (or auto-update service) |
| **Complexity** | Remaster existing ISO | Build from scratch |
| **Packages installed** | ~800+ (XFCE pulls everything) | ~100 (only what we need) |
| **RAM usage** | ~500 MB idle | ~200 MB idle (estimated) |

---

## 19. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Cage can't get seat access | Medium | High | Test seatd, logind linger, getty fallback |
| PipeWire audio broken | Medium | High | Tested workaround from Xubuntu build |
| Chromium Wayland rendering issues | Low | Medium | XWayland fallback, `--ozone-platform=x11` |
| live-boot doesn't work with linux-surface kernel | Low | High | Test early, fall back to direct install |
| Firmware missing from initramfs | Medium | High | Explicit `/etc/initramfs-tools/modules` |
| Surface Go touchscreen not responsive | Low | Medium | iptsd + libwacom are battle-tested |
| Total ISO exceeds 1.5 GB | Very Low | Low | Estimates show ~400 MB |
| Build script breaks on different host OS | Medium | Low | Docker-based build option |

---

## 20. Testing Plan

### Phase 1: Build Verification

1. Run build script on Debian 12 / Ubuntu 22.04 host
2. Verify ISO size is under target
3. Boot ISO in QEMU/KVM (without Surface hardware)
4. Verify: boots to GRUB → kernel loads → systemd starts → cage starts

### Phase 2: Display Stack

1. In QEMU: verify cage starts and Chromium opens
2. Test `--ozone-platform=wayland` vs `--ozone-platform=x11`
3. Test kiosk flags (no URL bar, no escape, fullscreen)

### Phase 3: Surface Go Hardware

1. Flash ISO to USB drive
2. Boot Surface Go from USB
3. Verify: touchscreen works (iptsd)
4. Verify: WiFi connects (iwlwifi + NetworkManager)
5. Verify: audio plays through speakers (PipeWire + SOF)
6. Verify: microphone works

### Phase 4: Install to Disk

1. Boot from USB, select "Install to Disk"
2. Verify: installer detects eMMC/NVMe
3. Verify: partitioning and rsync complete
4. Verify: GRUB installed and system boots from disk

### Phase 5: End-to-End

1. Boot installed system
2. Verify: auto-connects to WiFi
3. Verify: discovers Atlas server via mDNS
4. Verify: avatar loads in Chromium
5. Verify: audio bidirectional (TTS output + mic input)
6. Verify: touch interaction works on avatar

---

## 21. Implementation Order

1. **Create build script skeleton** — phases, error handling, cleanup
2. **Phase 1-3:** debootstrap + apt sources + kernel install
3. **Test:** Boot in QEMU (kernel only, console)
4. **Phase 4-6:** Cage + Chromium + kiosk script
5. **Test:** Boot in QEMU (kiosk display)
6. **Phase 7-8:** PipeWire + NetworkManager
7. **Phase 9-10:** Satellite agent + services
8. **Phase 11:** live-boot + squashfs + ISO creation
9. **Test:** Full ISO in QEMU
10. **Phase 12:** Install-to-disk script
11. **Test:** On real Surface Go hardware
12. **Phase 13:** Size optimization (firmware trimming, doc removal)
13. **Final test:** End-to-end on Surface Go
