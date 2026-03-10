"""WebSocket endpoint for satellite audio streaming and control.

Handles the real-time communication channel between Atlas server and
satellite devices:

  Satellite → Server:
    ANNOUNCE, WAKE, AUDIO_START, AUDIO_CHUNK, AUDIO_END, STATUS, HEARTBEAT

  Server → Satellite:
    ACCEPTED, TTS_START, TTS_CHUNK, TTS_END, PLAY_FILLER,
    COMMAND, CONFIG, SYNC_FILLERS
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import struct
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)

# STT configuration — supports "whisper_cpp" (HTTP) and "wyoming" (TCP) backends
_STT_BACKEND = os.environ.get("STT_BACKEND", "whisper_cpp")
_STT_HOST = os.environ.get("STT_HOST", "localhost")
_STT_PORT = int(os.environ.get("STT_PORT", "10300"))
_PIPER_HOST = os.environ.get("PIPER_HOST", os.environ.get("TTS_HOST", "localhost"))
_PIPER_PORT = int(os.environ.get("PIPER_PORT", os.environ.get("TTS_PORT", "10200")))

# Kokoro TTS configuration
_KOKORO_HOST = os.environ.get("KOKORO_HOST", "localhost")
_KOKORO_PORT = int(os.environ.get("KOKORO_PORT", "8880"))
_KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_bella")
_TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "orpheus")

# Orpheus TTS configuration
_ORPHEUS_URL = os.environ.get("ORPHEUS_FASTAPI_URL", "http://localhost:5005")


def _get_satellite_voice(satellite_id: str) -> str:
    """Read the configured TTS voice for a satellite from DB."""
    try:
        db = get_db()
        row = db.execute(
            "SELECT tts_voice FROM satellites WHERE id = ?", (satellite_id,)
        ).fetchone()
        return (row["tts_voice"] or "") if row else ""
    except Exception:
        return ""


def _get_system_default_voice() -> str:
    """Read the system-wide default TTS voice from settings."""
    try:
        db = get_db()
        row = db.execute(
            "SELECT value FROM system_settings WHERE key = 'default_tts_voice'"
        ).fetchone()
        return row["value"] if row else ""
    except Exception:
        return ""


def _get_user_voice(user_id: str) -> str:
    """Read the preferred TTS voice for a user from DB."""
    if not user_id:
        return ""
    try:
        db = get_db()
        row = db.execute(
            "SELECT preferred_voice FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        return (row["preferred_voice"] or "") if row else ""
    except Exception:
        return ""


def _resolve_voice(satellite_id: str, user_id: str = "") -> str:
    """Resolve the effective TTS voice using the shared helper."""
    from cortex.voice import resolve_default_voice
    return resolve_default_voice(user_id=user_id)


def _get_orpheus_provider():
    """Return the Orpheus TTS provider if configured, else None."""
    try:
        from cortex.voice.providers import get_tts_provider, _env_config
        cfg = _env_config()
        if cfg.get("TTS_PROVIDER", "orpheus").lower() == "orpheus":
            return get_tts_provider(cfg)
    except Exception:
        pass
    return None


# ── Connection registry ───────────────────────────────────────────

_connected_satellites: dict[str, SatelliteConnection] = {}


class SatelliteConnection:
    """Tracks a connected satellite's WebSocket and metadata."""

    def __init__(self, websocket: WebSocket, satellite_id: str) -> None:
        self.websocket = websocket
        self.satellite_id = satellite_id
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.session_id: str | None = None
        self.audio_buffer: bytearray = bytearray()
        self.audio_format: dict = {}
        self.has_wake_word: bool = False  # True if satellite has local wake word detection

    async def send(self, message: dict) -> None:
        """Send a JSON message to the satellite."""
        await self.websocket.send_json(message)

    async def send_command(self, action: str, params: dict | None = None) -> None:
        """Send a COMMAND message."""
        await self.send({
            "type": "COMMAND",
            "action": action,
            "params": params or {},
        })


def get_connected_satellites() -> dict[str, SatelliteConnection]:
    """Return all currently connected satellite connections."""
    return _connected_satellites.copy()


def get_connection(satellite_id: str) -> SatelliteConnection | None:
    """Get a specific satellite's connection."""
    return _connected_satellites.get(satellite_id)


# ── WebSocket handler ─────────────────────────────────────────────


