#!/bin/bash
# fix-kiosk-boot.sh — Fix Surface Go kiosk: switch from Cage/Wayland to X11
#
# Run on the tablet via SSH:
#   ssh atlas@<tablet-ip>
#   curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/tablet/fix-kiosk-boot.sh | sudo bash
#
# Or copy and run:
#   sudo bash fix-kiosk-boot.sh
#
set -euo pipefail

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
err()   { echo -e "\033[1;31m[ERROR]\033[0m $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || err "Must run as root (sudo)"

ATLAS_USER="atlas"
ATLAS_HOME="/home/$ATLAS_USER"

# ── 1. Disable Cage/Wayland if present ────────────────────────────
info "Disabling Cage/Wayland services (if present)..."
systemctl disable cage.service 2>/dev/null && ok "Disabled cage.service" || true
systemctl stop cage.service 2>/dev/null || true

# ── 2. Install X11 display stack ─────────────────────────────────
info "Installing X11 display stack..."
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xserver-xorg-core \
    xserver-xorg-input-libinput \
    xserver-xorg-video-intel \
    xinit \
    x11-xserver-utils \
    libgl1-mesa-dri \
    unclutter
ok "X11 packages installed"

# ── 3. Getty autologin on tty1 ───────────────────────────────────
info "Configuring getty autologin on tty1..."
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin atlas --noclear %I $TERM
EOF
ok "Getty autologin configured"

# ── 4. .bash_profile — auto-start X on tty1 ─────────────────────
info "Writing .bash_profile..."
cat > "$ATLAS_HOME/.bash_profile" << 'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx -- -nocursor 2>/dev/null
fi
EOF
chown "$ATLAS_USER:$ATLAS_USER" "$ATLAS_HOME/.bash_profile"
ok ".bash_profile created"

# ── 5. .xinitrc — Chromium kiosk ─────────────────────────────────
info "Writing .xinitrc..."
cat > "$ATLAS_HOME/.xinitrc" << 'XINITRC'
#!/bin/sh
# Disable screen blanking / power management
xset s off
xset -dpms
xset s noblank

# Hide cursor after 3 seconds idle
unclutter -idle 3 &

# Determine kiosk URL
SETUP_URL="http://localhost:8080"
KIOSK_URL="$SETUP_URL"

# Try mDNS discovery for Atlas server
ATLAS_HOST=$(avahi-resolve -n atlas-cortex.local 2>/dev/null | awk '{print $2}')
if [ -n "$ATLAS_HOST" ]; then
    KIOSK_URL="http://${ATLAS_HOST}:5100/chat"
elif [ -f /opt/atlas-satellite/config.json ]; then
    SERVER_URL=$(python3 -c "
import json, sys
try:
    cfg = json.load(open('/opt/atlas-satellite/config.json'))
    url = cfg.get('server_url','')
    if url:
        url = url.replace('ws://','http://').replace('wss://','https://')
        url = url.split('/ws/')[0]
        print(url + '/chat')
except Exception:
    pass
" 2>/dev/null)
    [ -n "$SERVER_URL" ] && KIOSK_URL="$SERVER_URL"
fi

# Detect chromium binary name
CHROMIUM=""
for bin in chromium chromium-browser; do
    if command -v "$bin" >/dev/null 2>&1; then
        CHROMIUM="$bin"
        break
    fi
done
[ -z "$CHROMIUM" ] && CHROMIUM="chromium"

exec "$CHROMIUM" \
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
    --disk-cache-dir=/tmp/chromium-cache \
    "$KIOSK_URL"
XINITRC
chown "$ATLAS_USER:$ATLAS_USER" "$ATLAS_HOME/.xinitrc"
chmod +x "$ATLAS_HOME/.xinitrc"
ok ".xinitrc created"

# ── 6. X11 config for Intel GPU (Surface Go) ────────────────────
info "Writing X11 config..."
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/20-intel.conf << 'EOF'
Section "Device"
    Driver      "intel"
    Option      "TearFree" "true"
    Option      "AccelMethod" "sna"
    Option      "DRI" "3"
EndSection

Section "ServerFlags"
    Option "BlankTime"   "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
EndSection
EOF
ok "Xorg Intel config written"

# ── 7. Allow atlas user to start X ──────────────────────────────
cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
ok "Xwrapper configured"

# ── 8. Logind overrides (no lid sleep, no blanking) ──────────────
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/atlas-kiosk.conf << 'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
NAutoVTs=2
EOF
ok "Logind overrides written"

# ── 9. Set default target to multi-user (no display manager) ────
systemctl set-default multi-user.target
systemctl disable lightdm.service 2>/dev/null || true
systemctl disable gdm.service 2>/dev/null || true
systemctl disable sddm.service 2>/dev/null || true
ok "Default target set to multi-user"

# ── 10. Reload and enable getty ──────────────────────────────────
systemctl daemon-reload
systemctl enable getty@tty1.service
ok "Getty service enabled"

echo ""
echo -e "\033[1;32m╔══════════════════════════════════════════════════════╗\033[0m"
echo -e "\033[1;32m║  Kiosk boot fix applied successfully!                ║\033[0m"
echo -e "\033[1;32m║                                                      ║\033[0m"
echo -e "\033[1;32m║  Boot chain:                                         ║\033[0m"
echo -e "\033[1;32m║    getty@tty1 → autologin atlas                      ║\033[0m"
echo -e "\033[1;32m║    .bash_profile → startx                            ║\033[0m"
echo -e "\033[1;32m║    .xinitrc → Chromium kiosk                         ║\033[0m"
echo -e "\033[1;32m║                                                      ║\033[0m"
echo -e "\033[1;32m║  Reboot now:  sudo reboot                            ║\033[0m"
echo -e "\033[1;32m╚══════════════════════════════════════════════════════╝\033[0m"
