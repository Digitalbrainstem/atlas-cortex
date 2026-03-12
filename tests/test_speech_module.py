"""Tests for cortex/speech/ — TTS synthesis & voice resolution.

Prove actual behavior: mock providers, verify fallback chains, test
database-backed voice resolution with real schema.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import init_db, set_db_path
from cortex.speech.voices import resolve_voice, to_orpheus_voice, ORPHEUS_VOICES, KOKORO_VOICE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "speech_test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


# ===========================================================================
# to_orpheus_voice mapping
# ===========================================================================

class TestToOrpheusVoice:
    def test_known_voice_passthrough(self):
        for v in ORPHEUS_VOICES:
            assert to_orpheus_voice(v) == v

    def test_strips_orpheus_prefix(self):
        assert to_orpheus_voice("orpheus_tara") == "tara"
        assert to_orpheus_voice("orpheus_leo") == "leo"

    def test_unknown_voice_maps_to_tara(self):
        assert to_orpheus_voice("af_bella") == "tara"
        assert to_orpheus_voice("totally_fake") == "tara"

    def test_none_maps_to_tara(self):
        assert to_orpheus_voice(None) == "tara"

    def test_empty_maps_to_tara(self):
        assert to_orpheus_voice("") == "tara"


# ===========================================================================
# resolve_voice — database-backed priority chain
# ===========================================================================

class TestResolveVoice:
    def test_returns_env_default_when_no_db(self):
        """With no user/satellite and a cold DB, falls back to KOKORO_VOICE."""
        voice = resolve_voice()
        assert voice == KOKORO_VOICE

    def test_user_preference_override(self, db):
        """User preference in user_profiles takes priority over env default."""
        db.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, preferred_voice) VALUES (?, ?, ?)",
            ("alice", "Alice", "af_heart"),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice(user_id="alice")
        assert voice == "af_heart"

    def test_satellite_voice_overrides_user(self, db):
        """Satellite-specific voice has higher priority than user pref."""
        db.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, preferred_voice) VALUES (?, ?, ?)",
            ("alice", "Alice", "af_heart"),
        )
        db.execute(
            "INSERT INTO satellites (id, display_name, tts_voice) VALUES (?, ?, ?)",
            ("sat-kitchen", "Kitchen", "af_sky"),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice(user_id="alice", satellite_id="sat-kitchen")
        assert voice == "af_sky"

    def test_system_settings_default(self, db):
        """System-wide default from system_settings table."""
        db.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?)",
            ("default_tts_voice", "af_nicole"),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice()
        assert voice == "af_nicole"

    def test_user_pref_overrides_system_default(self, db):
        """User pref beats system default."""
        db.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?)",
            ("default_tts_voice", "af_nicole"),
        )
        db.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, preferred_voice) VALUES (?, ?, ?)",
            ("bob", "Bob", "af_bella"),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice(user_id="bob")
        assert voice == "af_bella"

    def test_missing_satellite_falls_through(self, db):
        """Nonexistent satellite_id falls through to user pref / default."""
        db.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, preferred_voice) VALUES (?, ?, ?)",
            ("carol", "Carol", "leo"),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice(user_id="carol", satellite_id="nonexistent-sat")
        assert voice == "leo"

    def test_null_tts_voice_satellite_falls_through(self, db):
        """Satellite with NULL tts_voice field falls through.

        Note: The schema defaults tts_voice to '' (empty string), not NULL.
        resolve_voice checks `if row and row['tts_voice']` — empty string is falsy.
        """
        db.execute(
            "INSERT INTO satellites (id, display_name, tts_voice) VALUES (?, ?, ?)",
            ("sat-empty", "EmptyVoice", ""),
        )
        db.commit()
        with patch("cortex.db.get_db", return_value=db):
            voice = resolve_voice(satellite_id="sat-empty")
        assert voice == KOKORO_VOICE


# ===========================================================================
# synthesize_speech — fallback chain with mocked providers
# ===========================================================================

class TestSynthesizeSpeech:
    """Tests for the multi-provider TTS fallback chain."""

    @pytest.mark.asyncio
    async def test_kokoro_first_when_fast(self):
        """fast=True should try Kokoro first, not Orpheus."""
        import wave, io
        # Build a minimal valid WAV
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x01\x02\x03\x04")
        wav_bytes = buf.getvalue()

        mock_kokoro_inst = AsyncMock()
        mock_kokoro_inst.synthesize = AsyncMock(return_value=(wav_bytes, {"rate": 24000}))
        mock_kokoro_cls = MagicMock(return_value=mock_kokoro_inst)

        with patch("cortex.speech.tts._TTS_PROVIDER", "orpheus"), \
             patch("cortex.voice.kokoro.KokoroClient", mock_kokoro_cls):
            from cortex.speech.tts import synthesize_speech
            pcm, rate, provider = await synthesize_speech("hello", "af_bella", fast=True)
        assert provider == "kokoro"
        assert rate == 24000
        assert len(pcm) > 0

    @pytest.mark.asyncio
    async def test_all_providers_fail_returns_empty(self):
        """When every provider raises, returns (b'', 24000, 'none')."""
        with patch("cortex.voice.kokoro.KokoroClient", side_effect=Exception("no kokoro")), \
             patch("cortex.speech.tts._TTS_PROVIDER", "orpheus"), \
             patch("cortex.voice.wyoming.WyomingClient") as mock_piper:
            mock_piper.return_value.synthesize = AsyncMock(side_effect=Exception("no piper"))

            from cortex.speech.tts import synthesize_speech
            pcm, rate, provider = await synthesize_speech("test", "tara")

        assert pcm == b""
        assert rate == 24000
        assert provider == "none"

    @pytest.mark.asyncio
    async def test_orpheus_fail_falls_to_kokoro(self):
        """When Orpheus fails but Kokoro succeeds → returns 'kokoro'."""
        import wave, io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\xaa\xbb")
        wav_bytes = buf.getvalue()

        mock_kokoro_inst = AsyncMock()
        mock_kokoro_inst.synthesize = AsyncMock(return_value=(wav_bytes, {"rate": 24000}))
        mock_kokoro_cls = MagicMock(return_value=mock_kokoro_inst)

        # Make Orpheus fail via aiohttp
        import aiohttp
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(side_effect=Exception("orpheus down"))
        mock_session_cls = MagicMock(return_value=mock_session_ctx)

        with patch("cortex.speech.tts._TTS_PROVIDER", "orpheus"), \
             patch("aiohttp.ClientSession", mock_session_cls), \
             patch("cortex.voice.kokoro.KokoroClient", mock_kokoro_cls):
            from cortex.speech.tts import synthesize_speech
            pcm, rate, provider = await synthesize_speech("hello", "af_bella")

        assert provider == "kokoro"

    @pytest.mark.asyncio
    async def test_extract_pcm_raw_passthrough(self):
        """Non-WAV data passes through extract_pcm unchanged."""
        from cortex.speech.tts import extract_pcm
        raw = b"\x00\x01\x02\x03"
        pcm, rate = extract_pcm(raw)
        assert pcm == raw
        assert rate == 24000

    @pytest.mark.asyncio
    async def test_extract_pcm_empty_input(self):
        """Empty bytes pass through with default rate."""
        from cortex.speech.tts import extract_pcm
        pcm, rate = extract_pcm(b"")
        assert pcm == b""
        assert rate == 24000
