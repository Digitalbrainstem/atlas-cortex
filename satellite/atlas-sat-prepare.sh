#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Satellite — SD Card Prepare Script
#
# Configures a freshly-flashed SD card so the satellite boots ready
# for Atlas discovery. Works with Raspberry Pi OS, DietPi, Armbian,
# or any Debian-based image.
#
# Usage (after flashing with balenaEtcher, Rufus, dd, etc.):
#   ./atlas-sat-prepare.sh [OPTIONS]
#
# Usage (on a live running system):
#   curl -sSL .../atlas-sat-prepare.sh | bash -s -- --live
#
# What it does:
#   SD card mode:
#     - Sets hostname to atlas-sat-XX (random suffix or user-provided)
#     - Enables SSH
#     - Configures Wi-Fi (prompted)
#     - Creates atlas user with password
#     - Installs atlas-announce mDNS service (runs on first boot)
#   Live mode (--live):
#     - Sets hostname
#     - Creates atlas user (if not exists)
#     - Installs atlas-announce service and starts it immediately
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────
HOSTNAME_PREFIX="atlas-sat"
DEFAULT_USER="atlas"
DEFAULT_PASS="atlas-setup"
LIVE_MODE=false
BOOT_PART=""
ROOT_PART=""
CUSTOM_HOSTNAME=""
WIFI_SSID=""
WIFI_PASS=""
WIFI_COUNTRY="US"

# ── Parse arguments ───────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --live)       LIVE_MODE=true; shift ;;
        --hostname)   CUSTOM_HOSTNAME="$2"; shift 2 ;;
        --boot)       BOOT_PART="$2"; shift 2 ;;
        --root)       ROOT_PART="$2"; shift 2 ;;
        --ssid)       WIFI_SSID="$2"; shift 2 ;;
        --pass)       WIFI_PASS="$2"; shift 2 ;;
        --wifi-country) WIFI_COUNTRY="$2"; shift 2 ;;
        --user)       DEFAULT_USER="$2"; shift 2 ;;
        --password)   DEFAULT_PASS="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --live            Configure a live running system (not SD card)"
            echo "  --hostname NAME   Set a custom hostname (default: atlas-sat-XXXX)"
            echo "  --boot PATH       Path to mounted boot partition"
            echo "  --root PATH       Path to mounted root partition"
            echo "  --ssid SSID       Wi-Fi network name"
            echo "  --pass PASSWORD   Wi-Fi password"
            echo "  --wifi-country CC Country code for Wi-Fi (default: US)"
            echo "  --user USERNAME   Satellite user (default: atlas)"
            echo "  --password PASS   Satellite user password (default: atlas-setup)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Generate hostname ─────────────────────────────────────────────
if [ -z "$CUSTOM_HOSTNAME" ]; then
    SUFFIX=$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 4)
    SAT_HOSTNAME="${HOSTNAME_PREFIX}-${SUFFIX}"
else
    SAT_HOSTNAME="$CUSTOM_HOSTNAME"
fi

echo "╔══════════════════════════════════════════╗"
echo "║    Atlas Satellite — SD Card Prepare     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Hostname:  $SAT_HOSTNAME"
echo "  User:      $DEFAULT_USER"
echo "  Mode:      $([ "$LIVE_MODE" = true ] && echo 'Live system' || echo 'SD card')"
echo ""

# ── atlas-announce service (systemd unit) ─────────────────────────
# This lightweight service announces the satellite via mDNS on boot.
# It uses avahi-publish (avahi-utils) which is pre-installed on most
# Raspberry Pi OS and Debian images. Falls back to a Python script
# using zeroconf if avahi is not available.
ANNOUNCE_SERVICE='[Unit]
Description=Atlas Satellite mDNS Announcement
After=network-online.target avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/atlas-announce
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target'

ANNOUNCE_SCRIPT='#!/usr/bin/env bash
# Announce this satellite via mDNS so Atlas server can discover it.
# Uses avahi-publish if available, otherwise falls back to Python zeroconf.
set -euo pipefail

HOSTNAME=$(hostname)
SAT_ID="sat-${HOSTNAME}"

