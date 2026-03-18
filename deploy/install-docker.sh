#!/usr/bin/env bash
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*" >&2; }

echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Atlas Cortex — Docker Installer        ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

INSTALL_DIR="${ATLAS_INSTALL_DIR:-/opt/atlas-cortex}"
REPO_URL="https://github.com/Betanu701/atlas-cortex.git"
BRANCH="main"

# ── 1. Check Docker ─────────────────────────────────────────────────
info "Checking prerequisites..."

if ! command -v docker &>/dev/null; then
    err "Docker is not installed"
    echo "    Install: https://docs.docker.com/engine/install/"
    exit 1
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# Check docker compose (plugin or standalone)
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
    ok "Docker Compose $(docker compose version --short)"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
    ok "docker-compose $(docker-compose --version | awk '{print $4}' | tr -d ',')"
else
    err "Docker Compose is not installed"
    echo "    Install: https://docs.docker.com/compose/install/"
    exit 1
fi

# ── 2. Detect GPU ───────────────────────────────────────────────────
info "Detecting GPU..."

GPU="none"
COMPOSE_OVERRIDE=""

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU="nvidia"
    COMPOSE_OVERRIDE="docker-compose.gpu-nvidia.yml"
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    ok "NVIDIA GPU: $GPU_INFO"

    if ! docker info 2>/dev/null | grep -q "nvidia"; then
        warn "NVIDIA Container Toolkit may not be installed"
        echo "    Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
    fi
elif command -v rocm-smi &>/dev/null; then
    GPU="amd"
    COMPOSE_OVERRIDE="docker-compose.gpu-amd.yml"
    ok "AMD GPU detected (ROCm)"
elif [ -d /dev/dri ] && ls /dev/dri/renderD* &>/dev/null; then
    GPU="intel"
    COMPOSE_OVERRIDE="docker-compose.gpu-intel.yml"
    ok "Intel GPU detected"
else
    warn "No GPU detected — running CPU-only"
fi

# ── 3. Clone / update repo ──────────────────────────────────────────
info "Setting up project..."

if [ -d "$INSTALL_DIR/.git" ]; then
    info "Existing installation found — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    ok "Updated"
else
    mkdir -p "$INSTALL_DIR"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned to $INSTALL_DIR"
fi

cd "$INSTALL_DIR/docker"

# ── 4. Environment file ─────────────────────────────────────────────
info "Configuring environment..."

ENV_FILE="$INSTALL_DIR/docker/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'EOF'
# Atlas Cortex — Docker environment
# Copy this to .env and customize

# LLM
LLM_PROVIDER=ollama
MODEL_FAST=qwen2.5:7b
MODEL_THINKING=qwen3:30b-a3b

# TTS
TTS_PROVIDER=orpheus
KOKORO_VOICE=af_bella

# Whisper STT
WHISPER_MODEL=large-v3-turbo-q5_0

# Timezone
TZ=America/New_York

# Ports
CORTEX_PORT=5100
EOF
    ok "Created .env with defaults"
    info "Edit $ENV_FILE to customize"
else
    ok ".env already exists"
fi

# ── 5. Start containers ─────────────────────────────────────────────
info "Starting Atlas Cortex..."

COMPOSE_CMD="$COMPOSE -f docker-compose.yml"
if [ -n "$COMPOSE_OVERRIDE" ] && [ -f "$COMPOSE_OVERRIDE" ]; then
    COMPOSE_CMD="$COMPOSE_CMD -f $COMPOSE_OVERRIDE"
    ok "Using GPU override: $COMPOSE_OVERRIDE"
fi

$COMPOSE_CMD up -d --build

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Docker Setup Complete! 🐳           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}API:${NC}       http://localhost:5100"
echo -e "  ${BOLD}Admin:${NC}     http://localhost:5100/admin"
echo -e "  ${BOLD}GPU:${NC}       $GPU"
echo ""
echo -e "  ${BOLD}Commands:${NC}"
echo -e "    $COMPOSE -f docker-compose.yml logs -f atlas-cortex"
echo -e "    $COMPOSE -f docker-compose.yml restart atlas-cortex"
echo -e "    $COMPOSE -f docker-compose.yml down"
echo ""
