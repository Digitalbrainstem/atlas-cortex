#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Tablet OS — Debian 12 Minimal Kiosk Image Builder
#
# Builds a complete, ready-to-flash live ISO for Surface Go tablets
# using Debian 12 (Bookworm) debootstrap. No desktop environment,
# no display manager, no X11 — just Cage (Wayland kiosk) + Chromium.
#
# Architecture:
#   UEFI → GRUB → linux-surface kernel → systemd →
#   atlas-satellite (WiFi setup) → Cage → Chromium (kiosk) →
#   PipeWire (audio) + IPTS (touch)
#
# Requirements (build machine):
#   sudo apt install debootstrap squashfs-tools xorriso \
#       grub-efi-amd64-bin grub-pc-bin mtools dosfstools wget gnupg
#
# Usage:
#   sudo bash satellite/tablet/build-debian-kiosk.sh
#
# Output:
#   satellite/tablet/atlas-tablet-debian-v0.0.2-YYYYMMDD.HHMM.iso
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────
VERSION="0.0.2"
TIMESTAMP="$(date +%Y%m%d.%H%M)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build/debian-kiosk"
ROOTFS="$BUILD_DIR/rootfs"
ISO_DIR="$BUILD_DIR/iso"
OUTPUT_NAME="atlas-tablet-debian-v${VERSION}-${TIMESTAMP}.iso"
OUTPUT_ISO="$SCRIPT_DIR/$OUTPUT_NAME"
DEBIAN_MIRROR="http://deb.debian.org/debian"
DEBIAN_SUITE="bookworm"

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*" >&2; }
step()  { echo -e "\n${BOLD}══ Phase $1: $2 ══${NC}"; }
die()   { err "$@"; cleanup; exit 1; }

echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Atlas Tablet OS — Debian 12 Kiosk Image Builder ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
echo ""
info "Version: $VERSION  Timestamp: $TIMESTAMP"
info "Output:  $OUTPUT_ISO"
echo ""

# ── Must be root ──────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    die "Must run as root: sudo bash $0"
fi

# ── Chroot helper ─────────────────────────────────────────────────
chroot_exec() {
    chroot "$ROOTFS" /bin/bash -c "$1"
}

# ── Mount pseudo-filesystems ──────────────────────────────────────
mount_chroot() {
    info "Mounting pseudo-filesystems in chroot..."
    mount --bind /dev       "$ROOTFS/dev"
    mount --bind /dev/pts   "$ROOTFS/dev/pts"
    mount -t proc proc      "$ROOTFS/proc"
    mount -t sysfs sys      "$ROOTFS/sys"
    mount -t tmpfs tmpfs    "$ROOTFS/run"
    # DNS resolution inside chroot
    cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf" 2>/dev/null || true
}

# ── Unmount pseudo-filesystems (lazy, reverse order) ──────────────
umount_chroot() {
    info "Unmounting pseudo-filesystems..."
    sync
    umount -lf "$ROOTFS/run"      2>/dev/null || true
    umount -lf "$ROOTFS/sys"      2>/dev/null || true
    umount -lf "$ROOTFS/proc"     2>/dev/null || true
    umount -lf "$ROOTFS/dev/pts"  2>/dev/null || true
    umount -lf "$ROOTFS/dev"      2>/dev/null || true
    rm -f "$ROOTFS/etc/resolv.conf"
}

# ── Cleanup on exit ───────────────────────────────────────────────
cleanup() {
    warn "Cleaning up..."
    umount_chroot
}
trap cleanup EXIT

# ── Check build prerequisites ─────────────────────────────────────
step 0 "Checking build prerequisites"

REQUIRED_TOOLS=(debootstrap mksquashfs xorriso grub-mkrescue wget gpg)
MISSING=()
for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "$tool" &>/dev/null; then
        MISSING+=("$tool")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    warn "Missing tools: ${MISSING[*]} — installing..."
    apt-get update -qq
    apt-get install -y -qq debootstrap squashfs-tools xorriso \
        grub-efi-amd64-bin grub-pc-bin mtools dosfstools wget gnupg \
        2>&1 | tail -5
fi
ok "All build tools available"

# ── Prepare build directories ─────────────────────────────────────
if [ -d "$ROOTFS" ]; then
    warn "Removing previous rootfs at $ROOTFS"
    umount_chroot
    rm -rf "$ROOTFS"
fi
rm -rf "$ISO_DIR"
mkdir -p "$ROOTFS" "$ISO_DIR/live" "$ISO_DIR/boot/grub" "$ISO_DIR/EFI/BOOT" "$ISO_DIR/.disk"
echo "Atlas Tablet OS $VERSION ($TIMESTAMP)" > "$ISO_DIR/.disk/info"


