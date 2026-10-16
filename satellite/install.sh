#!/usr/bin/env bash
# Install or update Atlas Satellite on Raspberry Pi OS/Debian.
set -euo pipefail

REPOSITORY="${ATLAS_REPOSITORY:-https://github.com/Digitalbrainstem/atlas-cortex}"
REF="${ATLAS_REF:-main}"
MODE="${ATLAS_MODE:-dedicated}"
SERVER_URL="${ATLAS_SERVER_URL:-ws://atlas-cortex.local:5100/ws/satellite}"
ROOM="${ATLAS_ROOM:-}"
HAT_PROFILE="${ATLAS_HAT:-auto}"
OUTPUT_DEVICE="${ATLAS_OUTPUT_DEVICE:-default}"
WAKEWORD=false
INSTALL_DRIVER=true
START_SERVICE=true
ACTION=install
PURGE=false
SOURCE_DIR="${ATLAS_SOURCE_DIR:-}"

INSTALL_ROOT="/opt/atlas-satellite"
CONFIG_ROOT="/etc/atlas-satellite"
DATA_ROOT="/var/lib/atlas-satellite"
SERVICE_USER="atlas-satellite"
SERVICE_NAME="atlas-satellite.service"

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [OPTIONS]

Install/update:
  --server URL             Cortex WebSocket URL
  --room NAME              Room assigned to this satellite
  --hat PROFILE            auto, respeaker-2mic, respeaker-4mic, or none
  --output-device ALSA     Playback device (important for 4-Mic capture-only HAT)
  --ref GIT_REF            Branch, tag, or commit to install (default: main)
  --source DIR             Local atlas-cortex checkout (no network source download)
  --wakeword               Install optional local wake-word dependencies
  --no-driver              Do not install/configure a ReSpeaker kernel driver
  --no-start               Enable but do not start the service

Lifecycle:
  --rollback               Switch to the previous installed release
  --uninstall              Remove service and application; preserve config/data
  --purge                  With --uninstall, also remove config/data and service user

Examples:
  curl -fsSL https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
    sudo bash -s -- --hat respeaker-2mic --server ws://192.168.1.10:5100/ws/satellite --room Kitchen

  sudo ./satellite/install.sh --source . --hat respeaker-4mic \
    --output-device default --room Living-Room
EOF
}

while (($#)); do
    case "$1" in
        --server) SERVER_URL="${2:?missing server URL}"; shift 2 ;;
        --room) ROOM="${2:?missing room}"; shift 2 ;;
        --hat) HAT_PROFILE="${2:?missing HAT profile}"; shift 2 ;;
        --output-device) OUTPUT_DEVICE="${2:?missing output device}"; shift 2 ;;
        --ref) REF="${2:?missing ref}"; shift 2 ;;
        --source) SOURCE_DIR="${2:?missing source directory}"; shift 2 ;;
        --shared) MODE=shared; shift ;;
        --wakeword) WAKEWORD=true; shift ;;
        --no-driver) INSTALL_DRIVER=false; shift ;;
        --no-start) START_SERVICE=false; shift ;;
        --rollback) ACTION=rollback; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        --purge) PURGE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Run this installer as root (for example: curl ... | sudo bash)." >&2
    exit 1
fi

case "$MODE" in dedicated|shared) ;; *) echo "Invalid mode: $MODE" >&2; exit 2 ;; esac
case "$HAT_PROFILE" in
    auto|none|generic|respeaker-2mic|respeaker-4mic) ;;
    *) echo "Invalid --hat profile: $HAT_PROFILE" >&2; exit 2 ;;
