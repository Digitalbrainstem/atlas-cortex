"""Main satellite agent — state machine orchestrating all components.

States: IDLE → LISTENING → PROCESSING → SPEAKING → IDLE
                                                  ↗
  Wake word (or VAD) triggers LISTENING
  VAD silence ends LISTENING → PROCESSING
  Server TTS response → SPEAKING
  TTS playback complete → IDLE
"""

from __future__ import annotations

import asyncio
import base64
import enum
from dataclasses import replace
import json
import logging
import os
import struct
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .audio import AudioCapture, AudioPlayback
from .button import ButtonHandler
from .config import SatelliteConfig
from .filler_cache import FillerCache
from .led import LEDController, create_led
from .mdns import SatelliteAnnouncer, ServerDiscovery
from .vad import VoiceActivityDetector, _rms
from .wake_word import WakeWordDetector
from .ws_client import SatelliteWSClient

logger = logging.getLogger(__name__)

# Barge-in keywords — if we had an STT running during playback we'd check
# these.  For now the list is used only to document intent; actual detection
# relies on energy thresholds and wake-word confidence.
_BARGE_IN_KEYWORDS = frozenset({
    "stop", "nevermind", "shut up", "atlas stop", "hey atlas",
})


class State(enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"
    MUTED = "muted"


class SatelliteAgent:
    """Core satellite agent — captures audio, detects wake words,
    streams to server, and plays responses."""

    def __init__(self, config: SatelliteConfig):
        self.config = config
        self.state = State.IDLE
        self._running = False
        self._start_time = 0.0

        # Resolve paths based on mode
        if config.mode == "dedicated":
            base = Path("/opt/atlas-satellite")
        else:
            base = Path.home() / ".atlas-satellite"

        # Components (initialized in start())
        self.audio_in: Optional[AudioCapture] = None
        self.audio_out: Optional[AudioPlayback] = None
        self.vad: Optional[VoiceActivityDetector] = None
        self.wake_word: Optional[WakeWordDetector] = None
        self.ws: Optional[SatelliteWSClient] = None
        self.led: Optional[LEDController] = None
        self.announcer: Optional[SatelliteAnnouncer] = None
        self.server_discovery: Optional[ServerDiscovery] = None
        self.fillers: Optional[FillerCache] = None
        self.button: Optional[ButtonHandler] = None

        self._base_dir = base
        self._tts_buffer = bytearray()
        self._tts_sample_rate = 22050
        self._server_url_discovered = asyncio.Event()
        self._echo_suppress_until = 0.0  # timestamp: ignore VAD until this time
        self._auto_listen_deadline = 0.0  # auto-listen timeout
        self._capture_done = asyncio.Event()
        self._capture_done.set()
        self._playback_task: asyncio.Task | None = None
        self._tts_metadata: dict = {}
        self._capture_bytes = 0
        self._rejected_playbacks: deque[str] = deque(maxlen=32)
        self._config_error = ""
        self._restart_required: list[str] = []
        self._wake_task: asyncio.Task[None] | None = None

        # Barge-in detection: higher energy ratio during SPEAKING to reject
        # speaker echo picked up by the mic.  We require several consecutive
        # speech frames before triggering to avoid transient noise.
        self._bargein_energy_ratio = 3.5
        self._bargein_speech_frames = 0
        self._bargein_threshold_frames = 4  # ~120ms at 30ms/frame

    async def start(self) -> None:
        """Initialize components and run the main loop."""
        logger.info("=== Atlas Satellite Agent v0.1.0 ===")
        logger.info("ID: %s | Room: %s | Mode: %s",
                     self.config.satellite_id, self.config.room, self.config.mode)

        self._start_time = time.time()
        self._running = True

        # Initialize all components
        self._init_components()

        # Start mDNS announcement (so server can find us)
        if self.announcer:
            try:
                self.announcer.start()
            except Exception:
                logger.exception("mDNS announcement failed")

        # LED: boot pattern
        self.led.set_pattern("thinking")

        # Auto-discover server if no URL configured
        if not self.config.server_url or self.config.server_url == "ws://atlas-server:5100/ws/satellite":
            logger.info("No server URL configured — searching via mDNS...")
            self.server_discovery = ServerDiscovery(self._on_server_found)
            self.server_discovery.start()
            # Wait up to 30s for discovery, then fall back to retry loop
            try:
                await asyncio.wait_for(self._server_url_discovered.wait(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Server not found via mDNS — will keep searching in background")
        else:
            self._server_url_discovered.set()

        # Connect to server
        if self._server_url_discovered.is_set() and self.config.server_url:
            connected = await self.ws.connect()
            if not connected:
                logger.error("Initial connection failed — will retry in background")

        self.led.set_pattern("error" if self.state == State.ERROR or (
            self.wake_word is not None and not self.wake_word.available
        ) else "idle")

        # Start button handler (needs running event loop)
        if self.button:
            self.button.start(asyncio.get_event_loop())
        self._schedule_wake_initialization()
        if self.ws.connected:
            await self.ws.send_heartbeat(readiness=self.readiness())

        # Run concurrent loops
        try:
            await asyncio.gather(
                self._audio_loop(),
                self._server_listener(),
                self._heartbeat_loop(),
                self._reconnect_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully shut down all components."""
        logger.info("Shutting down satellite agent...")
        self._running = False
        if self._wake_task is not None:
            await asyncio.gather(self._wake_task, return_exceptions=False)

        if self.audio_in:
            self.audio_in.stop()
        if self.button:
            self.button.stop()
        if self.announcer:
            self.announcer.stop()
        if self.server_discovery:
            self.server_discovery.stop()
        if self.led:
            self.led.close()
        if self.ws:
            await self.ws.disconnect()
        if self._playback_task is not None:
            self._playback_task.cancel()
            await asyncio.gather(self._playback_task, return_exceptions=True)

    def _init_components(self) -> None:
        """Create and configure all subsystems."""
        cfg = self.config

        # Audio I/O
        self.audio_in = AudioCapture(
            device=cfg.audio_device_in,
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            period_size=cfg.frames_per_chunk,
            mic_gain=cfg.mic_gain,
        )
        self.audio_out = AudioPlayback(
            device=cfg.audio_device_out,
            volume=cfg.volume,
        )

        # VAD
        self.vad = VoiceActivityDetector(
            aggressiveness=cfg.vad_sensitivity,
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.chunk_ms,
            speech_threshold=cfg.speech_threshold_frames,
            silence_threshold=cfg.silence_threshold_frames,
            max_speech_frames=cfg.max_speech_frames,
            energy_threshold=cfg.vad_energy_threshold,
            window_size=cfg.vad_window_size,
            silence_ratio=cfg.vad_silence_ratio,
            speech_energy_ratio=cfg.vad_speech_energy_ratio,
        )

        # Wake word (optional)
        self.wake_word = None
        if cfg.wake_word_enabled:
            self.wake_word = WakeWordDetector(
                wake_word=cfg.wake_word,
                threshold=cfg.wake_word_threshold,
                model_path=cfg.wake_word_model,
                initialize=False,
            )

        # LED
        self.led = create_led(cfg.led_type, cfg.led_count, cfg.led_patterns)
        self.led.set_master_brightness(cfg.led_brightness)

        # WebSocket
        self.ws = SatelliteWSClient(
            server_url=cfg.server_url,
            satellite_id=cfg.satellite_id,
            room=cfg.room,
            capabilities=self._detect_capabilities(),
        )
        self._register_ws_handlers()

        # mDNS
        self.announcer = SatelliteAnnouncer(
            satellite_id=cfg.satellite_id,
            port=cfg.service_port,
            room=cfg.room,
        )

        # Filler cache
        cache_dir = self._base_dir / "cache" / "fillers"
        self.fillers = FillerCache(cache_dir)

        # Button (ReSpeaker 2-mic HAT GPIO 17)
        if cfg.button_enabled and cfg.led_type == "respeaker_2mic":
            self.button = ButtonHandler(mode=cfg.button_mode)
            self.button.register(
                on_press=self._on_button_press,
                on_release=self._on_button_release,
                on_toggle=self._on_button_toggle,
            )

        # Start audio capture
        try:
            self.audio_in.start()
        except Exception:
            logger.exception("Failed to start audio capture")
            self.state = State.ERROR

    def _detect_capabilities(self) -> list[str]:
        """Detect what this satellite can do."""
        caps = ["audio_capture", "audio_playback", "playback_ack"]
        if self.wake_word is not None and self.wake_word.available:
            caps.append("wake_word")
        if self.config.led_type != "none":
            caps.append("led")
        if self.config.filler_enabled:
            caps.append("filler_playback")
        return caps

    def _on_server_found(self, server_url: str) -> None:
        """Callback when Atlas server is discovered via mDNS."""
        logger.info("Auto-discovered Atlas server: %s", server_url)
        self.config.server_url = server_url
        if self.ws:
            self.ws.server_url = server_url
        self._server_url_discovered.set()

    def _register_ws_handlers(self) -> None:
        """Register handlers for server → satellite messages."""
        self.ws.on("TTS_START", self._on_tts_start)
        self.ws.on("TTS_CHUNK", self._on_tts_chunk)
        self.ws.on("TTS_END", self._on_tts_end)
        self.ws.on("PLAY_FILLER", self._on_play_filler)
        self.ws.on("COMMAND", self._on_command)
        self.ws.on("CONFIG", self._on_server_config)
        self.ws.on("SYNC_FILLERS", self._on_sync_fillers)
        self.ws.on("PIPELINE_ERROR", self._on_pipeline_error)
        self.ws.on("PIPELINE_STATUS", self._on_pipeline_status)
        # Remote management commands
        self.ws.on("CONFIG_UPDATE", self._on_remote_config_update)
        self.ws.on("EXEC_SCRIPT", self._on_remote_exec_script)
        self.ws.on("RESTART_SERVICE", self._on_remote_restart_service)
        self.ws.on("UPDATE_AGENT", self._on_remote_update_agent)
        self.ws.on("KIOSK_URL", self._on_remote_kiosk_url)
        self.ws.on("REBOOT", self._on_remote_reboot)
        self.ws.on("LOG_REQUEST", self._on_remote_log_request)

    # ── Audio loop ────────────────────────────────────────────────

    async def _audio_loop(self) -> None:
        """Main audio processing loop — runs every chunk_ms."""
        loop = asyncio.get_event_loop()

        # Discard initial audio frames to avoid startup noise triggering VAD
        self._echo_suppress_until = time.monotonic() + 2.0

        while self._running:
            if self.state in (State.ERROR, State.MUTED):
                await asyncio.sleep(0.1)
                continue

            # Timeout: if stuck in PROCESSING for >60s, return to IDLE
            if self.state == State.PROCESSING:
                if not hasattr(self, '_processing_start'):
                    self._processing_start = time.monotonic()
                elif time.monotonic() - self._processing_start > 60:
                    logger.warning("Processing timeout — returning to IDLE")
                    self.state = State.IDLE
                    self._capture_done.set()
                    self.led.set_pattern("idle")
                    self.vad.reset()
                    del self._processing_start
            elif hasattr(self, '_processing_start'):
                del self._processing_start

            # Read audio from mic (blocking call in executor)
            audio = await loop.run_in_executor(None, self.audio_in.read)
            if audio is None:
                await asyncio.sleep(0.01)
                continue

            if self.state == State.IDLE:
                await self._process_idle(audio)
            elif self.state == State.LISTENING:
                await self._process_listening(audio)
            elif self.state == State.SPEAKING:
                await self._process_speaking(audio)
            elif self.state == State.PROCESSING:
                await self._process_idle(audio)
            # PROCESSING is driven by server messages

    async def _process_idle(self, audio: bytes) -> None:
        """In IDLE state: check for wake word or VAD speech start."""
        if time.monotonic() < self._echo_suppress_until:
            # Still suppressing echo — feed wake word model to flush its
            # internal buffers (discard the result) and maintain VAD baseline.
            if self.wake_word:
                self.wake_word.process(audio)
            if self.vad and self.vad.active:
                self.vad.process(audio)

            self._echo_active = True
            return

        # Just exited echo suppression — reset wake word to discard any
        # residual high-confidence detections from speaker audio.
        if getattr(self, '_echo_active', False):
            self._echo_active = False
            if self.wake_word:
                self.wake_word.reset()

        if self.wake_word:
            # Feed VAD in idle to maintain ambient calibration
            if self.vad and self.vad.active:
                self.vad.process(audio)
            # Skip wake word detection if button-toggled off
            if self.button and not self.button.wake_word_enabled:
                return
            # Wake word mode
            confidence = self.wake_word.process(audio)
            if confidence >= self.config.wake_word_threshold:
                logger.info("Wake word detected (confidence: %.2f)", confidence)
                self.wake_word.reset()  # Clear buffers to prevent re-trigger
                await self._transition_to_listening(confidence)
        elif not self.config.wake_word_enabled and self.config.vad_enabled:
            # VAD-only mode: speech_start triggers listening
            result = self.vad.process(audio)
            if result == "speech_start":
                logger.info("Speech detected (VAD-only mode)")
                await self._transition_to_listening(1.0)

    async def _process_listening(self, audio: bytes) -> None:
        """In LISTENING state: stream audio, check for end of speech."""
        # Auto-listen timeout: if deadline passed without real speech, bail out
        deadline = getattr(self, '_auto_listen_deadline', 0)
        if deadline and time.monotonic() > deadline:
            if not self.vad._in_speech:
                logger.info("Auto-listen timeout — no speech detected, returning to IDLE")
                self._auto_listen_deadline = 0
                self.state = State.IDLE
                self.led.set_pattern("idle")
                self.vad.reset()
                if self.ws.connected:
                    await self.ws.send_audio_end("auto_listen_timeout")
                    await self.ws.send_status("idle")
                return

        if self._capture_bytes + len(audio) > 480000:
            await self._transition_to_processing("capture_limit")
            return
        if self.ws.connected:
            await self.ws.send_audio_chunk(audio)
            self._capture_bytes += len(audio)

        # Total listening timeout — report an incomplete capture, never trim its tail.
        listen_start = getattr(self, '_listening_start', 0)
        if listen_start and time.monotonic() - listen_start > self.config.max_listening_seconds:
            logger.info(
                "Total listening timeout (%.0fs) — finalising",
                self.config.max_listening_seconds,
            )
            self._auto_listen_deadline = 0
            await self._transition_to_processing("capture_limit")
            return

        result = self.vad.process(audio)
        if result == "speech_start" and deadline:
            # Real speech detected — clear auto-listen deadline
            self._auto_listen_deadline = 0
        if result == "phrase_end":
            logger.info("Phrase boundary detected — signalling server, continuing to listen")
            if self.ws.connected:
                await self.ws.send_audio_phrase_end()
        elif result == "speech_end":
            logger.info("Speech ended (VAD silence)")
            self._auto_listen_deadline = 0
            await self._transition_to_processing()

    async def _process_speaking(self, audio: bytes) -> None:
        """In SPEAKING state: detect user speaking over TTS (barge-in).

        Uses a higher energy ratio than normal VAD to avoid triggering on
        the speaker's own output picked up by the mic.  Also checks for
        wake-word as a reliable barge-in signal.
        """
        if not self.config.features.get("acoustic_echo_cancellation", False):
            return
        rms = _rms(audio)
        ambient = self.vad._ambient_rms if self.vad._calibrated else 0.0

        # Maintain VAD ambient calibration even during speaking
        if self.vad and self.vad.active:
            # Feed VAD to keep calibration fresh but ignore its speech detection
            self.vad.process(audio)

        # Wake word is a strong barge-in signal
        wake_triggered = False
        if self.wake_word:
            confidence = self.wake_word.process(audio)
            if confidence >= self.config.wake_word_threshold:
                wake_triggered = True
                logger.info("Barge-in: wake word detected during playback (%.2f)", confidence)

        # Energy-based barge-in: user voice must be significantly louder
        # than ambient to overcome speaker echo
        energy_triggered = False
        if ambient > 0:
            if rms > ambient * self._bargein_energy_ratio:
                self._bargein_speech_frames += 1
            else:
                self._bargein_speech_frames = 0

            if self._bargein_speech_frames >= self._bargein_threshold_frames:
                energy_triggered = True
                logger.info(
                    "Barge-in: energy threshold exceeded "
                    "(rms=%.0f, ambient=%.0f, ratio=%.1f, frames=%d)",
                    rms, ambient, rms / max(ambient, 1),
                    self._bargein_speech_frames,
                )

        if wake_triggered or energy_triggered:
            await self._handle_barge_in()

    async def _handle_barge_in(self) -> None:
        """Execute barge-in: stop playback, notify server, start listening."""
        logger.info("Barge-in triggered — interrupting playback")

        # 1. Stop audio playback immediately
        metadata = dict(self._tts_metadata)
        await self._stop_playback()

        # 2. Reset barge-in counters
        self._bargein_speech_frames = 0

        # 3. Notify server
        if self.ws.connected:
            await self.ws.send_barge_in(metadata)

        # 4. Skip echo suppression — user is actively talking
        self._echo_suppress_until = 0.0

        # 5. Reset wake word buffers
        if self.wake_word:
            self.wake_word.reset()

        # 6. Transition to listening — user's new speech will be captured
        await self._transition_to_listening(1.0)

    async def _stop_playback(self) -> None:
        self.audio_out.stop()
        playback_id = self._tts_metadata.get("playback_id")
        if playback_id:
            self._rejected_playbacks.append(playback_id)
        self._tts_buffer.clear()
        self._tts_metadata = {}
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
            await asyncio.gather(self._playback_task, return_exceptions=True)

    async def _transition_to_listening(self, confidence: float) -> None:
        if not self.ws.connected or (self.audio_in is not None and self.audio_in.available is False):
            logger.warning("Cannot listen: server or microphone unavailable")
            self.led.set_pattern("error" if self.ws.connected else "offline")
            return
        self.state = State.LISTENING
        self._capture_done.clear()
        self._capture_bytes = 0
        self.led.set_pattern("listening")
        self.vad.reset()
        self._listening_start = time.monotonic()

        if self.ws.connected:
            await self.ws.send_wake(confidence)
            await self.ws.send_audio_start()
            await self.ws.send_status("listening")

    async def _transition_to_processing(self, reason: str = "vad_silence") -> None:
        self.state = State.PROCESSING
        self._capture_done.set()
        self.led.set_pattern("thinking")

        if self.ws.connected:
            await self.ws.send_audio_end(reason)

    # ── Server message handlers ───────────────────────────────────

    async def _on_tts_start(self, msg: dict) -> None:
        """Server is about to send TTS audio."""
        if msg.get("playback_id") in self._rejected_playbacks:
            return
        logger.info("TTS_START received (rate=%s)", msg.get("sample_rate", "?"))
        self._tts_buffer.clear()
        self._tts_metadata = {key: msg[key] for key in ("turn_id", "revision", "playback_id") if key in msg}
        # Check explicit sample_rate first, then parse from format string
        if "sample_rate" in msg:
            self._tts_sample_rate = int(msg["sample_rate"])
        else:
            fmt = msg.get("format", "pcm_22k_16bit_mono")
            if "22k" in fmt:
                self._tts_sample_rate = 22050
            elif "16k" in fmt:
                self._tts_sample_rate = 16000
            elif "44k" in fmt:
                self._tts_sample_rate = 44100

    async def _on_tts_chunk(self, msg: dict) -> None:
        """Received a chunk of TTS audio."""
        if any(msg.get(key) != self._tts_metadata.get(key) for key in ("playback_id", "turn_id", "revision")):
            return
        audio_b64 = msg.get("audio", "")
        if audio_b64:
            if len(self._tts_buffer) + len(audio_b64) * 3 // 4 > 4 * 1024 * 1024:
                raise ValueError("TTS audio allocation exceeded")
            self._tts_buffer.extend(base64.b64decode(audio_b64))

    async def _on_tts_end(self, msg: dict) -> None:
        """TTS stream complete — play the buffered audio."""
        if any(msg.get(key) != self._tts_metadata.get(key) for key in ("playback_id", "turn_id", "revision")):
            return
        audio = bytes(self._tts_buffer)
        self._tts_buffer.clear()
        metadata = dict(self._tts_metadata)
        if metadata.get("playback_id"):
            if self._playback_task is not None and not self._playback_task.done():
                raise RuntimeError("Previous playback has not completed")
            self._playback_task = asyncio.create_task(
                self._play_received_audio(msg, audio, metadata), name="satellite-playback",
            )
            self._playback_task.add_done_callback(self._playback_finished)
        else:
            await self._play_received_audio(msg, audio, metadata)

    def _playback_finished(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Satellite playback failed", exc_info=task.exception())

    async def _play_received_audio(self, msg: dict, audio: bytes, metadata: dict) -> None:
        await self._capture_done.wait()
        is_filler = msg.get("is_filler", False)
        auto_listen = msg.get("auto_listen", False)
        more_pending = msg.get("more_pending", False)
        logger.info("TTS_END received (%d bytes buffered, rate=%d, filler=%s, more=%s)",
                     len(audio), self._tts_sample_rate, is_filler, more_pending)
        self.state = State.SPEAKING
        self.led.set_pattern("speaking")
        self._bargein_speech_frames = 0  # reset for this playback

        completed = False
        if audio:
            completed = await self.audio_out.play_pcm(audio, self._tts_sample_rate)
        if completed is True and metadata and self.state == State.SPEAKING:
            await self.ws.send_playback_ack(metadata)

        # If barge-in occurred during playback the state is already LISTENING
        if self.state != State.SPEAKING:
            logger.info("Barge-in occurred during TTS playback — skipping post-playback logic")
            return

        # Suppress echo: ignore VAD/wake for a longer window after playback.
        # The ReSpeaker mic picks up speaker audio which triggers openwakeword
        # at very high confidence (0.99) — 1.5s is not enough.
        self._echo_suppress_until = time.monotonic() + 3.0

        # Reset wake word model to clear internal audio buffers
        # (prevents TTS playback from triggering false wake detections)
        if self.wake_word:
            self.wake_word.reset()

        # Fillers: stay in PROCESSING — more audio coming
        if is_filler:
            self.state = State.PROCESSING
            self.led.set_pattern("processing")
            return

        # CE-2: More phrases queued on server — stay in PROCESSING
        if more_pending:
            logger.info("More phrases pending — staying in PROCESSING")
            self.state = State.PROCESSING
            self.led.set_pattern("processing")
            return

        # Auto-listen: system asked a question, start listening for reply
        if auto_listen:
            logger.info("Auto-listen: transitioning to LISTENING for follow-up")
            self.vad.reset()
            self.state = State.IDLE
            # Wait for echo to fully dissipate before listening
            await asyncio.sleep(1.5)
            if self.state == State.IDLE:  # not interrupted by something else
                self._auto_listen_deadline = time.monotonic() + 6.0
                await self._transition_to_listening(1.0)
            return

        # Return to idle
        self.state = State.IDLE
        self.led.set_pattern("idle")
        self.vad.reset()
        if self.ws.connected:
            await self.ws.send_status("idle")

    async def _on_play_filler(self, msg: dict) -> None:
        """Play a cached filler phrase while server processes response."""
        if not self.config.filler_enabled or not self.fillers:
            return
        filler_path = self.fillers.get_random()
        if filler_path:
            logger.debug("Playing filler: %s", filler_path)
            await self.audio_out.play_wav(filler_path)

    async def _on_pipeline_error(self, msg: dict) -> None:
        """Server pipeline failed — return to idle."""
        detail = msg.get("detail", "unknown")
        logger.warning("Pipeline error from server: %s", detail)
        await self._stop_playback()
        if self.state == State.LISTENING and self.ws.connected:
            await self.ws.send_audio_end(reason="cancelled")
        self._capture_done.set()
        self.state = State.IDLE
        self.led.set_pattern("idle")
        self.vad.reset()

    async def _on_pipeline_status(self, msg: dict) -> None:
        if type(msg.get("processing")) is bool:
            self.led.set_activity("processing" if msg["processing"] else None)

    # ── Button callbacks ──────────────────────────────────────────

    async def _on_button_press(self) -> None:
        """Button pressed in 'press' or 'hold' mode → start listening."""
        if self.state == State.SPEAKING:
            await self._handle_barge_in()
            return
        if self.state == State.PROCESSING:
            await self._transition_to_listening(1.0)
            return
        if self.state not in (State.IDLE, State.MUTED):
            return
        logger.info("Button: start listening")
        self._echo_suppress_until = 0  # clear any echo suppression
        await self._transition_to_listening(1.0)

    async def _on_button_release(self) -> None:
        """Button released in 'hold' mode → stop listening."""
        if self.state != State.LISTENING:
            return
        logger.info("Button: stop listening (hold release)")
        await self._transition_to_processing()

    async def _on_button_toggle(self, enabled: bool) -> None:
        """Button toggled wake word on/off."""
        if enabled:
            self.state = State.IDLE
            self.led.set_pattern("idle")
            logger.info("Button: wake word enabled")
        else:
            await self._stop_playback()
            if self.state == State.LISTENING and self.ws.connected:
                await self.ws.send_audio_end(reason="cancelled")
            self._capture_done.set()
            self.state = State.MUTED
            self.led.set_pattern("muted")
            logger.info("Button: wake word disabled (muted)")

    async def _on_command(self, msg: dict) -> None:
        """Execute a command from the server."""
        action = msg.get("action", "")
        params = msg.get("params", {})

        if action == "listen":
            await self._transition_to_listening(1.0)
        elif action == "stop":
            if params.get("playback_id") and params["playback_id"] != self._tts_metadata.get("playback_id"):
                return
            await self._stop_playback()
            self.state = State.IDLE
            self._capture_done.set()
            self.led.set_pattern("idle")
        elif action == "volume":
            self.config.volume = float(params.get("level", self.config.volume))
            self.audio_out.volume = self.config.volume
        elif action == "led":
            self.led.set_pattern(params.get("pattern", "idle"))
        elif action == "mute":
            await self._on_button_toggle(False)
        elif action == "unmute":
            self.state = State.IDLE
            self.led.set_pattern("idle")
        elif action == "reboot":
            logger.warning("Reboot requested by server")
            os.system("sudo reboot")
        elif action == "identify":
            # Flash LEDs for identification
            for _ in range(5):
                self.led.set_color(255, 255, 255, 1.0)
                await asyncio.sleep(0.3)
                self.led.off()
                await asyncio.sleep(0.3)
            self.led.set_pattern("idle")
        elif action == "test_audio":
            # Generate a short test tone (440Hz, 1 second)
            await self._play_test_tone()
        elif action == "test_leds":
            # Cycle through LED patterns for visual confirmation
            for pattern in ("listening", "thinking", "speaking", "error"):
                self.led.set_pattern(pattern)
                await asyncio.sleep(1.5)
            self.led.set_pattern("idle")
        elif action == "led_config":
            # Update LED pattern colors at runtime
            patterns = params.get("patterns", {})
            if patterns and hasattr(self.led, "update_patterns"):
                self.led.update_patterns(patterns)
                self.config.led_patterns.update(patterns)
                logger.info("LED patterns updated: %s", list(patterns.keys()))
        else:
            logger.warning("Unknown command: %s", action)

    async def _on_config(self, msg: dict) -> None:
        """Apply pushed configuration from server."""
        values = {key: value for key, value in msg.items() if key in self.config.__dataclass_fields__}
        path = self._base_dir / "config.json"
        if path.exists():
            with path.open() as stream:
                persisted = json.load(stream)
            values = {**{key: value for key, value in persisted.items()
                         if key in self.config.__dataclass_fields__}, **values}
        candidate = replace(self.config, **values)
        await asyncio.to_thread(candidate.save, self._base_dir / "config.json")
        previous = replace(self.config)
        live_fields = {
            "room", "wake_word", "wake_word_enabled", "wake_word_threshold", "wake_word_model",
            "volume", "mic_gain", "vad_sensitivity", "vad_enabled", "filler_enabled",
            "led_brightness", "led_patterns", "features", "button_mode", "audio_device_out",
        }
        self._restart_required = sorted(set(self._restart_required) | {
            key for key in values if key not in live_fields and getattr(previous, key) != getattr(candidate, key)
        })
        for key, value in vars(candidate).items():
            setattr(self.config, key, value)
        if self.audio_out:
            self.audio_out.volume = candidate.volume
            self.audio_out.device = candidate.audio_device_out or "default"
        if self.audio_in:
            self.audio_in.mic_gain = candidate.mic_gain
        if self.vad and previous.vad_sensitivity != candidate.vad_sensitivity:
            self.vad.set_aggressiveness(candidate.vad_sensitivity)
        if self.button:
            self.button.mode = candidate.button_mode
        if self.led:
            self.led.set_master_brightness(candidate.led_brightness)
            if hasattr(self.led, "update_patterns"):
                self.led.update_patterns(candidate.led_patterns)
        wake_fields = ("wake_word", "wake_word_enabled", "wake_word_threshold", "wake_word_model")
        if any(getattr(previous, key) != getattr(candidate, key) for key in wake_fields):
            self.wake_word = WakeWordDetector(
                candidate.wake_word, candidate.wake_word_threshold, candidate.wake_word_model,
                initialize=False,
            ) if candidate.wake_word_enabled else None
            self._schedule_wake_initialization()
        if self.ws:
            self.ws.room = candidate.room
            self.ws.capabilities = self._detect_capabilities()
            self._config_error = ""
            await self.ws.send_heartbeat(readiness=self.readiness())
        logger.info("Config persisted and applied: %s", sorted(values))

    async def _on_server_config(self, msg: dict) -> None:
        try:
            await self._on_config(msg)
            self._config_error = ""
        except Exception as exc:
            self._config_error = str(exc)
            logger.exception("Server configuration could not be applied")
            await self.ws.send_heartbeat(readiness=self.readiness())

    # ── Remote management commands ────────────────────────────────

    async def _on_remote_config_update(self, msg: dict) -> None:
        """Merge payload into satellite config.json."""
        cmd_id = msg.get("cmd_id")
        payload = msg.get("payload", {})
        try:
            unknown = set(payload) - self.config.__dataclass_fields__.keys()
            if unknown:
                raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
            await self._on_config(payload)
            logger.info("Config updated via remote command: %s", list(payload.keys()))
            await self.ws.send_cmd_ack(cmd_id, "ok")
        except Exception as e:
            logger.exception("CONFIG_UPDATE failed")
            await self.ws.send_cmd_ack(cmd_id, f"error: {e}")

    async def _on_remote_exec_script(self, msg: dict) -> None:
        """Run a shell script with timeout, capture output."""
        cmd_id = msg.get("cmd_id")
        payload = msg.get("payload", {})
        script = payload.get("script", "")
        timeout = max(1, min(int(payload.get("timeout", 30)), 300))
        if not script:
            await self.ws.send_cmd_ack(cmd_id, "error: empty script")
            return
        try:
            proc = await asyncio.create_subprocess_shell(
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
            result = {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace")[-4096:],
                "stderr": stderr.decode(errors="replace")[-4096:],
            }
            await self.ws.send_cmd_ack(cmd_id, json.dumps(result))
        except asyncio.TimeoutError:
            proc.kill()
            await self.ws.send_cmd_ack(cmd_id, "error: timeout")
        except Exception as e:
            await self.ws.send_cmd_ack(cmd_id, f"error: {e}")

    async def _on_remote_restart_service(self, msg: dict) -> None:
        """Restart a systemd service."""
        cmd_id = msg.get("cmd_id")
        payload = msg.get("payload", {})
        service = payload.get("service", "atlas-satellite")
        subprocess.Popen(["sudo", "systemctl", "restart", service])
        await self.ws.send_cmd_ack(cmd_id, "restarting")

    async def _on_remote_update_agent(self, msg: dict) -> None:
        """Git pull + pip install + schedule restart."""
        cmd_id = msg.get("cmd_id")
        try:
            steps = []
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(self._base_dir), "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            steps.append(f"git pull: exit {proc.returncode}")

            req_path = self._base_dir / "requirements.txt"
            if req_path.exists():
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "-q", "-r", str(req_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                steps.append(f"pip install: exit {proc.returncode}")

            await self.ws.send_cmd_ack(cmd_id, json.dumps({"steps": steps, "restarting": True}))
            # Schedule restart after ack is sent
            await asyncio.sleep(1)
            subprocess.Popen(["sudo", "systemctl", "restart", "atlas-satellite"])
        except Exception as e:
            await self.ws.send_cmd_ack(cmd_id, f"error: {e}")

    async def _on_remote_kiosk_url(self, msg: dict) -> None:
        """Change kiosk display URL."""
        cmd_id = msg.get("cmd_id")
        payload = msg.get("payload", {})
        url = payload.get("url", "")
        if not url:
            await self.ws.send_cmd_ack(cmd_id, "error: missing url")
            return
        try:
            config_path = self._base_dir / "config.json"
            current = {}
            if config_path.exists():
                with open(config_path) as f:
                    current = json.load(f)
            current["kiosk_url"] = url
            with open(config_path, "w") as f:
                json.dump(current, f, indent=2)
            # Try to reload chromium kiosk
            subprocess.Popen([
                "sudo", "-u", "atlas", "DISPLAY=:0",
                "xdotool", "key", "ctrl+l",
            ])
            await asyncio.sleep(0.2)
            subprocess.Popen([
                "sudo", "-u", "atlas", "DISPLAY=:0",
                "xdotool", "type", "--clearmodifiers", url,
            ])
            await asyncio.sleep(0.2)
            subprocess.Popen([
                "sudo", "-u", "atlas", "DISPLAY=:0",
                "xdotool", "key", "Return",
            ])
            logger.info("Kiosk URL changed to: %s", url)
            await self.ws.send_cmd_ack(cmd_id, "ok")
        except Exception as e:
            await self.ws.send_cmd_ack(cmd_id, f"error: {e}")

    async def _on_remote_reboot(self, msg: dict) -> None:
        """Reboot the device."""
        cmd_id = msg.get("cmd_id")
        await self.ws.send_cmd_ack(cmd_id, "rebooting")
        await asyncio.sleep(0.5)
        subprocess.Popen(["sudo", "reboot"])

    async def _on_remote_log_request(self, msg: dict) -> None:
        """Collect and upload journal logs."""
        cmd_id = msg.get("cmd_id")
        payload = msg.get("payload", {})
        lines = max(1, min(int(payload.get("lines", 100)), 5000))
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", "atlas-satellite", "-n", str(lines),
                "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            logs = stdout.decode(errors="replace")
            await self.ws.send_log_upload(cmd_id, logs)
        except Exception as e:
            await self.ws.send_cmd_ack(cmd_id, f"error: {e}")

    async def _play_test_tone(self) -> None:
        """Generate and play a short 440Hz test tone."""
        import math

        sample_rate = 16000
        duration = 1.0
        freq = 440
        n_samples = int(sample_rate * duration)
        samples = []
        for i in range(n_samples):
            t = i / sample_rate
            # Fade in/out to avoid clicks
            envelope = min(1.0, t * 10, (duration - t) * 10)
            val = int(16000 * envelope * math.sin(2 * math.pi * freq * t))
            samples.append(max(-32768, min(32767, val)))
        pcm_data = struct.pack(f"<{len(samples)}h", *samples)
        self.led.set_pattern("speaking")
        await self.audio_out.play_pcm(pcm_data, sample_rate)
        self.led.set_pattern("idle")

    async def _on_sync_fillers(self, msg: dict) -> None:
        """Sync filler phrase cache with server."""
        fillers = msg.get("fillers", [])
        if self.fillers:
            # Decode base64 audio in each filler
            decoded = []
            for f in fillers:
                decoded.append({
                    "id": f["id"],
                    "audio": base64.b64decode(f.get("audio", "")),
                })
            self.fillers.sync(decoded)
            logger.info("Synced %d filler phrases", len(decoded))

    # ── Background loops ──────────────────────────────────────────

    def _schedule_wake_initialization(self) -> None:
        if self.wake_word is not None and (self._wake_task is None or self._wake_task.done()):
            self._wake_task = asyncio.create_task(self._initialize_wake(), name="satellite-wake-loader")

    async def _initialize_wake(self) -> None:
        while self.wake_word is not None and self.wake_word.status == "loading":
            detector = self.wake_word
            await asyncio.to_thread(detector.initialize)
        if self.ws and self.ws.connected:
            self.ws.capabilities = self._detect_capabilities()
            try:
                await self.ws.send_heartbeat(readiness=self.readiness())
            except Exception:
                logger.warning("Wake readiness update could not reach the server", exc_info=True)

    def readiness(self) -> dict:
        return {
            "wake": self.wake_word.status if self.wake_word else (
                "unavailable" if self.config.wake_word_enabled else "disabled"
            ),
            "wake_error": self.wake_word.error if self.wake_word else "",
            "microphone": bool(self.audio_in and self.audio_in.available),
            "button": bool(self.button and self.button.available),
            "button_mode": self.button.mode if self.button else self.config.button_mode,
            "room": self.config.room,
            "led_count": self.config.led_count if self.config.led_type != "none" else 0,
            "state": self.state.value,
            "config_error": self._config_error,
            "restart_required": self._restart_required,
        }

    async def _server_listener(self) -> None:
        """Listen for messages from the server."""
        while self._running:
            if self.ws.connected:
                await self.ws.listen()
            await asyncio.sleep(1)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to server."""
        while self._running:
            await asyncio.sleep(30)
            if self.ws.connected:
                try:
                    await self.ws.send_heartbeat(
                        uptime=time.time() - self._start_time,
                        cpu_temp=_read_cpu_temp(),
                        wifi_rssi=_read_wifi_rssi(),
                        readiness=self.readiness(),
                    )
                except Exception:
                    logger.debug("Heartbeat send failed")

    async def _reconnect_loop(self) -> None:
        """Monitor connection and reconnect if dropped."""
        while self._running:
            await asyncio.sleep(5)
            if not self.ws.connected:
                await self._stop_playback()
                self._capture_done.set()
                if self.state != State.MUTED:
                    self.state = State.IDLE
                logger.info("Connection lost — attempting reconnect...")
                self.led.set_pattern("offline")
                success = await self.ws.connect()
                if success:
                    self.led.set_pattern("muted" if self.state == State.MUTED else "idle")
                    await self.ws.send_heartbeat(readiness=self.readiness())
                    logger.info("Reconnected to server")


# ── System info helpers ───────────────────────────────────────────


def _read_cpu_temp() -> float:
    """Read CPU temperature (Linux thermal zone)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return 0.0


def _read_wifi_rssi() -> int:
    """Read WiFi signal strength (dBm)."""
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
            if len(lines) >= 3:
                # Third line has signal level
                parts = lines[2].split()
                return int(float(parts[3]))
    except Exception:
        pass
    return 0
