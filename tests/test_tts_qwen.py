"""Tests for Qwen3-TTS provider — cortex/voice/providers/qwen3_tts.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.voice.providers.qwen3_tts import Qwen3TTSProvider, _QWEN3_VOICES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    """Create a Qwen3TTSProvider with test defaults."""
    return Qwen3TTSProvider({"QWEN_TTS_HOST": "testhost", "QWEN_TTS_PORT": "9999"})


@pytest.fixture
def default_provider():
    """Create a Qwen3TTSProvider with env-var defaults."""
    return Qwen3TTSProvider()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_custom_config(self, provider):
        assert provider.host == "testhost"
        assert provider.port == 9999
        assert provider.base_url == "http://testhost:9999"

    def test_default_config(self, default_provider):
        assert default_provider.host == "localhost"
        assert default_provider.port == 8766
        assert default_provider.base_url == "http://localhost:8766"

    def test_default_speaker(self, provider):
        assert provider.default_speaker == "Ryan"

    def test_default_language(self, provider):
        assert provider.default_language == "English"


# ---------------------------------------------------------------------------
# Voice resolution
# ---------------------------------------------------------------------------

class TestVoiceResolution:
    def test_none_voice_returns_default(self, provider):
        assert provider._resolve_speaker(None) == "Ryan"

    def test_prefixed_voice(self, provider):
        assert provider._resolve_speaker("qwen3_Vivian") == "Vivian"

    def test_bare_voice(self, provider):
        assert provider._resolve_speaker("Serena") == "Serena"

    def test_case_insensitive(self, provider):
        assert provider._resolve_speaker("ryan") == "Ryan"

    def test_unknown_voice_passthrough(self, provider):
        assert provider._resolve_speaker("custom_speaker") == "custom_speaker"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_english_speakers(self):
        assert Qwen3TTSProvider._detect_language("Ryan") == "English"
        assert Qwen3TTSProvider._detect_language("Aiden") == "English"

    def test_chinese_speakers(self):
        assert Qwen3TTSProvider._detect_language("Vivian") == "Chinese"
        assert Qwen3TTSProvider._detect_language("Uncle_Fu") == "Chinese"

    def test_japanese_speaker(self):
        assert Qwen3TTSProvider._detect_language("Ono_Anna") == "Japanese"

    def test_korean_speaker(self):
        assert Qwen3TTSProvider._detect_language("Sohee") == "Korean"

    def test_unknown_defaults_english(self):
        assert Qwen3TTSProvider._detect_language("UnknownSpeaker") == "English"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_supports_emotion(self, provider):
        assert provider.supports_emotion() is True

    def test_supports_streaming(self, provider):
        assert provider.supports_streaming() is True

    def test_no_phonemes(self, provider):
        assert provider.supports_phonemes() is False

    def test_emotion_format(self, provider):
        assert provider.get_emotion_format() == "description"


# ---------------------------------------------------------------------------
# WAV-to-PCM helper
# ---------------------------------------------------------------------------

class TestWavToPcm:
    def test_empty_input(self):
        assert Qwen3TTSProvider._wav_to_pcm(b"") == b""

    def test_raw_pcm_passthrough(self):
        raw = b"\x00\x01\x02\x03"
        assert Qwen3TTSProvider._wav_to_pcm(raw) == raw

    def test_wav_extraction(self):
        """Build a minimal WAV and confirm PCM extraction."""
        import io
        import wave
        pcm = b"\x00\x01" * 100
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm)
        wav_bytes = buf.getvalue()
        result = Qwen3TTSProvider._wav_to_pcm(wav_bytes)
        assert result == pcm


# ---------------------------------------------------------------------------
# Synthesize — mocked HTTP
# ---------------------------------------------------------------------------

class TestSynthesize:
    @pytest.fixture
    def _mock_wav(self):
        """Build a small WAV for mocking."""
        import io
        import wave
        pcm = b"\x00\x01" * 50
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm)
        return buf.getvalue(), pcm

    async def test_synthesize_builds_correct_payload(self, provider, _mock_wav):
        """Verify the JSON payload sent to /api/tts/custom-voice/stream."""
        wav_bytes, pcm = _mock_wav
        captured_payload = {}

        class FakeResp:
            status = 200
            content = _AsyncChunkIter([pcm])

            async def text(self):
                return ""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def post(self, url, json=None, **kw):
                captured_payload.update(json or {})
                captured_payload["_url"] = url
                return FakeResp()

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            chunks = []
            async for chunk in provider.synthesize("Hello world", voice="qwen3_Ryan"):
                chunks.append(chunk)

        assert captured_payload["_url"] == "http://testhost:9999/api/tts/custom-voice/stream"
        assert captured_payload["text"] == "Hello world"
        assert captured_payload["speaker"] == "Ryan"
        assert captured_payload["language"] == "English"

    async def test_synthesize_with_emotion(self, provider, _mock_wav):
        """Verify emotion/instruct is included in the payload."""
        wav_bytes, pcm = _mock_wav
        captured_payload = {}

        class FakeResp:
            status = 200
            content = _AsyncChunkIter([pcm])

            async def text(self):
                return ""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def post(self, url, json=None, **kw):
                captured_payload.update(json or {})
                return FakeResp()

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            chunks = []
            async for chunk in provider.synthesize(
                "Great news!", voice="qwen3_Aiden", emotion="Speak with excitement"
            ):
                chunks.append(chunk)

        assert captured_payload["instruct"] == "Speak with excitement"
        assert captured_payload["speaker"] == "Aiden"

    async def test_synthesize_server_error_yields_nothing(self, provider):
        """Non-200 from stream falls back to WAV; if WAV also fails → no audio."""

        class FakeResp:
            status = 500

            async def text(self):
                return "Internal Server Error"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def post(self, url, json=None, **kw):
                return FakeResp()

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            chunks = []
            async for chunk in provider.synthesize("Hello"):
                chunks.append(chunk)
            # Both stream and WAV fallback fail → empty
            assert all(c == b"" for c in chunks) or len(chunks) == 0


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:
    async def test_health_ok(self, provider):
        class FakeResp:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def get(self, url, **kw):
                assert "/health" in url
                return FakeResp()

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            assert await provider.health() is True

    async def test_health_unreachable(self, provider):
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def get(self, url, **kw):
                raise ConnectionError("refused")

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            assert await provider.health() is False


# ---------------------------------------------------------------------------
# Voice listing
# ---------------------------------------------------------------------------

class TestListVoices:
    async def test_list_voices_fallback(self, provider):
        """When the server is unreachable, return hardcoded voices."""

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def get(self, url, **kw):
                raise ConnectionError("unreachable")

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            voices = await provider.list_voices()

        assert len(voices) == len(_QWEN3_VOICES)
        ids = {v["id"] for v in voices}
        assert "qwen3_Ryan" in ids
        assert "qwen3_Vivian" in ids
        assert all(v["provider"] == "qwen3_tts" for v in voices)

    async def test_list_voices_from_server(self, provider):
        """When the server responds, use its speaker list."""
        server_speakers = [
            {"name": "Ryan", "gender": "male"},
            {"name": "CustomVoice", "gender": "female"},
        ]

        class FakeResp:
            status = 200

            async def json(self):
                return {"speakers": server_speakers}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def get(self, url, **kw):
                return FakeResp()

        with patch("aiohttp.ClientSession", return_value=FakeSession()):
            voices = await provider.list_voices()

        assert len(voices) == 2
        assert voices[0]["id"] == "qwen3_Ryan"
        assert voices[1]["id"] == "qwen3_CustomVoice"


# ---------------------------------------------------------------------------
# Provider factory registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_qwen3_registered(self):
        from cortex.voice.providers import _PROVIDER_REGISTRY
        assert "qwen3_tts" in _PROVIDER_REGISTRY

    def test_factory_creates_instance(self):
        from cortex.voice.providers import get_tts_provider
        p = get_tts_provider({"TTS_PROVIDER": "qwen3_tts"})
        assert isinstance(p, Qwen3TTSProvider)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AsyncChunkIter:
    """Simulate aiohttp response.content.iter_chunked()."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self._idx = 0

    def iter_chunked(self, size: int):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk
