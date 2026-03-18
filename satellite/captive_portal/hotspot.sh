#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# Atlas Satellite — Captive Portal Hotspot Manager
#
# Manages the WiFi hotspot for first-boot WiFi setup.
# Uses NetworkManager (nmcli) — compatible with RPi OS Bookworm.
#
# Usage:
#   hotspot.sh start   — Check WiFi, start hotspot + portal
#   hotspot.sh stop    — Stop everything
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

PORTAL_DIR="$(cd "$(dirname "$0")" && pwd)"
WIFI_INTERFACE="wlan0"
AP_IP="10.42.0.1"
MARKER_FILE="/var/lib/atlas/wifi-configured"
LOG_TAG="atlas-hotspot"

log() { echo "$(date '+%H:%M:%S') [$LOG_TAG] $*"; }

# ── Helpers ───────────────────────────────────────────────────────

get_mac_suffix() {
    cat "/sys/class/net/${WIFI_INTERFACE}/address" 2>/dev/null \
        | tr -d ':' | tail -c 5 | tr '[:lower:]' '[:upper:]' || echo "0000"
}

get_ssid() {
    echo "Atlas-Satellite-$(get_mac_suffix)"
}

is_wifi_connected() {
    nmcli -t -f TYPE,STATE device 2>/dev/null | grep -q "^wifi:connected$"
}

wait_for_wifi_hardware() {
    local timeout=${1:-60}
    local deadline=$((SECONDS + timeout))

    log "Waiting for WiFi hardware..."
    rfkill unblock wifi 2>/dev/null || true

    while [ $SECONDS -lt $deadline ]; do
        if nmcli -t -f DEVICE,TYPE device 2>/dev/null | grep -q ":wifi$"; then
            log "WiFi adapter found"
            return 0
        fi
        sleep 2
    done

    log "ERROR: WiFi adapter not found after ${timeout}s"
    return 1
}

check_preconfigured_wifi() {
    for conf in /boot/firmware/atlas-wifi.txt /boot/atlas-wifi.txt; do
        if [ -f "$conf" ]; then
            local ssid
            ssid=$(grep -E '^WIFI_SSID=' "$conf" 2>/dev/null | cut -d= -f2- || true)
            if [ -n "$ssid" ] && [ "$ssid" != "YOUR_WIFI_NAME" ]; then
                log "WiFi pre-configured in $conf — waiting for connection..."
                local i
                for i in $(seq 1 15); do
                    if is_wifi_connected; then
                        log "WiFi connected!"
                        return 0
                    fi
                    sleep 2
                done
                log "Pre-configured WiFi not connecting after 30s — starting portal"
                return 1
            fi
        fi
    done
    return 1
}

# ── Hotspot Management ────────────────────────────────────────────

start_hotspot() {
    local ssid
    ssid=$(get_ssid)
    log "Starting hotspot: $ssid"

    # Ensure WiFi radio is unblocked
    rfkill unblock wifi 2>/dev/null || true

    # Remove any stale hotspot connection
    nmcli connection delete "$ssid" 2>/dev/null || true

    # Create the hotspot (open network for easy phone connection)
    if ! nmcli device wifi hotspot \
        ifname "$WIFI_INTERFACE" \
        ssid "$ssid" \
        con-name "$ssid" 2>/dev/null; then
        log "ERROR: Failed to create hotspot"
        return 1
    fi

    # Set predictable IP and enable connection sharing (DHCP for clients)
    nmcli connection modify "$ssid" \
        ipv4.addresses "${AP_IP}/24" \
        ipv4.method shared 2>/dev/null || true
    nmcli connection up "$ssid" 2>/dev/null || true

    log "Hotspot active: $ssid at $AP_IP"
}

stop_hotspot() {
    local ssid
    ssid=$(get_ssid)
    log "Stopping hotspot"
    nmcli connection down "$ssid" 2>/dev/null || true
    nmcli connection delete "$ssid" 2>/dev/null || true
}

# ── Portal process ────────────────────────────────────────────────

start_portal() {
    # Find Python — prefer venv if available
    local python="python3"
    if [ -x "${PORTAL_DIR}/.venv/bin/python3" ]; then
        python="${PORTAL_DIR}/.venv/bin/python3"
    fi

    log "Starting captive portal web server"
    exec "$python" "${PORTAL_DIR}/portal.py"
}

# ── Commands ──────────────────────────────────────────────────────

cmd_start() {
    # Already configured?
    if [ -f "$MARKER_FILE" ]; then
        log "WiFi already configured (marker exists) — exiting"
        exit 0
    fi

    # Already connected?
    if is_wifi_connected; then
        log "WiFi already connected — portal not needed"
        exit 0
    fi

    # Pre-configured WiFi credentials?
    if check_preconfigured_wifi; then
        exit 0
    fi

    log "No WiFi connection — starting captive portal"

    # Wait for hardware
    if ! wait_for_wifi_hardware 90; then
        exit 1
    fi

    # Start hotspot with retries
    local attempt
    for attempt in 1 2 3 4 5 6; do
        if start_hotspot; then
            break
        fi
        local delay=$((5 * attempt))
        log "Hotspot attempt $attempt failed, retrying in ${delay}s..."
        sleep "$delay"
        if [ "$attempt" -eq 6 ]; then
            log "ERROR: Failed to start hotspot after all retries"
            exit 1
        fi
    done

    # Clean up hotspot on exit
    trap stop_hotspot EXIT

    # Start the Flask portal (blocks until WiFi is configured)
    start_portal
}

cmd_stop() {
    stop_hotspot
}

case "${1:-start}" in
    start) cmd_start ;;
    stop)  cmd_stop ;;
    *)     echo "Usage: $0 {start|stop}" >&2; exit 1 ;;
esac