# ══════════════════════════════════════════════════════════════════
# Phase 1: Create rootfs via debootstrap
# ══════════════════════════════════════════════════════════════════
step 1 "Creating Debian 12 minimal rootfs (debootstrap)"

debootstrap --variant=minbase --arch=amd64 \
    --include=systemd,systemd-sysv,dbus,udev,kmod,iproute2,ca-certificates,apt-transport-https,gnupg,wget,locales,console-setup,kbd \
    "$DEBIAN_SUITE" "$ROOTFS" "$DEBIAN_MIRROR"

ok "Debootstrap complete — base rootfs created"
ROOTFS_SIZE=$(du -sh "$ROOTFS" | cut -f1)
info "Base rootfs size: $ROOTFS_SIZE"


# ══════════════════════════════════════════════════════════════════
# Phase 2: Mount pseudo-filesystems
# ══════════════════════════════════════════════════════════════════
step 2 "Mounting pseudo-filesystems for chroot"
mount_chroot
ok "Pseudo-filesystems mounted"


# ══════════════════════════════════════════════════════════════════
# Phase 3: Configure apt sources (main + non-free-firmware + linux-surface)
# ══════════════════════════════════════════════════════════════════
step 3 "Configuring apt sources"

cat > "$ROOTFS/etc/apt/sources.list" << 'EOF'
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
deb http://deb.debian.org/debian bookworm-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware
EOF

# Import linux-surface GPG key
info "Importing linux-surface GPG key..."
wget -qO - https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc \
    | gpg --dearmor > "$ROOTFS/etc/apt/trusted.gpg.d/linux-surface.gpg"

# Add linux-surface repository
echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" \
    > "$ROOTFS/etc/apt/sources.list.d/linux-surface.list"

# Update package lists
chroot_exec "apt-get update -qq"
ok "Apt sources configured (Debian + linux-surface)"


# ══════════════════════════════════════════════════════════════════
# Phase 4: Install linux-surface kernel
# ══════════════════════════════════════════════════════════════════
step 4 "Installing linux-surface kernel"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    linux-image-surface \
    linux-headers-surface \
    libwacom-surface \
    iptsd \
    initramfs-tools"

ok "Linux-surface kernel installed"
KERNEL_VER=$(chroot_exec "ls /lib/modules/ | grep surface | head -1")
info "Kernel version: $KERNEL_VER"


# ══════════════════════════════════════════════════════════════════
# Phase 5: Install display stack (Cage + Mesa + XWayland)
# ══════════════════════════════════════════════════════════════════
step 5 "Installing display stack (Cage Wayland kiosk)"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    cage \
    libgl1-mesa-dri \
    xwayland \
    libinput-tools"

ok "Display stack installed (Cage + Mesa + XWayland)"


# ══════════════════════════════════════════════════════════════════
# Phase 6: Install Chromium browser
# ══════════════════════════════════════════════════════════════════
step 6 "Installing Chromium browser"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation"

ok "Chromium installed (native Debian .deb)"


# ══════════════════════════════════════════════════════════════════
# Phase 7: Install audio stack (PipeWire + SOF firmware)
# ══════════════════════════════════════════════════════════════════
step 7 "Installing audio stack (PipeWire + WirePlumber)"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    pipewire \
    pipewire-pulse \
    pipewire-alsa \
    wireplumber \
    alsa-utils \
    firmware-sof-signed"

ok "Audio stack installed"


# ══════════════════════════════════════════════════════════════════
# Phase 8: Install networking (NetworkManager + WiFi firmware)
# ══════════════════════════════════════════════════════════════════
step 8 "Installing networking stack"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    network-manager \
    wpasupplicant \
    wireless-regdb \
    firmware-iwlwifi \
    dnsmasq-base \
    avahi-daemon \
    avahi-utils"

ok "Networking stack installed"


# ══════════════════════════════════════════════════════════════════
# Phase 9: Install utilities and live-boot
# ══════════════════════════════════════════════════════════════════
step 9 "Installing utilities, live-boot, and boot tools"

chroot_exec "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-boot \
    live-boot-initramfs-tools \
    grub-efi-amd64 \
    efibootmgr \
    parted \
    gdisk \
    dosfstools \
    e2fsprogs \
    rsync \
    openssh-server \
    whiptail \
    python3 \
    python3-venv \
    python3-pip \
    sudo \
    less \
    nano \
    curl \
    intel-microcode \
    plymouth \
    plymouth-themes"

ok "Utilities and live-boot installed"


