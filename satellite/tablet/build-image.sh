#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Tablet OS — Image Builder
#
# Builds a complete, ready-to-flash OS image for x86_64 tablets.
# Uses debootstrap to create a minimal Ubuntu rootfs with everything
# pre-installed: Openbox kiosk, Chromium, Atlas satellite agent,
# captive portal, linux-surface kernel, PulseAudio, mDNS.
#
# The result is a bootable ISO/disk image. Flash it, boot the tablet,
# connect to WiFi on the touchscreen — Atlas avatar appears. Done.
#
# Requirements (build machine only):
#   sudo apt install debootstrap squashfs-tools xorriso grub-pc-bin \
#       grub-efi-amd64-bin mtools dosfstools
#
# Usage:
#   sudo ./build-image.sh              # Build ISO
#   sudo ./build-image.sh --iso        # Build ISO (default)
#   sudo ./build-image.sh --raw        # Build raw disk image (dd-flashable)
#
# Output:
#   atlas-tablet-os-YYYYMMDD.iso   (~1.5 GB)
#   atlas-tablet-os-YYYYMMDD.img   (raw disk, if --raw)
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
echo -e "${BLUE}║   Atlas Tablet OS — Image Builder            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Check root ────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "Must run as root: sudo $0"
    exit 1
fi

# ── Parse args ────────────────────────────────────────────────────
OUTPUT_FORMAT="iso"
for arg in "$@"; do
    case "$arg" in
        --raw) OUTPUT_FORMAT="raw" ;;
        --iso) OUTPUT_FORMAT="iso" ;;
        --help|-h)
            echo "Usage: sudo $0 [--iso|--raw]"
            echo "  --iso  Build bootable ISO (default)"
            echo "  --raw  Build raw disk image (dd to USB/drive)"
            exit 0 ;;
    esac
done

# ── Check prerequisites ──────────────────────────────────────────
step "Checking build tools"
REQUIRED_TOOLS=(mksquashfs xorriso grub-mkrescue wget)
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
SUITE="noble"  # Ubuntu 24.04 LTS
ARCH="amd64"
DATE=$(date +%Y%m%d)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SATELLITE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="/tmp/atlas-tablet-build-$$"
ROOTFS="$BUILD_DIR/rootfs"
ISO_DIR="$BUILD_DIR/iso"
OUTPUT_ISO="atlas-tablet-os-${DATE}.iso"
OUTPUT_IMG="atlas-tablet-os-${DATE}.img"

info "Suite:   Ubuntu ${SUITE} (24.04 LTS)"
info "Arch:    ${ARCH}"
info "Output:  ${OUTPUT_FORMAT} → $([ "$OUTPUT_FORMAT" = "iso" ] && echo "$OUTPUT_ISO" || echo "$OUTPUT_IMG")"

