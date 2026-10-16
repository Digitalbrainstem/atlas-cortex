#!/usr/bin/env bash
# Configure ReSpeaker 2-Mic or 4-Mic Raspberry Pi HAT hardware.
set -euo pipefail

PROFILE=""
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICECARD_REPOSITORY="https://github.com/respeaker/seeed-voicecard.git"
VOICECARD_REF="c52606626de050bdad85803d7e427a64cb0cf05c"

while (($#)); do
    case "$1" in
        --profile) PROFILE="${2:?missing profile}"; shift 2 ;;
        --source) SOURCE_DIR="$(realpath "${2:?missing source}")"; shift 2 ;;
        -h|--help)
            echo "Usage: sudo $0 --profile respeaker-2mic|respeaker-4mic [--source DIR]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Run setup-respeaker.sh as root." >&2
    exit 1
fi
case "$PROFILE" in
    respeaker-2mic|respeaker-4mic) ;;
    *) echo "--profile must be respeaker-2mic or respeaker-4mic" >&2; exit 2 ;;
esac

CONFIG_FILE=/boot/firmware/config.txt
OVERLAY_DIR=/boot/firmware/overlays
if [[ ! -f "$CONFIG_FILE" ]]; then
    CONFIG_FILE=/boot/config.txt
    OVERLAY_DIR=/boot/overlays
fi
[[ -f "$CONFIG_FILE" ]] || {
    echo "Raspberry Pi boot config was not found." >&2
    exit 1
}

ensure_line() {
    local line=$1 file=$2
    grep -Fqx "$line" "$file" || printf '%s\n' "$line" >> "$file"
}

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq device-tree-compiler git i2c-tools
ensure_line "dtparam=i2c_arm=on" "$CONFIG_FILE"
ensure_line "dtparam=spi=on" "$CONFIG_FILE"
printf '%s\n' i2c-dev spidev > /etc/modules-load.d/atlas-respeaker.conf

if [[ "$PROFILE" == respeaker-2mic ]]; then
    source_dts="$SOURCE_DIR/overlays/seeed-2mic-voicecard.dts"
    [[ -f "$source_dts" ]] || {
        echo "Missing bundled 2-Mic overlay: $source_dts" >&2
        exit 1
    }
    install -d -m 0755 "$OVERLAY_DIR"
    dtc -@ -I dts -O dtb -o "$OVERLAY_DIR/seeed-2mic-voicecard.dtbo" "$source_dts"
    ensure_line "dtoverlay=seeed-2mic-voicecard" "$CONFIG_FILE"
else
    apt-get install -y -qq dkms
    driver_dir="/usr/src/atlas-seeed-voicecard-$VOICECARD_REF"
    if [[ ! -d "$driver_dir/.git" ]]; then
        rm -rf -- "$driver_dir"
        git clone -q "$VOICECARD_REPOSITORY" "$driver_dir"
    fi
    git -C "$driver_dir" fetch -q --depth 1 origin "$VOICECARD_REF"
    git -C "$driver_dir" checkout -q --detach "$VOICECARD_REF"
    if [[ "$(uname -m)" == aarch64 ]]; then
        "$driver_dir/install_arm64.sh"
    else
        "$driver_dir/install.sh"
    fi
fi

install -d -m 0755 /var/lib/atlas-satellite
cat > /var/lib/atlas-satellite/hardware.json <<EOF
{
  "profile": "$PROFILE",
  "configured_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "boot_config": "$CONFIG_FILE",
  "voicecard_ref": "$([[ "$PROFILE" == respeaker-4mic ]] && echo "$VOICECARD_REF" || echo bundled-overlay)"
}
EOF
touch /run/atlas-satellite-reboot-required
echo "$PROFILE configured. Reboot is required before hardware diagnostics."