# ══════════════════════════════════════════════════════════════════
# Phase 10: Install atlas-satellite agent
# ══════════════════════════════════════════════════════════════════
step 10 "Installing Atlas satellite agent"

# Create satellite directory in rootfs
mkdir -p "$ROOTFS/opt/atlas-satellite"

# Copy satellite code
cp -r "$PROJECT_ROOT/satellite/atlas_satellite" "$ROOTFS/opt/atlas-satellite/"
cp "$PROJECT_ROOT/satellite/requirements.txt" "$ROOTFS/opt/atlas-satellite/"

# Copy the setup HTML for fallback UI
if [ -f "$SCRIPT_DIR/setup.html" ]; then
    cp "$SCRIPT_DIR/setup.html" "$ROOTFS/opt/atlas-satellite/"
fi

# Create Python venv and install dependencies
chroot_exec "python3 -m venv /opt/atlas-satellite/venv"
chroot_exec "/opt/atlas-satellite/venv/bin/pip install --no-cache-dir \
    -r /opt/atlas-satellite/requirements.txt 2>&1 | tail -5" || {
    warn "Some pip packages may have failed (non-critical on x86)"
}

ok "Atlas satellite agent installed"


# ══════════════════════════════════════════════════════════════════
# Phase 11: Create atlas user and configure groups
# ══════════════════════════════════════════════════════════════════
step 11 "Creating atlas user and configuring system"

chroot_exec "
    # Create atlas user (UID 1000)
    useradd -m -u 1000 -s /bin/bash -G audio,video,input,render,netdev,plugdev atlas 2>/dev/null || true
    echo 'atlas:atlas' | chpasswd
    echo 'atlas ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/atlas
    chmod 440 /etc/sudoers.d/atlas
"

# Hostname
echo "atlas-tablet" > "$ROOTFS/etc/hostname"
cat > "$ROOTFS/etc/hosts" << 'EOF'
127.0.0.1   localhost
127.0.1.1   atlas-tablet

::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

# Locale
chroot_exec "
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen
    echo 'LANG=en_US.UTF-8' > /etc/default/locale
"

# Timezone
chroot_exec "ln -sf /usr/share/zoneinfo/UTC /etc/localtime"

# Fstab (live-boot manages mounts, but provide sane defaults for disk install)
cat > "$ROOTFS/etc/fstab" << 'EOF'
# <file system>  <mount point>  <type>  <options>         <dump>  <pass>
# Live-boot manages mounts automatically.
# After install-to-disk, this file is regenerated with proper UUIDs.
tmpfs            /tmp           tmpfs   defaults,noatime  0       0
EOF

ok "Atlas user and system configuration done"


# ══════════════════════════════════════════════════════════════════
# Phase 12: Deploy systemd services
# ══════════════════════════════════════════════════════════════════
step 12 "Deploying systemd services"

# ── cage.service (Wayland kiosk) ──────────────────────────────────
cat > "$ROOTFS/etc/systemd/system/cage.service" << 'EOF'
[Unit]
Description=Atlas Kiosk Display
After=network-online.target atlas-satellite.service
Wants=network-online.target
ConditionPathExists=/usr/bin/cage

[Service]
Type=simple
User=atlas
Group=atlas
SupplementaryGroups=video input render audio

PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal

Environment=WLR_LIBINPUT_NO_DEVICES=1
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=XDG_SESSION_TYPE=wayland
Environment=XDG_CURRENT_DESKTOP=cage

ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/cage -s -- /usr/local/bin/atlas-kiosk

Restart=on-failure
RestartSec=5
MemoryMax=1G
TasksMax=512

[Install]
WantedBy=graphical.target
EOF

# ── atlas-satellite.service ───────────────────────────────────────
cat > "$ROOTFS/etc/systemd/system/atlas-satellite.service" << 'EOF'
[Unit]
Description=Atlas Satellite Agent
After=network-online.target NetworkManager.service avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/atlas-satellite
ExecStart=/opt/atlas-satellite/venv/bin/python -m atlas_satellite
Restart=on-failure
RestartSec=10
Environment=HOME=/root
Environment=XDG_RUNTIME_DIR=/run/user/0

[Install]
WantedBy=multi-user.target
EOF

# ── pipewire-alsa-fix.service (Surface Go audio workaround) ──────
cat > "$ROOTFS/etc/systemd/system/pipewire-alsa-fix.service" << 'EOF'
[Unit]
Description=Create PipeWire ALSA sink for Surface Go
After=pipewire.service wireplumber.service
Wants=pipewire.service

[Service]
Type=oneshot
User=atlas
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/pw-cli create-node adapter { factory.name=api.alsa.pcm.sink node.name=speakers api.alsa.path=hw:0,0 media.class=Audio/Sink node.description=Speakers audio.rate=48000 }
RemainAfterExit=yes

