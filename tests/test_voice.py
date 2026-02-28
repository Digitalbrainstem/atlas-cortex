"""Tests for the Voice & Speech Engine (C11)."""

from __future__ import annotations

import sqlite3
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

class _Sentiment:
    """Minimal sentiment stub mirroring what VADER produces."""

    def __init__(self, label="neutral", compound=0.0, category="neutral"):
        self.label = label
        self.compound = compound
        self.category = category


async def _async_chunks(data: bytes, chunk_size: int = 16):
    """Yield *data* in chunks, simulating a streaming TTS response."""
    for i in range(0, max(len(data), 1), chunk_size):
        yield data[i : i + chunk_size]


# ===========================================================================
# C11.1 — TTSProvider abstract interface
# ===========================================================================

class TestTTSProviderInterface:
    def test_abstract_methods_raise(self):
        from cortex.voice.base import TTSProvider

        p = TTSProvider()
        import asyncio
        with pytest.raises(NotImplementedError):
            asyncio.get_event_loop().run_until_complete(p.synthesize("hello"))

    def test_default_capabilities(self):
        from cortex.voice.base import TTSProvider

        p = TTSProvider()
        assert p.supports_emotion() is False
        assert p.supports_streaming() is False
        assert p.supports_phonemes() is False
        assert p.get_emotion_format() is None


# ===========================================================================
# C11.2 — Orpheus provider
# ===========================================================================