# Get local IP
LOCAL_IP=$(hostname -I 2>/dev/null | awk "{print \$1}" || echo "127.0.0.1")

echo "Atlas Satellite announcing: ${SAT_ID} at ${LOCAL_IP}:5110"

if command -v avahi-publish &>/dev/null; then
    # Use avahi (pre-installed on Raspberry Pi OS)
    exec avahi-publish -s "${SAT_ID}" _atlas-satellite._tcp 5110 \
        "id=${SAT_ID}" \
        "hostname=${HOSTNAME}" \
        "room=" \
        "hw_type=linux"
else
    # Fallback: Python + zeroconf (installed with satellite agent)
    python3 -c "
import socket, time
try:
    from zeroconf import ServiceInfo, Zeroconf
    ip = \"${LOCAL_IP}\"
    info = ServiceInfo(
        \"_atlas-satellite._tcp.local.\",
        \"${SAT_ID}._atlas-satellite._tcp.local.\",
        addresses=[socket.inet_aton(ip)],
        port=5110,
        properties={\"id\": \"${SAT_ID}\", \"hostname\": \"${HOSTNAME}\", \"room\": \"\", \"hw_type\": \"linux\"},
    )
    zc = Zeroconf()
    zc.register_service(info)
    print(f\"mDNS: announcing ${SAT_ID} at {ip}:5110\")
    while True:
        time.sleep(60)
except ImportError:
    print(\"Neither avahi-publish nor zeroconf available. Install avahi-utils.\")
    import sys; sys.exit(1)
"
fi'

# ══════════════════════════════════════════════════════════════════
# LIVE MODE — configure a running system
# ══════════════════════════════════════════════════════════════════
if [ "$LIVE_MODE" = true ]; then
    echo "→ Configuring live system..."

    # Set hostname
    echo "→ Setting hostname to $SAT_HOSTNAME"
    sudo hostnamectl set-hostname "$SAT_HOSTNAME" 2>/dev/null || {
        echo "$SAT_HOSTNAME" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$SAT_HOSTNAME/" /etc/hosts
    }

    # Create user if not exists
    if ! id "$DEFAULT_USER" &>/dev/null; then
        echo "→ Creating user: $DEFAULT_USER"
        sudo useradd -m -s /bin/bash -G sudo,audio,video,i2c,spi,gpio "$DEFAULT_USER" 2>/dev/null || \
        sudo useradd -m -s /bin/bash -G sudo,audio,video "$DEFAULT_USER"
        echo "${DEFAULT_USER}:${DEFAULT_PASS}" | sudo chpasswd
    fi

    # Install avahi if not present
    if ! command -v avahi-publish &>/dev/null; then
        echo "→ Installing avahi-utils for mDNS..."
        sudo apt-get update -qq && sudo apt-get install -y -qq avahi-utils 2>/dev/null || true
    fi

    # Install announce service
    echo "→ Installing atlas-announce service..."
    echo "$ANNOUNCE_SCRIPT" | sudo tee /usr/local/bin/atlas-announce > /dev/null
    sudo chmod +x /usr/local/bin/atlas-announce
    echo "$ANNOUNCE_SERVICE" | sudo tee /etc/systemd/system/atlas-announce.service > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable atlas-announce
    sudo systemctl restart atlas-announce

    echo ""
    echo "✅ Live system configured!"
    echo "   Hostname: $SAT_HOSTNAME"
    echo "   mDNS: broadcasting _atlas-satellite._tcp.local"
    echo "   This satellite should appear in your Atlas admin panel."
    exit 0
fi

# ══════════════════════════════════════════════════════════════════
# SD CARD MODE — configure mounted partitions
# ══════════════════════════════════════════════════════════════════

# ── Auto-detect mount points ──────────────────────────────────────
auto_detect_partitions() {
    # Common mount points for SD cards
    local candidates_boot=(
        "/media/$USER/bootfs"
        "/media/$USER/boot"
        "/Volumes/bootfs"
        "/Volumes/boot"
        "/mnt/boot"
        "/run/media/$USER/bootfs"
        "/run/media/$USER/boot"
    )
    local candidates_root=(
        "/media/$USER/rootfs"
        "/Volumes/rootfs"
        "/mnt/root"
        "/run/media/$USER/rootfs"
    )

    if [ -z "$BOOT_PART" ]; then
        for p in "${candidates_boot[@]}"; do
            if [ -d "$p" ] && [ -f "$p/config.txt" -o -f "$p/cmdline.txt" ]; then
                BOOT_PART="$p"
                break
            fi
        done
    fi

    if [ -z "$ROOT_PART" ]; then
        for p in "${candidates_root[@]}"; do
            if [ -d "$p" ] && [ -d "$p/etc" ]; then
                ROOT_PART="$p"
                break
            fi
        done
    fi
}

auto_detect_partitions

if [ -z "$BOOT_PART" ]; then
    echo "⚠  Could not auto-detect boot partition."
    echo "   Please specify with --boot /path/to/boot"
    read -rp "   Boot partition path: " BOOT_PART
fi

if [ -z "$ROOT_PART" ]; then
    echo "⚠  Could not auto-detect root partition."
    echo "   Please specify with --root /path/to/rootfs"
    read -rp "   Root partition path: " ROOT_PART
fi

if [ ! -d "$BOOT_PART" ]; then
    echo "❌ Boot partition not found: $BOOT_PART"
    exit 1
fi

if [ ! -d "$ROOT_PART" ]; then
    echo "❌ Root partition not found: $ROOT_PART"
    exit 1
fi

echo "  Boot:      $BOOT_PART"
echo "  Root:      $ROOT_PART"
echo ""

# ── 1. Enable SSH ─────────────────────────────────────────────────
echo "→ Enabling SSH..."
touch "$BOOT_PART/ssh"

# ── 2. Configure Wi-Fi ────────────────────────────────────────────
if [ -z "$WIFI_SSID" ]; then
    read -rp "→ Wi-Fi SSID (leave empty to skip): " WIFI_SSID
fi

if [ -n "$WIFI_SSID" ]; then
    if [ -z "$WIFI_PASS" ]; then
        read -rsp "→ Wi-Fi password: " WIFI_PASS
        echo ""
    fi

    # Raspberry Pi OS Bookworm uses NetworkManager, but the
    # wpa_supplicant.conf method still works for first boot
    cat > "$BOOT_PART/wpa_supplicant.conf" << WPAEOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=$WIFI_COUNTRY

network={
    ssid="$WIFI_SSID"
    psk="$WIFI_PASS"
    key_mgmt=WPA-PSK
}
WPAEOF
    echo "  ✓ Wi-Fi configured for: $WIFI_SSID"
fi

# ── 3. Set hostname ──────────────────────────────────────────────
echo "→ Setting hostname: $SAT_HOSTNAME"
echo "$SAT_HOSTNAME" > "$ROOT_PART/etc/hostname"

# Update /etc/hosts
if [ -f "$ROOT_PART/etc/hosts" ]; then
    sed -i "s/127.0.1.1.*/127.0.1.1\t$SAT_HOSTNAME/" "$ROOT_PART/etc/hosts"
fi

# ── 4. Create user (via firstrun script for RPi OS Bookworm) ─────
# Raspberry Pi OS Bookworm requires user creation on first boot.
# We use the userconf.txt method (boot partition).
echo "→ Configuring user: $DEFAULT_USER"
HASHED_PASS=$(openssl passwd -6 "$DEFAULT_PASS" 2>/dev/null || \
              python3 -c "import crypt; print(crypt.crypt('$DEFAULT_PASS', crypt.mksalt(crypt.METHOD_SHA512)))")
echo "${DEFAULT_USER}:${HASHED_PASS}" > "$BOOT_PART/userconf.txt"

# ── 5. Install atlas-announce service ─────────────────────────────
echo "→ Installing atlas-announce mDNS service..."

# Write the announce script
mkdir -p "$ROOT_PART/usr/local/bin"
cat > "$ROOT_PART/usr/local/bin/atlas-announce" << 'SCRIPT_EOF'
#!/usr/bin/env bash
# Announce this satellite via mDNS so Atlas server can discover it.
set -euo pipefail

HOSTNAME=$(hostname)
SAT_ID="sat-${HOSTNAME}"
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

echo "Atlas Satellite announcing: ${SAT_ID} at ${LOCAL_IP}:5110"

if command -v avahi-publish &>/dev/null; then
    exec avahi-publish -s "${SAT_ID}" _atlas-satellite._tcp 5110 \
        "id=${SAT_ID}" \
        "hostname=${HOSTNAME}" \
        "room=" \
        "hw_type=linux"
else
    echo "avahi-publish not found. Install avahi-utils: sudo apt install avahi-utils"
    # Wait and retry (might be installed by satellite agent later)
    while true; do
        sleep 30
        if command -v avahi-publish &>/dev/null; then
            exec avahi-publish -s "${SAT_ID}" _atlas-satellite._tcp 5110 \
                "id=${SAT_ID}" "hostname=${HOSTNAME}" "room=" "hw_type=linux"
        fi
    done
fi
SCRIPT_EOF
chmod +x "$ROOT_PART/usr/local/bin/atlas-announce"

# Write the systemd service
mkdir -p "$ROOT_PART/etc/systemd/system"
cat > "$ROOT_PART/etc/systemd/system/atlas-announce.service" << 'UNIT_EOF'
[Unit]
Description=Atlas Satellite mDNS Announcement
After=network-online.target avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/atlas-announce
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT_EOF

# Enable the service (create symlink manually since systemctl isn't available)
mkdir -p "$ROOT_PART/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/atlas-announce.service \
    "$ROOT_PART/etc/systemd/system/multi-user.target.wants/atlas-announce.service"

echo "  ✓ atlas-announce service installed and enabled"

# ── 6. Ensure avahi-utils gets installed on first boot ────────────
# Add to a firstboot script that installs avahi-utils if not present
FIRSTBOOT="$ROOT_PART/usr/local/bin/atlas-firstboot"
cat > "$FIRSTBOOT" << 'FBEOF'
#!/usr/bin/env bash
# Atlas satellite first-boot setup
set -euo pipefail
LOG="/var/log/atlas-firstboot.log"
exec > "$LOG" 2>&1

echo "$(date) — Atlas first-boot starting"

# Wait for network
for i in $(seq 1 30); do
    if ping -c1 -W2 8.8.8.8 &>/dev/null; then break; fi
    sleep 2
done

# Install avahi-utils if not present
if ! command -v avahi-publish &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq avahi-utils
fi

# Restart the announce service now that avahi is available
systemctl restart atlas-announce

echo "$(date) — Atlas first-boot complete"

# Disable ourselves
systemctl disable atlas-firstboot.service
FBEOF
chmod +x "$FIRSTBOOT"

cat > "$ROOT_PART/etc/systemd/system/atlas-firstboot.service" << 'FBUNIT'
[Unit]
Description=Atlas Satellite First Boot Setup
After=network-online.target
Wants=network-online.target
ConditionPathExists=/usr/local/bin/atlas-firstboot

[Service]
Type=oneshot
ExecStart=/usr/local/bin/atlas-firstboot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
FBUNIT

mkdir -p "$ROOT_PART/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/atlas-firstboot.service \
    "$ROOT_PART/etc/systemd/system/multi-user.target.wants/atlas-firstboot.service"

echo "  ✓ First-boot service installed (installs avahi-utils)"

# ── Done! ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      SD Card Preparation Complete! ✓     ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Hostname:   $SAT_HOSTNAME"
echo "  User:       $DEFAULT_USER / $DEFAULT_PASS"
echo "  SSH:        Enabled"
echo "  Wi-Fi:      $([ -n "$WIFI_SSID" ] && echo "$WIFI_SSID" || echo 'Not configured')"
echo "  mDNS:       atlas-announce service (auto-starts on boot)"
echo ""
echo "Next steps:"
echo "  1. Safely eject the SD card"
echo "  2. Insert into your satellite device and power on"
echo "  3. The satellite will appear in Atlas Admin → Satellites"
echo "     within 30-60 seconds of booting"
echo ""
echo "  To SSH in:  ssh ${DEFAULT_USER}@${SAT_HOSTNAME}.local"
echo ""
