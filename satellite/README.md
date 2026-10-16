# Atlas Raspberry Pi Satellite

This package turns a Raspberry Pi into an Atlas Cortex voice satellite. The
supported HAT profiles are:

| Profile | Capture | Playback | LEDs | Button |
|---|---|---|---|---|
| `respeaker-2mic` | WM8960, stereo S16_LE downmixed to mono | HAT jack/JST | 3 APA102 | GPIO 17 |
| `respeaker-4mic` | AC108, four-channel S32_LE downmixed to mono | Separate Pi/USB output | 12 APA102 | None |

The Atlas transport always sends mono PCM16 at 16 kHz. Hardware-specific capture
formats are converted locally before transmission.

## Prepare the Pi

Use a current 64-bit Raspberry Pi OS Lite image with SSH and Wi-Fi configured in
Raspberry Pi Imager. Attach the HAT only while the Pi is powered off. The
ReSpeaker 4-Mic board is capture-only, so connect a USB, HDMI, or Pi-supported
playback device.

The installer supports Python 3.11 or newer. Run it as root because it installs a
kernel overlay/driver, a dedicated service account, and a systemd unit.

## Install

Replace the server address and room. An IP address is the most deterministic
choice for first installation; mDNS (`atlas-cortex.local`) also works when the
network supports it.

### ReSpeaker 2-Mic Pi HAT

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
sudo bash -s -- \
  --hat respeaker-2mic \
  --server ws://192.168.1.10:5100/ws/satellite \
  --room Kitchen
```

### ReSpeaker 4-Mic Array

The 4-Mic board has no playback interface. Set `--output-device` to the ALSA
device used by the connected speaker. `default` is suitable when Raspberry Pi OS
already routes playback correctly.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
sudo bash -s -- \
  --hat respeaker-4mic \
  --output-device default \
  --server ws://192.168.1.10:5100/ws/satellite \
  --room "Living Room"
```

The driver setup marks the device for reboot. The service is enabled immediately
and starts automatically after reboot.

```bash
sudo reboot
```

## Verify

After reboot:

```bash
sudo systemctl status atlas-satellite --no-pager
sudo journalctl -u atlas-satellite -n 100 --no-pager
sudo -u atlas-satellite \
  /opt/atlas-satellite/current/venv/bin/atlas-satellite-diagnose \
  --config /etc/atlas-satellite/config.json
```

Diagnostics check Python dependencies, configuration permissions, ALSA capture
and playback, SPI/GPIO/I2C devices, writable state, and TCP access to Cortex.
They do not qualify wake-word accuracy, acoustic quality, false activations, or
audible interruption latency.

Useful hardware checks:

```bash
arecord -l
aplay -l

# 2-Mic: HAT capture and playback
arecord -D plughw:CARD=seeed2micvoicec,DEV=0 -f S16_LE -r 16000 -c 2 -d 5 /tmp/test.wav
aplay -D plughw:CARD=seeed2micvoicec,DEV=0 /tmp/test.wav

# 4-Mic: four-channel capture; playback uses a separate device
arecord -D plughw:CARD=seeed4micvoicec,DEV=0 -f S32_LE -r 16000 -c 4 -d 5 /tmp/test.wav
aplay -D default /tmp/test.wav
```

## Configuration

The installer creates `/etc/atlas-satellite/config.json`. The service writes
runtime state and filler cache data under `/var/lib/atlas-satellite`. Application
releases are root-owned under `/opt/atlas-satellite/releases`, and
`/opt/atlas-satellite/current` selects the active release.

Local wake-word dependencies are optional because they consume substantial RAM.
Install them with `--wakeword` only on a 64-bit Pi after selecting and installing
an actual Atlas wake model. Until then the generated configuration deliberately
uses VAD/button activation and does not claim wake-word readiness.

The setup web server is disabled in the system service. Wi-Fi should be configured
through Raspberry Pi Imager or NetworkManager rather than exposing an unauthenticated
configuration endpoint on the household LAN.

## Update and rollback

Rerun the installer to stage a new release. Existing configuration and state are
preserved, and the `current` symlink changes atomically after dependencies install.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
sudo bash -s -- --hat auto
```

Roll back to the previous installed release:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
sudo bash -s -- --rollback
```

Uninstall while preserving configuration and state:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/Digitalbrainstem/atlas-cortex/main/satellite/install.sh |
sudo bash -s -- --uninstall
```

Add `--purge` to remove configuration, state, and the service account as well.

## Hardware qualification

Installation success is not household acceptance. For each physical satellite,
retain the Pi model, OS/kernel, HAT, driver revision, configuration, room/noise
conditions, and recordings. Complete the wake, false-activation, simultaneous
room, and audible interruption measurements in
[`docs/satellite-system.md`](../docs/satellite-system.md) before declaring the
hardware qualified.
