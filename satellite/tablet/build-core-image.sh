#!/usr/bin/env bash
# Atlas Tablet OS — Ubuntu Core Image Builder
#
# Builds a custom Ubuntu Core image with:
#   - ubuntu-frame (Wayland kiosk compositor)
#   - wpe-webkit-mir-kiosk (web browser)
#   - network-manager (WiFi)
#   - atlas-satellite (our agent)
#
# Requirements: snapcraft, ubuntu-image, snap account
#
# Usage: sudo bash satellite/tablet/build-core-image.sh [--skip-snap]
#
# Options:
#   --skip-snap    Skip building the snap (use existing .snap file)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build/ubuntu-core"
VERSION="0.1.0"
TIMESTAMP="$(date +%Y%m%d.%H%M)"
OUTPUT_NAME="atlas-tablet-core-v${VERSION}-${TIMESTAMP}.img"

SKIP_SNAP=false
for arg in "$@"; do
    case "$arg" in
        --skip-snap) SKIP_SNAP=true ;;
    esac
done

# ── Colours ───────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { error "$@"; exit 1; }

# ── Dependency checks ─────────────────────────────────────────────

check_deps() {
    local missing=()
    for cmd in snapcraft ubuntu-image snap; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        die "Missing tools: ${missing[*]}
Install with:
  sudo snap install snapcraft --classic
  sudo snap install ubuntu-image --classic"
    fi
    ok "All build tools found"
}

# ── Phase 1: Build the atlas-satellite snap ───────────────────────

build_snap() {
    if [ "$SKIP_SNAP" = true ]; then
        local existing
        existing=$(find "$PROJECT_ROOT" -maxdepth 1 -name "atlas-satellite_*.snap" | head -1)
        if [ -z "$existing" ]; then
            die "No .snap file found — cannot use --skip-snap"
        fi
        info "Using existing snap: $existing"
        SNAP_FILE="$existing"
        return
    fi

    info "Phase 1: Building atlas-satellite snap..."
    cd "$PROJECT_ROOT"

    # snapcraft needs the snap directory at repo root
    if [ ! -L "$PROJECT_ROOT/snap" ] && [ ! -d "$PROJECT_ROOT/snap" ]; then
        ln -sf satellite/snap "$PROJECT_ROOT/snap"
    fi

    snapcraft --destructive-mode 2>&1 | tail -20
    SNAP_FILE=$(find "$PROJECT_ROOT" -maxdepth 1 -name "atlas-satellite_*.snap" -newer "$0" | head -1)
    if [ -z "$SNAP_FILE" ]; then
        die "Snap build failed — no .snap file produced"
    fi
    ok "Snap built: $SNAP_FILE"
}

# ── Phase 2: Create signed model assertion ────────────────────────

create_model_assertion() {
    info "Phase 2: Creating model assertion..."
    mkdir -p "$BUILD_DIR"

    local model_json="$SCRIPT_DIR/ubuntu-core/model.json"
    local model_assert="$BUILD_DIR/model.assert"

    if [ ! -f "$model_json" ]; then
        die "Model JSON not found: $model_json"
    fi

    # For development, use unsigned model with --dangerous flag
    # For production, sign with: snap sign -k <key> < model.json > model.assert
    cp "$model_json" "$BUILD_DIR/model.json"

    if snap known --remote account-key 2>/dev/null | grep -q "betanu701"; then
        info "Signing model assertion..."
        snap sign -k atlas-tablet < "$BUILD_DIR/model.json" > "$model_assert"
        ok "Model assertion signed"
    else
        warn "No snap signing key found — will use --dangerous flag"
        MODEL_DANGEROUS=true
    fi
}

# ── Phase 3: Build Ubuntu Core image ──────────────────────────────

build_image() {
    info "Phase 3: Building Ubuntu Core image..."
    mkdir -p "$BUILD_DIR"

    local img_path="$BUILD_DIR/$OUTPUT_NAME"
    local model_file

    local uc_args=(
        --image-size 4G
    )

    # Include our custom snap
    if [ -n "${SNAP_FILE:-}" ]; then
        uc_args+=(--snap "$SNAP_FILE")
    fi

    if [ "${MODEL_DANGEROUS:-false}" = true ]; then
        model_file="$BUILD_DIR/model.json"
        uc_args+=(--dangerous)
        warn "Building with --dangerous (unsigned model)"
    else
        model_file="$BUILD_DIR/model.assert"
    fi

    ubuntu-image snap "$model_file" \
        --output-dir "$BUILD_DIR" \
        "${uc_args[@]}" \
        2>&1

    # Rename output
    local raw_img
    raw_img=$(find "$BUILD_DIR" -name "*.img" -newer "$0" | head -1)
    if [ -z "$raw_img" ]; then
        die "Image build failed — no .img file produced"
    fi
    if [ "$raw_img" != "$img_path" ]; then
        mv "$raw_img" "$img_path"
    fi

    ok "Image built: $img_path"
    IMAGE_PATH="$img_path"
}

# ── Phase 4: Generate checksum ────────────────────────────────────

generate_checksum() {
    info "Phase 4: Generating checksum..."
    sha256sum "$IMAGE_PATH" > "${IMAGE_PATH}.sha256"
    ok "Checksum: ${IMAGE_PATH}.sha256"
}

# ── Phase 5: Summary ─────────────────────────────────────────────

summary() {
    local size
    size=$(du -h "$IMAGE_PATH" | cut -f1)

    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  Atlas Tablet OS — Ubuntu Core Image"
    echo "════════════════════════════════════════════════════════════"
    echo "  Image:     $IMAGE_PATH"
    echo "  Size:      $size"
    echo "  Checksum:  ${IMAGE_PATH}.sha256"
    echo ""
    echo "  Flash to USB/SD:"
    echo "    sudo dd if=$IMAGE_PATH of=/dev/sdX bs=32M status=progress"
    echo ""
    echo "  First-boot configuration:"
    echo "    snap set atlas-satellite server-url=ws://CORTEX_IP:5100/ws/satellite"
    echo "    snap set atlas-satellite room=\"Living Room\""
    echo "    snap set atlas-satellite wifi-ssid=MyNetwork wifi-password=secret"
    echo "════════════════════════════════════════════════════════════"
}

# ── Main ──────────────────────────────────────────────────────────

main() {
    info "Atlas Tablet OS — Ubuntu Core Image Builder"
    info "Version: $VERSION  Timestamp: $TIMESTAMP"
    echo ""

    check_deps
    build_snap
    create_model_assertion
    build_image
    generate_checksum
    summary

    ok "Done!"
}

MODEL_DANGEROUS=false
SNAP_FILE=""
IMAGE_PATH=""
main