esac
if [[ ! "$SERVER_URL" =~ ^wss?://[^/]+/.+ ]]; then
    echo "--server must be a ws:// or wss:// URL with a path" >&2
    exit 2
fi

cleanup() {
    [[ -z "${WORK_DIR:-}" ]] || rm -rf -- "$WORK_DIR"
    [[ -z "${STAGING_RELEASE:-}" ]] || rm -rf -- "$STAGING_RELEASE"
}
trap cleanup EXIT

remove_installation() {
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE_NAME"
    systemctl daemon-reload
    rm -rf -- "$INSTALL_ROOT"
    if [[ "$PURGE" == true ]]; then
        rm -rf -- "$CONFIG_ROOT" "$DATA_ROOT"
        userdel "$SERVICE_USER" 2>/dev/null || true
    fi
    echo "Atlas Satellite removed."
}

rollback_release() {
    local current previous link
    link="$INSTALL_ROOT/current"
    [[ -L "$link" ]] || { echo "No current release to roll back." >&2; exit 1; }
    current=$(readlink -f "$link")
    previous=$(
        while IFS= read -r candidate; do
            if [[ "$candidate" != "$current" &&
                  -f "$candidate/manifest.json" &&
                  -x "$candidate/venv/bin/atlas-satellite" ]]; then
                printf '%s\n' "$candidate"
                break
            fi
        done < <(
            find "$INSTALL_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -print |
            sort -r
        )
    )
    [[ -n "$previous" ]] || { echo "No previous release is available." >&2; exit 1; }
    ln -s "$previous" "$INSTALL_ROOT/.current.new"
    mv -Tf "$INSTALL_ROOT/.current.new" "$link"
    systemctl restart "$SERVICE_NAME"
    echo "Rolled back to $(basename "$previous")."
}

case "$ACTION" in
    uninstall) remove_installation; exit 0 ;;
    rollback) rollback_release; exit 0 ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    alsa-utils avahi-daemon build-essential ca-certificates curl device-tree-compiler git \
    i2c-tools libasound2-dev libgpiod-dev python3 python3-dev python3-venv

if [[ -z "$SOURCE_DIR" ]]; then
    WORK_DIR=$(mktemp -d)
    archive="$WORK_DIR/source.tar.gz"
    echo "Downloading Atlas Cortex source ref $REF..."
    curl --fail --location --silent --show-error \
        "$REPOSITORY/archive/$REF.tar.gz" -o "$archive"
    tar -xzf "$archive" -C "$WORK_DIR"
    SOURCE_DIR=$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)
else
    SOURCE_DIR=$(realpath "$SOURCE_DIR")
fi
SATELLITE_SOURCE="$SOURCE_DIR/satellite"
[[ -f "$SATELLITE_SOURCE/setup.py" ]] || {
    echo "Satellite package not found below $SOURCE_DIR" >&2
    exit 1
}

if [[ "$HAT_PROFILE" == auto ]]; then
    cards=$(arecord -l 2>/dev/null || true)
    if grep -Eqi 'seeed4micvoicec|seeed-4mic|ac108' <<<"$cards"; then
        HAT_PROFILE=respeaker-4mic
    elif grep -Eqi 'seeed2micvoicec|seeed-2mic|wm8960' <<<"$cards"; then
        HAT_PROFILE=respeaker-2mic
    else
        echo "Automatic HAT detection found no configured ReSpeaker ALSA card." >&2
        echo "For a fresh install, pass --hat respeaker-2mic or --hat respeaker-4mic." >&2
        echo "For a non-HAT device, pass --hat none." >&2
        exit 2
    fi
fi
[[ "$HAT_PROFILE" != none ]] || HAT_PROFILE=generic
case "$HAT_PROFILE" in
    respeaker-2mic) CONFIG_PROFILE=respeaker_2mic ;;
    respeaker-4mic) CONFIG_PROFILE=respeaker_4mic ;;
    *) CONFIG_PROFILE=generic ;;
esac

if [[ "$INSTALL_DRIVER" == true && "$HAT_PROFILE" == respeaker-* ]]; then
    "$SATELLITE_SOURCE/setup-respeaker.sh" \
        --profile "$HAT_PROFILE" \
        --source "$SATELLITE_SOURCE"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_ROOT" --create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
for group in audio gpio i2c spi; do
    getent group "$group" >/dev/null && usermod -a -G "$group" "$SERVICE_USER"
done

install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$INSTALL_ROOT/releases"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_ROOT"
install -d -o root -g "$SERVICE_USER" -m 0770 "$CONFIG_ROOT"

release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(tr -cs 'A-Za-z0-9._-' _ <<<"$REF" | sed 's/_$//')"
release_dir="$INSTALL_ROOT/releases/$release_id"
STAGING_RELEASE="$release_dir"
rm -rf -- "$STAGING_RELEASE"
install -d -o root -g root -m 0755 "$STAGING_RELEASE"
cp -a "$SATELLITE_SOURCE" "$STAGING_RELEASE/source"
chown -R root:root "$STAGING_RELEASE"
python3 -m venv --system-site-packages "$STAGING_RELEASE/venv"
"$STAGING_RELEASE/venv/bin/pip" install --disable-pip-version-check -q --upgrade pip wheel
extras=hat
[[ "$WAKEWORD" == true ]] && extras=hat,wakeword
"$STAGING_RELEASE/venv/bin/pip" install --disable-pip-version-check -q \
    "$STAGING_RELEASE/source[$extras]"

