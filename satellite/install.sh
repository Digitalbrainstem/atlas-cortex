#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Satellite Agent — Installer
# Installs the satellite agent on a Linux device (Raspberry Pi, etc.)
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh | bash
#
# Or for shared mode (no system changes):
#   curl -sL .../install.sh | ATLAS_MODE=shared bash
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_BASE="https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite"
MODE="${ATLAS_MODE:-dedicated}"

if [ "$MODE" = "dedicated" ]; then
    INSTALL_DIR="/opt/atlas-satellite"
else
    INSTALL_DIR="$HOME/.atlas-satellite"
fi

echo "╔══════════════════════════════════════════╗"
echo "║     Atlas Satellite Agent Installer      ║"
echo "║     Mode: $(printf '%-30s' "$MODE")║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. System dependencies ────────────────────────────────────────
echo "→ Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-venv python3-dev \
        libasound2-dev \
        i2c-tools \
        2>/dev/null
else
    echo "  ⚠ apt not found — please install python3, python3-venv, libasound2-dev manually"
fi

# ── 2. Create install directory ───────────────────────────────────
echo "→ Creating install directory: $INSTALL_DIR"
if [ "$MODE" = "dedicated" ]; then
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$USER:$USER" "$INSTALL_DIR"
else
    mkdir -p "$INSTALL_DIR"
fi

# ── 3. Create virtual environment ─────────────────────────────────
echo "→ Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"
# shellcheck disable=SC1091
source "$INSTALL_DIR/.venv/bin/activate"

# ── 4. Download agent code ────────────────────────────────────────
echo "→ Downloading satellite agent..."
mkdir -p "$INSTALL_DIR/atlas_satellite/platforms"

AGENT_FILES=(
    "__init__.py"
    "__main__.py"
    "agent.py"
    "audio.py"
    "config.py"
    "filler_cache.py"
    "led.py"
    "mdns.py"
    "vad.py"
    "wake_word.py"
    "ws_client.py"
)

for file in "${AGENT_FILES[@]}"; do
    curl -sL "$REPO_BASE/atlas_satellite/$file" -o "$INSTALL_DIR/atlas_satellite/$file"
    echo "  ✓ $file"
done

curl -sL "$REPO_BASE/atlas_satellite/platforms/__init__.py" \
    -o "$INSTALL_DIR/atlas_satellite/platforms/__init__.py"

# ── 5. Install Python dependencies ────────────────────────────────
echo "→ Installing Python dependencies..."
curl -sL "$REPO_BASE/requirements.txt" -o "$INSTALL_DIR/requirements.txt"
pip install -q -r "$INSTALL_DIR/requirements.txt"

# ── 6. Create cache directories ───────────────────────────────────
mkdir -p "$INSTALL_DIR/cache/fillers"

# ── 7. Create default config if none exists ───────────────────────
CONFIG_FILE="$INSTALL_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    HOSTNAME=$(hostname)

    # Auto-detect audio device and LED type
    AUDIO_DEV="default"
    LED_TYPE="none"
    if arecord -l 2>/dev/null | grep -q "seeed2micvoicec\|seeed-2mic"; then
        AUDIO_DEV="hw:0,0"
        LED_TYPE="respeaker_2mic"
        echo "  ✓ Detected ReSpeaker 2-Mic HAT"
    elif arecord -l 2>/dev/null | grep -q "seeed4micvoicec\|seeed-4mic"; then
        AUDIO_DEV="hw:0,0"
        LED_TYPE="respeaker_4mic"
        echo "  ✓ Detected ReSpeaker 4-Mic Array"
    fi

    cat > "$CONFIG_FILE" << EOF
{
  "satellite_id": "sat-$HOSTNAME",
  "server_url": "ws://atlas-server:5100/ws/satellite",
  "room": "",
  "mode": "$MODE",
  "service_port": 5110,
  "wake_word": "hey atlas",
  "volume": 0.7,
  "mic_gain": 0.8,
  "vad_sensitivity": 2,
  "audio_device_in": "$AUDIO_DEV",
  "audio_device_out": "$AUDIO_DEV",
  "led_type": "$LED_TYPE",
  "wake_word_enabled": false,
  "filler_enabled": true,
  "features": {}
}
EOF
    echo "→ Default config created at $CONFIG_FILE"
    echo "  ⚠ Edit server_url and room before starting!"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        Installation Complete! ✓          ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Edit config:  nano $CONFIG_FILE"
echo "  2. Test run:     $INSTALL_DIR/.venv/bin/python -m atlas_satellite --debug"
echo "  3. Install service: see setup instructions"
echo ""
