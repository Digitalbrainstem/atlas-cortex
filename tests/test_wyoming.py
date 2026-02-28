"""Tests for Wyoming protocol client (I3.1)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from cortex.voice.wyoming import WyomingClient, WyomingError


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

class FakeStreamReader:
    """Fake asyncio.StreamReader that returns pre-loaded lines."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self._idx = 0

    async def readline(self) -> bytes:
        if self._idx >= len(self._lines):
            return b""
        line = self._lines[self._idx]
        self._idx += 1
        return line


class FakeStreamWriter:
    """Fake asyncio.StreamWriter that captures written bytes."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _patch_connect(reader, writer):
    """Patch WyomingClient._connect to return fake streams."""
    async def _fake_connect(self_):
        return reader, writer
    return patch.object(WyomingClient, "_connect", _fake_connect)


import contextlib

def _patch_read_json(reader):
    """Patch _read_json and _read_line to bypass asyncio.wait_for."""
    async def _fake_read_line(self_, r):
        return await r.readline()

    async def _fake_read_json(self_, r):
        line = await r.readline()
        if not line:
            raise WyomingError("Connection closed before response")
        return json.loads(line.strip())

    @contextlib.contextmanager
    def _combined():
        with patch.object(WyomingClient, "_read_json", _fake_read_json):
            with patch.object(WyomingClient, "_read_line", _fake_read_line):
                yield

    return _combined()


# ------------------------------------------------------------------ #
# Transcribe tests
# ------------------------------------------------------------------ #

class TestTranscribe:
    @pytest.mark.asyncio
    async def test_basic_transcription(self):
        reader = FakeStreamReader([
            json.dumps({"type": "transcript", "data": {"text": "hello world"}}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10300)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            result = await client.transcribe(b"\x01\x02" * 10, sample_rate=16000)

        assert result == "hello world"

        written = bytes(writer.data)
        # First message is audio-start JSON
        first_line_end = written.index(b"\n")
        first = json.loads(written[:first_line_end])
        assert first["type"] == "audio-start"
        assert first["data"]["rate"] == 16000
        assert first["data"]["width"] == 2
        assert first["data"]["channels"] == 1

        # audio-stop JSON is written after the raw audio
        assert b'"audio-stop"' in written

    @pytest.mark.asyncio
    async def test_unexpected_response_type(self):
        reader = FakeStreamReader([
            json.dumps({"type": "error", "data": {"text": "fail"}}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10300)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            with pytest.raises(WyomingError, match="Expected transcript"):
                await client.transcribe(b"\x00" * 100)

    @pytest.mark.asyncio
    async def test_empty_audio(self):
        reader = FakeStreamReader([
            json.dumps({"type": "transcript", "data": {"text": ""}}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10300)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            result = await client.transcribe(b"")

        assert result == ""


# ------------------------------------------------------------------ #
# Synthesize tests
# ------------------------------------------------------------------ #

class TestSynthesize:
    @pytest.mark.asyncio
    async def test_basic_synthesis(self):
        audio_chunk = b"\x00\x01\x02\x03audio_data_here\n"
        reader = FakeStreamReader([
            json.dumps({"type": "audio-start", "data": {"rate": 22050, "width": 2, "channels": 1}}).encode() + b"\n",
            audio_chunk,
            json.dumps({"type": "audio-stop"}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10301)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            result = await client.synthesize("Hello there")

        written = bytes(writer.data)
        msg = json.loads(written.split(b"\n")[0])
        assert msg["type"] == "synthesize"
        assert msg["data"]["text"] == "Hello there"

    @pytest.mark.asyncio
    async def test_synthesis_with_voice(self):
        reader = FakeStreamReader([
            json.dumps({"type": "audio-start", "data": {"rate": 22050}}).encode() + b"\n",
            json.dumps({"type": "audio-stop"}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10301)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            await client.synthesize("Hi", voice="en-amy")

        written = bytes(writer.data)
        msg = json.loads(written.split(b"\n")[0])
        assert msg["data"]["voice"] == {"name": "en-amy"}

    @pytest.mark.asyncio
    async def test_unexpected_header(self):
        reader = FakeStreamReader([
            json.dumps({"type": "error"}).encode() + b"\n",
        ])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10301)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            with pytest.raises(WyomingError, match="Expected audio-start"):
                await client.synthesize("Hello")


# ------------------------------------------------------------------ #
# Health check tests
# ------------------------------------------------------------------ #

class TestHealth:
    @pytest.mark.asyncio
    async def test_healthy(self):
        writer = FakeStreamWriter()
        reader = FakeStreamReader([])

        client = WyomingClient("localhost", 10300)
        with _patch_connect(reader, writer):
            assert await client.health() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        async def _fail(self_):
            raise OSError("refused")

        client = WyomingClient("localhost", 10300)
        with patch.object(WyomingClient, "_connect", _fail):
            assert await client.health() is False


# ------------------------------------------------------------------ #
# Connection error tests
# ------------------------------------------------------------------ #

class TestConnectionErrors:
    @pytest.mark.asyncio
    async def test_connect_failure(self):
        async def _fail(self_):
            raise WyomingError("Cannot connect to localhost:10300: refused")

        client = WyomingClient("localhost", 10300)
        with patch.object(WyomingClient, "_connect", _fail):
            with pytest.raises(WyomingError, match="Cannot connect"):
                await client.transcribe(b"\x00")

    @pytest.mark.asyncio
    async def test_empty_response(self):
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()

        client = WyomingClient("localhost", 10300)
        with _patch_connect(reader, writer), _patch_read_json(reader):
            with pytest.raises(WyomingError, match="Connection closed"):
                await client.transcribe(b"\x00")
