#!/usr/bin/env bash
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*"; }
step()  { echo -e "\n${BOLD}── $* ──${NC}"; }

ask() {
    local prompt="$1" default="$2" reply
    echo -en "${BLUE}?${NC}  ${prompt} [${default}]: "
    read -r reply
    echo "${reply:-$default}"
}

INSTALL_DIR="/opt/atlas-cortex"
REPO_URL="https://github.com/Betanu701/atlas-cortex.git"
BRANCH="main"
CREATE_USER="yes"
INSTALL_SERVICE="yes"
START_SERVICE="yes"

# ── Banner ──────────────────────────────────────────────────────────
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Atlas Cortex — Server Installer      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Prerequisites ───────────────────────────────────────────────
step "Checking prerequisites"

MISSING=()

if ! command -v python3 &>/dev/null; then
    MISSING+=("python3")
else
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
        err "Python $PY_VER found — Python 3.11+ is required"
        exit 1
    fi
    ok "Python $PY_VER"
fi

if ! command -v git &>/dev/null; then
    MISSING+=("git")
else
    ok "git $(git --version | awk '{print $3}')"
fi

if ! python3 -c "import venv" &>/dev/null; then
    MISSING+=("python3-venv")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    err "Missing dependencies: ${MISSING[*]}"
    echo "    Install them with your package manager, e.g.:"
    echo "      sudo apt install ${MISSING[*]}"
    echo "      sudo dnf install ${MISSING[*]}"
    exit 1
fi

ok "All prerequisites satisfied"

# ── 2. Hardware detection ──────────────────────────────────────────
step "Detecting hardware"

GPU="none"
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU="nvidia"
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    ok "NVIDIA GPU: $GPU_INFO"
elif command -v rocm-smi &>/dev/null && rocm-smi &>/dev/null; then
    GPU="amd"
    ok "AMD GPU detected (ROCm)"
elif [ -d /dev/dri ] && ls /dev/dri/renderD* &>/dev/null; then
    GPU="intel"
    ok "Intel GPU detected (likely)"
else
    warn "No GPU detected — CPU-only mode"
fi

RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo "unknown")
if [ "$RAM_GB" != "unknown" ]; then
    ok "RAM: ${RAM_GB}GB"
    if [ "$RAM_GB" -lt 8 ]; then
        warn "Less than 8GB RAM — use smaller models (qwen2.5:1.5b)"
    fi
fi

