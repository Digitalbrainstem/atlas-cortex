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

NVIDIA_GPUS=()
AMD_GPUS=()
INTEL_GPUS=()

# NVIDIA
if command -v nvidia-smi &>/dev/null; then
    while IFS= read -r line; do
        [ -n "$line" ] && NVIDIA_GPUS+=("$line")
    done < <(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null)
fi

# AMD
if command -v rocm-smi &>/dev/null; then
    while IFS= read -r line; do
        [ -n "$line" ] && AMD_GPUS+=("$line")
    done < <(rocm-smi --showproductname 2>/dev/null | grep -iE 'card|gpu' | head -5)
fi
# AMD lspci fallback
if [ ${#AMD_GPUS[@]} -eq 0 ] && command -v lspci &>/dev/null; then
    while IFS= read -r line; do
        [ -n "$line" ] && AMD_GPUS+=("$line")
    done < <(lspci 2>/dev/null | grep -iE 'VGA.*amd|VGA.*radeon|Display.*amd|Display.*radeon')
fi

# Intel discrete (Arc)
if command -v lspci &>/dev/null; then
    while IFS= read -r line; do
        if echo "$line" | grep -qiE 'arc|a770|a750|a580|b580'; then
            INTEL_GPUS+=("$line")
        fi
    done < <(lspci 2>/dev/null | grep -iE 'VGA.*intel|Display.*intel')
fi

TOTAL_GPUS=$(( ${#NVIDIA_GPUS[@]} + ${#AMD_GPUS[@]} + ${#INTEL_GPUS[@]} ))

if [ "$TOTAL_GPUS" -eq 0 ]; then
    warn "No discrete GPU detected — CPU-only mode"
    GPU_MODE="cpu"
elif [ "$TOTAL_GPUS" -eq 1 ]; then
    ok "Single GPU detected"
    GPU_MODE="single"
else
    ok "Multi-GPU detected ($TOTAL_GPUS GPUs)"
    GPU_MODE="multi"
fi

# Show detailed GPU info
for gpu in "${NVIDIA_GPUS[@]}"; do
    ok "  NVIDIA: $gpu"
done
for gpu in "${AMD_GPUS[@]}"; do
    ok "  AMD: $gpu"
done
for gpu in "${INTEL_GPUS[@]}"; do
    ok "  Intel: $gpu"
done

# Recommend Docker Compose variant
if [ ${#NVIDIA_GPUS[@]} -gt 0 ] && [ ${#AMD_GPUS[@]} -gt 0 ]; then
    COMPOSE_VARIANT="gpu-both"
    info "Mixed GPU: NVIDIA + AMD — will configure both runtimes"
elif [ ${#NVIDIA_GPUS[@]} -gt 0 ]; then
    COMPOSE_VARIANT="gpu-nvidia"
elif [ ${#AMD_GPUS[@]} -gt 0 ]; then
    COMPOSE_VARIANT="gpu-amd"
elif [ ${#INTEL_GPUS[@]} -gt 0 ]; then
    COMPOSE_VARIANT="gpu-intel"
else
    COMPOSE_VARIANT="cpu"
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

# Detect Ollama (for summary — full setup in step 8b)
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
if [ "$GPU_MODE" != "cpu" ]; then
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

# ── 8b. Model setup ──────────────────────────────────────────────
step "Model Setup"

if command -v ollama &>/dev/null || curl -sf http://localhost:11434/api/version &>/dev/null; then
    ok "Ollama detected"
    OLLAMA_FOUND="yes"

    # Check if Atlas models are available
    ATLAS_ULTRA_OK="no"
    ATLAS_CORE_OK="no"
    if ollama show atlas-ultra:9b &>/dev/null 2>&1; then
        ATLAS_ULTRA_OK="yes"
        ok "atlas-ultra:9b available"
    fi
    if ollama show atlas-core:2b &>/dev/null 2>&1; then
        ATLAS_CORE_OK="yes"
        ok "atlas-core:2b available"
    fi

    # Determine recommended model based on GPU + Atlas availability
    case $GPU_MODE in
        multi)
            if [ "$ATLAS_ULTRA_OK" = "yes" ]; then
                RECOMMENDED_MODEL="atlas-ultra:9b"
            else
                RECOMMENDED_MODEL="atlas-ultra:9b"
                FALLBACK_MODEL="qwen2.5:14b"
            fi
            info "Multi-GPU: Recommending $RECOMMENDED_MODEL for inference"
            ;;
        single)
            FALLBACK_MODEL="qwen2.5:7b"
            if [ ${#NVIDIA_GPUS[@]} -gt 0 ]; then
                VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
                if [ -n "$VRAM" ] && [ "$VRAM" -ge 8000 ] 2>/dev/null; then
                    RECOMMENDED_MODEL="atlas-ultra:9b"
                    FALLBACK_MODEL="qwen2.5:7b"
                    [ -n "$VRAM" ] && [ "$VRAM" -ge 16000 ] 2>/dev/null && FALLBACK_MODEL="qwen2.5:14b"
                else
                    RECOMMENDED_MODEL="atlas-core:2b"
                    FALLBACK_MODEL="qwen2.5:3b"
                fi
            else
                RECOMMENDED_MODEL="atlas-ultra:9b"
            fi
            info "Single GPU: Recommending $RECOMMENDED_MODEL"
            ;;
        cpu)
            RECOMMENDED_MODEL="atlas-core:2b"
            FALLBACK_MODEL="qwen2.5:1.5b"
            info "CPU-only: Recommending $RECOMMENDED_MODEL (lightweight)"
            ;;
    esac

    if [ -t 0 ]; then
        echo ""
        info "Select model to pull:"
        echo "    1. atlas-ultra:9b  (9B params, 8GB+ VRAM)  — best quality"
        echo "    2. atlas-core:2b   (2B params, 4GB+ VRAM)  — faster, lighter"
        if [ -n "${FALLBACK_MODEL:-}" ]; then
            echo "    3. $FALLBACK_MODEL  — generic Qwen (no Atlas training)"
        fi
        echo "    4. Custom (enter model name)"
        echo ""
        PULL_MODEL=$(ask "Pull model?" "$RECOMMENDED_MODEL")

        # Resolve numbered choices
        case "$PULL_MODEL" in
            1) PULL_MODEL="atlas-ultra:9b" ;;
            2) PULL_MODEL="atlas-core:2b" ;;
            3) PULL_MODEL="${FALLBACK_MODEL:-$RECOMMENDED_MODEL}" ;;
            4)
                PULL_MODEL=$(ask "Enter model name" "$RECOMMENDED_MODEL")
                ;;
        esac

        if [ -n "$PULL_MODEL" ]; then
            info "Pulling $PULL_MODEL (this may take a while)..."
            if ollama pull "$PULL_MODEL" 2>/dev/null; then
                ok "Model $PULL_MODEL ready"
            elif [ -n "${FALLBACK_MODEL:-}" ] && [ "$PULL_MODEL" != "$FALLBACK_MODEL" ]; then
                warn "Could not pull $PULL_MODEL — trying fallback $FALLBACK_MODEL"
                if ollama pull "$FALLBACK_MODEL"; then
                    ok "Fallback model $FALLBACK_MODEL ready"
                else
                    warn "Model pull failed — try manually: ollama pull $FALLBACK_MODEL"
                fi
            else
                warn "Model pull failed — try manually: ollama pull $PULL_MODEL"
            fi
        fi

        # Offer LoRA adapters for capable systems
        if [ "$GPU_MODE" != "cpu" ]; then
            echo ""
            if [ -t 0 ]; then
                PULL_LORAS=$(ask "Pull LoRA adapters? (coding, reasoning, math, atlas)" "yes")
                if [ "$PULL_LORAS" = "yes" ]; then
                    for lora in coding.lora reasoning.lora math.lora atlas.lora; do
                        info "Pulling $lora..."
                        ollama pull "$lora" 2>/dev/null && ok "$lora ready" || warn "$lora not available yet"
                    done
                fi
            fi
        fi
    else
        info "Non-interactive: run 'ollama pull $RECOMMENDED_MODEL' to download the model"
    fi
else
    warn "Ollama not found — install it from https://ollama.com"
    info "After installing Ollama, run: ollama pull atlas-ultra:9b"
    OLLAMA_FOUND="no"
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
echo -e "  ${BOLD}GPU:${NC}           ${GPU_MODE} (${COMPOSE_VARIANT})"
for gpu in "${NVIDIA_GPUS[@]}"; do
    echo -e "                   NVIDIA: $gpu"
done
for gpu in "${AMD_GPUS[@]}"; do
    echo -e "                   AMD: $gpu"
done
for gpu in "${INTEL_GPUS[@]}"; do
    echo -e "                   Intel: $gpu"
done

if [ "$OLLAMA_FOUND" = "no" ]; then
    echo ""
    echo -e "  ${YELLOW}Next: Install Ollama for LLM support:${NC}"
    echo -e "    curl -fsSL https://ollama.ai/install.sh | sh"
    echo -e "    ollama pull atlas-ultra:9b"
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
