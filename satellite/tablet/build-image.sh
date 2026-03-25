#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Tablet OS — Image Builder (Xubuntu Remaster)
#
# Builds a complete, ready-to-flash OS image for x86_64 tablets by
# remastering the official Xubuntu 24.04.4 Minimal ISO.
#
# Xubuntu Minimal provides out of the box: XFCE desktop, display
# stack (Xorg), Intel GPU drivers, touchscreen (libinput), power
# management, NetworkManager. We customize it with: Chromium kiosk,
# Atlas satellite agent, captive portal WiFi setup, auto-login,
# screen blank disable, mDNS, install-to-disk.
#
# This replaces the old cloud-image-based builder which suffered
# from missing GPU drivers, black screens, fstab issues, SSH key
# failures, and console blanking — all because the cloud image was
# never meant for physical hardware with displays.
#
# Requirements (build machine only):
#   sudo apt install squashfs-tools xorriso grub-pc-bin \
#       grub-efi-amd64-bin mtools dosfstools wget
#
# Usage:
#   sudo ./build-image.sh              # Build ISO (default)
#   sudo ./build-image.sh --iso        # Build ISO (explicit)
#   sudo ./build-image.sh --help       # Show help
#
# Output:
#   atlas-tablet-os-v${VERSION}-${TIMESTAMP}.iso  (~3 GB)
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*"; }
step()  { echo -e "\n${BOLD}── $* ──${NC}"; }

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Atlas Tablet OS — Image Builder (Xubuntu)  ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Check root ────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "Must run as root: sudo $0"
    exit 1
fi

# ── Parse args ────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --iso) ;; # default, no-op
        --help|-h)
            echo "Usage: sudo $0 [--iso]"
            echo "  --iso  Build bootable ISO (default)"
            exit 0 ;;
    esac
done

# ── Check prerequisites ──────────────────────────────────────────
step "Checking build tools"
REQUIRED_TOOLS=(mksquashfs unsquashfs xorriso grub-mkrescue wget)
MISSING=()
for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "$tool" &>/dev/null; then
        MISSING+=("$tool")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    err "Missing tools: ${MISSING[*]}"
    echo ""
    echo "  Install with:"
    echo "    sudo apt install squashfs-tools xorriso \\"
    echo "        grub-pc-bin grub-efi-amd64-bin mtools dosfstools wget"
    exit 1
fi
ok "All build tools present"

# ── Configuration ─────────────────────────────────────────────────
ARCH="amd64"
BUILD_TS=$(date -u +%Y%m%d.%H%M)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CORTEX_VERSION=$(grep '__version__ =' "$REPO_DIR/cortex/version.py" 2>/dev/null | sed 's/.*"\(.*\)"/\1/' || echo "0.1.0")
SATELLITE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="/tmp/atlas-tablet-build-$$"
ROOTFS="$BUILD_DIR/rootfs"
ISO_DIR="$BUILD_DIR/iso"
ISO_MNT="$BUILD_DIR/iso-mount"
OUTPUT_ISO="atlas-tablet-os-v${CORTEX_VERSION}-${BUILD_TS}.iso"

XUBUNTU_ISO_URL="https://cdimage.ubuntu.com/xubuntu/releases/noble/release/xubuntu-24.04.4-minimal-amd64.iso"
XUBUNTU_ISO_FILE="/tmp/xubuntu-24.04.4-minimal-amd64.iso"
XUBUNTU_ISO_SHA256="a3af2c005da22f82e498e2a81b9bb362e8c59daf2a8ffcf5c4ca81d82ef7fe16"

info "Base:    Xubuntu 24.04.4 Minimal (${ARCH})"
info "Version: ${CORTEX_VERSION}"
info "Output:  ${OUTPUT_ISO}"