async def satellite_ws_handler(websocket: WebSocket) -> None:
    """Handle a satellite WebSocket connection.

    This is the main entry point — mount it in FastAPI via:
        app.add_api_websocket_route("/ws/satellite", satellite_ws_handler)
    """
    await websocket.accept()
    satellite_id: str | None = None
    conn = SatelliteConnection(websocket, "")

    try:
        # First message must be ANNOUNCE
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
        if raw.get("type") != "ANNOUNCE":
            await websocket.send_json({"type": "ERROR", "detail": "Expected ANNOUNCE"})
            await websocket.close()
            return

        satellite_id = raw.get("satellite_id", "")
        if not satellite_id:
            await websocket.send_json({"type": "ERROR", "detail": "Missing satellite_id"})
            await websocket.close()
            return

        conn.satellite_id = satellite_id
        session_id = f"sess-{uuid.uuid4().hex[:8]}"
        conn.session_id = session_id

        # Register connection
        _connected_satellites[satellite_id] = conn

        # Extract client IP and metadata from ANNOUNCE
        client_ip = websocket.client.host if websocket.client else None
        capabilities = raw.get("capabilities") or []
        conn.has_wake_word = "wake_word" in capabilities
        _update_satellite_status(
            satellite_id, "online",
            ip_address=client_ip,
            hostname=raw.get("hostname"),
            room=raw.get("room"),
            capabilities=capabilities,
            hardware_info=raw.get("hw_info"),
        )

        # Send ACCEPTED
        await conn.send({
            "type": "ACCEPTED",
            "satellite_id": satellite_id,
            "session_id": session_id,
        })

        logger.info("Satellite connected: %s (session %s)", satellite_id, session_id)

        # Message loop
        async for raw_msg in websocket.iter_json():
            msg_type = raw_msg.get("type", "")

            if msg_type == "HEARTBEAT":
                await _handle_heartbeat(conn, raw_msg)

            elif msg_type == "WAKE":
                await _handle_wake(conn, raw_msg)

            elif msg_type == "AUDIO_START":
                await _handle_audio_start(conn, raw_msg)

            elif msg_type == "AUDIO_CHUNK":
                await _handle_audio_chunk(conn, raw_msg)

            elif msg_type == "AUDIO_END":
                await _handle_audio_end(conn, raw_msg)

            elif msg_type == "STATUS":
                await _handle_status(conn, raw_msg)

            else:
                logger.warning(
                    "Unknown message type from %s: %s", satellite_id, msg_type
                )

    except WebSocketDisconnect:
        logger.info("Satellite disconnected: %s", satellite_id)
    except asyncio.TimeoutError:
        logger.warning("Satellite connection timed out (no ANNOUNCE)")
    except Exception:
        logger.exception("Error in satellite WebSocket for %s", satellite_id)
    finally:
        if satellite_id:
            _connected_satellites.pop(satellite_id, None)
            _update_satellite_status(satellite_id, "offline")


# ── Message handlers ──────────────────────────────────────────────