class TestOrpheusTTSProvider:
    def test_init_defaults(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        assert p.ollama_url == "http://localhost:11434"
        assert "Orpheus" in p.model
        assert p.fastapi_url is None

    def test_init_custom_config(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider({
            "ORPHEUS_URL": "http://gpu1:11435",
            "ORPHEUS_MODEL": "legraphista/Orpheus:Q4",
            "ORPHEUS_FASTAPI_URL": "http://gpu1:8080",
        })
        assert p.ollama_url == "http://gpu1:11435"
        assert p.fastapi_url == "http://gpu1:8080"

    def test_format_prompt_with_voice_and_emotion(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        result = p._format_prompt("Hello there.", "tara", "warm")
        assert result == "tara, warm: Hello there."

    def test_format_prompt_strips_orpheus_prefix(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        result = p._format_prompt("Hi.", "orpheus_leo", None)
        assert result == "leo: Hi."

    def test_format_prompt_no_prefix(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        assert p._format_prompt("Hi.", None, None) == "Hi."

    def test_capabilities(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        assert p.supports_emotion() is True
        assert p.supports_streaming() is True
        assert p.get_emotion_format() == "tags"

    async def test_list_voices(self):
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = OrpheusTTSProvider()
        voices = await p.list_voices()
        ids = {v["id"] for v in voices}
        assert "orpheus_tara" in ids
        assert "orpheus_leo" in ids
        assert len(voices) == 8

    async def test_synthesize_via_fastapi(self):
        """synthesize() should yield bytes when FastAPI backend responds."""
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        fake_audio = b"\x00\x01\x02\x03" * 64

        async def _fake_iter_chunked(n):
            for i in range(0, len(fake_audio), n):
                yield fake_audio[i : i + n]

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content.iter_chunked = _fake_iter_chunked
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            p = OrpheusTTSProvider({"ORPHEUS_FASTAPI_URL": "http://fakeserver"})
            chunks = []
            async for chunk in p.synthesize("Hello.", voice="tara", stream=True):
                chunks.append(chunk)

        assert b"".join(chunks) == fake_audio


# ===========================================================================
# C11.2 — Piper provider
# ===========================================================================

class TestPiperTTSProvider:
    def test_init_defaults(self):
        from cortex.voice.providers.piper import PiperTTSProvider

        p = PiperTTSProvider()
        assert "10200" in p.piper_url

    def test_capabilities(self):
        from cortex.voice.providers.piper import PiperTTSProvider

        p = PiperTTSProvider()
        assert p.supports_emotion() is False
        assert p.supports_streaming() is True
        assert p.get_emotion_format() == "ssml"

    async def test_list_voices(self):
        from cortex.voice.providers.piper import PiperTTSProvider

        voices = await PiperTTSProvider().list_voices()
        assert any(v["id"] == "piper_amy" for v in voices)

    def test_strip_ssml(self):
        from cortex.voice.providers.piper import _strip_ssml

        assert _strip_ssml('<speak><prosody rate="fast">Hello</prosody></speak>') == "Hello"
        assert _strip_ssml("Plain text") == "Plain text"


# ===========================================================================
# C11.1 — Provider registry
# ===========================================================================

class TestProviderRegistry:
    def test_get_orpheus_by_default(self):
        from cortex.voice.providers import get_tts_provider
        from cortex.voice.providers.orpheus import OrpheusTTSProvider

        p = get_tts_provider({"TTS_PROVIDER": "orpheus"})
        assert isinstance(p, OrpheusTTSProvider)

    def test_get_piper(self):
        from cortex.voice.providers import get_tts_provider
        from cortex.voice.providers.piper import PiperTTSProvider

        p = get_tts_provider({"TTS_PROVIDER": "piper"})
        assert isinstance(p, PiperTTSProvider)

    def test_unknown_provider_raises(self):
        from cortex.voice.providers import get_tts_provider

        with pytest.raises(ValueError, match="Unknown TTS provider"):
            get_tts_provider({"TTS_PROVIDER": "nonexistent"})

    def test_register_custom_provider(self):
        from cortex.voice.base import TTSProvider
        from cortex.voice.providers import get_tts_provider, register_provider

        class DummyProvider(TTSProvider):
            pass

        register_provider("dummy", DummyProvider)
        p = get_tts_provider({"TTS_PROVIDER": "dummy"})
        assert isinstance(p, DummyProvider)


# ===========================================================================
# C11.3 — Emotion Composer
# ===========================================================================

class TestEmotionComposer:
    def _make_provider(self, fmt):
        p = MagicMock()
        p.get_emotion_format.return_value = fmt
        return p

    def test_plain_fallback(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider(None)
        result = ec.compose("Hello.", _Sentiment(), provider=provider)
        assert result == "Hello."

    def test_orpheus_positive_adds_warm(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "Great news!", _Sentiment("positive", 0.3), provider=provider,
            context={"hour": 14},  # explicit daytime hour
        )
        assert result.startswith("warm:")

    def test_orpheus_neutral_no_tag(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose("Okay.", _Sentiment("neutral", 0.0), provider=provider)
        # neutral → no emotion prefix
        assert not result.startswith(("warm:", "happy:", "sad:", "concerned:"))
        assert "Okay." in result

    def test_orpheus_whisper_context(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "Quiet now.", _Sentiment(), provider=provider,
            context={"is_whisper": True}
        )
        assert result.startswith("whisper:")

    def test_orpheus_excited_context(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "Package arrived!", _Sentiment(), provider=provider,
            context={"is_excited": True}
        )
        assert result.startswith("happy, fast:")

    def test_orpheus_chuckle_for_joke(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "That's a terrible pun.", _Sentiment("neutral"), provider=provider,
            context={"is_joke": True, "hour": 14},
            user_profile={"age_group": "adult"},
        )
        assert "<chuckle>" in result

    def test_orpheus_no_consecutive_paralingual(self):
        """Same paralingual must not appear twice in a row."""
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        ctx = {"is_joke": True, "hour": 14}
        up = {"age_group": "adult"}
        r1 = ec.compose("Ha.", _Sentiment("neutral"), provider=provider, context=ctx, user_profile=up)
        r2 = ec.compose("Ha.", _Sentiment("neutral"), provider=provider, context=ctx, user_profile=up)
        assert "<chuckle>" in r1
        assert "<chuckle>" not in r2  # suppressed on second call

    def test_orpheus_sigh_for_frustrated_user(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "I'll sort that out.", _Sentiment("frustrated_user", -0.4),
            confidence=0.9, provider=provider,
            context={"hour": 14},
            user_profile={"age_group": "adult"},
        )
        assert "<sigh>" in result

    def test_orpheus_no_sigh_for_toddler(self):
        """Age-appropriate: no sarcastic sighs for toddlers."""
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "Let me help.", _Sentiment("frustrated_user", -0.4),
            confidence=0.9, provider=provider,
            context={"hour": 14},
            user_profile={"age_group": "toddler"},
        )
        assert "<sigh>" not in result

    def test_parler_returns_tuple(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("description")
        # Use a sentiment with matching category so Parler uses "friendly and warm"
        result = ec.compose("Hello.", _Sentiment("positive", 0.3, category="positive"), provider=provider)
        assert isinstance(result, tuple)
        text, desc = result
        assert text == "Hello."
        assert "friendly and warm" in desc

    def test_ssml_wraps_text(self):
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("ssml")
        result = ec.compose("Hello.", _Sentiment("excited", 0.8), provider=provider)
        assert "<speak>" in result
        assert "Hello." in result

    def test_night_mode_auto_detected(self):
        """After 10 PM the context should get night_mode=True injected."""
        from cortex.voice.composer import EmotionComposer

        ec = EmotionComposer()
        provider = self._make_provider("tags")
        result = ec.compose(
            "Good night.",
            _Sentiment("neutral"),
            provider=provider,
            context={"hour": 23, "slow": True},
        )
        assert result.startswith("calm, slow:")


# ===========================================================================
# C11.4 — Voice Registry
# ===========================================================================

class TestVoiceRegistry:
    def _make_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_init_creates_table(self):
        from cortex.voice.registry import init_voice_registry

        conn = self._make_db()
        init_voice_registry(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "tts_voices" in tables

    def test_seed_inserts_orpheus_voices(self):
        from cortex.voice.registry import init_voice_registry, list_voices

        conn = self._make_db()
        init_voice_registry(conn)
        voices = list_voices(conn, provider="orpheus")
        assert len(voices) == 8
        names = {v["display_name"] for v in voices}
        assert "Tara" in names
        assert "Leo" in names

    def test_default_voice_is_tara(self):
        from cortex.voice.registry import init_voice_registry, get_default_voice

        conn = self._make_db()
        init_voice_registry(conn)
        default = get_default_voice(conn)
        assert default is not None
        assert default["id"] == "orpheus_tara"

    def test_list_all_voices(self):
        from cortex.voice.registry import init_voice_registry, list_voices

        conn = self._make_db()
        init_voice_registry(conn)
        all_voices = list_voices(conn)
        assert len(all_voices) == 11  # 8 Orpheus + 3 Piper

    def test_idempotent_init(self):
        """Calling init twice must not duplicate rows."""
        from cortex.voice.registry import init_voice_registry, list_voices

        conn = self._make_db()
        init_voice_registry(conn)
        init_voice_registry(conn)
        assert len(list_voices(conn)) == 11


# ===========================================================================
# C11.5 — Sentence-boundary streaming
# ===========================================================================

class TestSentenceBoundaryStreaming:
    def test_extract_sentence_simple(self):
        from cortex.voice.streaming import extract_complete_sentence

        s, r = extract_complete_sentence("Hello world. This is remaining.")
        assert s == "Hello world."
        assert r == "This is remaining."

    def test_extract_sentence_no_boundary(self):
        from cortex.voice.streaming import extract_complete_sentence

        s, r = extract_complete_sentence("No boundary here")
        assert s == ""
        assert r == "No boundary here"

    def test_extract_sentence_exclamation(self):
        from cortex.voice.streaming import extract_complete_sentence

        s, r = extract_complete_sentence("Great! More text here.")
        assert s == "Great!"

    def test_extract_sentence_question(self):
        from cortex.voice.streaming import extract_complete_sentence

        s, r = extract_complete_sentence("How are you? I am fine.")
        assert s == "How are you?"

    async def test_stream_speech_yields_audio(self):
        from cortex.voice.streaming import stream_speech

        fake_audio = b"\xff\xfe" * 32

        async def _fake_synthesize(text, voice=None, stream=True, **kw):
            async for chunk in _async_chunks(fake_audio):
                yield chunk

        mock_provider = MagicMock()
        mock_provider.synthesize = _fake_synthesize
        mock_provider.get_emotion_format.return_value = None

        async def _tokens():
            for t in ["Hello world. ", "This is Atlas. "]:
                yield t

        chunks = []
        async for item in stream_speech(
            _tokens(),
            _Sentiment("positive", 0.3),
            provider=mock_provider,
        ):
            chunks.append(item)

        assert len(chunks) > 0
        assert all("audio" in c for c in chunks)
        assert all("text" in c for c in chunks)
        assert all("emotion" in c for c in chunks)

    async def test_stream_speech_flushes_remainder(self):
        """Remaining buffer without sentence end must still be spoken."""
        from cortex.voice.streaming import stream_speech

        fake_audio = b"\xab\xcd"

        async def _fake_synthesize(text, voice=None, stream=True, **kw):
            yield fake_audio

        mock_provider = MagicMock()
        mock_provider.synthesize = _fake_synthesize
        mock_provider.get_emotion_format.return_value = None

        async def _tokens():
            yield "No period at end"

        items = []
        async for item in stream_speech(_tokens(), _Sentiment(), provider=mock_provider):
            items.append(item)

        assert len(items) == 1
        assert items[0]["audio"] == fake_audio


# ===========================================================================
# C11.6 — Atlas TTS API endpoint
# ===========================================================================

class TestSpeechEndpoint:
    async def test_health(self):
        from cortex.server import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_list_voices(self):
        from cortex.server import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/audio/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "voices" in data
        assert len(data["voices"]) >= 8

    async def test_create_speech_streams_audio(self):
        """POST /v1/audio/speech should stream bytes from the provider."""
        from cortex.server import app

        fake_audio = b"\x00\x01\x02\x03" * 16

        async def _fake_synthesize(text, voice=None, emotion=None, speed=1.0, stream=True, **kw):
            yield fake_audio

        with patch("cortex.server.get_tts_provider") as mock_factory:
            mock_prov = MagicMock()
            mock_prov.synthesize = _fake_synthesize
            mock_prov.get_emotion_format.return_value = "tags"
            mock_factory.return_value = mock_prov

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/v1/audio/speech",
                    json={"input": "Hello there.", "voice": "tara", "model": "orpheus"},
                )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/")
        assert len(resp.content) > 0

    async def test_create_speech_with_emotion(self):
        """Explicit emotion should be prepended to the text for Orpheus."""
        from cortex.server import app

        captured = {}

        async def _fake_synthesize(text, voice=None, emotion=None, speed=1.0, stream=True, **kw):
            captured["text"] = text
            yield b"\x00"

        with patch("cortex.server.get_tts_provider") as mock_factory:
            mock_prov = MagicMock()
            mock_prov.synthesize = _fake_synthesize
            mock_prov.get_emotion_format.return_value = "tags"
            mock_factory.return_value = mock_prov

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post(
                    "/v1/audio/speech",
                    json={"input": "It worked!", "voice": "tara", "model": "orpheus", "emotion": "happy"},
                )

        assert captured.get("text", "").startswith("happy:")

    async def test_invalid_model_falls_back(self):
        """Unknown model should not raise 500 — it falls back to default."""
        from cortex.server import app

        async def _fake_synthesize(text, voice=None, emotion=None, speed=1.0, stream=True, **kw):
            yield b"\x00"

        with patch("cortex.server.get_tts_provider") as mock_factory:
            mock_prov = MagicMock()
            mock_prov.synthesize = _fake_synthesize
            mock_prov.get_emotion_format.return_value = None
            mock_factory.side_effect = [ValueError("Unknown"), mock_prov]

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/v1/audio/speech",
                    json={"input": "Hello.", "model": "unknown_model"},
                )

        assert resp.status_code == 200
