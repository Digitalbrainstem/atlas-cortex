"""Atlas Cortex orchestrator — coordinates STT → pipeline → TTS flow.

OWNERSHIP: This module owns the end-to-end voice interaction flow.
The satellite/websocket module handles only WS connection lifecycle
and delegates voice processing here.

Sub-modules:
  voice — Full voice pipeline: STT → pipeline → TTS → stream
  text  — Text processing helpers (sentence splitting, help-offer detection)
"""
from __future__ import annotations

from cortex.orchestrator.voice import process_voice_pipeline

__all__ = ["process_voice_pipeline"]
