"""Tests for voice WebSocket messages and unified dashboard navigation."""

from __future__ import annotations

import base64
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_server_deps():
    """Patch heavy server dependencies so we can import cortex.server."""
    mock_provider = MagicMock()
    mock_provider.health = AsyncMock(return_value=True)

    with (
        patch("cortex.server.init_db"),
        patch("cortex.server.get_db", return_value=MagicMock()),
        patch("cortex.server._get_provider", return_value=mock_provider),
        patch("cortex.server._get_db", return_value=MagicMock()),
    ):
        yield


@pytest.fixture()
def client(_patch_server_deps):
    from cortex.server import app

    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────


def _make_pcm_bytes(num_samples: int = 100) -> bytes:
    """Generate fake 16-bit PCM audio."""
    return struct.pack(f"<{num_samples}h", *([1000] * num_samples))


async def _fake_pipeline(**kwargs):
    for word in ["Hello", " ", "World", "!"]:
        yield word


# ── Voice Input (STT) Tests ──────────────────────────────────────


class TestVoiceSTT:
    def test_audio_roundtrip_with_transcript(self, client):
        """audio_start → audio_data → audio_end returns a transcript."""
        pcm = _make_pcm_bytes(50)
        b64 = base64.b64encode(pcm).decode()

        async def mock_transcribe(audio_data, *, sample_rate=16000):
            assert len(audio_data) > 0
            return "hello atlas"

        with (
            patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe),
            patch("cortex.speech.stt.is_hallucinated", return_value=False),
        ):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_data", "data": b64})
                ws.send_json({"type": "audio_end", "sample_rate": 16000})

                msg = ws.receive_json()
                assert msg["type"] == "transcript"
                assert msg["text"] == "hello atlas"

    def test_empty_audio_returns_empty_transcript(self, client):
        """Sending audio_end without audio_data yields empty transcript."""
        async def mock_transcribe(audio_data, *, sample_rate=16000):
            return ""

        with (
            patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe),
            patch("cortex.speech.stt.is_hallucinated", return_value=False),
        ):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_end"})

                msg = ws.receive_json()
                assert msg["type"] == "transcript"
                assert msg["text"] == ""

    def test_hallucinated_transcript_filtered(self, client):
        """Hallucinated transcripts are replaced with empty string."""
        pcm = _make_pcm_bytes(50)
        b64 = base64.b64encode(pcm).decode()

        async def mock_transcribe(audio_data, *, sample_rate=16000):
            return "thank you for watching"

        with (
            patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe),
            patch("cortex.speech.stt.is_hallucinated", return_value=True),
        ):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_data", "data": b64})
                ws.send_json({"type": "audio_end"})

                msg = ws.receive_json()
                assert msg["type"] == "transcript"
                assert msg["text"] == ""

    def test_stt_failure_returns_empty_transcript(self, client):
        """When STT backend fails, return empty transcript gracefully."""
        pcm = _make_pcm_bytes(50)
        b64 = base64.b64encode(pcm).decode()

        async def mock_transcribe(audio_data, *, sample_rate=16000):
            raise ConnectionError("STT backend down")

        with patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_data", "data": b64})
                ws.send_json({"type": "audio_end"})

                msg = ws.receive_json()
                assert msg["type"] == "transcript"
                assert msg["text"] == ""

    def test_multiple_audio_chunks(self, client):
        """Multiple audio_data chunks are accumulated correctly."""
        pcm1 = _make_pcm_bytes(25)
        pcm2 = _make_pcm_bytes(25)
        b64_1 = base64.b64encode(pcm1).decode()
        b64_2 = base64.b64encode(pcm2).decode()

        captured_audio = {}

        async def mock_transcribe(audio_data, *, sample_rate=16000):
            captured_audio["length"] = len(audio_data)
            return "two chunks"

        with (
            patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe),
            patch("cortex.speech.stt.is_hallucinated", return_value=False),
        ):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_data", "data": b64_1})
                ws.send_json({"type": "audio_data", "data": b64_2})
                ws.send_json({"type": "audio_end"})

                msg = ws.receive_json()
                assert msg["type"] == "transcript"
                assert msg["text"] == "two chunks"
                assert captured_audio["length"] == len(pcm1) + len(pcm2)


# ── Voice Output (TTS) Tests ─────────────────────────────────────


