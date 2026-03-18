// Atlas Satellite — ESPHome Custom Component (STUB)
//
// This header defines the protocol state machine and API surface for the
// Atlas ESP32 satellite component. It is a STUB — the actual implementation
// requires the ESP-IDF / ESPHome build toolchain and will be developed
// separately.
//
// Protocol: see ../../PROTOCOL.md
// Server handler: cortex/satellite/esp32_handler.py
//
// Build: This file is compiled by ESPHome's build system, not standalone.
//        esphome run generic-esp32-s3.yaml

#pragma once

#include "esphome/core/component.h"
// #include "esphome/components/microphone/microphone.h"
// #include "esphome/components/speaker/speaker.h"
// #include "esphome/components/light/light_state.h"

namespace esphome {
namespace atlas_satellite {

/// Connection states for the WebSocket protocol.
enum class ConnectionState {
  DISCONNECTED,   // Not connected to server
  CONNECTING,     // WebSocket handshake in progress
  REGISTERING,    // Connected, sending register message
  IDLE,           // Registered, waiting for wake/button
  LISTENING,      // Streaming mic audio to server
  PROCESSING,     // Waiting for server response
  SPEAKING,       // Playing TTS audio from server
  ERROR,          // Error state, will retry
};

/// LED patterns corresponding to protocol states.
struct LedPattern {
  uint8_t r, g, b;
  static LedPattern idle()       { return {0x00, 0x00, 0xff}; }  // Blue
  static LedPattern listening()  { return {0x00, 0xff, 0x00}; }  // Green
  static LedPattern processing() { return {0xff, 0xff, 0x00}; }  // Yellow
  static LedPattern speaking()   { return {0x00, 0xff, 0xff}; }  // Cyan
  static LedPattern error()      { return {0xff, 0x00, 0x00}; }  // Red
};

/// Main Atlas Satellite component.
///
/// Lifecycle:
///   setup()  — Connect WiFi, init audio, connect WebSocket
///   loop()   — Poll WebSocket, stream audio, update LEDs
///
/// TODO: Implement all methods below. Each has a docstring describing
///       the expected behavior and protocol messages involved.
class AtlasSatelliteComponent : public Component {
 public:
  // ── Configuration setters (called by ESPHome codegen) ──────────

  void set_server_url(const std::string &url) { server_url_ = url; }
  void set_device_name(const std::string &name) { device_name_ = name; }
  void set_hardware(const std::string &hw) { hardware_ = hw; }
  // void set_microphone(microphone::Microphone *mic) { mic_ = mic; }
  // void set_speaker(speaker::Speaker *spk) { spk_ = spk; }
  // void set_status_led(light::LightState *led) { led_ = led; }

  // ── Component lifecycle ────────────────────────────────────────

  /// Initialize audio peripherals and start WebSocket connection.
  /// Called once after ESPHome boots.
  void setup() override {
    // TODO:
    // 1. Validate configuration (server_url, mic, speaker)
    // 2. Initialize I2S microphone and speaker
    // 3. Start WebSocket connection task
    // 4. Set LED to "connecting" pattern
  }

  /// Main loop — called ~60 times/second by ESPHome.
  /// Drives the protocol state machine.
  void loop() override {
    // TODO:
    // switch (state_) {
    //   case ConnectionState::DISCONNECTED:
    //     attempt_reconnect();  // exponential backoff
    //     break;
    //   case ConnectionState::IDLE:
    //     check_wake_word_or_button();
    //     process_incoming_messages();
    //     break;
    //   case ConnectionState::LISTENING:
    //     read_mic_and_send_audio();
    //     check_silence_timeout();
    //     process_incoming_messages();
    //     break;
    //   case ConnectionState::SPEAKING:
    //     play_audio_chunks();
    //     process_incoming_messages();
    //     break;
    //   ...
    // }
  }

  float get_setup_priority() const override {
    return setup_priority::AFTER_WIFI;
  }

 protected:
  // ── WebSocket protocol ─────────────────────────────────────────

  /// Connect to Atlas server and send register message.
  /// Message: {"type": "register", "name": "...", "device_type": "esp32", "hardware": "..."}
  /// Expected response: {"type": "registered", "satellite_id": "..."}
  void connect_and_register_() {
    // TODO: Use esp_websocket_client to connect to server_url_
    // On connect: send register JSON
    // On "registered" response: transition to IDLE, set satellite_id_
    // On error: transition to ERROR, schedule reconnect
  }

