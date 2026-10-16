"""Configuration for Atlas satellite agent."""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
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
    data_dir: str = ""

    # Audio
    wake_word: str = "hey atlas"
    volume: float = 0.7
    mic_gain: float = 0.8
    vad_sensitivity: int = 1  # 0-3, webrtcvad aggressiveness (1=best for noisy mics)
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 30  # 30ms chunks for webrtcvad

    # Hardware
    audio_device_in: str = "default"
    audio_device_out: str = "default"
    capture_format: str = "S16_LE"
    capture_channels: int = 1
    hardware_profile: str = "generic"
    led_type: str = "none"  # none | respeaker | gpio
    led_count: int = 3

    # Wake word
    wake_word_enabled: bool = True  # enabled by default — uses openwakeword when available
    wake_word_threshold: float = 0.5
    wake_word_model: str = ""

    # VAD control
    vad_enabled: bool = True  # when False, only wake word triggers listening

    # Button (ReSpeaker 2-mic HAT GPIO 17)
    button_enabled: bool = True
    button_mode: str = "toggle"  # toggle | press | hold

    # Filler
    filler_enabled: bool = True

    # Silence detection
    silence_threshold_frames: int = 15  # ~450ms at 30ms/frame
    speech_threshold_frames: int = 3  # ~90ms to confirm speech start
    max_speech_frames: int = 333  # ~10s max recording

    # Extended listening (phrase-aware)
    phrase_silence_threshold: int = 15  # frames (~450ms) — short pause between phrases
    utterance_silence_threshold: int = 45  # frames (~1.35s) — long pause = speaker done
    max_listening_seconds: float = 15.0  # total listening timeout after wake

    # Energy-based VAD gating (RMS threshold for 16-bit PCM)
    vad_energy_threshold: float = 80.0
    vad_window_size: int = 40  # sliding window frames
    vad_silence_ratio: float = 0.70  # ratio of silence in window to trigger end
    vad_speech_energy_ratio: float = 2.2  # RMS must exceed ambient * this to be speech

    # LED master brightness (0.0-1.0, scales all pattern brightnesses)
    led_brightness: float = 1.0

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

    def __post_init__(self) -> None:
        if self.mode not in ("dedicated", "shared"):
            raise ValueError("mode must be dedicated or shared")
        if self.hardware_profile not in (
            "generic", "respeaker_2mic", "respeaker_4mic",
        ):
            raise ValueError(
                "hardware_profile must be generic, respeaker_2mic, or respeaker_4mic"
            )
        if self.led_type not in (
            "none", "gpio", "respeaker", "respeaker_2mic",
            "respeaker_4mic", "respeaker_apa102",
        ):
            raise ValueError("unsupported led_type")
        if self.capture_format not in ("S16_LE", "S32_LE"):
            raise ValueError("capture_format must be S16_LE or S32_LE")
        if type(self.capture_channels) is not int or self.capture_channels < 1:
            raise ValueError("capture_channels must be a positive integer")
        expected_capture = {
            "respeaker_2mic": ("S16_LE", 2),
            "respeaker_4mic": ("S32_LE", 4),
        }.get(self.hardware_profile)
        if expected_capture and (
            self.capture_format, self.capture_channels
        ) != expected_capture:
            expected_format, expected_channels = expected_capture
            raise ValueError(
                f"{self.hardware_profile} requires {expected_format} "
                f"with {expected_channels} capture channels"
            )
        if type(self.channels) is not int or self.channels != 1:
            raise ValueError("channels must be 1; Atlas transport requires mono audio")
        if type(self.led_count) is not int or self.led_count < 0:
            raise ValueError("led_count must be a non-negative integer")
        if self.button_mode not in ("toggle", "press", "hold"):
            raise ValueError("button_mode must be toggle, press, or hold")
        if type(self.vad_sensitivity) is not int or not 0 <= self.vad_sensitivity <= 3:
            raise ValueError("vad_sensitivity must be an integer from 0 to 3")
        for name in ("volume", "mic_gain", "led_brightness", "wake_word_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("wake_word_enabled", "button_enabled", "vad_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.hardware_profile == "respeaker_4mic" and self.button_enabled:
            raise ValueError("respeaker_4mic has no hardware button")
        if not isinstance(self.features, dict):
            raise ValueError("features must be an object")

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
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
                temporary = f.name
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
            temporary = None
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

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
