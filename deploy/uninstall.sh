#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${BLUE}ℹ${NC}  $*"; }
ok()    { echo -e "${GREEN}✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
err()   { echo -e "${RED}✖${NC}  $*" >&2; }

echo -e "${RED}╔══════════════════════════════════════════╗${NC}"
echo -e "${RED}║     Atlas Cortex — Uninstaller           ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════╝${NC}"
echo ""

INSTALL_DIR="${1:-/opt/atlas-cortex}"
REMOVE_DATA="no"
REMOVE_USER="no"

echo -e "${YELLOW}This will remove Atlas Cortex from: $INSTALL_DIR${NC}"
echo ""

# Confirm
echo -en "${RED}?${NC}  Are you sure? (yes/no) [no]: "
read -r CONFIRM
if [ "${CONFIRM}" != "yes" ]; then
    info "Aborted"
    exit 0
fi

echo -en "${RED}?${NC}  Also remove data (databases, config)? (yes/no) [no]: "
read -r REMOVE_DATA

# ── Stop and disable service ─────────────────────────────────────
if systemctl is-active --quiet atlas-cortex 2>/dev/null; then
    info "Stopping service..."
    sudo systemctl stop atlas-cortex
    ok "Service stopped"
fi

if systemctl is-enabled --quiet atlas-cortex 2>/dev/null; then
    info "Disabling service..."
    sudo systemctl disable atlas-cortex
    ok "Service disabled"
fi

if [ -f /etc/systemd/system/atlas-cortex.service ]; then
    sudo rm /etc/systemd/system/atlas-cortex.service
    sudo systemctl daemon-reload
    ok "Service file removed"
fi

# ── Remove installation ──────────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    if [ "$REMOVE_DATA" = "yes" ]; then
        info "Removing entire installation (including data)..."
        sudo rm -rf "$INSTALL_DIR"
        ok "Removed $INSTALL_DIR"
    else
        info "Removing installation (preserving data)..."
        # Keep data directory, remove everything else
        if [ -d "$INSTALL_DIR/data" ]; then
            DATA_BAK=$(mktemp -d "/tmp/atlas-cortex-data.XXXXXX")
            cp -a "$INSTALL_DIR/data" "$DATA_BAK/"
            sudo rm -rf "$INSTALL_DIR"
            mkdir -p "$INSTALL_DIR"
            mv "$DATA_BAK/data" "$INSTALL_DIR/"
            rmdir "$DATA_BAK"
            ok "Removed installation, data preserved at $INSTALL_DIR/data"
        else
            sudo rm -rf "$INSTALL_DIR"
            ok "Removed $INSTALL_DIR"
        fi
    fi
else
    warn "Install directory not found: $INSTALL_DIR"
fi

# ── Remove user ──────────────────────────────────────────────────
if id atlas &>/dev/null; then
    echo -en "${YELLOW}?${NC}  Remove 'atlas' system user? (yes/no) [no]: "
    read -r REMOVE_USER
    if [ "$REMOVE_USER" = "yes" ]; then
        sudo userdel atlas 2>/dev/null || true
        ok "Removed user 'atlas'"
    fi
fi

echo ""
echo -e "${GREEN}Uninstall complete.${NC}"
if [ "$REMOVE_DATA" != "yes" ] && [ -d "$INSTALL_DIR/data" ]; then
    info "Data preserved at: $INSTALL_DIR/data"
fi