  /// Read microphone data and send as base64-encoded audio_data messages.
  /// Message: {"type": "audio_data", "data": "<base64 PCM>"}
  /// Called in LISTENING state, ~10 times/second (100ms chunks).
  void read_mic_and_send_audio_() {
    // TODO:
    // 1. Read ~1600 samples (100ms at 16kHz) from I2S mic
    // 2. Base64-encode the PCM buffer
    // 3. Send {"type": "audio_data", "data": "..."} over WebSocket
    // Note: ESP32-S3 has hardware base64 but software is fine for 3.2KB chunks
  }

  /// Start audio capture: send audio_start and begin mic streaming.
  /// Message: {"type": "audio_start"}
  void start_listening_() {
    // TODO:
    // 1. Send {"type": "audio_start"}
    // 2. Start mic capture
    // 3. Set state to LISTENING
    // 4. Update LED to listening pattern (green)
  }

  /// Stop audio capture: send audio_end.
  /// Message: {"type": "audio_end"}
  void stop_listening_() {
    // TODO:
    // 1. Stop mic capture
    // 2. Send {"type": "audio_end"}
    // 3. Set state to PROCESSING
    // 4. Update LED to processing pattern (yellow)
  }

  /// Process incoming WebSocket message (JSON).
  /// Routes to appropriate handler based on "type" field.
  void handle_server_message_(const std::string &json) {
    // TODO: Parse JSON and dispatch:
    // "registered"     → store satellite_id_, set state IDLE
    // "speaking_start" → set state SPEAKING, prepare audio output
    // "audio_chunk"    → decode base64, queue for speaker playback
    // "speaking_end"   → set state IDLE, update LED
    // "led"            → update LED color/pattern
    // "playback_stop"  → stop speaker, set state IDLE
    // "error"          → log error, optionally disconnect
  }

  /// Play received audio chunk on speaker.
  /// Decodes base64 PCM data and writes to I2S speaker.
  void play_audio_chunk_(const std::string &base64_data) {
    // TODO:
    // 1. Base64-decode the audio data
    // 2. Resample from 24kHz to speaker's native rate if needed
    // 3. Write to I2S speaker buffer
    // 4. If buffer full, block briefly (speaker will drain it)
  }

  /// Update status LED color based on pattern name.
  void update_led_(const std::string &pattern) {
    // TODO:
    // Map pattern string to LedPattern color
    // Set LED via ESPHome light component
    // For multi-LED strips (e.g. Satellite1 12-LED ring), animate
  }

  /// Send heartbeat message every 30 seconds.
  /// Message: {"type": "heartbeat", "uptime": N, "wifi_rssi": N, "free_heap": N}
  void send_heartbeat_() {
    // TODO:
    // 1. Check if 30s elapsed since last heartbeat
    // 2. Gather system stats (uptime, WiFi RSSI, free heap)
    // 3. Send heartbeat JSON
  }

  /// Attempt reconnection with exponential backoff.
  /// Backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (max)
  void attempt_reconnect_() {
    // TODO:
    // 1. Check if backoff period elapsed
    // 2. If yes: connect_and_register_()
    // 3. If no: wait
    // 4. On failure: double backoff (max 60s)
    // 5. On success: reset backoff to 1s
  }

  // ── State ──────────────────────────────────────────────────────

  std::string server_url_;
  std::string device_name_;
  std::string hardware_;
  std::string satellite_id_;

  ConnectionState state_{ConnectionState::DISCONNECTED};

  // Reconnection backoff
  uint32_t reconnect_interval_ms_{1000};
  uint32_t last_reconnect_attempt_{0};

  // Heartbeat timing
  uint32_t last_heartbeat_ms_{0};
  static constexpr uint32_t HEARTBEAT_INTERVAL_MS = 30000;

  // Audio silence detection (simple energy-based)
  uint32_t silence_start_ms_{0};
  static constexpr uint32_t SILENCE_TIMEOUT_MS = 2000;

  // Component references (set by codegen)
  // microphone::Microphone *mic_{nullptr};
  // speaker::Speaker *spk_{nullptr};
  // light::LightState *led_{nullptr};
};

}  // namespace atlas_satellite
}  // namespace esphome
