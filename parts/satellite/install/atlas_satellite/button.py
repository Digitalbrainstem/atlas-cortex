"""ReSpeaker 2-mic HAT button handler.

The ReSpeaker 2-mic HAT has a tactile button on GPIO 17 (BCM).
Active LOW with internal pull-up (1 = not pressed, 0 = pressed).

Three modes controlled by ``button_mode`` config:

  ``toggle``  — Press toggles wake-word listening on/off (default).
                LED shows muted pattern when wake word is disabled.
  ``press``   — Single press starts listening (like saying the wake word).
                Satellite returns to IDLE after processing.
  ``hold``    — Hold to speak (intercom style).  Listening starts on
                press-down, ends on release.

The mode is configurable via admin portal or ``config.json``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

_HAS_GPIOD = False
try:
    import gpiod
    from gpiod.line import Bias, Edge
    _HAS_GPIOD = True
except ImportError:
    gpiod = None  # type: ignore[assignment]

BUTTON_PIN = 17  # BCM pin number on ReSpeaker 2-mic HAT
DEBOUNCE_S = 0.2  # Debounce time to prevent double triggers


class ButtonHandler:
    """Handles ReSpeaker button GPIO events via libgpiod."""

    def __init__(self, mode: str = "toggle") -> None:
        self.mode = mode  # toggle | press | hold
        self._on_press = None  # callback: async def on_press()
        self._on_release = None  # callback: async def on_release()
        self._on_toggle = None  # callback: async def on_toggle(enabled: bool)
        self._enabled = True  # For toggle mode: is wake word active?
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_event = 0.0
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._request = None

    def register(
        self,
        on_press=None,
        on_release=None,
        on_toggle=None,
    ) -> None:
        """Register async callbacks for button events."""
        self._on_press = on_press
        self._on_release = on_release
        self._on_toggle = on_toggle

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start listening for button events on GPIO 17."""
        if not _HAS_GPIOD:
            logger.warning("gpiod not available — button disabled")
            return

        self._loop = loop
        try:
            self._request = gpiod.request_lines(
                "/dev/gpiochip0",
                consumer="atlas-button",
                config={BUTTON_PIN: gpiod.LineSettings(
                    edge_detection=Edge.BOTH,
                    bias=Bias.PULL_UP,
                )},
            )
            self._active = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop, daemon=True, name="gpio-button",
            )
            self._thread.start()
            logger.info("Button handler started (GPIO %d, mode=%s)", BUTTON_PIN, self.mode)
        except Exception as e:
            logger.warning("Failed to set up button GPIO: %s", e)

    def stop(self) -> None:
        """Clean up GPIO resources."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._request:
            try:
                self._request.release()
            except Exception:
                pass
            self._request = None
        self._active = False

    def _poll_loop(self) -> None:
        """Background thread polling for GPIO edge events."""
        while not self._stop_event.is_set():
            try:
                if not self._request or not self._request.wait_edge_events(timeout=0.5):
                    continue
                events = self._request.read_edge_events()
                for ev in events:
                    self._handle_edge(ev)
            except Exception:
                if not self._stop_event.is_set():
                    time.sleep(0.1)

    def _handle_edge(self, event) -> None:
        """Handle a single edge event from gpiod."""
        now = time.monotonic()
        if now - self._last_event < DEBOUNCE_S:
            return
        self._last_event = now

        if not self._loop:
            return

        # FALLING = pressed (active LOW), RISING = released
        pressed = event.event_type == event.Type.FALLING_EDGE

        if self.mode == "toggle":
            if pressed:  # Only act on press-down
                self._enabled = not self._enabled
                logger.info("Button toggle: wake word %s",
                            "enabled" if self._enabled else "disabled")
                if self._on_toggle:
                    asyncio.run_coroutine_threadsafe(
                        self._on_toggle(self._enabled), self._loop)

        elif self.mode == "press":
            if pressed:  # Single press → start listening
                logger.info("Button press: start listening")
                if self._on_press:
                    asyncio.run_coroutine_threadsafe(
                        self._on_press(), self._loop)

        elif self.mode == "hold":
            if pressed:
                logger.info("Button hold: start listening")
                if self._on_press:
                    asyncio.run_coroutine_threadsafe(
                        self._on_press(), self._loop)
            else:
                logger.info("Button release: stop listening")
                if self._on_release:
                    asyncio.run_coroutine_threadsafe(
                        self._on_release(), self._loop)

    @property
    def wake_word_enabled(self) -> bool:
        """In toggle mode, returns whether wake word is currently active."""
        if self.mode != "toggle":
            return True
        return self._enabled
