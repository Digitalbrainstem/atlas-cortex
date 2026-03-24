#!/usr/bin/env bash
# Initialize the Atlas data directory structure on the storage pool.
#
# Usage:
#   ./deploy/init-storage.sh                    # Uses /mnt/fastpool/atlas
#   ./deploy/init-storage.sh /path/to/atlas     # Custom path
#
# Creates:
#   <base>/
#   ├── data/              # Cortex DB, config, integrity checksums
#   ├── models/
#   │   ├── ollama/        # Ollama model blobs (Qwen3.5, embeddings)
#   │   └── loras/         # LoRA adapters (coding, reasoning, math, atlas)
#   ├── cache/
#   │   ├── tts/           # Pre-generated TTS audio cache
#   │   └── fillers/       # Filler phrase audio
#   └── backups/           # Automated nightly backups

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
ok() { echo -e "${GREEN}✔${NC}  $*"; }
info() { echo -e "${BLUE}ℹ${NC}  $*"; }

BASE="${1:-/mnt/fastpool/atlas}"

echo -e "${BLUE}Initializing Atlas storage at: ${BASE}${NC}"
echo ""

# Create directory tree
for dir in \
    "$BASE/data" \
    "$BASE/models/ollama" \
    "$BASE/models/loras" \
    "$BASE/cache/tts" \
    "$BASE/cache/fillers" \
    "$BASE/backups"; do
    mkdir -p "$dir"
    ok "Created $dir"
done

# Set permissions (readable by Docker containers)
chmod -R 755 "$BASE"

echo ""
ok "Atlas storage initialized at ${BASE}"
echo ""
info "Directory layout:"
echo "  ${BASE}/"
echo "  ├── data/              # DB, config"
echo "  ├── models/"
echo "  │   ├── ollama/        # LLM models (mount to Ollama container)"
echo "  │   └── loras/         # LoRA adapters"
echo "  ├── cache/"
echo "  │   ├── tts/           # TTS audio cache"
echo "  │   └── fillers/       # Filler phrases"
echo "  └── backups/           # Nightly backups"
echo ""
info "Set ATLAS_DATA_BASE=${BASE} in your .env file"
info "Or: export ATLAS_DATA_BASE=${BASE}"
echo ""

# Check if Ollama models already exist elsewhere
if [ -d "/root/.ollama/models" ] || [ -d "$HOME/.ollama/models" ]; then
    EXISTING="${HOME}/.ollama/models"
    [ -d "/root/.ollama/models" ] && EXISTING="/root/.ollama/models"
    info "Found existing Ollama models at ${EXISTING}"
    info "To migrate: cp -r ${EXISTING}/* ${BASE}/models/ollama/"
fi

# Check available space
AVAIL=$(df -BG "$BASE" 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G')
if [ -n "$AVAIL" ]; then
    info "Available disk space: ${AVAIL}GB"
    if [ "$AVAIL" -lt 30 ]; then
        echo -e "${RED}⚠  Less than 30GB free — may not fit all models${NC}"
    fi
fi