[Install]
WantedBy=default.target
EOF

# ── atlas-runtime-dir.service (create XDG_RUNTIME_DIR) ────────────
cat > "$ROOTFS/etc/systemd/system/atlas-runtime-dir.service" << 'EOF'
[Unit]
Description=Create XDG_RUNTIME_DIR for atlas user
Before=cage.service atlas-satellite.service pipewire-alsa-fix.service

[Service]
Type=oneshot
ExecStart=/bin/mkdir -p /run/user/1000
ExecStart=/bin/chown atlas:atlas /run/user/1000
ExecStart=/bin/chmod 0700 /run/user/1000
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# ── atlas-first-boot.service ─────────────────────────────────────
cat > "$ROOTFS/etc/systemd/system/atlas-first-boot.service" << 'EOF'
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
EOF

# ── getty autologin override (tty1) ───────────────────────────────
mkdir -p "$ROOTFS/etc/systemd/system/getty@tty1.service.d"
cat > "$ROOTFS/etc/systemd/system/getty@tty1.service.d/override.conf" << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin atlas --noclear %I $TERM
Type=idle
EOF

# ── logind overrides (no lid sleep, no blanking) ──────────────────
mkdir -p "$ROOTFS/etc/systemd/logind.conf.d"
cat > "$ROOTFS/etc/systemd/logind.conf.d/atlas-kiosk.conf" << 'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
NAutoVTs=2
EOF

# ── Enable services ───────────────────────────────────────────────
chroot_exec "
    systemctl enable cage.service
    systemctl enable atlas-satellite.service
    systemctl enable atlas-runtime-dir.service
    systemctl enable atlas-first-boot.service
    systemctl enable pipewire-alsa-fix.service
    systemctl enable NetworkManager.service
    systemctl enable avahi-daemon.service
    systemctl enable iptsd.service 2>/dev/null || true
    systemctl enable ssh.service 2>/dev/null || true
    systemctl set-default graphical.target
"

ok "Systemd services deployed and enabled"


# ══════════════════════════════════════════════════════════════════
# Phase 13: Deploy kiosk launcher script
# ══════════════════════════════════════════════════════════════════
step 13 "Deploying kiosk launcher and helper scripts"

# ── atlas-kiosk (launched by cage.service) ────────────────────────
cat > "$ROOTFS/usr/local/bin/atlas-kiosk" << 'KIOSK_EOF'
#!/bin/bash
# Atlas Kiosk — launched by cage.service inside Cage Wayland compositor
set -euo pipefail

# Discover Atlas server via mDNS
ATLAS_URL=""
for attempt in $(seq 1 30); do
    ATLAS_HOST=$(avahi-resolve-host-name -4 atlas-cortex.local 2>/dev/null | awk '{print $2}') || true
    if [ -n "$ATLAS_HOST" ]; then
        ATLAS_URL="http://${ATLAS_HOST}:5100"
        break
    fi
    sleep 2
done

