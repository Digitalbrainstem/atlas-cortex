"""Configuration for Atlas satellite agent."""

from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SatelliteConfig:
    """Satellite agent configuration — loaded from config.json."""

    satellite_id: str = ""
    server_url: str = "ws://localhost:5100/ws/satellite"
    room: str = ""
    mode: str = "dedicated"
    service_port: int = 5110

    # Audio
    wake_word: str = "hey atlas"
    volume: float = 0.7
    mic_gain: float = 0.8
    vad_sensitivity: int = 2  # 0-3, webrtcvad aggressiveness
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 30  # 30ms chunks for webrtcvad

    # Hardware
    audio_device_in: str = "default"
    audio_device_out: str = "default"
    led_type: str = "none"  # none | respeaker | gpio
    led_count: int = 3

    # Wake word
    wake_word_enabled: bool = False  # disabled by default (VAD-only mode)
    wake_word_threshold: float = 0.5
    wake_word_model: str = ""

    # Filler
    filler_enabled: bool = True

    # Silence detection
    silence_threshold_frames: int = 30  # ~900ms at 30ms/frame
    speech_threshold_frames: int = 10  # ~300ms to confirm speech

    # LED patterns: state → {"r": int, "g": int, "b": int, "brightness": float}
    led_patterns: dict = field(default_factory=lambda: {
        "idle": {"r": 0, "g": 0, "b": 0, "brightness": 0.0},
        "listening": {"r": 0, "g": 100, "b": 255, "brightness": 0.4},
        "thinking": {"r": 255, "g": 165, "b": 0, "brightness": 0.3},
        "speaking": {"r": 0, "g": 200, "b": 100, "brightness": 0.4},
        "error": {"r": 255, "g": 0, "b": 0, "brightness": 0.5},
        "muted": {"r": 255, "g": 0, "b": 0, "brightness": 0.1},
        "wakeword": {"r": 0, "g": 200, "b": 255, "brightness": 0.6},
    })

    # Features
    features: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> SatelliteConfig:
        path = Path(path)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            known = {k for k in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        logger.warning("Config not found at %s, using defaults", path)
        return cls()

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for k, v in self.__dict__.items():
            data[k] = v
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @property
    def frame_size(self) -> int:
        """Bytes per audio frame (chunk_ms of 16-bit mono audio)."""
        return int(self.sample_rate * self.chunk_ms / 1000) * 2

    @property
    def frames_per_chunk(self) -> int:
        """Samples per audio frame."""
        return int(self.sample_rate * self.chunk_ms / 1000)

    def generate_id(self) -> str:
        """Generate a satellite ID from hostname."""
        hostname = socket.gethostname()
        return f"sat-{hostname}"