DISK_AVAIL=$(df -BG "${INSTALL_DIR%/*}" 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G' || echo "unknown")
if [ "$DISK_AVAIL" != "unknown" ]; then
    ok "Disk available: ${DISK_AVAIL}GB"
    if [ "$DISK_AVAIL" -lt 10 ]; then
        warn "Less than 10GB free — consider freeing space"
    fi
fi

# Detect Ollama
if command -v ollama &>/dev/null || curl -sf http://localhost:11434/api/version &>/dev/null; then
    ok "Ollama detected"
    OLLAMA_FOUND="yes"
else
    info "Ollama not found — you'll need an LLM backend"
    OLLAMA_FOUND="no"
fi

# ── 3. Configuration ──────────────────────────────────────────────
step "Configuration"

INSTALL_DIR=$(ask "Install directory" "$INSTALL_DIR")

if [ "$(id -u)" -eq 0 ]; then
    CREATE_USER=$(ask "Create 'atlas' system user? (yes/no)" "$CREATE_USER")
    INSTALL_SERVICE=$(ask "Install systemd service? (yes/no)" "$INSTALL_SERVICE")
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        START_SERVICE=$(ask "Start service after install? (yes/no)" "$START_SERVICE")
    fi
else
    warn "Not running as root — skipping user creation and systemd setup"
    CREATE_USER="no"
    INSTALL_SERVICE="no"
    START_SERVICE="no"
fi

echo ""
info "Install directory: $INSTALL_DIR"
info "Create user:       $CREATE_USER"
info "Systemd service:   $INSTALL_SERVICE"
echo ""

# ── 4. Create atlas user ──────────────────────────────────────────
if [ "$CREATE_USER" = "yes" ]; then
    step "Creating atlas user"
    if id atlas &>/dev/null; then
        ok "User 'atlas' already exists"
    else
        useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" atlas
        ok "Created system user 'atlas'"
    fi
fi

# ── 5. Clone repository ──────────────────────────────────────────
step "Installing Atlas Cortex"

if [ -d "$INSTALL_DIR/.git" ]; then
    info "Existing installation found — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    ok "Updated to latest"
else
    mkdir -p "$INSTALL_DIR"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned to $INSTALL_DIR"
fi

# ── 6. Virtual environment + dependencies ─────────────────────────
step "Setting up Python environment"

VENV="$INSTALL_DIR/venv"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
    ok "Created virtual environment"
else
    ok "Virtual environment exists"
fi

"$VENV/bin/pip" install --upgrade pip setuptools wheel --quiet
ok "Updated pip/setuptools"

"$VENV/bin/pip" install -e "$INSTALL_DIR" --quiet
ok "Installed atlas-cortex"

# Install extras based on hardware
if [ "$GPU" != "none" ]; then
    info "GPU detected — consider installing media extras:"
    info "  $VENV/bin/pip install -e '$INSTALL_DIR[media]'"
fi

# ── 7. Data directory ────────────────────────────────────────────
step "Preparing data directory"

DATA_DIR="$INSTALL_DIR/data"
mkdir -p "$DATA_DIR"

if [ "$CREATE_USER" = "yes" ]; then
    chown -R atlas:atlas "$INSTALL_DIR"
fi

ok "Data directory: $DATA_DIR"

# ── 8. Run installer wizard ──────────────────────────────────────
step "Configuration wizard"

if [ -t 0 ]; then
    info "Running interactive setup..."
    "$VENV/bin/python" -m cortex.install || warn "Wizard skipped or failed — configure manually"
else
    warn "Non-interactive mode — skipping wizard"
    info "Run later: $VENV/bin/python -m cortex.install"
fi

# ── 9. Systemd service ──────────────────────────────────────────
if [ "$INSTALL_SERVICE" = "yes" ]; then
    step "Installing systemd service"

    SERVICE_SRC="$INSTALL_DIR/deploy/atlas-cortex.service"
    SERVICE_DST="/etc/systemd/system/atlas-cortex.service"

    if [ -f "$SERVICE_SRC" ]; then
        cp "$SERVICE_SRC" "$SERVICE_DST"
    else
        cat > "$SERVICE_DST" << 'EOF'
[Unit]
Description=Atlas Cortex — Personal AI Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=atlas
Group=atlas
WorkingDirectory=/opt/atlas-cortex
ExecStart=/opt/atlas-cortex/venv/bin/atlas-server
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment="CORTEX_DATA_DIR=/opt/atlas-cortex/data"
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/atlas-cortex/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Patch paths if install dir differs
    if [ "$INSTALL_DIR" != "/opt/atlas-cortex" ]; then
        sed -i "s|/opt/atlas-cortex|$INSTALL_DIR|g" "$SERVICE_DST"
    fi

    systemctl daemon-reload
    systemctl enable atlas-cortex
    ok "Service installed and enabled"

    if [ "$START_SERVICE" = "yes" ]; then
        systemctl start atlas-cortex
        sleep 2
        if systemctl is-active --quiet atlas-cortex; then
            ok "Service is running"
        else
            warn "Service failed to start — check: journalctl -u atlas-cortex"
        fi
    fi
fi

# ── 10. Summary ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      Installation Complete! 🎉           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Install path:${NC}  $INSTALL_DIR"
echo -e "  ${BOLD}Python venv:${NC}   $VENV"
echo -e "  ${BOLD}Data:${NC}          $DATA_DIR"
echo -e "  ${BOLD}GPU:${NC}           $GPU"

if [ "$OLLAMA_FOUND" = "no" ]; then
    echo ""
    echo -e "  ${YELLOW}Next: Install Ollama for LLM support:${NC}"
    echo -e "    curl -fsSL https://ollama.ai/install.sh | sh"
    echo -e "    ollama pull qwen2.5:7b"
fi

echo ""
echo -e "  ${BOLD}Quick start:${NC}"
if [ "$INSTALL_SERVICE" = "yes" ]; then
    echo -e "    sudo systemctl status atlas-cortex"
    echo -e "    journalctl -u atlas-cortex -f"
else
    echo -e "    source $VENV/bin/activate"
    echo -e "    atlas-server"
fi
echo ""
echo -e "  ${BOLD}API:${NC}           http://localhost:5100"
echo -e "  ${BOLD}Admin:${NC}         http://localhost:5100/admin"
echo -e "  ${BOLD}Docs:${NC}          http://localhost:5100/docs"
echo ""