cleanup() {
    info "Cleaning up build directory..."
    umount -lf "$ROOTFS/dev/pts" 2>/dev/null || true
    umount -lf "$ROOTFS/dev" 2>/dev/null || true
    umount -lf "$ROOTFS/proc" 2>/dev/null || true
    umount -lf "$ROOTFS/sys" 2>/dev/null || true
    umount -lf "$ROOTFS/run" 2>/dev/null || true
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

# ══════════════════════════════════════════════════════════════════
# PHASE 1: Download official Ubuntu minimal cloud rootfs
# ══════════════════════════════════════════════════════════════════
step "Phase 1: Downloading Ubuntu ${SUITE} minimal cloud image"

mkdir -p "$ROOTFS" "$ISO_DIR"

CLOUD_URL="https://cloud-images.ubuntu.com/minimal/releases/${SUITE}/release"
ROOTFS_TAR="ubuntu-24.04-minimal-cloudimg-amd64-root.tar.xz"

info "Downloading ${ROOTFS_TAR} (~200MB)..."
wget -q --show-progress "${CLOUD_URL}/${ROOTFS_TAR}" -O "$BUILD_DIR/rootfs.tar.xz"

info "Extracting..."
tar xJf "$BUILD_DIR/rootfs.tar.xz" -C "$ROOTFS"
rm "$BUILD_DIR/rootfs.tar.xz"

ok "Cloud rootfs extracted: $(du -sh "$ROOTFS" | cut -f1)"

# ── Mount pseudo-filesystems for chroot ───────────────────────────
mount --bind /dev  "$ROOTFS/dev"
mount --bind /dev/pts "$ROOTFS/dev/pts"
mount -t proc proc "$ROOTFS/proc"
mount -t sysfs sys "$ROOTFS/sys"
mount -t tmpfs tmpfs "$ROOTFS/run"

# DNS inside chroot
rm -f "$ROOTFS/etc/resolv.conf" 2>/dev/null || true
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"

# ── Configure apt sources ────────────────────────────────────────
cat > "$ROOTFS/etc/apt/sources.list" << SOURCES
deb http://archive.ubuntu.com/ubuntu ${SUITE} main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu ${SUITE}-updates main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu ${SUITE}-security main restricted universe multiverse
SOURCES

# ══════════════════════════════════════════════════════════════════
# PHASE 2: Install all packages inside chroot
# ══════════════════════════════════════════════════════════════════
step "Phase 2: Installing packages"

chroot "$ROOTFS" /bin/bash << 'CHROOT_PACKAGES'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq

# ── Linux kernel ──────────────────────────────────────────────────
apt-get install -y -qq linux-generic live-boot

# ── Display: Xorg + Openbox (no full DE) ─────────────────────────
apt-get install -y -qq \
    xorg xinit openbox xterm \
    chromium-browser \
    unclutter xdotool

# ── Audio ─────────────────────────────────────────────────────────
apt-get install -y -qq \
    pulseaudio pulseaudio-utils alsa-utils

# ── Networking ────────────────────────────────────────────────────
apt-get install -y -qq \
    network-manager \
    avahi-daemon avahi-utils \
    iw wireless-tools wpasupplicant rfkill

# ── Python + build essentials ─────────────────────────────────────
apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    python3-flask \
    build-essential libasound2-dev

# ── Utilities ─────────────────────────────────────────────────────
apt-get install -y -qq \
    curl wget git \
    openssh-server \
    usbutils pciutils \
    less nano locales \
    parted dosfstools e2fsprogs rsync

# ── GRUB bootloader ──────────────────────────────────────────────
apt-get install -y -qq \
    grub-pc grub-efi-amd64-bin grub-efi-amd64-signed \
    shim-signed

# ── Clean apt cache ──────────────────────────────────────────────
apt-get clean
rm -rf /var/lib/apt/lists/*
CHROOT_PACKAGES

ok "Packages installed"

# ══════════════════════════════════════════════════════════════════
# PHASE 3: Install linux-surface kernel (Surface Go support)
# ══════════════════════════════════════════════════════════════════
step "Phase 3: Adding linux-surface kernel"

chroot "$ROOTFS" /bin/bash << 'CHROOT_SURFACE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Import linux-surface GPG key
curl -fsSL https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc \
    | gpg --dearmor > /etc/apt/trusted.gpg.d/linux-surface.gpg

echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" \
    > /etc/apt/sources.list.d/linux-surface.list

apt-get update -qq
apt-get install -y -qq linux-image-surface linux-headers-surface iptsd libwacom-surface || {
    echo "WARNING: linux-surface install failed — Surface Go may need manual kernel install"
}
apt-get clean
rm -rf /var/lib/apt/lists/*
CHROOT_SURFACE

ok "linux-surface kernel added"

# ══════════════════════════════════════════════════════════════════
# PHASE 4: Create atlas user and configure system
# ══════════════════════════════════════════════════════════════════
step "Phase 4: System configuration"

chroot "$ROOTFS" /bin/bash << 'CHROOT_CONFIG'
set -euo pipefail

# ── Create atlas user ────────────────────────────────────────────
useradd -m -s /bin/bash -G audio,video,input,netdev,sudo atlas
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

# ── Enable SSH ────────────────────────────────────────────────────
systemctl enable ssh

# ── Enable NetworkManager and avahi ──────────────────────────────
systemctl enable NetworkManager
systemctl enable avahi-daemon

# ── fstab (minimal — will be updated by first-boot if needed) ────
cat > /etc/fstab << 'FSTAB'
# Atlas Tablet OS
# Filesystem is on the USB/disk the image was flashed to.
# UUID will be updated on first boot if needed.
LABEL=ATLASROOT  /  ext4  errors=remount-ro  0  1
FSTAB
CHROOT_CONFIG

ok "System configured"

# ══════════════════════════════════════════════════════════════════
# PHASE 5: Install Atlas satellite agent
# ══════════════════════════════════════════════════════════════════
step "Phase 5: Installing Atlas satellite agent"

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
# PHASE 6: Install captive portal (first-boot WiFi setup)
# ══════════════════════════════════════════════════════════════════
step "Phase 6: Installing captive portal"

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
# PHASE 7: Configure kiosk mode (Openbox + Chromium)
# ══════════════════════════════════════════════════════════════════
step "Phase 7: Configuring kiosk mode"

# ── Openbox autostart ─────────────────────────────────────────────
AUTOSTART_DIR="$ROOTFS/home/atlas/.config/openbox"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/autostart" << 'KIOSK'
# Atlas Tablet Kiosk — Openbox autostart
# Launched automatically when X starts on tty1.

# Check if we booted with atlas.install=1 (Install to Disk mode)
if grep -q 'atlas.install=1' /proc/cmdline 2>/dev/null; then
    xterm -fullscreen -e "sudo install-to-disk" &
    exit 0
fi

# Hide cursor after 3 seconds idle
unclutter -idle 3 &

# Disable screen blanking and power management
xset s off
xset -dpms
xset s noblank

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

# If no server found, Chromium opens the captive portal page.
# Once WiFi connects and server is discovered, it will redirect.
if [ -z "$ATLAS_URL" ]; then
    KIOSK_URL="http://10.42.0.1/"
else
    KIOSK_URL="${ATLAS_URL}/avatar#skin=nick"
fi

# Launch Chromium in kiosk mode
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
    "$KIOSK_URL" &
KIOSK
chown -R 1000:1000 "$ROOTFS/home/atlas/.config"

# ── .bash_profile: auto-start X on tty1 ─────────────────────────
cat > "$ROOTFS/home/atlas/.bash_profile" << 'PROFILE'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx -- -nocursor 2>/dev/null
fi
PROFILE
chown 1000:1000 "$ROOTFS/home/atlas/.bash_profile"

# ── .xinitrc: launch Openbox ─────────────────────────────────────
cat > "$ROOTFS/home/atlas/.xinitrc" << 'XINITRC'
exec openbox-session
XINITRC
chown 1000:1000 "$ROOTFS/home/atlas/.xinitrc"

ok "Kiosk mode configured"

# ══════════════════════════════════════════════════════════════════
# PHASE 8: Install systemd services
# ══════════════════════════════════════════════════════════════════
step "Phase 8: Systemd services"

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

# ── Enable services ──────────────────────────────────────────────
chroot "$ROOTFS" systemctl enable atlas-announce
chroot "$ROOTFS" systemctl enable atlas-firstboot
# atlas-satellite is enabled by firstboot after ID generation

ok "Services configured"

# ══════════════════════════════════════════════════════════════════
# PHASE 9: Set max brightness + tablet display tweaks
# ══════════════════════════════════════════════════════════════════
step "Phase 9: Display tweaks"

cat > "$ROOTFS/etc/udev/rules.d/99-atlas-tablet.rules" << 'UDEV'
# Atlas Tablet: auto-detect orientation from accelerometer
# Surface Go default: landscape
UDEV

# Script to max out backlight on boot
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

chroot "$ROOTFS" systemctl enable atlas-brightness

ok "Display configured"

# ── Install-to-disk script ────────────────────────────────────────
cp "$SCRIPT_DIR/install-to-disk.sh" "$ROOTFS/usr/local/bin/install-to-disk"
chmod +x "$ROOTFS/usr/local/bin/install-to-disk"
ok "Install-to-disk script installed"

# ══════════════════════════════════════════════════════════════════
# PHASE 10: Clean up rootfs
# ══════════════════════════════════════════════════════════════════
step "Phase 10: Cleaning rootfs"

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
# PHASE 11: Build bootable ISO
# ══════════════════════════════════════════════════════════════════
step "Phase 11: Building bootable image"

# ── Create squashfs ───────────────────────────────────────────────
info "Compressing rootfs (this takes a few minutes)..."
mkdir -p "$ISO_DIR/live" "$ISO_DIR/boot/grub"

mksquashfs "$ROOTFS" "$ISO_DIR/live/filesystem.squashfs" \
    -comp gzip -b 256K -no-duplicates -quiet

ok "Squashfs created: $(du -sh "$ISO_DIR/live/filesystem.squashfs" | cut -f1)"

# ── Copy kernel + initrd ─────────────────────────────────────────
# Prefer the Surface kernel if available, else generic
VMLINUZ=$(ls -1t "$ROOTFS/boot"/vmlinuz-*surface* 2>/dev/null | head -1 || \
          ls -1t "$ROOTFS/boot"/vmlinuz-* 2>/dev/null | head -1)
INITRD=$(ls -1t "$ROOTFS/boot"/initrd.img-*surface* 2>/dev/null | head -1 || \
         ls -1t "$ROOTFS/boot"/initrd.img-* 2>/dev/null | head -1)

if [ -z "$VMLINUZ" ] || [ -z "$INITRD" ]; then
    err "No kernel/initrd found in rootfs!"
    exit 1
fi

cp "$VMLINUZ" "$ISO_DIR/live/vmlinuz"
cp "$INITRD"  "$ISO_DIR/live/initrd.img"
ok "Kernel: $(basename "$VMLINUZ")"

# ── GRUB config ───────────────────────────────────────────────────
cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=3

menuentry "Atlas Tablet OS" {
    linux /live/vmlinuz boot=live toram quiet splash i915.enable_psr=0
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS (Safe Mode)" {
    linux /live/vmlinuz boot=live toram nomodeset quiet
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS (Install to Disk)" {
    linux /live/vmlinuz boot=live toram quiet splash i915.enable_psr=0 atlas.install=1
    initrd /live/initrd.img
}
GRUBCFG

# ── Build ISO ─────────────────────────────────────────────────────
info "Building ISO with GRUB (BIOS + EFI)..."

grub-mkrescue -o "$OUTPUT_ISO" "$ISO_DIR" \
    -- -volid "ATLAS_TABLET" 2>/dev/null

ok "ISO built: $(du -sh "$OUTPUT_ISO" | cut -f1)"

# ── Optional: raw disk image ─────────────────────────────────────
if [ "$OUTPUT_FORMAT" = "raw" ]; then
    step "Building raw disk image"

    SQUASHFS_SIZE=$(stat -c%s "$ISO_DIR/live/filesystem.squashfs")
    IMG_SIZE_MB=$(( (SQUASHFS_SIZE / 1048576) + 512 ))

    dd if=/dev/zero of="$OUTPUT_IMG" bs=1M count="$IMG_SIZE_MB" status=none

    # Create partition table
    parted -s "$OUTPUT_IMG" mklabel gpt
    parted -s "$OUTPUT_IMG" mkpart EFI fat32 1MiB 100MiB
    parted -s "$OUTPUT_IMG" set 1 esp on
    parted -s "$OUTPUT_IMG" mkpart ATLASROOT ext4 100MiB 100%

    ok "Raw image: $(du -sh "$OUTPUT_IMG" | cut -f1)"
    info "Flash with: sudo dd if=$OUTPUT_IMG of=/dev/sdX bs=4M status=progress"
fi

# ══════════════════════════════════════════════════════════════════
# PHASE 12: Generate checksum
# ══════════════════════════════════════════════════════════════════
step "Generating checksum"

if [ "$OUTPUT_FORMAT" = "iso" ]; then
    sha256sum "$OUTPUT_ISO" > "${OUTPUT_ISO}.sha256"
    cat "${OUTPUT_ISO}.sha256"
else
    sha256sum "$OUTPUT_IMG" > "${OUTPUT_IMG}.sha256"
    cat "${OUTPUT_IMG}.sha256"
fi

# ══════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Atlas Tablet OS — Build Complete! ✓        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
if [ "$OUTPUT_FORMAT" = "iso" ]; then
    echo "  Output: ${OUTPUT_ISO} ($(du -sh "$OUTPUT_ISO" | cut -f1))"
    echo ""
    echo "  Flash to USB:"
    echo "    sudo dd if=${OUTPUT_ISO} of=/dev/sdX bs=4M status=progress"
    echo ""
    echo "  Or use balenaEtcher / Rufus."
else
    echo "  Output: ${OUTPUT_IMG} ($(du -sh "$OUTPUT_IMG" | cut -f1))"
    echo ""
    echo "  Flash to USB or internal drive:"
    echo "    sudo dd if=${OUTPUT_IMG} of=/dev/sdX bs=4M status=progress"
fi
echo ""
echo "  Boot the tablet → WiFi setup on screen → Atlas avatar appears."
echo "  No further installation needed."
