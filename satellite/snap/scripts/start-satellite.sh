#!/bin/bash
# Atlas Satellite — zero-touch setup daemon
# Takes over console, handles WiFi, installs kiosk, connects to Atlas.

LOG=/tmp/atlas-satellite.log
echo "$(date) Atlas satellite starting..." > $LOG

# Wait for snapd
for i in $(seq 1 60); do snap version &>/dev/null && break; sleep 2; done

# ── WiFi Setup (runs on console if no network) ──────────────
setup_wifi() {
    # Take over tty1 for WiFi setup
    exec < /dev/tty1 > /dev/tty1 2>&1
    
    clear
    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║   Atlas Tablet — WiFi Setup                  ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""
    echo "  Scanning for WiFi networks..."
    echo ""
    
    # Wait for NetworkManager
    for i in $(seq 1 30); do
        nmcli general status &>/dev/null && break
        sleep 1
    done
    
    # Scan and list networks
    nmcli device wifi rescan 2>/dev/null
    sleep 3
    
    NETWORKS=$(nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null | grep -v '^$' | sort -t: -k2 -rn | head -10)
    
    i=0
    declare -a SSIDS
    while IFS=: read -r ssid signal security; do
        [ -z "$ssid" ] && continue
        i=$((i+1))
        SSIDS[$i]="$ssid"
        # Signal bars
        bars="    "
        [ "$signal" -gt 20 ] && bars="█   "
        [ "$signal" -gt 40 ] && bars="██  "
        [ "$signal" -gt 60 ] && bars="███ "
        [ "$signal" -gt 80 ] && bars="████"
        printf "    %2d. %-30s %s %s\n" "$i" "$ssid" "$bars" "$security"
    done <<< "$NETWORKS"
    
    echo ""
    echo "    $((i+1)). Enter SSID manually (hidden network)"
    echo "     0. Skip (no WiFi)"
    echo ""
    read -p "  Select network: " SEL
    
    [ "$SEL" = "0" ] && return 1
    
    if [ "$SEL" = "$((i+1))" ]; then
        read -p "  SSID: " SSID
    else
        SSID="${SSIDS[$SEL]}"
    fi
    
    [ -z "$SSID" ] && return 1
    
    read -s -p "  Password: " PASS
    echo ""
    echo "  Connecting to $SSID..."
    
    nmcli device wifi connect "$SSID" password "$PASS" 2>&1
    sleep 3
    
    if ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        echo "  ✔ Connected!"
        return 0
    else
        echo "  ✖ Connection failed. Try again."
        setup_wifi
    fi
}

# Check if network is up
if ! ping -c 1 -W 3 8.8.8.8 &>/dev/null 2>&1; then
    echo "$(date) No network, starting WiFi setup..." >> $LOG
    setup_wifi
fi

# ── Install kiosk snaps if needed ────────────────────────────
if ! snap list ubuntu-frame &>/dev/null; then
    echo "$(date) Installing kiosk snaps..." >> $LOG
    # Show progress on console
    exec < /dev/tty1 > /dev/tty1 2>&1
    echo ""
    echo "  Installing display system..."
    snap install ubuntu-frame --channel=24/stable 2>&1 | tail -1
    echo "  Installing browser..."  
    snap install wpe-webkit-mir-kiosk 2>&1 | tail -1
    echo "  Installing GPU drivers..."
    snap install mesa-2404 2>&1 | tail -1
    snap install mesa-core20 2>&1 | tail -1
    snap install core20 2>&1 | tail -1
    
    # Set kiosk URL
    snap set wpe-webkit-mir-kiosk url="http://localhost:8080/setup" 2>/dev/null
    
    echo ""
    echo "  ✔ Kiosk installed! Rebooting..."
    sleep 3
    reboot
fi

# ── Start satellite agent ────────────────────────────────────
echo "$(date) Starting satellite agent..." >> $LOG
cd $SNAP/lib
exec python3 -m atlas_satellite 2>&1 >> $LOG
