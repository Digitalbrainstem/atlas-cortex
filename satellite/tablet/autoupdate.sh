#!/usr/bin/env bash
# Atlas Satellite Auto-Update — checks server for new satellite code on boot.
set -euo pipefail

ATLAS_URL=$(avahi-resolve -n atlas-cortex.local 2>/dev/null | awk '{print $2}')
if [ -z "$ATLAS_URL" ]; then exit 0; fi

REMOTE_VER=$(curl -sf "http://${ATLAS_URL}:5100/api/satellite/version" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null)
LOCAL_VER=$(cat /opt/atlas-satellite/.version 2>/dev/null || echo "0")

if [ "$REMOTE_VER" != "$LOCAL_VER" ] && [ -n "$REMOTE_VER" ]; then
    curl -sf "http://${ATLAS_URL}:5100/api/satellite/update" -o /tmp/atlas-update.tar.gz
    tar xzf /tmp/atlas-update.tar.gz -C /opt/atlas-satellite/
    echo "$REMOTE_VER" > /opt/atlas-satellite/.version
    systemctl restart atlas-satellite
fi
