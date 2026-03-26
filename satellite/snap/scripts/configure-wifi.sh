#!/bin/bash
# Configure WiFi on Ubuntu Core via NetworkManager.
# Usage: atlas-satellite.configure-wifi <SSID> [PASSWORD]

set -euo pipefail

SSID="${1:-}"
PASSWORD="${2:-}"

if [ -z "$SSID" ]; then
    echo "Usage: atlas-satellite.configure-wifi <SSID> [PASSWORD]"
    echo ""
    echo "Available networks:"
    nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null | sort -t: -k2 -rn | while IFS=: read -r ssid signal security; do
        [ -z "$ssid" ] && continue
        printf "  %-30s  %3s%%  %s\n" "$ssid" "$signal" "$security"
    done
    exit 1
fi

echo "Connecting to '$SSID'..."
if [ -n "$PASSWORD" ]; then
    nmcli device wifi connect "$SSID" password "$PASSWORD"
else
    nmcli device wifi connect "$SSID"
fi

echo "Connected. IP address:"
nmcli -t -f IP4.ADDRESS device show | grep -v '^$' | head -1
