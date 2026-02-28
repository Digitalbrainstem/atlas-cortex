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
import logging
import os
import time
from pathlib import Path
from typing import Optional

from .audio import AudioCapture, AudioPlayback
from .config import SatelliteConfig
from .filler_cache import FillerCache
from .led import LEDController, create_led
from .mdns import SatelliteAnnouncer, ServerDiscovery
from .vad import VoiceActivityDetector
from .wake_word import WakeWordDetector
from .ws_client import SatelliteWSClient

logger = logging.getLogger(__name__)


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

        self._base_dir = base
        self._tts_buffer = bytearray()
        self._tts_sample_rate = 22050
        self._server_url_discovered = asyncio.Event()

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

        self.led.set_pattern("idle")

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

        if self.audio_in:
            self.audio_in.stop()
        if self.announcer:
            self.announcer.stop()
        if self.server_discovery:
            self.server_discovery.stop()
        if self.led:
            self.led.close()
        if self.ws:
            await self.ws.disconnect()

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
        )

        # Wake word (optional)
        self.wake_word = None
        if cfg.wake_word_enabled:
            self.wake_word = WakeWordDetector(
                wake_word=cfg.wake_word,
                threshold=cfg.wake_word_threshold,
                model_path=cfg.wake_word_model,
            )
            if not self.wake_word.available:
                logger.warning("Wake word requested but not available — using VAD-only")
                self.wake_word = None

        # LED
        self.led = create_led(cfg.led_type, cfg.led_count)

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

        # Start audio capture
        try:
            self.audio_in.start()
        except Exception:
            logger.exception("Failed to start audio capture")
            self.state = State.ERROR

    def _detect_capabilities(self) -> list[str]:
        """Detect what this satellite can do."""
        caps = ["audio_capture", "audio_playback"]
        if self.config.wake_word_enabled:
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
        self.ws.on("CONFIG", self._on_config)
        self.ws.on("SYNC_FILLERS", self._on_sync_fillers)

    # ── Audio loop ────────────────────────────────────────────────

    async def _audio_loop(self) -> None:
        """Main audio processing loop — runs every chunk_ms."""
        loop = asyncio.get_event_loop()

        while self._running:
            if self.state in (State.ERROR, State.MUTED):
                await asyncio.sleep(0.1)
                continue

            # Read audio from mic (blocking call in executor)
            audio = await loop.run_in_executor(None, self.audio_in.read)
            if audio is None:
                await asyncio.sleep(0.01)
                continue

            if self.state == State.IDLE:
                await self._process_idle(audio)
            elif self.state == State.LISTENING:
                await self._process_listening(audio)
            # PROCESSING and SPEAKING are driven by server messages

    async def _process_idle(self, audio: bytes) -> None:
        """In IDLE state: check for wake word or VAD speech start."""
        if self.wake_word:
            # Wake word mode
            confidence = self.wake_word.process(audio)
            if confidence >= self.config.wake_word_threshold:
                logger.info("Wake word detected (confidence: %.2f)", confidence)
                await self._transition_to_listening(confidence)
        else:
            # VAD-only mode: speech_start triggers listening
            result = self.vad.process(audio)
            if result == "speech_start":
                logger.info("Speech detected (VAD-only mode)")
                await self._transition_to_listening(1.0)

    async def _process_listening(self, audio: bytes) -> None:
        """In LISTENING state: stream audio, check for end of speech."""
        if self.ws.connected:
            await self.ws.send_audio_chunk(audio)

        result = self.vad.process(audio)
        if result == "speech_end":
            logger.info("Speech ended (VAD silence)")
            await self._transition_to_processing()

    async def _transition_to_listening(self, confidence: float) -> None:
        self.state = State.LISTENING
        self.led.set_pattern("listening")
        self.vad.reset()

        if self.ws.connected:
            await self.ws.send_wake(confidence)
            await self.ws.send_audio_start()
            await self.ws.send_status("listening")

    async def _transition_to_processing(self) -> None:
        self.state = State.PROCESSING
        self.led.set_pattern("thinking")

        if self.ws.connected:
            await self.ws.send_audio_end("vad_silence")

    # ── Server message handlers ───────────────────────────────────

    async def _on_tts_start(self, msg: dict) -> None:
        """Server is about to send TTS audio."""
        self._tts_buffer.clear()
        fmt = msg.get("format", "pcm_22k_16bit_mono")
        if "22k" in fmt:
            self._tts_sample_rate = 22050
        elif "16k" in fmt:
            self._tts_sample_rate = 16000
        elif "44k" in fmt:
            self._tts_sample_rate = 44100

    async def _on_tts_chunk(self, msg: dict) -> None:
        """Received a chunk of TTS audio."""
        audio_b64 = msg.get("audio", "")
        if audio_b64:
            self._tts_buffer.extend(base64.b64decode(audio_b64))

    async def _on_tts_end(self, msg: dict) -> None:
        """TTS stream complete — play the buffered audio."""
        self.state = State.SPEAKING
        self.led.set_pattern("speaking")

        if self._tts_buffer:
            await self.audio_out.play_pcm(
                bytes(self._tts_buffer), self._tts_sample_rate
            )
            self._tts_buffer.clear()

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

    async def _on_command(self, msg: dict) -> None:
        """Execute a command from the server."""
        action = msg.get("action", "")
        params = msg.get("params", {})

        if action == "listen":
            await self._transition_to_listening(1.0)
        elif action == "stop":
            self.state = State.IDLE
            self.led.set_pattern("idle")
        elif action == "volume":
            self.config.volume = float(params.get("level", self.config.volume))
            self.audio_out.volume = self.config.volume
        elif action == "led":
            self.led.set_pattern(params.get("pattern", "idle"))
        elif action == "mute":
            self.state = State.MUTED
            self.led.set_pattern("muted")
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
        else:
            logger.warning("Unknown command: %s", action)

    async def _on_config(self, msg: dict) -> None:
        """Apply pushed configuration from server."""
        if "wake_word" in msg:
            self.config.wake_word = msg["wake_word"]
        if "volume" in msg:
            self.config.volume = msg["volume"]
            self.audio_out.volume = msg["volume"]
        if "led_brightness" in msg:
            pass  # TODO: apply brightness
        if "vad_sensitivity" in msg:
            self.config.vad_sensitivity = msg["vad_sensitivity"]
        if "features" in msg:
            self.config.features = msg["features"]
        logger.info("Config updated from server")

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
                    )
                except Exception:
                    logger.debug("Heartbeat send failed")

    async def _reconnect_loop(self) -> None:
        """Monitor connection and reconnect if dropped."""
        while self._running:
            await asyncio.sleep(5)
            if not self.ws.connected:
                logger.info("Connection lost — attempting reconnect...")
                self.led.set_color(255, 165, 0, 0.2)
                success = await self.ws.connect()
                if success:
                    self.led.set_pattern("idle")
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