async def _handle_heartbeat(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status from heartbeat."""
    conn.last_heartbeat = time.time()
    try:
        db = get_db()
        db.execute(
            """UPDATE satellites
               SET last_seen = ?, uptime_seconds = ?, wifi_rssi = ?, cpu_temp = ?
               WHERE id = ?""",
            (
                datetime.now(timezone.utc).isoformat(),
                msg.get("uptime"),
                msg.get("wifi_rssi"),
                msg.get("cpu_temp"),
                conn.satellite_id,
            ),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to update heartbeat for %s", conn.satellite_id)


async def _handle_wake(conn: SatelliteConnection, msg: dict) -> None:
    """Handle wake word detection from satellite."""
    logger.info(
        "Wake word from %s (confidence: %.2f)",
        conn.satellite_id,
        msg.get("wake_word_confidence", 0),
    )
    # Create an audio session
    session_id = f"audio-{uuid.uuid4().hex[:8]}"
    conn.session_id = session_id
    try:
        db = get_db()
        db.execute(
            "INSERT INTO satellite_audio_sessions (id, satellite_id) VALUES (?, ?)",
            (session_id, conn.satellite_id),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to create audio session")


async def _handle_audio_start(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has started from the satellite."""
    conn.audio_buffer = bytearray()
    conn.audio_format = msg.get("format_info", {"rate": 16000, "width": 2, "channels": 1})
    logger.debug("Audio start from %s (format: %s)", conn.satellite_id, msg.get("format"))


async def _handle_audio_chunk(conn: SatelliteConnection, msg: dict) -> None:
    """Receive an audio chunk from the satellite and buffer it."""
    audio_b64 = msg.get("audio", "")
    if audio_b64:
        conn.audio_buffer.extend(base64.b64decode(audio_b64))


async def _handle_audio_end(conn: SatelliteConnection, msg: dict) -> None:
    """Audio streaming has ended — run STT → Pipeline → TTS."""
    reason = msg.get("reason", "vad_silence")
    audio_data = bytes(conn.audio_buffer)
    conn.audio_buffer = bytearray()

    logger.info(
        "Audio end from %s (reason: %s, %d bytes)",
        conn.satellite_id, reason, len(audio_data),
    )

    # Auto-listen timeout means no one spoke — discard silently
    if reason == "auto_listen_timeout":
        logger.info("Auto-listen timeout from %s — no speech, discarding", conn.satellite_id)
        return

    # Update session
    if conn.session_id:
        try:
            db = get_db()
            db.execute(
                "UPDATE satellite_audio_sessions SET ended_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), conn.session_id),
            )
            db.commit()
        except Exception:
            pass

    if len(audio_data) < 1600:
        # Too short to be meaningful speech (~50ms)
        logger.debug("Audio too short (%d bytes), ignoring", len(audio_data))
        return

    # Cap audio at ~15 seconds (480000 bytes at 16kHz 16-bit mono) to prevent
    # VAD runaway and whisper hallucination on very long recordings.
    MAX_AUDIO_BYTES = 480000  # 15 seconds
    if len(audio_data) > MAX_AUDIO_BYTES:
        logger.warning(
            "Audio from %s too long (%d bytes / %.1fs), truncating to last %.0fs",
            conn.satellite_id, len(audio_data),
            len(audio_data) / 32000, MAX_AUDIO_BYTES / 32000,
        )
        audio_data = audio_data[-MAX_AUDIO_BYTES:]

    # Run the voice pipeline in a background task so websocket stays responsive
    asyncio.create_task(_process_voice_pipeline(conn, audio_data))


async def _handle_status(conn: SatelliteConnection, msg: dict) -> None:
    """Update satellite status (idle, listening, speaking)."""
    status = msg.get("status", "idle")
    logger.debug("Satellite %s status: %s", conn.satellite_id, status)


# ── Voice pipeline ────────────────────────────────────────────────


def _is_hallucinated(transcript: str) -> bool:
    """Detect whisper hallucination patterns (repeated phrases, noise)."""
    # Common whisper hallucinations on silence/noise — check these FIRST
    # regardless of word count, since many are just 1-2 words.
    lower = transcript.lower().strip().rstrip(".")
    hallucination_exact = {
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "transcription by castingwords",
        "subtitles by the amara.org community",
        "you",
        "...",
        "okay",
        "thank you",
        "thanks",
        "bye",
        "goodbye",
        "hmm",
    }
    if lower in hallucination_exact:
        return True

    words = transcript.split()
    # Check for repeated short phrases (e.g. "Okay. Okay. Okay.")
    segments = [s.strip() for s in transcript.replace("\n", " ").split(".") if s.strip()]
    if len(segments) >= 4:
        unique = set(s.lower() for s in segments)
        if len(unique) <= 2:
            return True
    # Whisper commonly hallucinates "I'm going to go..." on noise
    hallucination_prefixes = (
        "i'm going to go",
        "i'm going to get",
        "i'm going to do",
        "i'm going to take",
        "i'm going to have",
        "so i'm going to",
        "and i'm going to",
    )
    if lower.startswith(hallucination_prefixes):
        return True
    return False


# Generic LLM "help offer" closers that should NOT trigger auto-listen.
_HELP_OFFER_PATTERNS = (
    "what can i help",
    "how can i help",
    "how can i assist",
    "what would you like",
    "what do you need",
    "what information are you looking for",
    "what are you looking for",
    "what else can i",
    "anything else",
    "is there anything else",
    "what's the next question",
    "what's your next question",
    "need help with anything",
    "what topic",
    "what question",
    "where would you like to go",
    "what can i do for you",
    "how may i help",
    "what would you like to know",
    "what do you want to know",
    "what specific",
)


def _is_help_offer(sentence: str) -> bool:
    """Return True if sentence is a generic LLM help-offer closer."""
    lower = sentence.lower().strip()
    return any(lower.startswith(p) or p in lower for p in _HELP_OFFER_PATTERNS)


_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

# Sentence boundary for streaming: punctuation followed by whitespace
_STREAM_SENT_RE = re.compile(r'[.!?]\s+')


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences at punctuation boundaries.

    Keeps short fragments together to avoid tiny TTS calls.
    Minimum sentence length ~20 chars before splitting.
    """
    raw = _SENTENCE_RE.split(text.strip())
    if not raw:
        return [text.strip()] if text.strip() else []

    sentences: list[str] = []
    buf = ""
    for part in raw:
        buf = (buf + " " + part).strip() if buf else part
        if len(buf) >= 20:
            sentences.append(buf)
            buf = ""
    if buf:
        if sentences:
            sentences[-1] = sentences[-1] + " " + buf
        else:
            sentences.append(buf)
    return sentences


def _extract_pcm(raw_audio: bytes, default_rate: int = 24000) -> tuple[bytes, int]:
    """Extract PCM data and sample rate from WAV or raw audio."""
    if raw_audio and raw_audio[:4] == b"RIFF":
        import wave, io
        with wave.open(io.BytesIO(raw_audio), "rb") as wf:
            return wf.readframes(wf.getnframes()), wf.getframerate()
    return raw_audio, default_rate


async def _synthesize_text(text: str, voice: str, *, fast: bool = False) -> tuple[bytes, int, str]:
    """Synthesize text to PCM audio using available TTS providers.

    Returns (pcm_audio, sample_rate, provider_name).
    Provider priority: Orpheus (GPU) → Kokoro → Piper.
    When fast=True, prefer Kokoro (CPU, ~200ms) over Orpheus (GPU, ~5s)
    for latency-sensitive paths like instant answers and fillers.
    """
    from cortex.voice.wyoming import WyomingClient, WyomingError

    # --- Fast path: Kokoro first for instant answers/fillers ---
    if fast or _TTS_PROVIDER == "kokoro":
        try:
            from cortex.voice.kokoro import KokoroClient
            kokoro = KokoroClient(_KOKORO_HOST, _KOKORO_PORT, timeout=15.0)
            kokoro_voice = voice if voice and not voice.startswith("orpheus_") else _KOKORO_VOICE
            raw, info = await kokoro.synthesize(text, voice=kokoro_voice, response_format="wav")
            if raw:
                pcm, rate = _extract_pcm(raw, info.get("rate", 24000))
                return pcm, rate, "kokoro"
        except Exception as e:
            logger.warning("Kokoro TTS failed: %s", e)

    # --- Orpheus (CUDA GPU, higher quality) ---
    if _TTS_PROVIDER in ("orpheus", "auto"):
        try:
            import aiohttp
            bare_voice = (voice or "tara").replace("orpheus_", "")
            payload = {
                "input": text,
                "model": "orpheus",
                "voice": bare_voice,
                "response_format": "wav",
                "stream": False,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_ORPHEUS_URL}/v1/audio/speech",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if raw:
                            pcm, rate = _extract_pcm(raw, 24000)
                            return pcm, rate, "orpheus"
                    else:
                        error = await resp.text()
                        logger.warning("Orpheus TTS failed (%d): %s", resp.status, error[:200])
        except Exception as e:
            logger.warning("Orpheus TTS failed: %s", e)

    # --- Kokoro (fallback if Orpheus fails) ---
    if not fast:
        try:
            from cortex.voice.kokoro import KokoroClient
            kokoro = KokoroClient(_KOKORO_HOST, _KOKORO_PORT, timeout=15.0)
            kokoro_voice = voice if voice and not voice.startswith("orpheus_") else _KOKORO_VOICE
            raw, info = await kokoro.synthesize(text, voice=kokoro_voice, response_format="wav")
            if raw:
                pcm, rate = _extract_pcm(raw, info.get("rate", 24000))
                return pcm, rate, "kokoro"
        except Exception as e:
            logger.warning("Kokoro TTS failed: %s", e)

    # --- Piper (last resort) ---
    try:
        piper = WyomingClient(_PIPER_HOST, _PIPER_PORT, timeout=15.0)
        piper_voice = voice if voice and not voice.startswith("orpheus_") else None
        audio, info = await piper.synthesize(text, voice=piper_voice)
        return audio, info.get("rate", 22050), "piper"
    except Exception as e:
        logger.warning("Piper TTS failed: %s", e)

    return b"", 24000, "none"


async def _stream_audio_to_satellite(
    conn: SatelliteConnection, audio: bytes, rate: int,
    text: str, is_filler: bool = False, auto_listen: bool = False,
) -> None:
    """Stream PCM audio to a satellite as TTS_START/CHUNK/END."""
    await conn.send({
        "type": "TTS_START",
        "session_id": conn.session_id,
        "format": f"pcm_{rate // 1000}k_16bit_mono",
        "sample_rate": rate,
        "text": text,
        "is_filler": is_filler,
    })
    chunk_size = 4096
    for offset in range(0, len(audio), chunk_size):
        chunk = audio[offset:offset + chunk_size]
        await conn.send({
            "type": "TTS_CHUNK",
            "session_id": conn.session_id,
            "audio": base64.b64encode(chunk).decode("ascii"),
        })
    msg: dict[str, Any] = {
        "type": "TTS_END",
        "session_id": conn.session_id,
        "is_filler": is_filler,
    }
    if auto_listen:
        msg["auto_listen"] = True
    await conn.send(msg)


async def _process_voice_pipeline(conn: SatelliteConnection, audio_data: bytes) -> None:
    """Full STT → Pipeline → TTS → stream back to satellite."""

    satellite_id = conn.satellite_id
    t_start = time.monotonic()

    # Reject very short audio clips — likely echo/noise, not real speech.
    # 16kHz 16-bit mono = 32000 bytes/sec. Minimum ~1.5s of audio needed.
    min_audio_bytes = 48000  # ~1.5s
    if len(audio_data) < min_audio_bytes:
        audio_sec = len(audio_data) / 32000
        logger.info("Audio too short from %s (%.1fs, %d bytes) — dropping",
                     satellite_id, audio_sec, len(audio_data))
        try:
            await conn.send({"type": "PIPELINE_ERROR", "detail": "Audio too short"})
        except Exception:
            pass
        return

    # Always import Wyoming for Piper TTS (filler + fallback)
    from cortex.voice.wyoming import WyomingClient, WyomingError

    try:
        # ── Step 1: STT ──────────────────────────────────────────
        t_stt_start = time.monotonic()
        logger.info("Running STT on %d bytes (%.1fs audio) from %s (backend=%s)",
                     len(audio_data), len(audio_data) / 32000,
                     satellite_id, _STT_BACKEND)

        try:
            if _STT_BACKEND == "whisper_cpp":
                from cortex.voice.whisper_cpp import WhisperCppClient, WhisperCppError
                stt_client = WhisperCppClient(_STT_HOST, _STT_PORT, timeout=60.0)
                transcript = await stt_client.transcribe(audio_data, sample_rate=16000)
            else:
                from cortex.voice.wyoming import WyomingClient, WyomingError as _WErr
                stt_client = WyomingClient(_STT_HOST, _STT_PORT, timeout=30.0)
                transcript = await stt_client.transcribe(audio_data, sample_rate=16000)
        except Exception as e:
            logger.error("STT failed for %s: %s", satellite_id, e)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": f"STT failed: {e}"})
            except Exception:
                pass
            return

        transcript = transcript.strip()
        t_stt_end = time.monotonic()
        stt_ms = (t_stt_end - t_stt_start) * 1000

        if not transcript:
            logger.info("Empty transcript from %s (STT took %.0fms), ignoring", satellite_id, stt_ms)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "Empty transcript"})
            except Exception:
                pass
            return

        # Guard against whisper hallucination (repeated noise patterns)
        if _is_hallucinated(transcript):
            logger.warning("Hallucinated transcript from %s (%.0fms): %r — dropping",
                           satellite_id, stt_ms, transcript[:100])
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "No clear speech detected"})
            except Exception:
                pass
            return

        logger.info("STT result from %s (%.0fms): %r", satellite_id, stt_ms, transcript)

        # Server-side wake word filter for satellites without local wake word.
        # If the satellite has no openwakeword, VAD triggers on ANY speech.
        # We check the transcript for the wake word before running the pipeline.
        if not conn.has_wake_word:
            wake_keywords = {"atlas", "atmos", "alice"}  # "atmos"/"alice" = common whisper mishears
            transcript_lower = transcript.lower()
            has_keyword = any(kw in transcript_lower for kw in wake_keywords)
            if not has_keyword:
                logger.info("No wake keyword in transcript from %s (no local wake word), dropping: %r",
                            satellite_id, transcript[:80])
                try:
                    await conn.send({"type": "PIPELINE_ERROR", "detail": "No wake word detected"})
                except Exception:
                    pass
                return
            # Strip the wake word from the transcript so LLM gets clean input
            for kw in sorted(wake_keywords, key=len, reverse=True):
                idx = transcript_lower.find(kw)
                if idx != -1:
                    # Remove wake word and surrounding "hey", "ok", etc.
                    prefix = transcript[:idx].strip().lower()
                    clean_prefixes = {"hey", "ok", "okay", "hi", "yo", ""}
                    if prefix in clean_prefixes:
                        transcript = transcript[idx + len(kw):].strip()
                    else:
                        transcript = (transcript[:idx] + transcript[idx + len(kw):]).strip()
                    break
            # After stripping, if nothing left, ignore
            if not transcript:
                logger.info("Transcript empty after wake word removal from %s", satellite_id)
                try:
                    await conn.send({"type": "PIPELINE_ERROR", "detail": "Empty after wake word"})
                except Exception:
                    pass
                return
            logger.info("After wake word filter from %s: %r", satellite_id, transcript)

        # ── Step 2: Pipeline (filler-first streaming) ─────────────
        t_llm_start = time.monotonic()
        from cortex.providers import get_provider
        from cortex.pipeline import run_pipeline

        provider = get_provider()

        pipeline_gen = await run_pipeline(
            message=transcript,
            provider=provider,
            satellite_id=satellite_id,
            model_fast="qwen2.5:7b",
        )

        # The pipeline yields tokens: Layer 1/2 yield a single complete answer,
        # while Layer 3 yields a filler phrase first, then LLM tokens.
        # We peek ahead: collect up to 2 tokens to distinguish instant answers
        # (single token) from LLM streaming (filler + tokens).
        filler_text = ""
        first_token = True
        tts_voice = _resolve_voice(satellite_id)
        token_buf = ""
        response_parts: list[str] = []
        sentences_sent = 0
        total_tts_bytes = 0
        tts_used = "none"
        _filler_task: asyncio.Task | None = None

        # Collect tokens: first token is either the complete instant answer
        # or a filler phrase. We need at least 2 tokens to distinguish.
        tokens = []
        async for token in pipeline_gen:
            tokens.append(token)
            if len(tokens) == 1:
                # Got first token — try to get a second to peek ahead
                continue
            break  # Got 2 tokens or generator exhausted

        if len(tokens) == 1 and tokens[0].strip():
            # ── Instant answer (Layer 1/2): single token IS the answer ──
            instant_text = tokens[0].strip()
            t_tts = time.monotonic()
            audio, rate, provider = await _synthesize_text(instant_text, tts_voice, fast=True)
            tts_ms = (time.monotonic() - t_tts) * 1000
            if audio:
                logger.info("Instant TTS [%s] %.0fms (%d bytes): %r",
                            provider, tts_ms, len(audio), instant_text[:60])
                await _stream_audio_to_satellite(
                    conn, audio, rate, instant_text, is_filler=False,
                    auto_listen=instant_text.rstrip().endswith("?"),
                )
                total_tts_bytes = len(audio)
                tts_used = provider
                sentences_sent = 1
            else:
                # TTS failed — send TTS_END so satellite isn't stuck
                await conn.send({
                    "type": "TTS_END",
                    "session_id": conn.session_id,
                    "is_filler": False,
                })

            t_total = time.monotonic() - t_stt_start
            logger.info("Pipeline complete for %s: total=%.1fs (STT=%.0fms instant [%s] %d bytes)",
                        satellite_id, t_total, stt_ms, tts_used, total_tts_bytes)
            return

        # ── LLM streaming path: first token is filler, rest are response ──
        for i, token in enumerate(tokens):
            if i == 0:
                filler_text = token.strip()
                if filler_text:
                    from cortex.filler.cache import get_filler_cache
                    cache = get_filler_cache()

                    async def _do_filler(text: str = filler_text) -> None:
                        await asyncio.sleep(0.35)  # natural thinking pause
                        try:
                            cached = cache.get("question") if cache.ready else None
                            if cached:
                                logger.info(
                                    "Cached filler for %s: %r (%.1fs, %d bytes)",
                                    satellite_id, cached.phrase,
                                    cached.duration_ms / 1000, len(cached.audio))
                                await _stream_audio_to_satellite(
                                    conn, cached.audio, cached.sample_rate,
                                    cached.phrase, is_filler=True)
                            else:
                                audio, rate, prov = await _synthesize_text(
                                    text, tts_voice, fast=True)
                                if audio:
                                    logger.info(
                                        "Filler TTS for %s: %r (%.0fms, %d bytes)",
                                        satellite_id, text,
                                        (time.monotonic() - t_llm_start) * 1000,
                                        len(audio))
                                    await _stream_audio_to_satellite(
                                        conn, audio, rate, text, is_filler=True)
                        except Exception as e:
                            logger.warning("Filler failed: %s", e)

                    _filler_task = asyncio.create_task(_do_filler())
            else:
                response_parts.append(token)
                token_buf += token

        async for token in pipeline_gen:
            response_parts.append(token)
            token_buf += token

            # Stream complete sentences as they arrive from the LLM.
            # _STREAM_SENT_RE matches sentence-ending punctuation followed
            # by whitespace, confirming the sentence is truly finished.
            while True:
                m = _STREAM_SENT_RE.search(token_buf)
                if not m:
                    break
                sentence = token_buf[:m.end()].strip()
                token_buf = token_buf[m.end():]
                if len(sentence) < 20:
                    # Too short — prepend to next sentence
                    token_buf = sentence + " " + token_buf
                    break
                t_sent = time.monotonic()
                audio, rate, provider = await _synthesize_text(sentence, tts_voice)
                sent_ms = (time.monotonic() - t_sent) * 1000
                if audio:
                    logger.info("Sentence TTS [%s] %.0fms (%d bytes): %r",
                                provider, sent_ms, len(audio), sentence[:60])
                    await _stream_audio_to_satellite(conn, audio, rate, sentence, is_filler=True)
                    sentences_sent += 1
                    total_tts_bytes += len(audio)
                    tts_used = provider

        full_response = "".join(response_parts).strip()
        t_llm_end = time.monotonic()
        llm_ms = (t_llm_end - t_llm_start) * 1000

        # Ensure background filler task is done before sending final response
        if _filler_task and not _filler_task.done():
            await _filler_task

        if not full_response:
            # Instant answers are handled above (single-token early return).
            # If we get here with no response, something went wrong.
            logger.warning("Empty pipeline response for %r (LLM took %.0fms)", transcript, llm_ms)
            return

        logger.info("Pipeline response for %s (LLM %.0fms): %r", satellite_id, llm_ms, full_response[:200])

        # ── Step 3: Final sentence + auto-listen ─────────────────
        # Only auto-listen for DIRECT questions to the user (e.g. "Would you
        # like me to set a timer?"), NOT generic LLM help-offer closers like
        # "What can I help you with?" or "What information are you looking for?"
        last_sentence = full_response.rstrip().rsplit(".", 1)[-1].strip()
        is_question = (
            last_sentence.endswith("?")
            and len(last_sentence) < 100
            and not last_sentence.lower().startswith(("i wonder", "who knows"))
            and not _is_help_offer(last_sentence)
        )

        if token_buf.strip():
            t_sent = time.monotonic()
            audio, rate, provider = await _synthesize_text(token_buf.strip(), tts_voice)
            sent_ms = (time.monotonic() - t_sent) * 1000
            if audio:
                logger.info("Final sentence TTS [%s] %.0fms (%d bytes): %r",
                            provider, sent_ms, len(audio), token_buf.strip()[:60])
                await _stream_audio_to_satellite(
                    conn, audio, rate, token_buf.strip(),
                    is_filler=False, auto_listen=is_question,
                )
                total_tts_bytes += len(audio)
                tts_used = provider or tts_used
                sentences_sent += 1
        elif sentences_sent > 0:
            # All text was already sent as intermediate sentences.
            # Send a zero-length final TTS_END to signal completion.
            msg: dict[str, Any] = {
                "type": "TTS_END",
                "session_id": conn.session_id,
                "is_filler": False,
            }
            if is_question:
                msg["auto_listen"] = True
            await conn.send(msg)
        else:
            logger.warning("No sentences synthesized for %s", satellite_id)
            return

        # Auto-listen is handled via auto_listen flag in TTS_END;
        # no separate COMMAND needed.
        if is_question:
            logger.info("Response was a question — auto_listen flag set for %s", satellite_id)

        t_total = time.monotonic() - t_start
        logger.info(
            "Pipeline complete for %s: total=%.1fs (STT=%.0fms LLM=%.0fms %d sentences [%s] %d bytes)",
            satellite_id, t_total, stt_ms, llm_ms, sentences_sent, tts_used, total_tts_bytes,
        )

    except WebSocketDisconnect:
        logger.warning("Satellite %s disconnected during pipeline", satellite_id)
    except Exception:
        logger.exception("Voice pipeline error for %s", satellite_id)
        # Notify satellite to return to IDLE on failure
        try:
            await conn.send({"type": "PIPELINE_ERROR", "detail": "Voice pipeline failed"})
        except Exception:
            pass


def _resample_pcm(data: bytes, src_rate: int, dst_rate: int, channels: int = 1) -> bytes:
    """Simple linear interpolation resampling for 16-bit PCM."""
    if src_rate == dst_rate:
        return data
    samples_per_frame = channels
    n_frames = len(data) // (2 * samples_per_frame)
    if n_frames == 0:
        return data

    # Decode to samples (mono for simplicity)
    if channels > 1:
        # Mix to mono first
        all_samples = struct.unpack(f"<{n_frames * channels}h", data[:n_frames * channels * 2])
        mono = []
        for i in range(0, len(all_samples), channels):
            mono.append(sum(all_samples[i:i+channels]) // channels)
    else:
        mono = list(struct.unpack(f"<{n_frames}h", data[:n_frames * 2]))

    # Resample via linear interpolation
    ratio = src_rate / dst_rate
    new_len = int(n_frames / ratio)
    resampled = []
    for i in range(new_len):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx
        if idx + 1 < len(mono):
            val = int(mono[idx] * (1 - frac) + mono[idx + 1] * frac)
        else:
            val = mono[min(idx, len(mono) - 1)]
        resampled.append(max(-32768, min(32767, val)))

    return struct.pack(f"<{len(resampled)}h", *resampled)


# ── Helpers ───────────────────────────────────────────────────────


def _update_satellite_status(
    satellite_id: str,
    status: str,
    ip_address: str | None = None,
    hostname: str | None = None,
    room: str | None = None,
    capabilities: list | None = None,
    hardware_info: dict | None = None,
) -> None:
    """Update the satellite status in the database (upsert)."""
    try:
        import json as _json

        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        caps_json = _json.dumps(capabilities) if capabilities else None
        hw_json = _json.dumps(hardware_info) if hardware_info else None

        db.execute(
            """INSERT INTO satellites (id, display_name, status, last_seen,
                   ip_address, hostname, room, capabilities, hardware_info)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   status = ?,
                   last_seen = ?,
                   ip_address = COALESCE(?, ip_address),
                   hostname = COALESCE(?, hostname),
                   room = COALESCE(?, room),
                   capabilities = COALESCE(?, capabilities),
                   hardware_info = COALESCE(?, hardware_info)""",
            (
                satellite_id, satellite_id, status, now,
                ip_address, hostname, room, caps_json, hw_json,
                status, now,
                ip_address, hostname, room, caps_json, hw_json,
            ),
        )
        db.commit()
    except Exception:
        logger.exception("Failed to update satellite status: %s", satellite_id)


# ── Utility functions for sending to satellites ───────────────────


async def send_play_filler(satellite_id: str) -> bool:
    """Tell a satellite to play a cached filler phrase."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send({"type": "PLAY_FILLER", "session_id": conn.session_id})
        return True
    return False


async def send_command(satellite_id: str, action: str, params: dict | None = None) -> bool:
    """Send a command to a connected satellite."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send_command(action, params)
        return True
    return False


async def send_config(satellite_id: str, config: dict) -> bool:
    """Push configuration to a connected satellite."""
    conn = _connected_satellites.get(satellite_id)
    if conn:
        await conn.send({"type": "CONFIG", **config})
        return True
    return False
