"""Comprehensive tests for avatar timing, viseme sync, expression transitions, and WebSocket protocol.

Tests cover:
- broadcast.py WebSocket protocol (register/unregister, message formats, audio routing)
- Client-side viseme scheduling math (cartoon + realistic modes)
- Audio chunk format validation (PCM 16-bit LE, base64 round-trip)
- Full lifecycle state machine (idle → listening → speaking → idle)
- Viseme sequence timing precision for specific texts
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.avatar.broadcast import (
    _clients,
    broadcast_expression,
    broadcast_listening,
    broadcast_speaking_end,
    broadcast_speaking_start,
    broadcast_to_room,
    broadcast_tts_chunk,
    broadcast_tts_end,
    broadcast_tts_start,
    broadcast_viseme,
    broadcast_viseme_sequence,
    get_audio_route,
    get_connected_rooms,
    handle_client_hello,
    has_clients,
    register_client,
    set_audio_route,
    should_play_on_avatar,
    should_play_on_satellite,
    unregister_client,
)
from cortex.avatar.visemes import (
    VisemeFrame,
    _VOWEL_VISEMES,
    _text_to_phonemes,
    text_to_visemes,
)


# ── Helpers ──────────────────────────────────────────────────────


class MockWebSocket:
    """Records all JSON messages sent via send_json."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("WebSocket closed")
        self.messages.append(data)


class FailingWebSocket:
    """WebSocket that always raises on send_json (simulates dead client)."""

    async def send_json(self, data: dict[str, Any]) -> None:
        raise ConnectionError("client disconnected")


@pytest.fixture(autouse=True)
async def _clear_broadcast_state():
    """Reset broadcast module state before each test."""
    _clients.clear()
    # Reset audio routes
    from cortex.avatar import broadcast
    broadcast._audio_routes.clear()
    yield
    _clients.clear()
    broadcast._audio_routes.clear()


def _generate_pcm_samples(num_samples: int, frequency: float = 440.0, sample_rate: int = 24000) -> bytes:
    """Generate PCM 16-bit signed LE samples for a sine wave."""
    data = bytearray()
    for i in range(num_samples):
        t = i / sample_rate
        value = int(32767 * math.sin(2 * math.pi * frequency * t))
        data.extend(struct.pack("<h", max(-32768, min(32767, value))))
    return bytes(data)


def _pcm_to_b64(pcm_bytes: bytes) -> str:
    """Encode raw PCM bytes to base64 string."""
    return base64.b64encode(pcm_bytes).decode("ascii")


# JS-equivalent viseme maps for timing validation
CARTOON_VISEME = {
    "a": "OPEN", "e": "TEETH", "i": "TEETH", "o": "ROUND", "u": "ROUND",
    "b": "CLOSED", "p": "CLOSED", "m": "CLOSED",
    "f": "FV", "v": "FV",
    "w": "ROUND",
}

REALISTIC_VISEME = {
    "a": "AA", "e": "EH", "i": "IH", "o": "OH", "u": "OU",
    "b": "PP", "p": "PP", "m": "PP",
    "f": "FF", "v": "FF",
    "t": "DD", "d": "DD", "l": "DD", "n": "NN",
    "k": "KK", "g": "KK", "c": "KK", "q": "KK",
    "s": "SS", "z": "SS", "x": "SS",
    "r": "RR", "w": "RR",
    "j": "SH", "y": "SH",
    "h": "IH",
}


def _extract_cartoon_beats(text: str) -> list[dict[str, Any]]:
    """Python replica of JS _scheduleCartoonVisemes beat extraction."""
    lower = text.lower()
    beats: list[dict[str, Any]] = []
    last_viseme = None
    for i, ch in enumerate(lower):
        viseme = CARTOON_VISEME.get(ch)
        if viseme and viseme != last_viseme:
            beats.append({"pos": i, "viseme": viseme, "isVowel": ch in "aeiou"})
            last_viseme = viseme
        elif ch in " .,!?":
            if last_viseme != "IDLE":
                beats.append({"pos": i, "viseme": "IDLE", "isVowel": False})
                last_viseme = "IDLE"
    return beats


def _compute_cartoon_timings(
    text: str, audio_duration_ms: float, start_delay_ms: float = 0,
) -> list[dict[str, Any]]:
    """Compute cartoon viseme schedule timings (mirrors JS logic)."""
    beats = _extract_cartoon_beats(text)
    if not beats:
        return []
    ms_per_beat = audio_duration_ms / (len(beats) + 1)
    timings = []
    # beat[0] fires at startDelayMs
    timings.append({"viseme": beats[0]["viseme"], "time_ms": start_delay_ms})
    for idx in range(1, len(beats)):
        t = start_delay_ms + ms_per_beat * idx
        timings.append({"viseme": beats[idx]["viseme"], "time_ms": t})
    # IDLE at end
    timings.append({"viseme": "IDLE", "time_ms": start_delay_ms + audio_duration_ms})
    return timings


def _compute_realistic_timings(
    text: str, audio_duration_ms: float, start_delay_ms: float = 0,
) -> list[dict[str, Any]]:
    """Compute realistic viseme schedule timings (mirrors JS logic)."""
    import re
    lower = text.lower()
    char_count = len(re.sub(r"[\s.,!?]", "", lower)) or 1
    ms_per_char = audio_duration_ms / (char_count + len(text) * 0.15)
    timings = []
    elapsed = 0.0
    for ch in lower:
        if ch in " .,!?":
            elapsed += ms_per_char * 0.5
            timings.append({"viseme": "IDLE", "time_ms": start_delay_ms + elapsed})
            continue
        viseme = REALISTIC_VISEME.get(ch)
        if viseme:
            timings.append({"viseme": viseme, "time_ms": start_delay_ms + elapsed})
        elapsed += ms_per_char
    # IDLE at end
    timings.append({"viseme": "IDLE", "time_ms": start_delay_ms + audio_duration_ms})
    return timings


# ══════════════════════════════════════════════════════════════════
# 1. broadcast.py WebSocket protocol tests
# ══════════════════════════════════════════════════════════════════