cat > "$STAGING_RELEASE/manifest.json" <<EOF
{
  "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "repository": "$REPOSITORY",
  "ref": "$REF",
  "hardware_profile": "$HAT_PROFILE"
}
EOF
STAGING_RELEASE=""

case "$HAT_PROFILE" in
    respeaker-2mic)
        CAPTURE_DEVICE="plughw:CARD=seeed2micvoicec,DEV=0"
        CAPTURE_FORMAT=S16_LE
        CAPTURE_CHANNELS=2
        LED_TYPE=respeaker_2mic
        LED_COUNT=3
        BUTTON_ENABLED=true
        [[ "$OUTPUT_DEVICE" != default ]] || OUTPUT_DEVICE="$CAPTURE_DEVICE"
        ;;
    respeaker-4mic)
        CAPTURE_DEVICE="plughw:CARD=seeed4micvoicec,DEV=0"
        CAPTURE_FORMAT=S32_LE
        CAPTURE_CHANNELS=4
        LED_TYPE=respeaker_4mic
        LED_COUNT=12
        BUTTON_ENABLED=false
        ;;
    *)
        CAPTURE_DEVICE=default
        CAPTURE_FORMAT=S16_LE
        CAPTURE_CHANNELS=1
        LED_TYPE=none
        LED_COUNT=0
        BUTTON_ENABLED=false
        ;;
esac

config_file="$CONFIG_ROOT/config.json"
if [[ ! -f "$config_file" ]]; then
    SATELLITE_ID="sat-$(hostname)"
    export SATELLITE_ID SERVER_URL ROOM MODE DATA_ROOT CONFIG_PROFILE CAPTURE_DEVICE
    export CAPTURE_FORMAT CAPTURE_CHANNELS OUTPUT_DEVICE LED_TYPE LED_COUNT BUTTON_ENABLED
    python3 - "$config_file" <<'PY'
import json
import os
import sys
from pathlib import Path

config = {
    "satellite_id": os.environ["SATELLITE_ID"],
    "server_url": os.environ["SERVER_URL"],
    "room": os.environ["ROOM"],
    "mode": os.environ["MODE"],
    "data_dir": os.environ["DATA_ROOT"],
    "hardware_profile": os.environ["CONFIG_PROFILE"],
    "sample_rate": 16000,
    "channels": 1,
    "capture_format": os.environ["CAPTURE_FORMAT"],
    "capture_channels": int(os.environ["CAPTURE_CHANNELS"]),
    "audio_device_in": os.environ["CAPTURE_DEVICE"],
    "audio_device_out": os.environ["OUTPUT_DEVICE"],
    "led_type": os.environ["LED_TYPE"],
    "led_count": int(os.environ["LED_COUNT"]),
    "button_enabled": os.environ["BUTTON_ENABLED"] == "true",
    "button_mode": "press",
    "wake_word": "atlas",
    "wake_word_enabled": False,
    "vad_enabled": True,
    "volume": 0.7,
    "mic_gain": 0.8,
    "vad_sensitivity": 2,
    "filler_enabled": True,
    "features": {"acoustic_echo_cancellation": False},
}
path = Path(sys.argv[1])
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(config, indent=2) + "\n")
temporary.replace(path)
PY
else
    echo "Preserving existing configuration at $config_file"
fi
chown root:"$SERVICE_USER" "$config_file"
chmod 0660 "$config_file"

install -o root -g root -m 0644 \
    "$SATELLITE_SOURCE/systemd/atlas-satellite.service" \
    "/etc/systemd/system/$SERVICE_NAME"
ln -s "$release_dir" "$INSTALL_ROOT/.current.new"
mv -Tf "$INSTALL_ROOT/.current.new" "$INSTALL_ROOT/current"
mapfile -t old_releases < <(
    find "$INSTALL_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -print |
    sort -r | tail -n +4
)
for old_release in "${old_releases[@]}"; do
    rm -rf -- "$old_release"
done
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

if [[ "$START_SERVICE" == true && ! -e /run/atlas-satellite-reboot-required ]]; then
    systemctl restart "$SERVICE_NAME"
fi

echo
echo "Atlas Satellite $release_id installed."
echo "  Profile: $HAT_PROFILE"
echo "  Config:  $config_file"
echo "  Logs:    journalctl -u $SERVICE_NAME -f"
echo "  Check:   $INSTALL_ROOT/current/venv/bin/atlas-satellite-diagnose --config $config_file"
if [[ -e /run/atlas-satellite-reboot-required ]]; then
    echo "  REBOOT REQUIRED: the service is enabled and will start after reboot."
elif [[ "$START_SERVICE" != true ]]; then
    echo "  Service enabled but not started (--no-start)."
fi
