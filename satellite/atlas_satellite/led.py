"""LED control abstraction for satellite devices.

Supports multiple backends:
  - none: No LEDs (default)
  - respeaker: ReSpeaker HAT APA102 LEDs via SPI
  - gpio: Standard GPIO LEDs

Dual-state model: LEDs show TWO independent states simultaneously:
  - Primary state: listening/speaking/idle (what Atlas is doing)
  - Activity state: processing/thinking overlay (background work)
  For 3-LED devices: LED 0 = activity indicator, LEDs 1-2 = primary
  For 12-LED devices: inner 4 = activity, outer 8 = primary
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LEDController(ABC):
    """Abstract LED controller interface."""

    def __init__(self):
        self._master_brightness: float = 1.0
        self._primary_state: str = "idle"
        self._activity_state: str | None = None

    def set_master_brightness(self, brightness: float) -> None:
        """Set master brightness (0.0-1.0) that scales all patterns."""
        self._master_brightness = max(0.0, min(1.0, brightness))

    @abstractmethod
    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        """Set all LEDs to a solid color."""

    @abstractmethod
    def set_pattern(self, pattern: str) -> None:
        """Set the primary LED pattern."""

    def set_activity(self, activity: str | None) -> None:
        """Set secondary activity indicator (processing/thinking overlay)."""
        self._activity_state = activity
        self._render_dual_state()

    def _render_dual_state(self) -> None:
        """Render combined primary + activity state. Override in subclasses."""
        if self._activity_state:
            self.set_pattern(self._activity_state)
        else:
            self.set_pattern(self._primary_state)

    @abstractmethod
    def off(self) -> None:
        """Turn all LEDs off."""

    def close(self) -> None:
        """Clean up resources."""
        self.off()


class NullLED(LEDController):
    """No-op LED controller when no LEDs are available."""

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        pass

    def set_pattern(self, pattern: str) -> None:
        self._primary_state = pattern

    def off(self) -> None:
        pass


class ReSpeakerLED(LEDController):
    """ReSpeaker HAT APA102 RGB LEDs via SPI.

    ReSpeaker 2-Mic HAT: 3 LEDs (LED 0 = activity, LEDs 1-2 = primary)
    ReSpeaker 4-Mic Array: 12 LEDs (inner 4 = activity, outer 8 = primary)
    """

    DEFAULT_PATTERNS = {
        "idle": (0, 0, 0, 0.0),
        "listening": (0, 100, 255, 0.4),
        "thinking": (255, 165, 0, 0.3),
        "processing": (255, 165, 0, 0.3),
        "speaking": (0, 200, 100, 0.4),
        "error": (255, 0, 0, 0.5),
        "muted": (255, 0, 0, 0.1),
        "wakeword": (0, 200, 255, 0.6),
    }

    def __init__(self, num_leds: int = 3, patterns: dict | None = None):
        super().__init__()
        self.num_leds = num_leds
        self._spi = None
        self._lock = threading.Lock()
        self._pulse_thread = None
        self._pulsing = False
        self.patterns: dict[str, tuple[int, int, int, float]] = dict(self.DEFAULT_PATTERNS)
        if patterns:
            self.update_patterns(patterns)

        # Determine LED zones for dual-state rendering
        if num_leds <= 3:
            self._activity_leds = [0]
            self._primary_leds = list(range(1, num_leds))
        elif num_leds <= 6:
            self._activity_leds = [0, 1]
            self._primary_leds = list(range(2, num_leds))
        else:
            # 12-LED ring: inner 4 = activity, outer 8 = primary
            self._activity_leds = [0, 3, 6, 9]
            self._primary_leds = [i for i in range(num_leds) if i not in [0, 3, 6, 9]]

        try:
            import spidev

            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 8_000_000
            logger.info("ReSpeaker LED initialized (%d LEDs, %d activity + %d primary)",
                        num_leds, len(self._activity_leds), len(self._primary_leds))
        except (ImportError, OSError) as e:
            logger.warning("SPI not available for LEDs: %s", e)

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        self._stop_pulse()
        self._write_leds_uniform(r, g, b, brightness)

    def _write_leds_uniform(self, r: int, g: int, b: int, brightness: float) -> None:
        if not self._spi:
            return
        brightness = brightness * self._master_brightness
        with self._lock:
            bright_byte = 0xE0 | int(max(0.0, min(1.0, brightness)) * 31)
            data = [0x00, 0x00, 0x00, 0x00]  # start frame
            for _ in range(self.num_leds):
                data.extend([bright_byte, b & 0xFF, g & 0xFF, r & 0xFF])
            end_bytes = (self.num_leds + 15) // 16
            data.extend([0xFF] * end_bytes)
            try:
                self._spi.xfer2(data)
            except Exception:
                logger.exception("SPI write error")

    def _write_leds_per_pixel(self, pixels: list[tuple[int, int, int, float]]) -> None:
        """Write individual color per LED for dual-state rendering."""
        if not self._spi:
            return
        with self._lock:
            data = [0x00, 0x00, 0x00, 0x00]  # start frame
            for r, g, b, brightness in pixels:
                brightness = brightness * self._master_brightness
                bright_byte = 0xE0 | int(max(0.0, min(1.0, brightness)) * 31)
                data.extend([bright_byte, b & 0xFF, g & 0xFF, r & 0xFF])
            end_bytes = (self.num_leds + 15) // 16
            data.extend([0xFF] * end_bytes)
            try:
                self._spi.xfer2(data)
            except Exception:
                logger.exception("SPI write error")

    def update_patterns(self, patterns: dict) -> None:
        """Update LED patterns from config dict.

        Accepts either:
          {"state": {"r": int, "g": int, "b": int, "brightness": float}}
        or:
          {"state": [r, g, b, brightness]}
        """
        for name, val in patterns.items():
            if isinstance(val, dict):
                self.patterns[name] = (
                    int(val.get("r", 0)),
                    int(val.get("g", 0)),
                    int(val.get("b", 0)),
                    float(val.get("brightness", 0.4)),
                )
            elif isinstance(val, (list, tuple)) and len(val) >= 4:
                self.patterns[name] = tuple(val[:4])

    def set_pattern(self, pattern: str) -> None:
        self._primary_state = pattern
        if self._activity_state:
            self._render_dual_state()
        else:
            color = self.patterns.get(pattern, (0, 0, 0, 0.0))
            if pattern == "thinking":
                self._start_pulse(*color)
            else:
                self.set_color(*color)

    def _render_dual_state(self) -> None:
        """Render primary state on primary LEDs + activity on activity LEDs."""
        self._stop_pulse()
        primary = self.patterns.get(self._primary_state, (0, 0, 0, 0.0))
        activity = self.patterns.get(self._activity_state or "idle", (0, 0, 0, 0.0))

        pixels = [(0, 0, 0, 0.0)] * self.num_leds
        for i in self._primary_leds:
            if i < self.num_leds:
                pixels[i] = primary
        for i in self._activity_leds:
            if i < self.num_leds:
                pixels[i] = activity
        self._write_leds_per_pixel(pixels)

        # Pulse activity LEDs if activity is thinking/processing
        if self._activity_state in ("thinking", "processing"):
            self._start_pulse_activity(activity)

    def _start_pulse(self, r: int, g: int, b: int, brightness: float) -> None:
        """Pulse ALL LEDs (used when no dual-state active)."""
        self._stop_pulse()
        self._pulsing = True

        def _pulse():
            phase = 0.0
            while self._pulsing:
                import math

                b_mod = brightness * (0.3 + 0.7 * abs(math.sin(phase)))
                self._write_leds_uniform(r, g, b, b_mod)
                phase += 0.15
                time.sleep(0.05)

        self._pulse_thread = threading.Thread(target=_pulse, daemon=True)
        self._pulse_thread.start()

    def _start_pulse_activity(self, color: tuple[int, int, int, float]) -> None:
        """Pulse only activity LEDs while primary LEDs stay solid."""
        self._stop_pulse()
        self._pulsing = True
        primary = self.patterns.get(self._primary_state, (0, 0, 0, 0.0))

        def _pulse():
            phase = 0.0
            while self._pulsing:
                import math

                mod = 0.3 + 0.7 * abs(math.sin(phase))
                pixels = [(0, 0, 0, 0.0)] * self.num_leds
                for i in self._primary_leds:
                    if i < self.num_leds:
                        pixels[i] = primary
                for i in self._activity_leds:
                    if i < self.num_leds:
                        pixels[i] = (color[0], color[1], color[2], color[3] * mod)
                self._write_leds_per_pixel(pixels)
                phase += 0.15
                time.sleep(0.05)

        self._pulse_thread = threading.Thread(target=_pulse, daemon=True)
        self._pulse_thread.start()

    def _stop_pulse(self) -> None:
        self._pulsing = False
        if self._pulse_thread:
            self._pulse_thread.join(timeout=0.5)
            self._pulse_thread = None

    def off(self) -> None:
        self._stop_pulse()
        self._write_leds(0, 0, 0, 0)

    def close(self) -> None:
        self.off()
        if self._spi:
            self._spi.close()
            self._spi = None


class GPIOLED(LEDController):
    """Simple GPIO LED (single color, on/off or PWM brightness)."""

    PATTERNS = {
        "idle": False,
        "listening": True,
        "thinking": True,
        "speaking": True,
        "error": True,
        "muted": False,
    }

    def __init__(self, pin: int = 25):
        super().__init__()
        self.pin = pin
        self._gpio = None
        try:
            import RPi.GPIO as GPIO

            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
        except (ImportError, RuntimeError) as e:
            logger.warning("GPIO not available: %s", e)

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        if self._gpio:
            state = (brightness * self._master_brightness) > 0.1
            self._gpio.output(self.pin, self._gpio.HIGH if state else self._gpio.LOW)

    def set_pattern(self, pattern: str) -> None:
        on = self.PATTERNS.get(pattern, False)
        if self._gpio:
            self._gpio.output(self.pin, self._gpio.HIGH if on else self._gpio.LOW)

    def off(self) -> None:
        if self._gpio:
            self._gpio.output(self.pin, self._gpio.LOW)

    def close(self) -> None:
        self.off()
        if self._gpio:
            self._gpio.cleanup(self.pin)


def create_led(led_type: str, led_count: int = 3, patterns: dict | None = None) -> LEDController:
    """Factory: create the appropriate LED controller."""
    lt = led_type.lower()
    if lt in ("respeaker", "respeaker_2mic", "respeaker_4mic", "respeaker_apa102"):
        return ReSpeakerLED(num_leds=led_count, patterns=patterns)
    elif lt == "gpio":
        return GPIOLED()
    else:
        return NullLED()