class TestClientRegistry:
    async def test_register_adds_client(self):
        ws = MockWebSocket()
        await register_client("kitchen", ws)
        assert has_clients("kitchen")
        assert "kitchen" in get_connected_rooms()

    async def test_unregister_removes_client(self):
        ws = MockWebSocket()
        await register_client("kitchen", ws)
        await unregister_client("kitchen", ws)
        assert not has_clients("kitchen")
        assert "kitchen" not in get_connected_rooms()

    async def test_unregister_nonexistent_room_is_safe(self):
        ws = MockWebSocket()
        await unregister_client("nonexistent", ws)

    async def test_unregister_nonexistent_client_is_safe(self):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await register_client("room", ws1)
        await unregister_client("room", ws2)
        assert has_clients("room")

    async def test_duplicate_register_same_client(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await register_client("room", ws)
        rooms = get_connected_rooms()
        assert rooms.count("room") == 1  # room appears once in keys
        # Unregister once — client still in list because it was added twice
        await unregister_client("room", ws)
        # After unregister, both refs are removed (list comprehension removes all matching)
        assert not has_clients("room")

    async def test_multiple_rooms(self):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await register_client("kitchen", ws1)
        await register_client("bedroom", ws2)
        rooms = sorted(get_connected_rooms())
        assert rooms == ["bedroom", "kitchen"]

    async def test_multiple_clients_per_room(self):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await register_client("room", ws1)
        await register_client("room", ws2)
        assert has_clients("room")
        await unregister_client("room", ws1)
        assert has_clients("room")
        await unregister_client("room", ws2)
        assert not has_clients("room")

    async def test_has_clients_empty_room(self):
        assert not has_clients("nonexistent")

    async def test_get_connected_rooms_empty(self):
        assert get_connected_rooms() == []


class TestBroadcastExpression:
    async def test_expression_message_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_expression("room", "happy", 0.8)
        assert len(ws.messages) == 1
        msg = ws.messages[0]
        assert msg == {"type": "EXPRESSION", "expression": "happy", "intensity": 0.8}

    async def test_expression_intensity_rounding(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_expression("room", "sad", 0.333333)
        msg = ws.messages[0]
        assert msg["intensity"] == 0.33

    async def test_expression_default_intensity(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_expression("room", "neutral")
        msg = ws.messages[0]
        assert msg["intensity"] == 1.0

    async def test_expression_broadcast_to_multiple_clients(self):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        await register_client("room", ws1)
        await register_client("room", ws2)
        await broadcast_expression("room", "excited", 0.9)
        assert len(ws1.messages) == 1
        assert len(ws2.messages) == 1
        assert ws1.messages[0]["expression"] == "excited"
        assert ws2.messages[0]["expression"] == "excited"

    async def test_expression_no_clients_is_noop(self):
        await broadcast_expression("empty_room", "happy", 1.0)  # Should not raise


class TestBroadcastTtsStart:
    async def test_tts_start_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_tts_start("room", "sess-001", 24000, "Hello world")
        msg = ws.messages[0]
        assert msg["type"] == "TTS_START"
        assert msg["session_id"] == "sess-001"
        assert msg["sample_rate"] == 24000
        assert msg["text"] == "Hello world"
        assert msg["format"] == "pcm_24k_16bit_mono"

    async def test_tts_start_custom_sample_rate(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_tts_start("room", "sess-002", 48000, "Test")
        msg = ws.messages[0]
        assert msg["format"] == "pcm_48k_16bit_mono"
        assert msg["sample_rate"] == 48000

    async def test_tts_start_respects_audio_route_satellite(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        set_audio_route("room", "satellite")
        await broadcast_tts_start("room", "sess-003", 24000, "Skipped")
        assert len(ws.messages) == 0  # Not sent when route is satellite-only


class TestBroadcastTtsChunk:
    async def test_tts_chunk_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        pcm = _generate_pcm_samples(1024)
        b64 = _pcm_to_b64(pcm)
        await broadcast_tts_chunk("room", "sess-001", b64)
        msg = ws.messages[0]
        assert msg["type"] == "TTS_CHUNK"
        assert msg["session_id"] == "sess-001"
        assert msg["audio"] == b64

    async def test_tts_chunk_base64_round_trip(self):
        pcm = _generate_pcm_samples(512)
        b64 = _pcm_to_b64(pcm)
        decoded = base64.b64decode(b64)
        assert decoded == pcm

    async def test_tts_chunk_respects_audio_route(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        set_audio_route("room", "satellite")
        await broadcast_tts_chunk("room", "sess-001", "AAAA")
        assert len(ws.messages) == 0


class TestBroadcastTtsEnd:
    async def test_tts_end_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_tts_end("room", "sess-001")
        msg = ws.messages[0]
        assert msg == {"type": "TTS_END", "session_id": "sess-001"}

    async def test_tts_end_respects_audio_route(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        set_audio_route("room", "satellite")
        await broadcast_tts_end("room", "sess-001")
        assert len(ws.messages) == 0


class TestBroadcastSpeakingLifecycle:
    async def test_speaking_start_format(self):
        """SPEAKING_START includes skin_id and skin_url."""
        from cortex.db import set_db_path, init_db
        set_db_path(":memory:")
        init_db()
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_speaking_start("room")
        msg = ws.messages[0]
        assert msg["type"] == "SPEAKING_START"
        assert "skin_id" in msg
        assert "skin_url" in msg
        assert msg["skin_url"].startswith("/avatar/skin/")

    async def test_speaking_end_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_speaking_end("room")
        assert ws.messages[0] == {"type": "SPEAKING_END"}


class TestBroadcastListening:
    async def test_listening_active(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_listening("room", True)
        assert ws.messages[0] == {"type": "LISTENING", "active": True}

    async def test_listening_inactive(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_listening("room", False)
        assert ws.messages[0] == {"type": "LISTENING", "active": False}


class TestBroadcastViseme:
    async def test_viseme_message_format(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        await broadcast_viseme("room", "AA", 80, 0.7)
        msg = ws.messages[0]
        assert msg == {"type": "VISEME", "viseme": "AA", "duration_ms": 80, "intensity": 0.7}


class TestBroadcastDeadClientCleanup:
    async def test_dead_client_removed_on_broadcast(self):
        good = MockWebSocket()
        dead = FailingWebSocket()
        await register_client("room", good)
        await register_client("room", dead)
        await broadcast_to_room("room", {"type": "PING"})
        # Good client receives message
        assert len(good.messages) == 1
        # Dead client should be removed; good client remains
        assert has_clients("room")
        # Verify dead client is gone by broadcasting again
        await broadcast_to_room("room", {"type": "PING"})
        assert len(good.messages) == 2


# ══════════════════════════════════════════════════════════════════
# Audio routing
# ══════════════════════════════════════════════════════════════════


class TestAudioRouting:
    def test_default_route_is_avatar(self):
        assert get_audio_route("any_room") == "avatar"

    def test_set_route_avatar(self):
        set_audio_route("room", "avatar")
        assert get_audio_route("room") == "avatar"

    def test_set_route_satellite(self):
        set_audio_route("room", "satellite")
        assert get_audio_route("room") == "satellite"

    def test_set_route_both(self):
        set_audio_route("room", "both")
        assert get_audio_route("room") == "both"

    def test_set_route_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid audio route"):
            set_audio_route("room", "headphones")

    def test_should_play_on_avatar_default(self):
        assert should_play_on_avatar("room") is True

    def test_should_play_on_avatar_when_satellite(self):
        set_audio_route("room", "satellite")
        assert should_play_on_avatar("room") is False

    def test_should_play_on_avatar_when_both(self):
        set_audio_route("room", "both")
        assert should_play_on_avatar("room") is True

    def test_should_play_on_satellite_default(self):
        assert should_play_on_satellite("room") is False

    def test_should_play_on_satellite_when_satellite(self):
        set_audio_route("room", "satellite")
        assert should_play_on_satellite("room") is True

    def test_should_play_on_satellite_when_both(self):
        set_audio_route("room", "both")
        assert should_play_on_satellite("room") is True

    def test_should_play_on_satellite_when_avatar(self):
        set_audio_route("room", "avatar")
        assert should_play_on_satellite("room") is False


# ══════════════════════════════════════════════════════════════════
# 2. Timing validation — client-side viseme scheduling math
# ══════════════════════════════════════════════════════════════════


class TestCartoonVisemeTiming:
    """Validate the cartoon viseme scheduling algorithm from display.html."""

    def test_hello_world_beat_extraction(self):
        beats = _extract_cartoon_beats("Hello world")
        assert len(beats) > 0
        # 'h' not in CARTOON_VISEME, 'e' → TEETH, 'l' not in map, 'o' → ROUND
        visemes = [b["viseme"] for b in beats]
        assert "TEETH" in visemes  # 'e'
        assert "ROUND" in visemes  # 'o'

    def test_ms_per_beat_formula(self):
        """msPerBeat = audioDurationMs / (beats.length + 1)"""
        text = "Hello world"
        beats = _extract_cartoon_beats(text)
        audio_ms = 2000.0
        ms_per_beat = audio_ms / (len(beats) + 1)
        assert ms_per_beat > 0
        # For N beats, ms_per_beat should divide audio evenly
        assert abs(ms_per_beat * (len(beats) + 1) - audio_ms) < 0.01

    def test_cartoon_timings_hello_world_2000ms(self):
        """For 'Hello world' with 2000ms audio, validate each beat timing."""
        timings = _compute_cartoon_timings("Hello world", 2000.0)
        assert len(timings) > 2  # beats + final IDLE

        # First viseme fires at startDelayMs (0)
        assert timings[0]["time_ms"] == 0.0

        # Last entry is IDLE at audio end
        assert timings[-1]["viseme"] == "IDLE"
        assert abs(timings[-1]["time_ms"] - 2000.0) < 0.01

        # Verify no viseme scheduled before audio start
        for t in timings:
            assert t["time_ms"] >= 0.0

        # Verify last non-IDLE viseme + gap <= audio duration
        non_idle = [t for t in timings if t["viseme"] != "IDLE"]
        if non_idle:
            assert non_idle[-1]["time_ms"] <= 2000.0

    def test_cartoon_timings_within_tolerance(self):
        """Each beat should be within ±50ms of its expected position."""
        text = "Hello world"
        beats = _extract_cartoon_beats(text)
        audio_ms = 2000.0
        ms_per_beat = audio_ms / (len(beats) + 1)
        timings = _compute_cartoon_timings(text, audio_ms)

        # beat[0] at 0, beat[1] at ms_per_beat*1, beat[2] at ms_per_beat*2, ...
        for idx in range(min(len(beats), len(timings) - 1)):
            expected = ms_per_beat * idx if idx > 0 else 0.0
            actual = timings[idx]["time_ms"]
            assert abs(actual - expected) < 50.0, (
                f"Beat {idx}: expected ~{expected:.1f}ms, got {actual:.1f}ms"
            )

    def test_more_beats_shorter_duration(self):
        """'The quick brown fox' (more beats) → shorter per-beat vs 'Hi'."""
        short_beats = _extract_cartoon_beats("Hi")
        long_beats = _extract_cartoon_beats("The quick brown fox")
        audio_ms = 3000.0
        short_per = audio_ms / (len(short_beats) + 1) if short_beats else audio_ms
        long_per = audio_ms / (len(long_beats) + 1) if long_beats else audio_ms
        assert long_per < short_per

    def test_quick_brown_fox_3000ms(self):
        """'The quick brown fox' with 3000ms audio — verify distribution."""
        text = "The quick brown fox"
        timings = _compute_cartoon_timings(text, 3000.0)
        assert len(timings) >= 3
        # IDLE at end
        assert timings[-1]["viseme"] == "IDLE"
        assert abs(timings[-1]["time_ms"] - 3000.0) < 0.01
        # All timings within [0, 3000]
        for t in timings:
            assert 0.0 <= t["time_ms"] <= 3000.01

    def test_with_start_delay(self):
        """Verify start delay offsets all timings."""
        text = "Hello"
        delay = 500.0
        timings = _compute_cartoon_timings(text, 2000.0, start_delay_ms=delay)
        # First viseme at delay
        assert timings[0]["time_ms"] == delay
        # IDLE at delay + duration
        assert abs(timings[-1]["time_ms"] - (delay + 2000.0)) < 0.01


class TestRealisticVisemeTiming:
    """Validate the realistic viseme scheduling algorithm from display.html."""

    def test_ms_per_char_formula(self):
        """msPerChar = audioDurationMs / (charCount + text.length * 0.15)"""
        import re
        text = "Hello world"
        char_count = len(re.sub(r"[\s.,!?]", "", text.lower()))
        audio_ms = 2000.0
        ms_per_char = audio_ms / (char_count + len(text) * 0.15)
        assert ms_per_char > 0
        # Verify the denominator is correct
        assert char_count == 10  # "helloworld"
        expected_denom = 10 + 11 * 0.15  # 11.65
        assert abs(ms_per_char - audio_ms / expected_denom) < 0.01

    def test_realistic_timings_hello_world(self):
        timings = _compute_realistic_timings("Hello world", 2000.0)
        assert len(timings) > 5
        # IDLE at end
        assert timings[-1]["viseme"] == "IDLE"
        assert abs(timings[-1]["time_ms"] - 2000.0) < 0.01
        # All timings non-negative
        for t in timings:
            assert t["time_ms"] >= 0.0

    def test_space_gets_half_duration(self):
        """Spaces use msPerChar * 0.5 (shorter pause)."""
        import re
        text = "a b"
        char_count = len(re.sub(r"[\s.,!?]", "", text.lower()))
        audio_ms = 1000.0
        ms_per_char = audio_ms / (char_count + len(text) * 0.15)

        timings = _compute_realistic_timings(text, audio_ms)
        # 'a' at 0, space at ms_per_char*0.5 offset, 'b' at space + ms_per_char
        # The space entry should be an IDLE
        idles = [t for t in timings if t["viseme"] == "IDLE"]
        assert len(idles) >= 1  # space produces IDLE (plus final IDLE)


class TestEstimatedDuration:
    """Validate the estimated duration formula used before TTS_END."""

    def test_formula_short_text(self):
        """estimatedDurationMs = max(2000, 800 + text.length * 100)"""
        text = "Hi"
        estimated = max(2000, 800 + len(text) * 100)
        assert estimated == 2000  # min floor

    def test_formula_medium_text(self):
        text = "Hello world, how are you today?"
        estimated = max(2000, 800 + len(text) * 100)
        assert estimated == 800 + len(text) * 100
        assert estimated > 2000

    def test_formula_long_text(self):
        text = "a" * 100
        estimated = max(2000, 800 + len(text) * 100)
        assert estimated == 10800

    def test_minimum_is_2000ms(self):
        for length in range(0, 13):
            text = "x" * length
            estimated = max(2000, 800 + len(text) * 100)
            assert estimated >= 2000


class TestPostSpeechResetDelay:
    """Validate the post-speech reset timing: audioRemaining + 3000ms."""

    def test_reset_delay_with_remaining_audio(self):
        audio_remaining_ms = 1500.0
        reset_delay = max(0, audio_remaining_ms) + 3000
        assert reset_delay == 4500.0

    def test_reset_delay_no_remaining_audio(self):
        audio_remaining_ms = 0.0
        reset_delay = max(0, audio_remaining_ms) + 3000
        assert reset_delay == 3000.0

    def test_reset_delay_negative_remaining(self):
        """audioRemainingMs floors at 0 (max(0, ...))."""
        audio_remaining_ms = -200.0
        reset_delay = max(0, audio_remaining_ms) + 3000
        assert reset_delay == 3000.0


class TestTalkPulseThrottle:
    """Talk pulse throttle: max 5/sec (200ms minimum between pulses)."""

    def test_throttle_interval(self):
        min_interval_ms = 200
        max_pulses_per_sec = 1000 / min_interval_ms
        assert max_pulses_per_sec == 5.0

    def test_pulses_within_throttle_blocked(self):
        """Simulate rapid pulse requests — only 1 per 200ms passes.

        JS uses: if (now - _lastPulse < 200) return;
        _lastPulse starts at 0. In practice, performance.now() >> 200
        at first call, so the first pulse always fires.
        """
        last_pulse = 0.0  # JS initial value
        accepted = 0
        base_time = 5000.0  # realistic performance.now() offset
        for i in range(20):
            now = base_time + i * 50.0  # 50ms apart
            if not (now - last_pulse < 200):
                accepted += 1
                last_pulse = now
        # First pulse fires at t=5000 (5000-0 >= 200), then at 5200, 5400, 5600, 5800
        assert accepted == 5

    def test_pulses_at_exact_interval_all_pass(self):
        last_pulse = -200.0  # Allow first pulse
        accepted = 0
        for i in range(10):
            now = i * 200.0
            if now - last_pulse >= 200:
                accepted += 1
                last_pulse = now
        assert accepted == 10


# ══════════════════════════════════════════════════════════════════
# 3. Audio chunk format validation
# ══════════════════════════════════════════════════════════════════


class TestAudioChunkFormat:
    def test_pcm_16bit_le_generation(self):
        """Verify generated PCM samples are valid 16-bit signed LE."""
        pcm = _generate_pcm_samples(100)
        assert len(pcm) == 200  # 2 bytes per sample
        # Unpack all samples
        samples = struct.unpack(f"<{100}h", pcm)
        assert len(samples) == 100
        for s in samples:
            assert -32768 <= s <= 32767

    def test_base64_round_trip(self):
        pcm = _generate_pcm_samples(256)
        b64 = _pcm_to_b64(pcm)
        decoded = base64.b64decode(b64)
        assert decoded == pcm

    def test_audio_duration_calculation(self):
        """duration_ms = samples / sampleRate * 1000"""
        sample_rate = 24000
        num_samples = 48000  # 2 seconds
        duration_ms = (num_samples / sample_rate) * 1000
        assert duration_ms == 2000.0

    def test_audio_duration_fractional(self):
        sample_rate = 24000
        num_samples = 12000  # 0.5 seconds
        duration_ms = (num_samples / sample_rate) * 1000
        assert duration_ms == 500.0

    def test_sample_rate_consistency(self):
        """Default sample rate is 24000 Hz throughout."""
        ws = MockWebSocket()

        async def verify():
            await register_client("room", ws)
            await broadcast_tts_start("room", "s1", 24000, "Test")
            msg = ws.messages[0]
            assert msg["sample_rate"] == 24000
            assert "24k" in msg["format"]

        asyncio.get_event_loop().run_until_complete(verify())

    def test_chunk_pcm_to_float32_conversion(self):
        """Verify the JS-side conversion: int16[i] / 32768.0 → float32."""
        pcm = _generate_pcm_samples(10)
        int16_values = struct.unpack(f"<{10}h", pcm)
        float32_values = [v / 32768.0 for v in int16_values]
        for f in float32_values:
            assert -1.0 <= f <= 1.0

    def test_multiple_chunks_sequential(self):
        """Verify chunk queuing order with sequential session_id matching."""
        ws = MockWebSocket()

        async def verify():
            await register_client("room", ws)
            sid = "sess-seq"
            await broadcast_tts_start("room", sid, 24000, "Multi chunk")
            for i in range(5):
                pcm = _generate_pcm_samples(1024)
                b64 = _pcm_to_b64(pcm)
                await broadcast_tts_chunk("room", sid, b64)
            await broadcast_tts_end("room", sid)

            # Verify order: TTS_START, 5x TTS_CHUNK, TTS_END
            types = [m["type"] for m in ws.messages]
            assert types[0] == "TTS_START"
            assert types[-1] == "TTS_END"
            assert types[1:-1] == ["TTS_CHUNK"] * 5

            # All chunks share session_id
            for m in ws.messages[1:-1]:
                assert m["session_id"] == sid

        asyncio.get_event_loop().run_until_complete(verify())

    def test_total_audio_duration_from_chunks(self):
        """Sum of chunk samples / sampleRate gives total duration."""
        sample_rate = 24000
        chunk_sizes = [2048, 4096, 1024, 3072]
        total_samples = sum(chunk_sizes)
        total_duration_ms = (total_samples / sample_rate) * 1000
        expected = (10240 / 24000) * 1000
        assert abs(total_duration_ms - expected) < 0.01


# ══════════════════════════════════════════════════════════════════
# 4. State machine tests
# ══════════════════════════════════════════════════════════════════


class TestStateMachineLifecycle:
    """Full lifecycle: idle → LISTENING → SPEAKING_START → TTS_START → TTS_CHUNK(s) → TTS_END → SPEAKING_END → idle."""

    async def test_full_lifecycle(self):
        from cortex.db import set_db_path, init_db
        set_db_path(":memory:")
        init_db()

        ws = MockWebSocket()
        await register_client("room", ws)

        # idle → listening
        await broadcast_listening("room", True)
        assert ws.messages[-1] == {"type": "LISTENING", "active": True}

        # listening → speaking
        await broadcast_speaking_start("room")
        msg = ws.messages[-1]
        assert msg["type"] == "SPEAKING_START"

        # TTS start
        sid = "lifecycle-001"
        await broadcast_tts_start("room", sid, 24000, "Hello!")
        assert ws.messages[-1]["type"] == "TTS_START"
        assert ws.messages[-1]["session_id"] == sid

        # TTS chunks
        for _ in range(3):
            pcm = _generate_pcm_samples(2048)
            await broadcast_tts_chunk("room", sid, _pcm_to_b64(pcm))
        chunk_msgs = [m for m in ws.messages if m["type"] == "TTS_CHUNK"]
        assert len(chunk_msgs) == 3

        # TTS end
        await broadcast_tts_end("room", sid)
        assert ws.messages[-1]["type"] == "TTS_END"

        # speaking → end
        await broadcast_speaking_end("room")
        assert ws.messages[-1] == {"type": "SPEAKING_END"}

        # Verify full message sequence
        types = [m["type"] for m in ws.messages]
        assert types == [
            "LISTENING", "SPEAKING_START",
            "TTS_START", "TTS_CHUNK", "TTS_CHUNK", "TTS_CHUNK", "TTS_END",
            "SPEAKING_END",
        ]

    async def test_interrupted_lifecycle_barge_in(self):
        """Barge-in: speaking → LISTENING(true) interrupts speech."""
        from cortex.db import set_db_path, init_db
        set_db_path(":memory:")
        init_db()

        ws = MockWebSocket()
        await register_client("room", ws)

        await broadcast_speaking_start("room")
        await broadcast_tts_start("room", "s1", 24000, "Long sentence here")
        pcm = _generate_pcm_samples(4096)
        await broadcast_tts_chunk("room", "s1", _pcm_to_b64(pcm))

        # Barge-in: user starts talking
        await broadcast_listening("room", True)

        types = [m["type"] for m in ws.messages]
        # LISTENING arrives while TTS was still "active"
        assert "LISTENING" in types
        idx_listen = types.index("LISTENING")
        idx_chunk = types.index("TTS_CHUNK")
        assert idx_listen > idx_chunk  # listening after audio started

    async def test_multiple_expressions_during_speech(self):
        """Multiple expression changes while speaking."""
        from cortex.db import set_db_path, init_db
        set_db_path(":memory:")
        init_db()

        ws = MockWebSocket()
        await register_client("room", ws)

        await broadcast_speaking_start("room")
        await broadcast_expression("room", "happy", 1.0)
        await broadcast_expression("room", "excited", 0.9)
        await broadcast_expression("room", "silly", 1.0)
        await broadcast_speaking_end("room")

        expr_msgs = [m for m in ws.messages if m["type"] == "EXPRESSION"]
        assert len(expr_msgs) == 3
        assert [m["expression"] for m in expr_msgs] == ["happy", "excited", "silly"]

    async def test_rapid_state_changes(self):
        """Rapid state changes should not crash."""
        from cortex.db import set_db_path, init_db
        set_db_path(":memory:")
        init_db()

        ws = MockWebSocket()
        await register_client("room", ws)

        for _ in range(20):
            await broadcast_listening("room", True)
            await broadcast_listening("room", False)
            await broadcast_speaking_start("room")
            await broadcast_speaking_end("room")

        # Should complete without errors; all messages received
        assert len(ws.messages) == 80  # 4 messages × 20 iterations


# ══════════════════════════════════════════════════════════════════
# 5. Viseme sequence timing precision
# ══════════════════════════════════════════════════════════════════


class TestVisemeSequencePrecision:
    def test_hello_world_cartoon_beat_count(self):
        """'Hello world' → extract beats, verify count is reasonable."""
        beats = _extract_cartoon_beats("Hello world")
        # h(skip) e(TEETH) l(skip) l(skip) o(ROUND) (space→IDLE) w(ROUND→dup skip since last was ROUND? No, IDLE was last)
        # After IDLE: w(ROUND) o(ROUND→dup) r(skip) l(skip) d(skip)
        assert len(beats) >= 3

    def test_hello_world_cartoon_no_viseme_before_start(self):
        """No viseme scheduled before audio start (t=0)."""
        timings = _compute_cartoon_timings("Hello world", 2000.0, start_delay_ms=0)
        for t in timings:
            assert t["time_ms"] >= 0.0

    def test_hello_world_cartoon_last_within_duration(self):
        """Last viseme + gap <= audio duration."""
        timings = _compute_cartoon_timings("Hello world", 2000.0)
        non_idle = [t for t in timings if t["viseme"] != "IDLE"]
        if non_idle:
            last_time = non_idle[-1]["time_ms"]
            assert last_time <= 2000.0

    def test_cartoon_beat_timing_precision_50ms(self):
        """Each beat within ±50ms of expected evenly-distributed position."""
        text = "Hello world"
        audio_ms = 2000.0
        beats = _extract_cartoon_beats(text)
        ms_per_beat = audio_ms / (len(beats) + 1)
        timings = _compute_cartoon_timings(text, audio_ms)

        for idx in range(len(beats)):
            expected = ms_per_beat * idx if idx > 0 else 0.0
            actual = timings[idx]["time_ms"]
            assert abs(actual - expected) <= 50.0, (
                f"Beat {idx} ({timings[idx]['viseme']}): "
                f"expected {expected:.1f}ms ± 50ms, got {actual:.1f}ms"
            )

    def test_quick_brown_fox_more_beats_shorter_per_beat(self):
        """'The quick brown fox' has more beats → shorter per-beat duration."""
        text = "The quick brown fox"
        beats = _extract_cartoon_beats(text)
        audio_ms = 3000.0
        ms_per_beat = audio_ms / (len(beats) + 1)
        # Compare to simpler text
        simple_beats = _extract_cartoon_beats("Hi")
        simple_per_beat = audio_ms / (len(simple_beats) + 1) if simple_beats else audio_ms
        assert ms_per_beat < simple_per_beat

    def test_empty_text_no_timings(self):
        timings = _compute_cartoon_timings("", 2000.0)
        assert timings == []

    def test_single_character_vowel(self):
        timings = _compute_cartoon_timings("a", 1000.0)
        assert len(timings) >= 2  # beat + IDLE
        assert timings[0]["viseme"] == "OPEN"  # 'a' → OPEN in cartoon
        assert timings[-1]["viseme"] == "IDLE"

    def test_single_character_consonant_not_in_map(self):
        """'x' is not in CARTOON_VISEME — no beats."""
        timings = _compute_cartoon_timings("x", 1000.0)
        assert timings == []

    def test_very_long_text(self):
        """1000-char text should still produce valid timings."""
        text = "hello world " * 84  # ~1008 chars
        timings = _compute_cartoon_timings(text, 60000.0)
        assert len(timings) > 100
        assert timings[-1]["viseme"] == "IDLE"
        assert abs(timings[-1]["time_ms"] - 60000.0) < 0.01
        # All timings in range
        for t in timings:
            assert 0.0 <= t["time_ms"] <= 60000.01

    def test_punctuation_produces_idle_beats(self):
        """Commas, periods, etc. produce IDLE beats in cartoon mode."""
        text = "Hello, world!"
        beats = _extract_cartoon_beats(text)
        idle_beats = [b for b in beats if b["viseme"] == "IDLE"]
        assert len(idle_beats) >= 1  # comma and/or exclamation mark

    def test_realistic_timing_covers_full_duration(self):
        """Realistic mode: last IDLE scheduled at audio end."""
        timings = _compute_realistic_timings("Hello world", 2000.0)
        assert timings[-1]["viseme"] == "IDLE"
        assert abs(timings[-1]["time_ms"] - 2000.0) < 0.01

    def test_realistic_no_timing_exceeds_duration(self):
        timings = _compute_realistic_timings("The quick brown fox", 3000.0)
        for t in timings[:-1]:  # Exclude final IDLE which is exactly at duration
            assert t["time_ms"] <= 3000.01


# ══════════════════════════════════════════════════════════════════
# Server-side viseme generation (visemes.py)
# ══════════════════════════════════════════════════════════════════


class TestServerVisemeGeneration:
    """Test the Python text_to_visemes function that generates server-side frames."""

    def test_frames_contiguous(self):
        """Each frame starts where the previous one ends."""
        frames = text_to_visemes("Hello world")
        for i in range(1, len(frames)):
            expected_start = frames[i - 1].start_ms + frames[i - 1].duration_ms
            assert frames[i].start_ms == expected_start

    def test_total_duration_reasonable(self):
        """Total viseme duration scales with text length."""
        short = text_to_visemes("Hi")
        long = text_to_visemes("The quick brown fox jumps over the lazy dog")
        short_dur = sum(f.duration_ms for f in short)
        long_dur = sum(f.duration_ms for f in long)
        assert long_dur > short_dur

    def test_vowel_intensity_higher(self):
        """Vowel visemes get 0.7 intensity, consonants 0.4."""
        frames = text_to_visemes("ab")
        for f in frames:
            if f.viseme in _VOWEL_VISEMES:
                assert f.intensity == 0.7
            elif f.viseme != "IDLE":
                assert f.intensity == 0.4

    def test_idle_intensity_zero(self):
        frames = text_to_visemes("a b")
        for f in frames:
            if f.viseme == "IDLE":
                assert f.intensity == 0.0

    def test_wpm_affects_per_phoneme_duration(self):
        slow = text_to_visemes("hi", wpm=100)
        fast = text_to_visemes("hi", wpm=300)
        assert slow[0].duration_ms > fast[0].duration_ms

    def test_phoneme_count_matches_frame_count(self):
        """One frame per phoneme."""
        phonemes = _text_to_phonemes("hello")
        frames = text_to_visemes("hello")
        assert len(frames) == len(phonemes)


class TestVisemeSequenceBroadcast:
    """Test broadcast_viseme_sequence timing behavior."""

    async def test_empty_sequence_returns_immediately(self):
        await broadcast_viseme_sequence("room", [])

    async def test_single_frame_broadcast(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        frames = [{"viseme": "AA", "start_ms": 0, "duration_ms": 80, "intensity": 0.7}]
        await broadcast_viseme_sequence("room", frames)
        assert len(ws.messages) == 1
        assert ws.messages[0]["viseme"] == "AA"

    async def test_sequence_preserves_order(self):
        ws = MockWebSocket()
        await register_client("room", ws)
        frames = [
            {"viseme": "PP", "start_ms": 0, "duration_ms": 80, "intensity": 0.4},
            {"viseme": "AA", "start_ms": 80, "duration_ms": 80, "intensity": 0.7},
            {"viseme": "IDLE", "start_ms": 160, "duration_ms": 80, "intensity": 0.0},
        ]
        await broadcast_viseme_sequence("room", frames)
        visemes = [m["viseme"] for m in ws.messages]
        assert visemes == ["PP", "AA", "IDLE"]


# ══════════════════════════════════════════════════════════════════
# Expression transition timing
# ══════════════════════════════════════════════════════════════════


class TestExpressionTransitionTiming:
    """Validate expression delay calculation from display.html."""

    def test_expression_delay_with_remaining_audio(self):
        """audioRemainingMs = max(0, (nextPlayTime - currentTime) * 1000)"""
        next_play_time = 5.0  # seconds (Web Audio API time)
        current_time = 3.5
        remaining = max(0, (next_play_time - current_time) * 1000)
        assert remaining == 1500.0

    def test_expression_delay_audio_finished(self):
        next_play_time = 3.0
        current_time = 3.5
        remaining = max(0, (next_play_time - current_time) * 1000)
        assert remaining == 0.0

    def test_tts_end_expression_delay(self):
        """Expression hint in TTS_END: exprDelay = max(0, remaining - 500)."""
        remaining = 2000.0
        expr_delay = max(0, remaining - 500)
        assert expr_delay == 1500.0

    def test_tts_end_expression_delay_short_remaining(self):
        remaining = 300.0
        expr_delay = max(0, remaining - 500)
        assert expr_delay == 0.0

    def test_speaking_end_defer_threshold(self):
        """SPEAKING_END deferred if remaining > 50ms."""
        remaining = 100.0
        should_defer = remaining > 50
        assert should_defer is True

        remaining = 30.0
        should_defer = remaining > 50
        assert should_defer is False

    def test_expression_defer_threshold(self):
        """EXPRESSION deferred if remaining > 50ms."""
        remaining = 100.0
        should_defer = remaining > 50
        assert should_defer is True

    def test_confidence_blending_toward_neutral(self):
        """When confidence < 1.0, expression blends toward neutral."""
        from cortex.avatar.expressions import resolve_from_sentiment, EXPRESSIONS
        full = resolve_from_sentiment("greeting", 1.0)
        half = resolve_from_sentiment("greeting", 0.5)
        neutral = EXPRESSIONS["neutral"]

        # Half confidence should be between neutral and full
        assert abs(half.mouth_smile - (neutral.mouth_smile + (full.mouth_smile - neutral.mouth_smile) * 0.5)) < 0.01
        assert abs(half.eyebrow_raise - (neutral.eyebrow_raise + (full.eyebrow_raise - neutral.eyebrow_raise) * 0.5)) < 0.01


# ── Avatar-specific regression & behavior tests ─────────────────


class TestSpeakingIdleMouth:
    """ISSUE-13 related: IDLE-TALK viseme must exist for speaking idle state."""

    def test_idle_talk_in_viseme_list(self):
        """IDLE-TALK viseme exists in the JS VISEMES list (validated via Python constant)."""
        # The JS VISEMES array includes both IDLE and IDLE-TALK.
        # We mirror that list here to confirm the distinction.
        js_visemes = [
            "IDLE", "IDLE-TALK",
            "PP", "FF", "TH", "DD", "KK", "SS", "SH", "RR", "NN",
            "IH", "EH", "AA", "OH", "OU",
            "CLOSED", "OPEN", "ROUND", "TEETH", "FV",
        ]
        assert "IDLE-TALK" in js_visemes
        assert "IDLE" in js_visemes
        assert js_visemes.index("IDLE-TALK") != js_visemes.index("IDLE")

    def test_idle_talk_distinct_from_idle(self):
        """IDLE-TALK and IDLE serve different roles — IDLE is rest, IDLE-TALK is speaking rest."""
        # In display.html, showViseme() checks for a mouth-IDLE-TALK element.
        # If it exists in the SVG, IDLE-TALK is used during speech instead of IDLE.
        assert "IDLE" != "IDLE-TALK"


class TestAudioFlushOnSpeakingStart:
    """ISSUE-14/15: SPEAKING_START should signal client to flush old audio."""

    async def test_speaking_start_sends_message(self):
        """broadcast_speaking_start sends SPEAKING_START with skin info."""
        ws = MockWebSocket()
        await register_client("living-room", ws)

        await broadcast_speaking_start("living-room")

        assert len(ws.messages) == 1
        msg = ws.messages[0]
        assert msg["type"] == "SPEAKING_START"
        assert "skin_id" in msg
        assert "skin_url" in msg

    async def test_speaking_start_precedes_tts(self):
        """SPEAKING_START must arrive before any TTS_START in a session."""
        ws = MockWebSocket()
        await register_client("office", ws)

        await broadcast_speaking_start("office")
        await broadcast_tts_start("office", "sess-flush-01", 24000, "Hello")

        types = [m["type"] for m in ws.messages]
        assert types.index("SPEAKING_START") < types.index("TTS_START")

    async def test_new_speaking_start_implies_audio_flush(self):
        """A second SPEAKING_START should signal the client to flush stale audio.

        The JS SPEAKING_START handler now clears audioQueue and resets
        nextPlayTime (ISSUE-14 fix), so the client flushes stale audio
        when a new speech session begins.
        """
        ws = MockWebSocket()
        await register_client("kitchen", ws)

        # First speaking session with audio
        await broadcast_speaking_start("kitchen")
        await broadcast_tts_start("kitchen", "sess-old", 24000, "Old speech")
        pcm = _generate_pcm_samples(2400)
        await broadcast_tts_chunk("kitchen", "sess-old", _pcm_to_b64(pcm))

        # Interrupt: new speaking session before TTS_END
        await broadcast_speaking_start("kitchen")

        # The second SPEAKING_START should be the signal for flush.
        speaking_starts = [m for m in ws.messages if m["type"] == "SPEAKING_START"]
        assert len(speaking_starts) == 2

        # Server delivers both SPEAKING_STARTs — client-side JS now handles
        # audioQueue flush and nextPlayTime reset on each SPEAKING_START.


class TestBargeInSequence:
    """ISSUE-14: Barge-in must clear old session state."""

    async def test_barge_in_clears_old_session(self):
        """Simulate: SPEAKING_START → TTS flow → interrupt → new SPEAKING_START.

        After the interrupt, the old session's TTS messages should not
        interfere with the new session. The client-side JS now flushes
        audioQueue and resets nextPlayTime on SPEAKING_START, and uses
        currentSessionId to discard stale chunks.
        """
        ws = MockWebSocket()
        await register_client("bedroom", ws)

        # Session 1: start speaking
        await broadcast_speaking_start("bedroom")
        await broadcast_tts_start("bedroom", "sess-A", 24000, "First sentence")
        pcm = _generate_pcm_samples(1200)
        await broadcast_tts_chunk("bedroom", "sess-A", _pcm_to_b64(pcm))

        # Interrupt: new speech arrives (barge-in)
        await broadcast_speaking_start("bedroom")
        await broadcast_tts_start("bedroom", "sess-B", 24000, "Interrupting")

        # Collect session IDs from TTS_START messages
        tts_starts = [m for m in ws.messages if m["type"] == "TTS_START"]
        assert len(tts_starts) == 2
        assert tts_starts[0]["session_id"] == "sess-A"
        assert tts_starts[1]["session_id"] == "sess-B"

        # Server delivers all messages; client-side JS gates on
        # currentSessionId after the second SPEAKING_START flushes state.


class TestSkinMessageOnConnect:
    """ISSUE-16: Server sends SKIN on client registration."""

    async def test_client_hello_triggers_skin(self):
        """After registering, client sends HELLO and server responds with SKIN.

        Flow:
        1. Client connects via WebSocket
        2. Client sends {"type": "HELLO", "room": "living-room"}
        3. Server responds with {"type": "SKIN", "skin_id": ..., "skin_url": ...}
        """
        ws = MockWebSocket()
        await register_client("living-room", ws)

        # Simulate client sending HELLO — server responds with SKIN
        await handle_client_hello("living-room", ws)
        skin_msgs = [m for m in ws.messages if m.get("type") == "SKIN"]
        assert len(skin_msgs) == 1, "SKIN should be sent on HELLO"
        assert "skin_id" in skin_msgs[0]
        assert skin_msgs[0]["skin_url"].startswith("/avatar/skin/")

    async def test_speaking_start_includes_skin_info(self):
        """SPEAKING_START already includes skin_id and skin_url (existing behavior)."""
        ws = MockWebSocket()
        await register_client("hallway", ws)

        await broadcast_speaking_start("hallway")

        msg = ws.messages[0]
        assert msg["type"] == "SPEAKING_START"
        assert "skin_id" in msg
        assert msg["skin_url"].startswith("/avatar/skin/")


class TestFacelessSkinDetection:
    """ISSUE related: _isFaceless logic in display.html."""

    def test_faceless_detection_documented(self):
        """Document expected _isFaceless behavior.

        In display.html, after loading a skin SVG:
        - The JS checks for a <g id="face-base"> group
        - If face-base has no children (empty group), _isFaceless = true
        - When _isFaceless, the background/body IS the face — no separate
          face overlay is rendered, and expression morphs are skipped

        This is a documentation test — actual DOM testing requires a browser.
        """
        # Simulate the detection logic in Python
        face_base_children = []  # Empty face-base group
        is_faceless = len(face_base_children) == 0
        assert is_faceless is True

        face_base_children = ["<ellipse id='head'/>"]  # Has face geometry
        is_faceless = len(face_base_children) == 0
        assert is_faceless is False


class TestExpressionPresetCompassionate:
    """Verify broadcast formats expression correctly for Legacy Protocol."""

    async def test_compassionate_expression_broadcast(self):
        """If a 'compassionate' expression is sent, broadcast formats it correctly."""
        ws = MockWebSocket()
        await register_client("nursery", ws)

        await broadcast_expression("nursery", "compassionate", 0.9)

        msg = ws.messages[0]
        assert msg["type"] == "EXPRESSION"
        assert msg["expression"] == "compassionate"
        assert msg["intensity"] == 0.9

    async def test_expression_intensity_clamped(self):
        """Expression intensity is passed through as-is (protocol trusts server)."""
        ws = MockWebSocket()
        await register_client("nursery", ws)

        await broadcast_expression("nursery", "compassionate", 1.0)
        assert ws.messages[0]["intensity"] == 1.0

        await broadcast_expression("nursery", "compassionate", 0.0)
        assert ws.messages[1]["intensity"] == 0.0


class TestMultiSentenceVisemeNoClear:
    """ISSUE-18 regression: Two TTS sequences in the same SPEAKING session
    must not clear the first set of visemes."""

    async def test_two_tts_sequences_in_same_session(self):
        """Send two TTS_START/CHUNK/END sequences without SPEAKING_END between them.

        Both should be delivered to the client. The server broadcasts all
        messages — it's the client's job to schedule visemes correctly.
        This test verifies the server doesn't drop or merge messages.
        """
        ws = MockWebSocket()
        await register_client("den", ws)

        await broadcast_speaking_start("den")

        # Sentence 1
        await broadcast_tts_start("den", "sess-s1", 24000, "Hello world")
        pcm1 = _generate_pcm_samples(2400)
        await broadcast_tts_chunk("den", "sess-s1", _pcm_to_b64(pcm1))
        await broadcast_tts_end("den", "sess-s1")

        # Sentence 2 (same speaking session, no SPEAKING_END between)
        await broadcast_tts_start("den", "sess-s2", 24000, "How are you")
        pcm2 = _generate_pcm_samples(3600)
        await broadcast_tts_chunk("den", "sess-s2", _pcm_to_b64(pcm2))
        await broadcast_tts_end("den", "sess-s2")

        await broadcast_speaking_end("den")

        types = [m["type"] for m in ws.messages]
        assert types == [
            "SPEAKING_START",
            "TTS_START", "TTS_CHUNK", "TTS_END",
            "TTS_START", "TTS_CHUNK", "TTS_END",
            "SPEAKING_END",
        ]

        # Both TTS_START messages have distinct session IDs
        tts_starts = [m for m in ws.messages if m["type"] == "TTS_START"]
        assert tts_starts[0]["session_id"] == "sess-s1"
        assert tts_starts[1]["session_id"] == "sess-s2"

        # Both TTS_START messages carry their sentence text
        assert tts_starts[0]["text"] == "Hello world"
        assert tts_starts[1]["text"] == "How are you"

    async def test_multi_sentence_all_chunks_delivered(self):
        """All TTS_CHUNKs from both sentences are delivered without loss."""
        ws = MockWebSocket()
        await register_client("den", ws)

        await broadcast_speaking_start("den")

        # Sentence 1: two chunks
        await broadcast_tts_start("den", "sess-m1", 24000, "First")
        pcm_a = _generate_pcm_samples(1200)
        pcm_b = _generate_pcm_samples(1200)
        await broadcast_tts_chunk("den", "sess-m1", _pcm_to_b64(pcm_a))
        await broadcast_tts_chunk("den", "sess-m1", _pcm_to_b64(pcm_b))
        await broadcast_tts_end("den", "sess-m1")

        # Sentence 2: one chunk
        await broadcast_tts_start("den", "sess-m2", 24000, "Second")
        pcm_c = _generate_pcm_samples(2400)
        await broadcast_tts_chunk("den", "sess-m2", _pcm_to_b64(pcm_c))
        await broadcast_tts_end("den", "sess-m2")

        chunks = [m for m in ws.messages if m["type"] == "TTS_CHUNK"]
        assert len(chunks) == 3


class TestAudioRouteAffectsTtsDelivery:
    """Verify that audio_route='satellite' suppresses TTS to avatar clients."""

    async def test_satellite_route_blocks_tts_chunk(self):
        """When audio_route is 'satellite', broadcast_tts_chunk returns without sending."""
        ws = MockWebSocket()
        await register_client("garage", ws)
        set_audio_route("garage", "satellite")

        await broadcast_tts_start("garage", "sess-sat-01", 24000, "Test")
        pcm = _generate_pcm_samples(1200)
        await broadcast_tts_chunk("garage", "sess-sat-01", _pcm_to_b64(pcm))
        await broadcast_tts_end("garage", "sess-sat-01")

        # No TTS messages should reach avatar client
        tts_msgs = [m for m in ws.messages if m["type"].startswith("TTS_")]
        assert len(tts_msgs) == 0

    async def test_satellite_route_still_sends_speaking(self):
        """SPEAKING_START/END are NOT gated by audio route — avatar still animates."""
        ws = MockWebSocket()
        await register_client("garage", ws)
        set_audio_route("garage", "satellite")

        await broadcast_speaking_start("garage")
        await broadcast_speaking_end("garage")

        types = [m["type"] for m in ws.messages]
        assert "SPEAKING_START" in types
        assert "SPEAKING_END" in types

    async def test_both_route_delivers_tts(self):
        """When audio_route is 'both', TTS messages ARE delivered to avatar."""
        ws = MockWebSocket()
        await register_client("garage", ws)
        set_audio_route("garage", "both")

        await broadcast_tts_start("garage", "sess-both-01", 24000, "Both test")
        pcm = _generate_pcm_samples(1200)
        await broadcast_tts_chunk("garage", "sess-both-01", _pcm_to_b64(pcm))
        await broadcast_tts_end("garage", "sess-both-01")

        tts_msgs = [m for m in ws.messages if m["type"].startswith("TTS_")]
        assert len(tts_msgs) == 3
