#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# ReSpeaker HAT Setup for Raspberry Pi
# Configures I2C, SPI, and audio overlay for ReSpeaker 2-Mic/4-Mic HAT
#
# Tested on: Raspberry Pi OS Bookworm (32-bit), Pi Zero 2 W
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

echo "╔══════════════════════════════════════════╗"
echo "║     ReSpeaker HAT Setup Script           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="/boot/config.txt"
fi

echo "→ Using config: $CONFIG_FILE"

# ── 1. Enable I2C ─────────────────────────────────────────────────
echo "→ Enabling I2C..."
sudo raspi-config nonint do_i2c 0

# ── 2. Enable SPI (for APA102 LEDs) ──────────────────────────────
echo "→ Enabling SPI..."
sudo raspi-config nonint do_spi 0

# ── 3. Install seeed-voicecard drivers ────────────────────────────
# The ReSpeaker 2-Mic HAT uses the WM8960 codec which needs
# a device tree overlay. The seeed-voicecard project provides this.
echo "→ Installing ReSpeaker audio drivers..."

if [ ! -d "/tmp/seeed-voicecard" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq git dkms
    cd /tmp
    git clone --depth 1 https://github.com/HinTak/seeed-voicecard.git
    cd seeed-voicecard
else
    cd /tmp/seeed-voicecard
    git pull
fi

# Install the driver
sudo ./install.sh

# ── 4. Verify overlay is in config.txt ────────────────────────────
if ! grep -q "seeed-2mic-voicecard" "$CONFIG_FILE" 2>/dev/null; then
    echo "→ Adding device tree overlay to $CONFIG_FILE"
    echo "dtoverlay=seeed-2mic-voicecard" | sudo tee -a "$CONFIG_FILE" > /dev/null
fi

# ── 5. Set up ALSA defaults ──────────────────────────────────────
echo "→ Configuring ALSA defaults..."
cat > "$HOME/.asoundrc" << 'EOF'
# ReSpeaker 2-Mic HAT as default audio device
pcm.!default {
    type asym
    playback.pcm "plughw:seeed2micvoicec"
    capture.pcm "plughw:seeed2micvoicec"
}

ctl.!default {
    type hw
    card seeed2micvoicec
}
EOF

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     ReSpeaker Setup Complete! ✓          ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "⚠  A REBOOT is required for changes to take effect."
echo ""
echo "After reboot, test with:"
echo "  arecord -D plughw:seeed2micvoicec -f S16_LE -r 16000 -c 1 -d 5 test.wav"
echo "  aplay test.wav"
echo ""
read -p "Reboot now? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
fi
