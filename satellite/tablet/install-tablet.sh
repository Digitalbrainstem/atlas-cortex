#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Tablet Satellite — Installer
#
# Turns an x86_64 tablet (Surface Go, etc.) into a fullscreen Atlas
# satellite with avatar display, mic, speaker, and touchscreen.
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/tablet/install-tablet.sh | sudo bash
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
echo -e "${BLUE}║   Atlas Tablet Satellite — Installer         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"

# ── 1. Check we're on x86_64 ─────────────────────────────────────
step "Checking system"
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ]; then
    err "This installer is for x86_64 tablets. Detected: $ARCH"
    err "For Raspberry Pi, use: satellite/install.sh"
    exit 1
fi
ok "Architecture: $ARCH"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    err "Please run as root: sudo bash install-tablet.sh"
    exit 1
fi

# ── 2. Detect hardware ───────────────────────────────────────────
step "Detecting hardware"

# Surface Go detection
SURFACE=false
if dmesg 2>/dev/null | grep -qi "surface" || \
   lsmod 2>/dev/null | grep -qi "surface" || \
   [ -d /sys/bus/surface_aggregator ] 2>/dev/null; then
    SURFACE=true
    ok "Microsoft Surface detected"
else
    info "Non-Surface device (generic x86 tablet)"
fi

# Touchscreen
if command -v xinput &>/dev/null && xinput list 2>/dev/null | grep -qi "touch"; then
    ok "Touchscreen detected"
