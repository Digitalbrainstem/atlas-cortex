# Atlas ESP32 Satellite Protocol

WebSocket protocol for communication between ESP32 satellite devices and the
Atlas Cortex server. This is a simplified protocol compared to the full Pi
satellite protocol — the server handles VAD, STT, and TTS rather than the device.

## WebSocket Connection

Connect to: `ws://<atlas-server>:5100/ws/satellite`

All messages are JSON. Binary audio data is base64-encoded within JSON payloads.

## Connection Lifecycle

```
ESP32                          Server
  |                              |
  |--- register --------------->|   (name, device_type, hardware)
  |<-- registered --------------|   (satellite_id)
  |                              |
  |--- audio_start ------------>|   (wake word or button press)
  |--- audio_data ------------->|   (base64 PCM 16kHz 16-bit mono)
  |--- audio_data ------------->|   ...
  |--- audio_end -------------->|   (silence or timeout)
  |                              |
  |<-- led (processing) --------|
  |                              |   [server: STT → pipeline → TTS]
  |<-- speaking_start ----------|
  |<-- audio_chunk -------------|   (base64 PCM 24kHz 16-bit mono)
  |<-- audio_chunk -------------|   ...
  |<-- speaking_end ------------|
  |<-- led (idle) --------------|
  |                              |
  |--- heartbeat --------------->|   (periodic keep-alive)
  |                              |
```

## Client → Server Messages

### `register` — Initial handshake (must be first message)

```json
{
  "type": "register",
  "name": "kitchen-satellite",
  "device_type": "esp32",
  "hardware": "esp32-s3-box-3",
  "firmware_version": "1.0.0"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Human-friendly device name |
| `device_type` | string | yes | Must be `"esp32"` |
| `hardware` | string | no | Hardware variant (e.g. `esp32-s3-box-3`, `satellite1`, `generic`) |
| `firmware_version` | string | no | ESPHome firmware version |

### `audio_start` — Begin audio streaming

```json
{
  "type": "audio_start"
}
```

Sent when the user presses a button or (if supported) a wake word is detected.

### `audio_data` — Raw PCM audio chunk

```json
{
  "type": "audio_data",
  "data": "<base64-encoded PCM>"
}
```

Audio format: **16-bit signed little-endian PCM, 16 kHz, mono**.
Chunks should be ~100–200ms of audio (~3200–6400 bytes before encoding).

### `audio_end` — End audio streaming

```json
{
  "type": "audio_end"
}
```

Sent when the user releases a button, or after a silence timeout on the device.

### `button` — Physical button event

```json
{
  "type": "button",
  "action": "press"
}
```

| Action | Meaning |
|--------|---------|
| `press` | Button pressed (can be used for push-to-talk toggle) |
| `long_press` | Long press (e.g. mute toggle) |
| `double_press` | Double press (e.g. skip/cancel) |

### `heartbeat` — Keep-alive

```json
{
  "type": "heartbeat",
  "uptime": 3600,
  "wifi_rssi": -45,
  "free_heap": 120000
}
```

Sent every 30 seconds. All fields except `type` are optional.

## Server → Client Messages

### `registered` — Registration accepted

```json
{
  "type": "registered",
  "satellite_id": "esp32-kitchen-satellite"
}
```

### `speaking_start` — TTS audio stream beginning

```json
{
  "type": "speaking_start"
}
```

### `audio_chunk` — TTS audio data

```json
{
  "type": "audio_chunk",
  "data": "<base64-encoded PCM>"
}
```

Audio format: **16-bit signed little-endian PCM, 24 kHz, mono**.
The ESP32 should resample if its DAC runs at a different rate.

### `speaking_end` — TTS complete

```json
{
  "type": "speaking_end"
}
```

### `led` — LED state change

```json
{
  "type": "led",
  "pattern": "listening",
  "color": "#00ff00"
}
```

| Pattern | Color | Meaning |
|---------|-------|---------|
| `idle` | `#0000ff` (blue) | Waiting for wake word / button |
| `listening` | `#00ff00` (green) | Recording audio |
| `processing` | `#ffff00` (yellow) | Server processing speech |
| `speaking` | `#00ffff` (cyan) | Playing TTS response |
| `error` | `#ff0000` (red) | Error state |

### `playback_stop` — Cancel current playback

```json
{
  "type": "playback_stop"
}
```

Sent if the server needs to interrupt playback (e.g. new higher-priority response).

## Error Handling

If the server rejects the connection, it sends:

```json
{
  "type": "error",
  "detail": "Human-readable error message"
}
```

The ESP32 should reconnect with exponential backoff (1s, 2s, 4s, … max 60s).

## Differences from Pi Satellite Protocol

| Feature | Pi Protocol | ESP32 Protocol |
|---------|-------------|----------------|
| First message | `ANNOUNCE` | `register` |
| VAD | On-device | Server-side |
| Wake word | On-device | On-device or button |
| Phrase detection | On-device (`AUDIO_PHRASE_END`) | Not supported |
| STT | Server-side | Server-side |
| TTS streaming | `TTS_CHUNK` | `audio_chunk` |
| Filler audio | Cached locally (`SYNC_FILLERS`) | Not supported |
| Barge-in | On-device detection | Not supported |
| LED control | On-device | Server-controlled |
| Config push | `CONFIG` message | Not supported (re-flash) |
