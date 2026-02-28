"""LED control abstraction for satellite devices.

Supports multiple backends:
  - none: No LEDs (default)
  - respeaker: ReSpeaker HAT APA102 LEDs via SPI
  - gpio: Standard GPIO LEDs
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LEDController(ABC):
    """Abstract LED controller interface."""

    @abstractmethod
    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        """Set all LEDs to a solid color."""

    @abstractmethod
    def set_pattern(self, pattern: str) -> None:
        """Set a named pattern: idle, listening, thinking, speaking, error, muted."""

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
        pass

    def off(self) -> None:
        pass


class ReSpeakerLED(LEDController):
    """ReSpeaker HAT APA102 RGB LEDs via SPI.

    ReSpeaker 2-Mic HAT: 3 LEDs
    ReSpeaker 4-Mic Array: 12 LEDs
    """

    PATTERNS = {
        "idle": (0, 0, 0, 0.0),
        "listening": (0, 100, 255, 0.4),
        "thinking": (255, 165, 0, 0.3),
        "speaking": (0, 200, 100, 0.4),
        "error": (255, 0, 0, 0.5),
        "muted": (255, 0, 0, 0.1),
    }

    def __init__(self, num_leds: int = 3):
        self.num_leds = num_leds
        self._spi = None
        self._lock = threading.Lock()
        self._pulse_thread = None
        self._pulsing = False

        try:
            import spidev

            self._spi = spidev.SpiDev()
            self._spi.open(0, 0)
            self._spi.max_speed_hz = 8_000_000
            logger.info("ReSpeaker LED initialized (%d LEDs)", num_leds)
        except (ImportError, OSError) as e:
            logger.warning("SPI not available for LEDs: %s", e)

    def set_color(self, r: int, g: int, b: int, brightness: float = 1.0) -> None:
        self._stop_pulse()
        self._write_leds(r, g, b, brightness)

    def _write_leds(self, r: int, g: int, b: int, brightness: float) -> None:
        if not self._spi:
            return
        with self._lock:
            bright_byte = 0xE0 | int(max(0.0, min(1.0, brightness)) * 31)
            # APA102 protocol: start frame + LED frames + end frame
            data = [0x00, 0x00, 0x00, 0x00]  # start frame
            for _ in range(self.num_leds):
                data.extend([bright_byte, b & 0xFF, g & 0xFF, r & 0xFF])
            end_bytes = (self.num_leds + 15) // 16
            data.extend([0xFF] * end_bytes)
            try:
                self._spi.xfer2(data)
            except Exception:
                logger.exception("SPI write error")

    def set_pattern(self, pattern: str) -> None:
        color = self.PATTERNS.get(pattern, (0, 0, 0, 0.0))
        if pattern == "thinking":
            self._start_pulse(*color)
        else:
            self.set_color(*color)

    def _start_pulse(self, r: int, g: int, b: int, brightness: float) -> None:
        self._stop_pulse()
        self._pulsing = True

        def _pulse():
            phase = 0.0
            while self._pulsing:
                import math

                b_mod = brightness * (0.3 + 0.7 * abs(math.sin(phase)))
                self._write_leds(r, g, b, b_mod)
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
            state = brightness > 0.1
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


def create_led(led_type: str, led_count: int = 3) -> LEDController:
    """Factory: create the appropriate LED controller."""
    if led_type == "respeaker":
        return ReSpeakerLED(num_leds=led_count)
    elif led_type == "gpio":
        return GPIOLED()
    else:
        return NullLED()