class TestVoiceTTS:
    def test_tts_request_returns_audio(self, client):
        """tts_request with text returns tts_audio with base64 PCM."""
        fake_pcm = _make_pcm_bytes(200)

        async def mock_synthesize(text, voice, **kw):
            return fake_pcm, 24000, "kokoro"

        with patch("cortex.speech.tts.synthesize_speech", side_effect=mock_synthesize):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "tts_request", "text": "hello there"})

                msg = ws.receive_json()
                assert msg["type"] == "tts_audio"
                assert msg["sample_rate"] == 24000
                assert msg["provider"] == "kokoro"

                decoded = base64.b64decode(msg["data"])
                assert decoded == fake_pcm

    def test_tts_empty_text_no_response(self, client):
        """tts_request with empty text doesn't send a response."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "tts_request", "text": ""})
            # Send a chat message to verify the ws is still working
            with patch("cortex.server.run_pipeline", side_effect=_fake_pipeline):
                ws.send_json({"message": "hi"})
                msg = ws.receive_json()
                assert msg["type"] == "start"

    def test_tts_failure_returns_error(self, client):
        """When TTS fails, return tts_error gracefully."""
        async def mock_synthesize(text, voice, **kw):
            raise RuntimeError("TTS provider offline")

        with patch("cortex.speech.tts.synthesize_speech", side_effect=mock_synthesize):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "tts_request", "text": "test"})

                msg = ws.receive_json()
                assert msg["type"] == "tts_error"

    def test_tts_no_provider_returns_error(self, client):
        """When TTS returns empty audio, return tts_error."""
        async def mock_synthesize(text, voice, **kw):
            return b"", 24000, "none"

        with patch("cortex.speech.tts.synthesize_speech", side_effect=mock_synthesize):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"type": "tts_request", "text": "test"})

                msg = ws.receive_json()
                assert msg["type"] == "tts_error"
                assert "No TTS provider" in msg["text"]


# ── Mixed Text + Voice Tests ─────────────────────────────────────


class TestMixedChatVoice:
    def test_text_chat_still_works(self, client):
        """Regular text chat continues to work alongside voice."""
        with patch("cortex.server.run_pipeline", side_effect=_fake_pipeline):
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({"message": "hi", "user_id": "tester"})

                msg = ws.receive_json()
                assert msg["type"] == "start"

                tokens = []
                while True:
                    msg = ws.receive_json()
                    if msg["type"] == "token":
                        tokens.append(msg["text"])
                    elif msg["type"] == "end":
                        break

                assert tokens == ["Hello", " ", "World", "!"]

    def test_voice_then_text(self, client):
        """Voice input followed by text chat on same connection."""
        async def mock_transcribe(audio_data, *, sample_rate=16000):
            return "voice msg"

        pcm = _make_pcm_bytes(50)
        b64 = base64.b64encode(pcm).decode()

        with (
            patch("cortex.speech.stt.transcribe", side_effect=mock_transcribe),
            patch("cortex.speech.stt.is_hallucinated", return_value=False),
            patch("cortex.server.run_pipeline", side_effect=_fake_pipeline),
        ):
            with client.websocket_connect("/ws/chat") as ws:
                # Voice input
                ws.send_json({"type": "audio_start"})
                ws.send_json({"type": "audio_data", "data": b64})
                ws.send_json({"type": "audio_end"})
                msg = ws.receive_json()
                assert msg["type"] == "transcript"

                # Text input
                ws.send_json({"message": "hello"})
                msg = ws.receive_json()
                assert msg["type"] == "start"


# ── Navigation / Dashboard Tests ─────────────────────────────────


class TestUnifiedDashboard:
    def test_default_route_redirects_to_chat(self):
        """The '/' route redirects to '/chat'."""
        import pathlib

        router_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "router"
            / "index.js"
        )
        content = router_path.read_text()
        assert "redirect: '/chat'" in content or 'redirect: "/chat"' in content

    def test_dashboard_route_exists(self):
        """Dashboard still has its own route at /dashboard."""
        import pathlib

        router_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "router"
            / "index.js"
        )
        content = router_path.read_text()
        assert "'/dashboard'" in content or '"/dashboard"' in content

    def test_navbar_has_chat_section(self):
        """NavBar has chat items grouped separately."""
        import pathlib

        navbar_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "components"
            / "NavBar.vue"
        )
        content = navbar_path.read_text()
        assert "chatItems" in content
        assert "'Chat'" in content or '"Chat"' in content

    def test_navbar_has_admin_section(self):
        """NavBar has admin items grouped separately."""
        import pathlib

        navbar_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "components"
            / "NavBar.vue"
        )
        content = navbar_path.read_text()
        assert "adminItems" in content
        assert "'Dashboard'" in content or '"Dashboard"' in content
        assert "'Plugins'" in content or '"Plugins"' in content
        assert "'System'" in content or '"System"' in content

    def test_navbar_admin_label(self):
        """NavBar shows 'Admin' section label."""
        import pathlib

        navbar_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "components"
            / "NavBar.vue"
        )
        content = navbar_path.read_text()
        assert "Admin" in content

    def test_chatview_has_voice_controls(self):
        """ChatView has mic, speaker, and avatar toggle buttons."""
        import pathlib

        chat_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "views"
            / "ChatView.vue"
        )
        content = chat_path.read_text()
        assert "mic-btn" in content or "toggleRecording" in content
        assert "ttsEnabled" in content
        assert "avatarVisible" in content
        assert "avatar-panel" in content

    def test_chatview_has_mobile_responsive(self):
        """ChatView has mobile responsive styles."""
        import pathlib

        chat_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin"
            / "src"
            / "views"
            / "ChatView.vue"
        )
        content = chat_path.read_text()
        assert "@media" in content
        assert "768px" in content