# Fallback: check config file
if [ -z "$ATLAS_URL" ] && [ -f /opt/atlas-satellite/config.json ]; then
    ATLAS_URL=$(python3 -c "
import json
with open('/opt/atlas-satellite/config.json') as f:
    print(json.load(f).get('server_url', ''))
" 2>/dev/null) || true
fi

# Fallback: local setup page
if [ -z "$ATLAS_URL" ]; then
    ATLAS_URL="file:///opt/atlas-satellite/setup.html"
fi

# Launch Chromium in kiosk mode
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
KIOSK_EOF
chmod +x "$ROOTFS/usr/local/bin/atlas-kiosk"

# ── atlas-first-boot script ──────────────────────────────────────
cat > "$ROOTFS/usr/local/bin/atlas-first-boot" << 'FIRSTBOOT_EOF'
#!/bin/bash
set -euo pipefail

MARKER="/etc/atlas/.first-boot-done"
[ -f "$MARKER" ] && exit 0

mkdir -p /etc/atlas

# Generate unique hostname from board serial or random
SERIAL=$(cat /sys/class/dmi/id/board_serial 2>/dev/null | tr -dc 'a-zA-Z0-9' | tail -c 6) || true
HOSTNAME="atlas-tablet-${SERIAL:-$(head -c 3 /dev/urandom | xxd -p)}"
hostnamectl set-hostname "$HOSTNAME" 2>/dev/null || echo "$HOSTNAME" > /etc/hostname
echo "127.0.1.1 $HOSTNAME" >> /etc/hosts

# Generate machine ID if not set
[ -s /etc/machine-id ] || systemd-machine-id-setup 2>/dev/null || true

# Save config
cat > /etc/atlas/tablet.conf << EOF
hostname=$HOSTNAME
first_boot=$(date -Iseconds)
version=0.0.2
EOF

touch "$MARKER"
FIRSTBOOT_EOF
chmod +x "$ROOTFS/usr/local/bin/atlas-first-boot"

# ── install-to-disk script ────────────────────────────────────────
cat > "$ROOTFS/usr/local/bin/atlas-install-to-disk" << 'INSTALL_EOF'
#!/bin/bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "  ${GREEN}✔${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()   { echo -e "  ${RED}✖${NC} $*" >&2; }

echo -e "\n${BOLD}Atlas Tablet OS — Install to Disk${NC}\n"

if [ "$EUID" -ne 0 ]; then
    err "Must run as root"
    exit 1
fi

# Detect internal drive
TARGET=""
if [ -b /dev/nvme0n1 ]; then
    TARGET="/dev/nvme0n1"
elif [ -b /dev/mmcblk0 ]; then
    TARGET="/dev/mmcblk0"
elif [ -b /dev/sda ]; then
    TARGET="/dev/sda"
fi

if [ -z "$TARGET" ]; then
    err "No internal drive found (checked nvme0n1, mmcblk0, sda)"
    exit 1
fi

SIZE_GB=$(( $(lsblk -b -n -d -o SIZE "$TARGET" 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))
MODEL=$(lsblk -n -d -o MODEL "$TARGET" 2>/dev/null | xargs)
echo -e "  Target: ${BOLD}$TARGET${NC} (${SIZE_GB}GB — $MODEL)"
echo ""
echo -e "  ${RED}WARNING: ALL DATA ON $TARGET WILL BE ERASED${NC}"
echo ""
read -rp "  Type 'YES' to continue: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "  Cancelled."
    exit 0
fi

info "Partitioning $TARGET..."
wipefs -a "$TARGET" >/dev/null 2>&1
parted -s "$TARGET" mklabel gpt
parted -s "$TARGET" mkpart ESP fat32 1MiB 513MiB
parted -s "$TARGET" set 1 esp on
parted -s "$TARGET" mkpart ATLASROOT ext4 513MiB 100%

# Determine partition device names
case "$TARGET" in
    /dev/nvme*|/dev/mmcblk*) P1="${TARGET}p1"; P2="${TARGET}p2" ;;
    *) P1="${TARGET}1"; P2="${TARGET}2" ;;
esac

sleep 2
info "Formatting partitions..."
mkfs.fat -F 32 -n ATLAS_EFI "$P1"
mkfs.ext4 -L ATLAS_ROOT -q "$P2"

info "Mounting target..."
TMOUNT="/mnt/atlas-install"
mkdir -p "$TMOUNT"
mount "$P2" "$TMOUNT"
mkdir -p "$TMOUNT/boot/efi"
mount "$P1" "$TMOUNT/boot/efi"