elif [ -d /sys/class/input ] && grep -rql "touch" /sys/class/input/*/name 2>/dev/null; then
    ok "Touchscreen detected (via sysfs)"
else
    warn "No touchscreen detected (will use mouse/trackpad)"
fi

# Audio
if aplay -l 2>/dev/null | grep -qi "card"; then
    ok "Audio device detected"
else
    warn "No audio device detected — install may add drivers"
fi

# ── 3. Install dependencies ──────────────────────────────────────
step "Installing packages"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# Core packages
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    chromium-browser \
    openbox xorg xinit \
    pulseaudio pulseaudio-utils alsa-utils \
    network-manager \
    avahi-daemon avahi-utils \
    unclutter xdotool \
    git curl wget \
    2>/dev/null || true

ok "Core packages installed"

# Surface-specific kernel (if Surface detected)
if [ "$SURFACE" = true ]; then
    step "Installing linux-surface kernel"

    # Add linux-surface repo
    if [ ! -f /etc/apt/sources.list.d/linux-surface.list ]; then
        wget -qO - https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc | \
            gpg --dearmor | tee /etc/apt/trusted.gpg.d/linux-surface.gpg > /dev/null

        echo "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main" | \
            tee /etc/apt/sources.list.d/linux-surface.list

        apt-get update -qq
    fi

    apt-get install -y -qq linux-image-surface linux-headers-surface \
        iptsd libwacom-surface 2>/dev/null || true

    ok "Surface kernel + touchscreen drivers installed"
fi

# ── 4. Create atlas user ─────────────────────────────────────────
step "Creating atlas user"

if ! id atlas &>/dev/null; then
    useradd -m -s /bin/bash -G audio,video,input,netdev atlas
    echo "atlas:atlas-setup" | chpasswd
    ok "User 'atlas' created (password: atlas-setup)"
else
    ok "User 'atlas' already exists"
fi

# ── 5. Install Atlas satellite agent ─────────────────────────────
step "Installing Atlas satellite agent"

INSTALL_DIR="/opt/atlas-satellite"
mkdir -p "$INSTALL_DIR"

# Clone or copy satellite code
if [ -d "/opt/atlas-cortex/satellite" ]; then
    cp -r /opt/atlas-cortex/satellite/* "$INSTALL_DIR/" 2>/dev/null || true
elif [ -d "$(dirname "$0")/../atlas_satellite" ]; then
    # Running from within the repo
    REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    cp -r "$REPO_DIR"/* "$INSTALL_DIR/" 2>/dev/null || true
else
    git clone --depth 1 https://github.com/Betanu701/atlas-cortex.git /tmp/atlas-cortex
    cp -r /tmp/atlas-cortex/satellite/* "$INSTALL_DIR/"
    rm -rf /tmp/atlas-cortex
fi

# Create venv and install deps
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip 2>/dev/null || true
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null || true
fi

ok "Satellite agent installed"

# ── 6. Configure kiosk mode ──────────────────────────────────────
step "Configuring kiosk mode"

# Auto-login on tty1
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin atlas --noclear %I $TERM
EOF

# Openbox autostart — launch kiosk browser
AUTOSTART_DIR="/home/atlas/.config/openbox"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/autostart" << 'KIOSK'
# Hide cursor after 3 seconds of inactivity
unclutter -idle 3 &

# Disable screen blanking / power management
xset s off
xset -dpms
xset s noblank

# Wait for network
for i in $(seq 1 30); do
    if ping -c 1 -W 1 8.8.8.8 &>/dev/null; then
        break
    fi
    sleep 1
done

# Determine Atlas server URL
ATLAS_URL=""

# Try mDNS discovery
ATLAS_HOST=$(avahi-resolve -n atlas-cortex.local 2>/dev/null | awk '{print $2}')
if [ -n "$ATLAS_HOST" ]; then
    ATLAS_URL="http://${ATLAS_HOST}:5100"
fi

# Fallback: check config file
if [ -z "$ATLAS_URL" ] && [ -f /opt/atlas-satellite/config.json ]; then
    ATLAS_URL=$(python3 -c "
import json, sys
try:
    print(json.load(open('/opt/atlas-satellite/config.json')).get('server_url',''))
except Exception:
    sys.exit(0)
" 2>/dev/null)
fi

# Fallback: show setup page
if [ -z "$ATLAS_URL" ]; then
    ATLAS_URL="file:///opt/atlas-satellite/tablet/setup.html"
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
    "${ATLAS_URL}/avatar#skin=nick" &
KIOSK
chown -R atlas:atlas /home/atlas/.config

# .bash_profile to auto-start X on tty1
cat > /home/atlas/.bash_profile << 'PROFILE'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx -- -nocursor 2>/dev/null
fi
PROFILE
chown atlas:atlas /home/atlas/.bash_profile

# .xinitrc
cat > /home/atlas/.xinitrc << 'XINITRC'
exec openbox-session
XINITRC
chown atlas:atlas /home/atlas/.xinitrc

ok "Kiosk mode configured"

# ── 7. Configure satellite agent service ─────────────────────────
step "Configuring satellite service"

ATLAS_UID=$(id -u atlas 2>/dev/null || echo 1000)

cat > /etc/systemd/system/atlas-satellite.service << SVCEOF
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
Environment=PULSE_SERVER=unix:/run/user/${ATLAS_UID}/pulse/native

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable atlas-satellite

ok "Satellite service configured"

# ── 8. Install kiosk systemd service (backup for Openbox) ────────
step "Installing kiosk service"

if [ -f "$INSTALL_DIR/tablet/atlas-kiosk.service" ]; then
    cp "$INSTALL_DIR/tablet/atlas-kiosk.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable atlas-kiosk 2>/dev/null || true
    ok "Kiosk service installed"
else
    info "Kiosk service file not found — using Openbox autostart"
fi

# ── 9. Configure WiFi setup (first boot) ─────────────────────────
step "Configuring first-boot WiFi setup"

if [ -d "$INSTALL_DIR/captive_portal" ]; then
    # Install Flask for captive portal
    "$INSTALL_DIR/venv/bin/pip" install -q flask 2>/dev/null || true

    if [ -f "$INSTALL_DIR/captive_portal/atlas-wifi-setup.service" ]; then
        cp "$INSTALL_DIR/captive_portal/atlas-wifi-setup.service" \
           /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable atlas-wifi-setup 2>/dev/null || true
        ok "WiFi setup service configured"
    fi
else
    info "No captive portal found — configure WiFi manually:"
    info "  nmcli dev wifi connect SSID password PASS"
fi

# ── 10. Screen and power settings ────────────────────────────────
step "Display configuration"

# Disable suspend/sleep on lid close (tablets may have covers)
mkdir -p /etc/systemd/logind.conf.d/
cat > /etc/systemd/logind.conf.d/atlas-tablet.conf << 'LOGIND'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
LOGIND

# udev rule for tablet features
cat > /etc/udev/rules.d/99-atlas-tablet.rules << 'UDEV'
# Atlas tablet satellite — auto-detect orientation from accelerometer
# Surface Go default: landscape
UDEV

# Try to set screen brightness to max
for bl in /sys/class/backlight/*/brightness; do
    if [ -f "$bl" ]; then
        max=$(cat "$(dirname "$bl")/max_brightness" 2>/dev/null || echo 100)
        echo "$max" > "$bl" 2>/dev/null || true
    fi
done

ok "Display configured"

# ── 11. Finalize ─────────────────────────────────────────────────
step "Finalizing"

# Create marker file
touch /opt/atlas-satellite/.tablet-installed

# Summary
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Atlas Tablet Satellite — Installed! ✓      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo "    1. Reboot: sudo reboot"
echo "    2. Connect to WiFi (shown on screen)"
echo "    3. Atlas avatar appears when server is found"
echo ""
echo "  SSH access: ssh atlas@$(hostname).local"
echo "  Password: atlas-setup (change after setup!)"
echo ""
echo "  To reconfigure: sudo /opt/atlas-satellite/tablet/install-tablet.sh"