cleanup() {
    info "Cleaning up build directory..."
    umount -lf "$ROOTFS/dev/pts" 2>/dev/null || true
    umount -lf "$ROOTFS/dev" 2>/dev/null || true
    umount -lf "$ROOTFS/proc" 2>/dev/null || true
    umount -lf "$ROOTFS/sys" 2>/dev/null || true
    umount -lf "$ROOTFS/run" 2>/dev/null || true
    umount -lf "$ISO_MNT" 2>/dev/null || true
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

# ══════════════════════════════════════════════════════════════════
# PHASE 1: Download Xubuntu Minimal ISO
# ══════════════════════════════════════════════════════════════════
step "Phase 1: Downloading Xubuntu 24.04.4 Minimal ISO"

mkdir -p "$ROOTFS" "$ISO_DIR" "$ISO_MNT"

# Cache the ISO in /tmp to avoid re-downloading
NEED_DOWNLOAD=true
if [ -f "$XUBUNTU_ISO_FILE" ]; then
    info "Found cached ISO at ${XUBUNTU_ISO_FILE}"
    if [ -n "$XUBUNTU_ISO_SHA256" ]; then
        ACTUAL_SHA=$(sha256sum "$XUBUNTU_ISO_FILE" | awk '{print $1}')
        if [ "$ACTUAL_SHA" = "$XUBUNTU_ISO_SHA256" ]; then
            ok "SHA256 matches — using cached ISO"
            NEED_DOWNLOAD=false
        else
            warn "SHA256 mismatch — re-downloading"
        fi
    else
        ok "Using cached ISO (no SHA256 to verify)"
        NEED_DOWNLOAD=false
    fi
fi

if [ "$NEED_DOWNLOAD" = true ]; then
    info "Downloading Xubuntu 24.04.4 Minimal ISO (~2.7GB)..."
    wget -q --show-progress "$XUBUNTU_ISO_URL" -O "$XUBUNTU_ISO_FILE"
    ok "Download complete"
fi

# Mount the ISO and extract the squashfs layers
info "Mounting ISO..."
mount -o loop,ro "$XUBUNTU_ISO_FILE" "$ISO_MNT"

# Xubuntu minimal uses layered squashfs: minimal.squashfs (base) + minimal.live.squashfs (live overlay)
# Desktop uses a single filesystem.squashfs
info "Extracting squashfs filesystem (this takes a few minutes)..."
if [ -f "$ISO_MNT/casper/minimal.squashfs" ]; then
    info "Found layered squashfs (Xubuntu minimal)"
    # Extract base layer
    unsquashfs -d "$ROOTFS" -f "$ISO_MNT/casper/minimal.squashfs"
    # Overlay live layer on top (adds live-boot packages)
    if [ -f "$ISO_MNT/casper/minimal.live.squashfs" ]; then
        info "Applying live overlay layer..."
        unsquashfs -d "$ROOTFS" -f "$ISO_MNT/casper/minimal.live.squashfs"
    fi
elif [ -f "$ISO_MNT/casper/filesystem.squashfs" ]; then
    info "Found standard squashfs (Xubuntu desktop)"
    unsquashfs -d "$ROOTFS" -f "$ISO_MNT/casper/filesystem.squashfs"
else
    err "Cannot find squashfs in the ISO!"
    err "Found in /casper: $(ls "$ISO_MNT/casper/"*.squashfs 2>/dev/null || echo 'nothing')"
    exit 1
fi

# Copy the ISO structure for later repacking (excluding squashfs files — we'll rebuild them)
info "Copying ISO structure..."
rsync -a --exclude='*.squashfs' "$ISO_MNT/" "$ISO_DIR/"
mkdir -p "$ISO_DIR/casper"

umount "$ISO_MNT"

ok "Xubuntu rootfs extracted: $(du -sh "$ROOTFS" | cut -f1)"

# ── Mount pseudo-filesystems for chroot ───────────────────────────
mount --bind /dev  "$ROOTFS/dev"
mount --bind /dev/pts "$ROOTFS/dev/pts"
mount -t proc proc "$ROOTFS/proc"
mount -t sysfs sys "$ROOTFS/sys"
mount -t tmpfs tmpfs "$ROOTFS/run"

# DNS inside chroot
rm -f "$ROOTFS/etc/resolv.conf" 2>/dev/null || true
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

# ══════════════════════════════════════════════════════════════════
# PHASE 2: Customize the rootfs (chroot)
# ══════════════════════════════════════════════════════════════════
step "Phase 2: Installing additional packages"

chroot "$ROOTFS" /bin/bash << 'CHROOT_PACKAGES'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq

# ── Chromium, SSH, kiosk tools ────────────────────────────────────
apt-get install -y -qq \
    chromium-browser \
    openssh-server \
    python3 python3-venv python3-pip python3-dev python3-flask \
    build-essential libasound2-dev

# ── Audio ─────────────────────────────────────────────────────────
apt-get install -y -qq \
    pulseaudio pulseaudio-utils alsa-utils

# ── mDNS / discovery ─────────────────────────────────────────────
apt-get install -y -qq \
    avahi-daemon avahi-utils

# ── Kiosk helpers ─────────────────────────────────────────────────
apt-get install -y -qq \
    unclutter xdotool

# ── Utilities ─────────────────────────────────────────────────────
apt-get install -y -qq \
    curl wget git \
    usbutils pciutils \
    less nano locales \
    parted dosfstools e2fsprogs rsync \
    casper

# ── GRUB bootloader (for install-to-disk) ────────────────────────
apt-get install -y -qq \
    grub-pc grub-efi-amd64-bin grub-efi-amd64-signed \
    shim-signed

# ── Clean apt cache ──────────────────────────────────────────────
apt-get clean
rm -rf /var/lib/apt/lists/*

# ── Strip bloat (kiosk doesn't need these) ───────────────────────
# Remove XFCE apps we don't use (Chromium is our only app)
apt-get remove --purge -y -qq \
    thunderbird* libreoffice* simple-scan* \
    transmission-gtk* parole* ristretto* \
    xfce4-screensaver xfce4-screenshooter \
    gnome-mines gnome-sudoku sgt-puzzles \
    gimp* hexchat* pidgin* \
    xfburn* mousepad* catfish* \
    2>/dev/null || true
apt-get autoremove -y -qq 2>/dev/null || true

# Remove docs, man pages, wallpapers, themes we don't need
rm -rf /usr/share/doc/* \
       /usr/share/man/* \
       /usr/share/info/* \
       /usr/share/lintian/* \
       /usr/share/backgrounds/x*  \
       /usr/share/themes/Greybird-dark-accessibility \
       /usr/share/themes/Greybird-accessibility \
       /var/cache/apt/archives/*.deb \
       /var/log/*.log \
       /tmp/* \
       2>/dev/null || true

# Strip non-English locales (save ~100MB)
find /usr/share/locale -mindepth 1 -maxdepth 1 \
    ! -name 'en' ! -name 'en_US' ! -name 'locale.alias' \
    -exec rm -rf {} + 2>/dev/null || true
find /usr/share/help -mindepth 1 -maxdepth 1 \
    ! -name 'C' ! -name 'en' \
    -exec rm -rf {} + 2>/dev/null || true

apt-get clean
CHROOT_PACKAGES

ok "Additional packages installed"

# ══════════════════════════════════════════════════════════════════
# PHASE 2b: System configuration
# ══════════════════════════════════════════════════════════════════
step "Phase 2b: System configuration"

chroot "$ROOTFS" /bin/bash << 'CHROOT_CONFIG'
set -euo pipefail

# ── Create atlas user ────────────────────────────────────────────
useradd -m -s /bin/bash -G audio,video,input,netdev,sudo atlas 2>/dev/null || true
echo "atlas:atlas-setup" | chpasswd

# ── Locale ────────────────────────────────────────────────────────
echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
locale-gen
echo 'LANG=en_US.UTF-8' > /etc/default/locale

# ── Hostname ──────────────────────────────────────────────────────
echo "atlas-tablet" > /etc/hostname
cat > /etc/hosts << 'HOSTS'
127.0.0.1   localhost
127.0.1.1   atlas-tablet
HOSTS

# ── Timezone ──────────────────────────────────────────────────────
ln -sf /usr/share/zoneinfo/UTC /etc/localtime

# ── Auto-login on tty1 ───────────────────────────────────────────
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'AUTOLOGIN'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin atlas --noclear %I $TERM
AUTOLOGIN

# ── Disable lid-close suspend (tablets have covers) ──────────────
mkdir -p /etc/systemd/logind.conf.d/
cat > /etc/systemd/logind.conf.d/atlas-tablet.conf << 'LOGIND'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
LOGIND

# ── NetworkManager: manage all interfaces ────────────────────────
mkdir -p /etc/NetworkManager/conf.d/
cat > /etc/NetworkManager/conf.d/atlas.conf << 'NMCONF'
[main]
plugins=ifupdown,keyfile
[ifupdown]
managed=true
[device]
wifi.scan-rand-mac-address=no
NMCONF

# ── Enable SSH + generate host keys ──────────────────────────────
ssh-keygen -A
systemctl enable ssh

# ── Enable NetworkManager and avahi ──────────────────────────────
systemctl enable NetworkManager
systemctl enable avahi-daemon

# ── fstab (empty for live boot — casper overlay handles mounts) ──
cat > /etc/fstab << 'FSTAB'
# Atlas Tablet OS (live-boot)
# No disk mounts — live-boot overlay manages everything.
FSTAB

# ── Disable console screen blanking (prevents black screen) ──────
cat > /etc/systemd/system/disable-blanking.service << 'BLANKUNIT'
[Unit]
Description=Disable console screen blanking
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/setterm --blank 0 --powerdown 0 --powersave off
ExecStart=/bin/sh -c 'echo 0 > /sys/module/kernel/parameters/consoleblank'
StandardOutput=tty
TTYPath=/dev/console

[Install]
WantedBy=multi-user.target
BLANKUNIT
systemctl enable disable-blanking
CHROOT_CONFIG

ok "System configured"

# ── XFCE power manager: disable all blanking/sleep ────────────────
XFCE_PM_DIR="$ROOTFS/home/atlas/.config/xfce4/xfconf/xfce-perchannel-xml"
mkdir -p "$XFCE_PM_DIR"
cat > "$XFCE_PM_DIR/xfce4-power-manager.xml" << 'XFCEPM'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="show-tray-icon" type="bool" value="false"/>
    <property name="blank-on-ac" type="int" value="0"/>
    <property name="blank-on-battery" type="int" value="0"/>
    <property name="dpms-enabled" type="bool" value="false"/>
    <property name="dpms-on-ac-sleep" type="uint" value="0"/>
    <property name="dpms-on-ac-off" type="uint" value="0"/>
    <property name="dpms-on-battery-sleep" type="uint" value="0"/>
    <property name="dpms-on-battery-off" type="uint" value="0"/>
    <property name="brightness-on-ac" type="uint" value="100"/>
    <property name="brightness-on-battery" type="uint" value="100"/>
    <property name="inactivity-on-ac" type="uint" value="0"/>
    <property name="inactivity-on-battery" type="uint" value="0"/>
    <property name="inactivity-sleep-mode-on-ac" type="uint" value="1"/>
    <property name="inactivity-sleep-mode-on-battery" type="uint" value="1"/>
    <property name="lid-action-on-ac" type="uint" value="0"/>
    <property name="lid-action-on-battery" type="uint" value="0"/>
  </property>
</channel>
XFCEPM
ok "XFCE power manager configured"

# ══════════════════════════════════════════════════════════════════
# PHASE 2c: linux-surface kernel (OPTIONAL — Surface Go support)
# ══════════════════════════════════════════════════════════════════
step "Phase 2c: Adding linux-surface kernel (optional add-on)"

chroot "$ROOTFS" /bin/bash << 'CHROOT_SURFACE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Import linux-surface GPG key
curl -fsSL https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc \
    | gpg --dearmor > /etc/apt/trusted.gpg.d/linux-surface.gpg

echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" \
    > /etc/apt/sources.list.d/linux-surface.list

apt-get update -qq
apt-get install -y -qq linux-image-surface linux-headers-surface iptsd libwacom-surface \
    || echo "WARNING: linux-surface install failed — Surface Go may need manual kernel install"
apt-get clean
rm -rf /var/lib/apt/lists/*
CHROOT_SURFACE

ok "linux-surface kernel added (installed on top of Xubuntu kernel)"

# ══════════════════════════════════════════════════════════════════
# PHASE 3: Install Atlas satellite agent
# ══════════════════════════════════════════════════════════════════
step "Phase 3: Installing Atlas satellite agent"

INSTALL_DIR="$ROOTFS/opt/atlas-satellite"
mkdir -p "$INSTALL_DIR/atlas_satellite/platforms"
mkdir -p "$INSTALL_DIR/cache/fillers"

# Copy satellite agent code
for f in "$SATELLITE_DIR"/atlas_satellite/*.py; do
    [ -f "$f" ] && cp "$f" "$INSTALL_DIR/atlas_satellite/"
done
if [ -f "$SATELLITE_DIR/atlas_satellite/platforms/__init__.py" ]; then
    cp "$SATELLITE_DIR/atlas_satellite/platforms/__init__.py" \
        "$INSTALL_DIR/atlas_satellite/platforms/"
fi
cp "$SATELLITE_DIR/requirements.txt" "$INSTALL_DIR/"

# Create venv and install deps
chroot "$ROOTFS" /bin/bash << 'CHROOT_VENV'
set -euo pipefail
python3 -m venv /opt/atlas-satellite/venv
/opt/atlas-satellite/venv/bin/pip install -q --upgrade pip
/opt/atlas-satellite/venv/bin/pip install -q -r /opt/atlas-satellite/requirements.txt || {
    echo "WARNING: Some satellite deps failed (pyalsaaudio may need headers)"
}
CHROOT_VENV

# Default satellite config (mDNS auto-discovery)
cat > "$INSTALL_DIR/config.json" << 'SATCFG'
{
  "satellite_id": "",
  "server_url": "",
  "room": "",
  "mode": "dedicated",
  "device_type": "tablet",
  "service_port": 5110,
  "wake_word": "hey atlas",
  "volume": 0.7,
  "mic_gain": 0.8,
  "vad_sensitivity": 2,
  "audio_device_in": "default",
  "audio_device_out": "default",
  "led_type": "none",
  "wake_word_enabled": false,
  "filler_enabled": true,
  "features": {}
}
SATCFG

ok "Satellite agent installed"

# ══════════════════════════════════════════════════════════════════
# PHASE 4: Install captive portal (first-boot WiFi setup)
# ══════════════════════════════════════════════════════════════════
step "Phase 4: Installing captive portal"

PORTAL_DIR="$ROOTFS/opt/atlas-captive-portal"
mkdir -p "$PORTAL_DIR/templates"

cp "$SATELLITE_DIR/captive_portal/portal.py"                "$PORTAL_DIR/portal.py"
cp "$SATELLITE_DIR/captive_portal/hotspot.sh"               "$PORTAL_DIR/hotspot.sh"
cp "$SATELLITE_DIR/captive_portal/requirements.txt"         "$PORTAL_DIR/requirements.txt"
cp "$SATELLITE_DIR/captive_portal/templates/setup.html"     "$PORTAL_DIR/templates/setup.html"
chmod +x "$PORTAL_DIR/hotspot.sh"

# Create marker directory
mkdir -p "$ROOTFS/var/lib/atlas"

# Captive portal systemd service
cat > "$ROOTFS/etc/systemd/system/atlas-captive-portal.service" << 'PORTALUNIT'
[Unit]
Description=Atlas Satellite WiFi Setup (Captive Portal)
After=NetworkManager.service
Wants=NetworkManager.service
ConditionPathExists=!/var/lib/atlas/wifi-configured

[Service]
Type=simple
ExecStartPre=/bin/sleep 10
ExecStart=/opt/atlas-captive-portal/hotspot.sh start
ExecStop=/opt/atlas-captive-portal/hotspot.sh stop
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
PORTALUNIT

# Enable captive portal
chroot "$ROOTFS" systemctl enable atlas-captive-portal

ok "Captive portal installed"

# ══════════════════════════════════════════════════════════════════
# PHASE 5: Configure kiosk mode (XFCE + Chromium)
# ══════════════════════════════════════════════════════════════════
step "Phase 5: Configuring kiosk mode"

# ── Xorg Intel GPU config (Surface Go HD 615) ────────────────────
mkdir -p "$ROOTFS/etc/X11/xorg.conf.d"
cat > "$ROOTFS/etc/X11/xorg.conf.d/20-intel.conf" << 'XORGCONF'
Section "Device"
    Identifier  "Intel Graphics"
    Driver      "intel"
    Option      "TearFree"    "true"
    Option      "AccelMethod" "sna"
    Option      "DRI"         "3"
EndSection

Section "ServerFlags"
    Option "BlankTime"  "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
EndSection
XORGCONF

# ── Atlas kiosk script (launched by XFCE autostart) ──────────────
cat > "$ROOTFS/usr/local/bin/atlas-kiosk" << 'KIOSKSCRIPT'
#!/bin/bash
# Atlas Tablet Kiosk — launched by XFCE autostart

# Check if we booted with atlas.install=1
if grep -q 'atlas.install=1' /proc/cmdline 2>/dev/null; then
    xfce4-terminal --fullscreen -e "sudo install-to-disk"
    exit 0
fi

# Disable screen blanking
xset s off
xset -dpms
xset s noblank

# Hide cursor after 3 seconds
unclutter -idle 3 &

# Wait for network (up to 60s)
for i in $(seq 1 60); do
    if ping -c 1 -W 1 1.1.1.1 &>/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Discover Atlas server via mDNS
ATLAS_URL=""
ATLAS_HOST=$(avahi-resolve -n atlas-cortex.local 2>/dev/null | awk '{print $2}')
if [ -n "$ATLAS_HOST" ]; then
    ATLAS_URL="http://${ATLAS_HOST}:5100"
fi

# Fallback: read from config
if [ -z "$ATLAS_URL" ] && [ -f /opt/atlas-satellite/config.json ]; then
    ATLAS_URL=$(python3 -c "
import json, sys
try:
    url = json.load(open('/opt/atlas-satellite/config.json')).get('server_url','')
    if url.startswith('ws'):
        url = url.replace('ws://', 'http://').replace('wss://', 'https://').split('/ws/')[0]
    print(url)
except Exception:
    sys.exit(0)
" 2>/dev/null || true)
fi

# No server? Open captive portal for WiFi setup
if [ -z "$ATLAS_URL" ]; then
    KIOSK_URL="http://10.42.0.1/"
else
    KIOSK_URL="${ATLAS_URL}/avatar#skin=nick"
fi

# Launch Chromium kiosk
chromium-browser \
    --kiosk \
    --no-first-run \
    --disable-translate \
    --disable-infobars \
    --disable-suggestions-ui \
    --disable-save-password-bubble \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --enable-features=OverlayScrollbar \
    --check-for-update-interval=31536000 \
    --noerrdialogs \
    --start-fullscreen \
    --disable-gpu-compositing \
    --ozone-platform=x11 \
    "$KIOSK_URL" &

wait
KIOSKSCRIPT
chmod +x "$ROOTFS/usr/local/bin/atlas-kiosk"

# ── XFCE autostart entry ─────────────────────────────────────────
XFCE_AUTOSTART_DIR="$ROOTFS/home/atlas/.config/autostart"
mkdir -p "$XFCE_AUTOSTART_DIR"
cat > "$XFCE_AUTOSTART_DIR/atlas-kiosk.desktop" << 'KIOSK_DESKTOP'
[Desktop Entry]
Type=Application
Name=Atlas Kiosk
Exec=/usr/local/bin/atlas-kiosk
X-GNOME-Autostart-enabled=true
KIOSK_DESKTOP

# ── Openbox fallback autostart (if XFCE isn't present) ───────────
OPENBOX_DIR="$ROOTFS/home/atlas/.config/openbox"
mkdir -p "$OPENBOX_DIR"
cat > "$OPENBOX_DIR/autostart" << 'OPENBOX_FALLBACK'
# Atlas Tablet Kiosk — Openbox fallback autostart
# Used only if XFCE is not available for some reason.
exec /usr/local/bin/atlas-kiosk
OPENBOX_FALLBACK

chown -R 1000:1000 "$ROOTFS/home/atlas/.config"

ok "Kiosk mode configured (XFCE autostart + Openbox fallback)"

# ══════════════════════════════════════════════════════════════════
# PHASE 6: Install systemd services
# ══════════════════════════════════════════════════════════════════
step "Phase 6: Systemd services"

# ── Atlas satellite agent service ─────────────────────────────────
cat > "$ROOTFS/etc/systemd/system/atlas-satellite.service" << 'SATUNIT'
[Unit]
Description=Atlas Satellite Agent
After=network-online.target pulseaudio.service
Wants=network-online.target

[Service]
Type=simple
User=atlas
Group=atlas
WorkingDirectory=/opt/atlas-satellite
ExecStart=/opt/atlas-satellite/venv/bin/python -m atlas_satellite
Restart=on-failure
RestartSec=5
Environment=ATLAS_DEVICE_TYPE=tablet
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SATUNIT

# ── Atlas mDNS announce service ──────────────────────────────────
cat > "$ROOTFS/usr/local/bin/atlas-announce" << 'ANNOUNCE'
#!/usr/bin/env bash
set -euo pipefail
HOSTNAME=$(hostname)
SAT_ID="tablet-${HOSTNAME}"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
echo "Atlas Tablet announcing: ${SAT_ID} at ${LOCAL_IP}:5110"
if command -v avahi-publish &>/dev/null; then
    exec avahi-publish -s "${SAT_ID}" _atlas-satellite._tcp 5110 \
        "id=${SAT_ID}" "hostname=${HOSTNAME}" "room=" "hw_type=tablet"
else
    while true; do
        sleep 30
        command -v avahi-publish &>/dev/null && \
            exec avahi-publish -s "${SAT_ID}" _atlas-satellite._tcp 5110 \
                "id=${SAT_ID}" "hostname=${HOSTNAME}" "room=" "hw_type=tablet"
    done
fi
ANNOUNCE
chmod +x "$ROOTFS/usr/local/bin/atlas-announce"

cat > "$ROOTFS/etc/systemd/system/atlas-announce.service" << 'ANNUNIT'
[Unit]
Description=Atlas Tablet mDNS Announcement
After=network-online.target avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/atlas-announce
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
ANNUNIT

# ── First-boot service (one-shot, assigns unique satellite ID) ───
cat > "$ROOTFS/usr/local/bin/atlas-firstboot" << 'FIRSTBOOT'
#!/usr/bin/env bash
set -euo pipefail
LOG="/var/log/atlas-firstboot.log"
exec > "$LOG" 2>&1

echo "$(date) — Atlas Tablet first-boot starting"

# Generate unique hostname
SUFFIX=$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 4)
SAT_HOSTNAME="atlas-tablet-${SUFFIX}"
hostnamectl set-hostname "$SAT_HOSTNAME"
sed -i "s/127.0.1.1.*/127.0.1.1\t${SAT_HOSTNAME}/" /etc/hosts
echo "Hostname: $SAT_HOSTNAME"

# Generate satellite ID in config
CONFIG="/opt/atlas-satellite/config.json"
if [ -f "$CONFIG" ]; then
    python3 -c "
import json
c = json.load(open('$CONFIG'))
c['satellite_id'] = 'tablet-${SAT_HOSTNAME}'
json.dump(c, open('$CONFIG', 'w'), indent=2)
" 2>/dev/null || true
fi

# Enable satellite agent now that ID is set
systemctl enable atlas-satellite
systemctl start atlas-satellite
systemctl restart atlas-announce

echo "$(date) — Atlas Tablet first-boot complete!"
echo "Satellite: tablet-${SAT_HOSTNAME}"

# Disable self (one-shot)
systemctl disable atlas-firstboot.service
FIRSTBOOT
chmod +x "$ROOTFS/usr/local/bin/atlas-firstboot"

cat > "$ROOTFS/etc/systemd/system/atlas-firstboot.service" << 'FBUNIT'
[Unit]
Description=Atlas Tablet First Boot Setup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/atlas-firstboot
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
FBUNIT

# ── Brightness service ───────────────────────────────────────────
cat > "$ROOTFS/usr/local/bin/atlas-brightness" << 'BRIGHTNESS'
#!/usr/bin/env bash
for bl in /sys/class/backlight/*/brightness; do
    [ -f "$bl" ] || continue
    max=$(cat "$(dirname "$bl")/max_brightness" 2>/dev/null || echo 100)
    echo "$max" > "$bl" 2>/dev/null || true
done
BRIGHTNESS
chmod +x "$ROOTFS/usr/local/bin/atlas-brightness"

cat > "$ROOTFS/etc/systemd/system/atlas-brightness.service" << 'BLUNIT'
[Unit]
Description=Atlas Tablet Brightness
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/atlas-brightness

[Install]
WantedBy=multi-user.target
BLUNIT

# ── Install-to-disk script ────────────────────────────────────────
cp "$SCRIPT_DIR/install-to-disk.sh" "$ROOTFS/usr/local/bin/install-to-disk"
chmod +x "$ROOTFS/usr/local/bin/install-to-disk"

# ── Tablet udev rules ────────────────────────────────────────────
cat > "$ROOTFS/etc/udev/rules.d/99-atlas-tablet.rules" << 'UDEV'
# Atlas Tablet: auto-detect orientation from accelerometer
# Surface Go default: landscape
UDEV

# ── Enable services ──────────────────────────────────────────────
chroot "$ROOTFS" systemctl enable atlas-announce
chroot "$ROOTFS" systemctl enable atlas-firstboot
chroot "$ROOTFS" systemctl enable atlas-brightness
# atlas-satellite is enabled by firstboot after ID generation

ok "Services configured"

# ══════════════════════════════════════════════════════════════════
# PHASE 7: Clean up rootfs
# ══════════════════════════════════════════════════════════════════
step "Phase 7: Cleaning rootfs"

# Remove apt cache, logs, tmp
rm -rf "$ROOTFS/var/cache/apt/archives"/*.deb
rm -rf "$ROOTFS/var/lib/apt/lists"/*
rm -rf "$ROOTFS/tmp"/*
rm -f  "$ROOTFS/etc/resolv.conf"

# Unmount pseudo-filesystems
umount -lf "$ROOTFS/run"         2>/dev/null || true
umount -lf "$ROOTFS/dev/pts"     2>/dev/null || true
umount -lf "$ROOTFS/dev"         2>/dev/null || true
umount -lf "$ROOTFS/proc"        2>/dev/null || true
umount -lf "$ROOTFS/sys"         2>/dev/null || true

ok "Rootfs cleaned"

# ══════════════════════════════════════════════════════════════════
# PHASE 8: Repack — build the remastered ISO
# ══════════════════════════════════════════════════════════════════
step "Phase 8: Building remastered ISO"

# ── Re-squash the customized rootfs ──────────────────────────────
info "Compressing customized rootfs (this takes several minutes)..."

# Remove layered squashfs from ISO structure — we merge everything into one
rm -f "$ISO_DIR/casper/minimal.squashfs" "$ISO_DIR/casper/minimal.live.squashfs"

mksquashfs "$ROOTFS" "$ISO_DIR/casper/filesystem.squashfs" \
    -comp xz -b 1M -Xdict-size 100% -no-duplicates -quiet

ok "Squashfs created: $(du -sh "$ISO_DIR/casper/filesystem.squashfs" | cut -f1)"

# ── Update filesystem.size ───────────────────────────────────────
du -sx --block-size=1 "$ROOTFS" | cut -f1 > "$ISO_DIR/casper/filesystem.size"

# ── Rewrite install-sources.yaml for single-layer squashfs ───────
cat > "$ISO_DIR/casper/install-sources.yaml" << 'SOURCES'
- default: true
  description:
    en: Atlas Tablet OS based on Xubuntu Minimal.
  id: atlas-tablet-os
  locale_support: langpack
  name:
    en: Atlas Tablet OS
  path: filesystem.squashfs
  type: fsimage
  variant: desktop
SOURCES

# ── Copy kernel + initrd to casper ───────────────────────────────
# Prefer the Surface kernel if available, else use what Xubuntu provides
VMLINUZ=$(ls -1t "$ROOTFS/boot"/vmlinuz-*surface* 2>/dev/null | head -1)
if [ -z "$VMLINUZ" ]; then
    VMLINUZ=$(ls -1t "$ROOTFS/boot"/vmlinuz-* 2>/dev/null | head -1)
fi
INITRD=$(ls -1t "$ROOTFS/boot"/initrd.img-*surface* 2>/dev/null | head -1)
if [ -z "$INITRD" ]; then
    INITRD=$(ls -1t "$ROOTFS/boot"/initrd.img-* 2>/dev/null | head -1)
fi

if [ -z "$VMLINUZ" ] || [ -z "$INITRD" ]; then
    err "No kernel/initrd found in rootfs!"
    exit 1
fi

cp "$VMLINUZ" "$ISO_DIR/casper/vmlinuz"
cp "$INITRD"  "$ISO_DIR/casper/initrd"
ok "Kernel: $(basename "$VMLINUZ")"

# ── GRUB config ───────────────────────────────────────────────────
mkdir -p "$ISO_DIR/boot/grub"
cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=3

menuentry "Atlas Tablet OS" {
    linux /casper/vmlinuz boot=casper toram quiet splash consoleblank=0
    initrd /casper/initrd
}

menuentry "Atlas Tablet OS (Safe Mode)" {
    linux /casper/vmlinuz boot=casper toram nomodeset consoleblank=0
    initrd /casper/initrd
}

menuentry "Atlas Tablet OS (Debug - console only)" {
    linux /casper/vmlinuz boot=casper toram consoleblank=0 atlas.nox=1 systemd.unit=multi-user.target
    initrd /casper/initrd
}

menuentry "Atlas Tablet OS (Install to Disk)" {
    linux /casper/vmlinuz boot=casper toram quiet splash consoleblank=0 atlas.install=1
    initrd /casper/initrd
}
GRUBCFG

# ── Build ISO ─────────────────────────────────────────────────────
info "Building ISO with GRUB (BIOS + EFI)..."

grub-mkrescue -o "$OUTPUT_ISO" "$ISO_DIR" \
    -- -volid "ATLAS_TABLET" 2>/dev/null

ok "ISO built: $(du -sh "$OUTPUT_ISO" | cut -f1)"

# ══════════════════════════════════════════════════════════════════
# PHASE 9: Generate checksum
# ══════════════════════════════════════════════════════════════════
step "Generating checksum"

sha256sum "$OUTPUT_ISO" > "${OUTPUT_ISO}.sha256"
cat "${OUTPUT_ISO}.sha256"

# ══════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Atlas Tablet OS — Build Complete! ✓        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Base:    Xubuntu 24.04.4 Minimal (remastered)"
echo "  Output:  ${OUTPUT_ISO} ($(du -sh "$OUTPUT_ISO" | cut -f1))"
echo ""
echo "  Flash to USB:"
echo "    sudo dd if=${OUTPUT_ISO} of=/dev/sdX bs=4M status=progress"
echo ""
echo "  Or use balenaEtcher / Rufus."
echo ""
echo "  Boot the tablet → XFCE desktop → Chromium kiosk with Atlas avatar."
echo "  WiFi auto-setup if no network is found."