info "Copying system (this takes a few minutes)..."
rsync -aAXH --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' \
    --exclude='/run/*' --exclude='/tmp/*' --exclude='/mnt/*' \
    --exclude='/media/*' --exclude='/live/*' \
    / "$TMOUNT/" 2>/dev/null

# Create empty mountpoints
mkdir -p "$TMOUNT"/{proc,sys,dev,run,tmp,mnt,media}

# Generate fstab
EFI_UUID=$(blkid -s UUID -o value "$P1")
ROOT_UUID=$(blkid -s UUID -o value "$P2")
cat > "$TMOUNT/etc/fstab" << FSTAB
UUID=$ROOT_UUID  /          ext4  errors=remount-ro  0  1
UUID=$EFI_UUID   /boot/efi  vfat  umask=0077         0  1
tmpfs            /tmp       tmpfs defaults,noatime   0  0
FSTAB

# Install GRUB
info "Installing GRUB..."
mount --bind /dev     "$TMOUNT/dev"
mount --bind /dev/pts "$TMOUNT/dev/pts"
mount -t proc proc    "$TMOUNT/proc"
mount -t sysfs sys    "$TMOUNT/sys"

chroot "$TMOUNT" grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=atlas --recheck 2>/dev/null
chroot "$TMOUNT" update-grub 2>/dev/null

# Copy to fallback EFI path
mkdir -p "$TMOUNT/boot/efi/EFI/BOOT"
cp "$TMOUNT/boot/efi/EFI/atlas/grubx64.efi" "$TMOUNT/boot/efi/EFI/BOOT/BOOTX64.EFI" 2>/dev/null || true

# Clean up chroot mounts
umount -lf "$TMOUNT/sys"      2>/dev/null || true
umount -lf "$TMOUNT/proc"     2>/dev/null || true
umount -lf "$TMOUNT/dev/pts"  2>/dev/null || true
umount -lf "$TMOUNT/dev"      2>/dev/null || true

# Remove live-boot marker so installed system boots normally
rm -f "$TMOUNT/etc/atlas/.first-boot-done"

# Unmount
umount "$TMOUNT/boot/efi"
umount "$TMOUNT"

info "Installation complete! Remove USB and reboot."
echo ""
read -rp "  Press Enter to reboot..." _
reboot
INSTALL_EOF
chmod +x "$ROOTFS/usr/local/bin/atlas-install-to-disk"

ok "Kiosk launcher and helper scripts deployed"


# ══════════════════════════════════════════════════════════════════
# Phase 14: NetworkManager configuration
# ══════════════════════════════════════════════════════════════════
step 14 "Configuring NetworkManager"

mkdir -p "$ROOTFS/etc/NetworkManager"
cat > "$ROOTFS/etc/NetworkManager/NetworkManager.conf" << 'EOF'
[main]
plugins=ifupdown,keyfile
dns=default

[ifupdown]
managed=false

[device]
wifi.scan-rand-mac-address=no
wifi.backend=wpa_supplicant
EOF

ok "NetworkManager configured"


# ══════════════════════════════════════════════════════════════════
# Phase 15: WirePlumber ALSA configuration for Surface Go
# ══════════════════════════════════════════════════════════════════
step 15 "Configuring WirePlumber for Surface Go audio"

mkdir -p "$ROOTFS/etc/wireplumber/main.lua.d"
cat > "$ROOTFS/etc/wireplumber/main.lua.d/51-surface-alsa.lua" << 'EOF'
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
EOF

ok "WirePlumber ALSA rules deployed"


# ══════════════════════════════════════════════════════════════════
# Phase 16: Plymouth boot splash
# ══════════════════════════════════════════════════════════════════
step 16 "Creating Plymouth Atlas boot splash"

PLYMOUTH_DIR="$ROOTFS/usr/share/plymouth/themes/atlas"
mkdir -p "$PLYMOUTH_DIR"

# Plymouth theme descriptor
cat > "$PLYMOUTH_DIR/atlas.plymouth" << 'EOF'
[Plymouth Theme]
Name=Atlas
Description=Atlas Tablet OS boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/atlas
ScriptFile=/usr/share/plymouth/themes/atlas/atlas.script
EOF

# Plymouth script with pulsing logo effect
cat > "$PLYMOUTH_DIR/atlas.script" << 'PLYSCRIPT'
# Atlas boot splash — dark background with pulsing logo

Window.SetBackgroundTopColor(0.04, 0.04, 0.10);
Window.SetBackgroundBottomColor(0.02, 0.02, 0.06);

logo.image = Image("logo.png");
logo.sprite = Sprite(logo.image);
logo.sprite.SetX(Window.GetWidth() / 2 - logo.image.GetWidth() / 2);
logo.sprite.SetY(Window.GetHeight() / 2 - logo.image.GetHeight() / 2);
logo.sprite.SetZ(10);
logo.opacity = 1.0;
logo.direction = -1;

fun refresh_callback() {
    logo.opacity += logo.direction * 0.015;
    if (logo.opacity <= 0.3) {
        logo.direction = 1;
    }
    if (logo.opacity >= 1.0) {
        logo.direction = -1;
    }
    logo.sprite.SetOpacity(logo.opacity);
}

Plymouth.SetRefreshFunction(refresh_callback);

# Progress bar
progress_box.image = Image("progress-bar.png");
progress_box.sprite = Sprite(progress_box.image);
progress_box.x = Window.GetWidth() / 2 - progress_box.image.GetWidth() / 2;
progress_box.y = Window.GetHeight() * 0.75;
progress_box.sprite.SetPosition(progress_box.x, progress_box.y, 5);

fun boot_progress_callback(duration, progress) {
    progress_box.sprite.SetOpacity(progress);
}
Plymouth.SetBootProgressFunction(boot_progress_callback);
PLYSCRIPT

# Generate a simple Atlas logo PNG (128x128, white "A" on transparent)
# Using a minimal Python script to create the PNG
chroot_exec "python3 -c \"
import struct, zlib

# 128x128 RGBA PNG — atlas 'A' shape on dark background
W, H = 128, 128
pixels = bytearray()
for y in range(H):
    pixels.append(0)  # filter byte
    for x in range(W):
        # Simple triangle 'A' shape
        cx, cy = W//2, H//2
        dx, dy = abs(x - cx), cy - y
        # Outer triangle
        in_tri = (dy > -10) and (dy < 50) and (dx < (50 - dy) * 0.6 + 5)
        # Inner cutout
        in_cut = (dy > -10) and (dy < 30) and (dx < (30 - dy) * 0.4)
        # Crossbar
        in_bar = (dy > 5) and (dy < 15) and in_tri
        if (in_tri and not in_cut) or in_bar:
            # Cyan-ish glow
            r, g, b, a = 100, 200, 255, 255
        else:
            r, g, b, a = 0, 0, 0, 0
        pixels.extend([r, g, b, a])

def make_png(w, h, raw):
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    return b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b'')

with open('/usr/share/plymouth/themes/atlas/logo.png', 'wb') as f:
    f.write(make_png(W, H, pixels))
print('Logo PNG created')
\"" || warn "Logo PNG generation failed (non-critical)"

# Generate simple progress bar PNG (400x8)
chroot_exec "python3 -c \"
import struct, zlib

W, H = 400, 8
pixels = bytearray()
for y in range(H):
    pixels.append(0)
    for x in range(W):
        # Gradient bar
        alpha = int(255 * x / W)
        pixels.extend([100, 200, 255, alpha])

def make_png(w, h, raw):
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    return b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b'')

with open('/usr/share/plymouth/themes/atlas/progress-bar.png', 'wb') as f:
    f.write(make_png(W, H, pixels))
print('Progress bar PNG created')
\"" || warn "Progress bar PNG generation failed (non-critical)"

# Set Plymouth theme
chroot_exec "plymouth-set-default-theme atlas 2>/dev/null || true"

ok "Plymouth Atlas boot splash created"


# ══════════════════════════════════════════════════════════════════
# Phase 17: Initramfs modules configuration
# ══════════════════════════════════════════════════════════════════
step 17 "Configuring initramfs for Surface Go hardware"

# Ensure critical modules are included in initramfs
cat >> "$ROOTFS/etc/initramfs-tools/modules" << 'EOF'

# Surface Go hardware
i915
iwlwifi
snd_hda_intel
snd_sof
snd_sof_pci
ipts
EOF

ok "Initramfs modules configured"


# ══════════════════════════════════════════════════════════════════
# Phase 18: Strip unnecessary files to reduce image size
# ══════════════════════════════════════════════════════════════════
step 18 "Stripping unnecessary files"

# Remove documentation, man pages, extra locales
chroot_exec "
    rm -rf /usr/share/doc/* 2>/dev/null || true
    rm -rf /usr/share/man/* 2>/dev/null || true
    rm -rf /usr/share/info/* 2>/dev/null || true
    rm -rf /usr/share/lintian 2>/dev/null || true
    rm -rf /usr/share/linda 2>/dev/null || true

    # Keep only en_US locale
    find /usr/share/locale -mindepth 1 -maxdepth 1 ! -name 'en' ! -name 'en_US' -type d -exec rm -rf {} + 2>/dev/null || true

    # Remove linux-headers (not needed in production image)
    apt-get remove -y --purge linux-headers-surface 2>/dev/null || true
    apt-get autoremove -y 2>/dev/null || true
"

ROOTFS_SIZE=$(du -sh "$ROOTFS" | cut -f1)
info "Rootfs size after stripping: $ROOTFS_SIZE"
ok "Unnecessary files removed"


# ══════════════════════════════════════════════════════════════════
# Phase 19: Clean apt cache and temp files
# ══════════════════════════════════════════════════════════════════
step 19 "Cleaning caches and temp files"

chroot_exec "
    apt-get clean
    rm -rf /var/cache/apt/archives/*
    rm -rf /var/lib/apt/lists/*
    rm -rf /tmp/* /var/tmp/*
    rm -f /var/log/*.log /var/log/apt/*
    > /var/log/lastlog
    > /var/log/wtmp
    > /var/log/btmp
"

ROOTFS_SIZE=$(du -sh "$ROOTFS" | cut -f1)
info "Rootfs size after cleaning: $ROOTFS_SIZE"
ok "Caches cleaned"


# ══════════════════════════════════════════════════════════════════
# Phase 20: Update initramfs
# ══════════════════════════════════════════════════════════════════
step 20 "Generating initramfs"

chroot_exec "update-initramfs -u -k all 2>&1 | tail -5"
ok "Initramfs updated"


# ══════════════════════════════════════════════════════════════════
# Phase 21: Unmount pseudo-filesystems
# ══════════════════════════════════════════════════════════════════
step 21 "Unmounting chroot pseudo-filesystems"
umount_chroot
ok "Pseudo-filesystems unmounted"


# ══════════════════════════════════════════════════════════════════
# Phase 22: Copy kernel + initrd to ISO structure
# ══════════════════════════════════════════════════════════════════
step 22 "Copying kernel and initrd to ISO"

# Find the surface kernel and initrd
VMLINUZ=$(ls "$ROOTFS/boot/vmlinuz-"*surface* 2>/dev/null | sort -V | tail -1)
INITRD=$(ls "$ROOTFS/boot/initrd.img-"*surface* 2>/dev/null | sort -V | tail -1)

if [ -z "$VMLINUZ" ]; then
    # Fallback: any kernel
    VMLINUZ=$(ls "$ROOTFS/boot/vmlinuz-"* 2>/dev/null | sort -V | tail -1)
    INITRD=$(ls "$ROOTFS/boot/initrd.img-"* 2>/dev/null | sort -V | tail -1)
fi

if [ -z "$VMLINUZ" ] || [ -z "$INITRD" ]; then
    die "No kernel or initrd found in rootfs!"
fi

cp "$VMLINUZ" "$ISO_DIR/live/vmlinuz"
cp "$INITRD"  "$ISO_DIR/live/initrd.img"

info "Kernel: $(basename "$VMLINUZ")"
info "Initrd: $(basename "$INITRD")"
ok "Kernel and initrd copied"


# ══════════════════════════════════════════════════════════════════
# Phase 23: Create squashfs
# ══════════════════════════════════════════════════════════════════
step 23 "Creating squashfs (this takes a few minutes)"

mksquashfs "$ROOTFS" "$ISO_DIR/live/filesystem.squashfs" \
    -comp xz -b 1M -Xdict-size 100% -no-duplicates -quiet \
    -e boot

SQUASH_SIZE=$(du -sh "$ISO_DIR/live/filesystem.squashfs" | cut -f1)
info "Squashfs size: $SQUASH_SIZE"
ok "Squashfs created"


# ══════════════════════════════════════════════════════════════════
# Phase 24: Write GRUB configuration
# ══════════════════════════════════════════════════════════════════
step 24 "Writing GRUB configuration"

cat > "$ISO_DIR/boot/grub/grub.cfg" << 'EOF'
set default=0
set timeout=3

insmod all_video
insmod gfxterm
set gfxmode=auto
terminal_output gfxterm

menuentry "Atlas Tablet OS" {
    linux /live/vmlinuz boot=live components toram quiet splash consoleblank=0
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Safe Mode" {
    linux /live/vmlinuz boot=live components toram nomodeset consoleblank=0
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Install to Disk" {
    linux /live/vmlinuz boot=live components toram quiet consoleblank=0 atlas.install=1 systemd.unit=multi-user.target
    initrd /live/initrd.img
}

menuentry "Atlas Tablet OS — Debug (console only)" {
    linux /live/vmlinuz boot=live components toram consoleblank=0 systemd.unit=multi-user.target
    initrd /live/initrd.img
}
EOF

ok "GRUB configuration written"


# ══════════════════════════════════════════════════════════════════
# Phase 25: Build hybrid ISO (BIOS + EFI)
# ══════════════════════════════════════════════════════════════════
step 25 "Building hybrid ISO (BIOS + EFI)"

grub-mkrescue -o "$OUTPUT_ISO" "$ISO_DIR" -- -volid "ATLAS_TABLET" 2>&1 | tail -5

if [ ! -f "$OUTPUT_ISO" ]; then
    die "ISO creation failed!"
fi

# Generate SHA256 checksum
sha256sum "$OUTPUT_ISO" > "${OUTPUT_ISO}.sha256"

ok "ISO created successfully"


# ══════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════
ISO_SIZE=$(du -h "$OUTPUT_ISO" | cut -f1)
ISO_SIZE_MB=$(du -m "$OUTPUT_ISO" | cut -f1)

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Atlas Tablet OS — Build Complete                        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  ISO:       $OUTPUT_ISO"
echo "  Size:      $ISO_SIZE ($ISO_SIZE_MB MB)"
echo "  Checksum:  ${OUTPUT_ISO}.sha256"
echo "  Kernel:    $(basename "$VMLINUZ")"
echo ""
echo "  Flash to USB:"
echo "    sudo dd if=$OUTPUT_ISO of=/dev/sdX bs=4M status=progress"
echo ""
echo "  Or use Rufus / balenaEtcher on Windows/Mac."
echo ""

if [ "$ISO_SIZE_MB" -gt 1500 ]; then
    warn "ISO exceeds 1.5 GB target ($ISO_SIZE)"
else
    ok "ISO is under 1.5 GB target ✓"
fi

echo ""
ok "Build complete! 🚀"
