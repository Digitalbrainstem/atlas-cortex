"""Atlas Cortex — Satellite System (Part 2.5).

Server-side components for managing distributed satellite devices:
  - Discovery: passive mDNS listener + on-demand scan
  - Hardware detection: SSH-based platform/audio/sensor probing
  - Provisioning: agent install, SSH key deploy, hostname config
  - WebSocket: real-time audio streaming and command channel
  - ESP32 handler: lightweight protocol for ESP32-S3 voice satellites
  - Manager: orchestrates lifecycle for all satellites
"""

# Module ownership: Satellite device protocol and management
