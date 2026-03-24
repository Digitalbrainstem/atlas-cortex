#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Tablet Satellite — USB Installer Image Builder
#
# Creates a customized Ubuntu ISO that boots into the Atlas satellite
# installer automatically. Flash the output ISO to a USB drive and
# boot the tablet from it.
#
# Prerequisites: cubic (GUI) or live-build (CLI)
#
# For a simpler approach, install Ubuntu minimal manually, then run:
#   curl -sL .../install-tablet.sh | sudo bash
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*"; }

echo -e "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Atlas Tablet Image Builder                 ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Check prerequisites ──────────────────────────────────────────
MISSING=()
for tool in wget xorriso; do
    if ! command -v "$tool" &>/dev/null; then
        MISSING+=("$tool")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    err "Missing required tools: ${MISSING[*]}"
    echo ""
    echo "  Install with: sudo apt install ${MISSING[*]}"
    echo ""
    echo "  Alternatively, skip image building and install directly:"
    echo "    1. Install Ubuntu Server/Minimal on the tablet"
    echo "    2. Run: curl -sL https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/tablet/install-tablet.sh | sudo bash"
    exit 1
fi

# ── Configuration ────────────────────────────────────────────────
UBUNTU_VERSION="24.04.1"
UBUNTU_ISO_URL="https://releases.ubuntu.com/24.04/ubuntu-${UBUNTU_VERSION}-live-server-amd64.iso"
UBUNTU_ISO="ubuntu-${UBUNTU_VERSION}-live-server-amd64.iso"
OUTPUT_ISO="atlas-tablet-satellite-${UBUNTU_VERSION}.iso"
WORK_DIR="/tmp/atlas-tablet-build-$$"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info "Base ISO: Ubuntu ${UBUNTU_VERSION} Server"
info "Output:   ${OUTPUT_ISO}"
echo ""

# ── Download base ISO (if not cached) ────────────────────────────
if [ ! -f "$UBUNTU_ISO" ]; then
    info "Downloading Ubuntu ${UBUNTU_VERSION} ISO..."
    wget -q --show-progress -O "$UBUNTU_ISO" "$UBUNTU_ISO_URL"
    ok "Downloaded base ISO"
else
    ok "Using cached ISO: ${UBUNTU_ISO}"
fi

# ── Extract ISO ──────────────────────────────────────────────────
info "Extracting ISO to ${WORK_DIR}..."
mkdir -p "$WORK_DIR"
xorriso -osirrox on -indev "$UBUNTU_ISO" -extract / "$WORK_DIR" 2>/dev/null
ok "ISO extracted"

# ── Inject Atlas installer ───────────────────────────────────────
info "Injecting Atlas tablet installer..."
mkdir -p "$WORK_DIR/atlas"
cp "$SCRIPT_DIR/install-tablet.sh" "$WORK_DIR/atlas/"
cp "$SCRIPT_DIR/setup.html" "$WORK_DIR/atlas/"
cp "$SCRIPT_DIR/atlas-kiosk.service" "$WORK_DIR/atlas/"
ok "Installer files injected"

# ── Add autoinstall config (late-command runs our installer) ─────
info "Configuring autoinstall..."
mkdir -p "$WORK_DIR/autoinstall"
cat > "$WORK_DIR/autoinstall/user-data" << 'AUTOINSTALL'
#cloud-config
autoinstall:
  version: 1
  locale: en_US.UTF-8
  keyboard:
    layout: us
  identity:
    hostname: atlas-tablet
    username: atlas
    password: "$6$rounds=4096$atlas$placeholder"
  ssh:
    install-server: true
  packages:
    - python3
    - python3-venv
    - git
    - curl
  late-commands:
    - cp -r /cdrom/atlas /target/tmp/atlas
    - curtin in-target -- bash /tmp/atlas/install-tablet.sh
AUTOINSTALL

cat > "$WORK_DIR/autoinstall/meta-data" << 'META'
META
ok "Autoinstall configured"

# ── Rebuild ISO ──────────────────────────────────────────────────
info "Building output ISO..."
xorriso -as mkisofs \
    -r -V "Atlas Tablet Satellite" \
    -o "$OUTPUT_ISO" \
    -J -joliet-long \
    -iso-level 3 \
    "$WORK_DIR" 2>/dev/null
ok "Built: ${OUTPUT_ISO}"

# ── Cleanup ──────────────────────────────────────────────────────
rm -rf "$WORK_DIR"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Image built successfully! ✓                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Flash to USB:"
echo "    sudo dd if=${OUTPUT_ISO} of=/dev/sdX bs=4M status=progress"
echo ""
echo "  Then boot the tablet from the USB drive."
echo "  The installer runs automatically — no interaction needed."
