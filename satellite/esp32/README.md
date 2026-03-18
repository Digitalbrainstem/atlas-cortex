# Atlas Satellite — ESP32 Firmware

Flash ESPHome firmware to ESP32-S3 devices for use as Atlas voice satellites.

ESP32 satellites are cheaper and simpler than Raspberry Pi satellites. They run
ESPHome firmware and speak a lightweight WebSocket protocol to the Atlas server,
which handles VAD, STT, pipeline processing, and TTS.

## Supported Hardware

### 1. ESP32-S3-BOX-3 (~$20)
Pre-built dev board with mic, speaker, and touch screen.
→ Use `esp32-s3-box-3.yaml`

### 2. Satellite1 by FutureProofHomes (~$60)
4-mic XMOS array, 25W speaker, sensors.
→ Use `satellite1.yaml`

### 3. Custom Build (~$10-15)
ESP32-S3 + INMP441 mic + MAX98357A amp + speaker
→ Use `generic-esp32-s3.yaml`

## Wiring (Generic Build)

### INMP441 Microphone
| INMP441 | ESP32-S3 |
|---------|----------|
| VDD     | 3.3V     |
| GND     | GND      |
| SD      | GPIO 4   |
| SCK     | GPIO 5   |
| WS      | GPIO 6   |
| L/R     | GND      |

### MAX98357A Amplifier
| MAX98357A | ESP32-S3 |
|-----------|----------|
| VIN       | 5V       |
| GND       | GND      |
| DIN       | GPIO 15  |
| BCLK      | GPIO 16  |
| LRC       | GPIO 17  |

### Status LED (optional)
| LED    | ESP32-S3 |
|--------|----------|
| Signal | GPIO 48  |

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for the full WebSocket protocol specification.
The server-side handler lives in `cortex/satellite/esp32_handler.py`.

## Flashing

1. Install ESPHome: `pip install esphome`
2. Create a `secrets.yaml` next to the config:
   ```yaml
   wifi_ssid: "YourNetwork"
   wifi_password: "YourPassword"
   ```
3. Edit the YAML config and set your Atlas server IP in the `atlas_satellite` section
4. Flash: `esphome run generic-esp32-s3.yaml`

## Custom Component

The `components/atlas_satellite/` directory contains a custom ESPHome component
that implements the Atlas WebSocket protocol. This is a **stub** — the actual C++
implementation requires embedded development tools and will be built separately.

See the component's header file for the protocol state machine and API surface.
