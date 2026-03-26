#!/bin/bash
# Quick WiFi configuration helper
if [ $# -lt 2 ]; then
    echo "Usage: atlas-satellite.configure-wifi <SSID> <PASSWORD>"
    exit 1
fi
nmcli device wifi connect "$1" password "$2"
