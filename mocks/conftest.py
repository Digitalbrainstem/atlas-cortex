"""Pytest fixtures for Atlas Cortex mock servers.

Auto-starts mock LLM, STT, and TTS servers on ephemeral ports for tests.

Usage in tests:
    def test_pipeline(mock_servers):
        # mock_servers is a dict with server URLs
        # Environment is already configured for Atlas Cortex
        assert mock_servers["llm_url"].startswith("http://")
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import AsyncGenerator

import pytest
import uvicorn


def _free_port() -> int:
    """Get a free port from the OS."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _MockServerRunner:
    """Runs a uvicorn server in a background task."""

    def __init__(self, app_import: str, port: int):
        self.port = port
        self._config = uvicorn.Config(
            app_import, host="127.0.0.1", port=port,
            log_level="error", access_log=False,
        )
        self._server = uvicorn.Server(self._config)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve())
        # Wait for server to be ready
        for _ in range(50):  # 5 seconds max
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                await writer.wait_closed()
                return
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.1)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task:
            await self._task


@pytest.fixture
async def mock_servers():
    """Start mock LLM, STT, TTS servers and configure environment.

    Yields a dict:
        {
            "llm_url": "http://127.0.0.1:PORT",
            "stt_host": "127.0.0.1",
            "stt_port": PORT,
            "tts_url": "http://127.0.0.1:PORT",
        }
    """
    llm_port = _free_port()
    stt_port = _free_port()
    tts_port = _free_port()

    runners = [
        _MockServerRunner("mocks.mock_llm_server:app", llm_port),
        _MockServerRunner("mocks.mock_stt_server:app", stt_port),
        _MockServerRunner("mocks.mock_tts_server:app", tts_port),
    ]

    # Set env vars for Atlas Cortex
    env_backup = {}
    env_vars = {
        "LLM_URL": f"http://127.0.0.1:{llm_port}",
        "LLM_PROVIDER": "ollama",
        "STT_HOST": "127.0.0.1",
        "STT_PORT": str(stt_port),
        "TTS_PROVIDER": "kokoro",
        "KOKORO_URL": f"http://127.0.0.1:{tts_port}",
        "MODEL_FAST": "qwen2.5:7b",
        "MODEL_THINKING": "qwen2.5:7b",
    }
    for key, val in env_vars.items():
        env_backup[key] = os.environ.get(key)
        os.environ[key] = val

    # Start all servers
    for r in runners:
        await r.start()

    yield {
        "llm_url": f"http://127.0.0.1:{llm_port}",
        "stt_host": "127.0.0.1",
        "stt_port": stt_port,
        "tts_url": f"http://127.0.0.1:{tts_port}",
    }

    # Cleanup
    for r in runners:
        await r.stop()

    for key, val in env_backup.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


@pytest.fixture
def benchmark_data() -> list[dict]:
    """Load benchmark results as test data."""
    results_path = Path(__file__).parent / "data" / "benchmark_results.json"
    if results_path.exists():
        return json.loads(results_path.read_text())
    return []


@pytest.fixture
def timing_profiles() -> dict:
    """Load timing profiles."""
    profiles_path = Path(__file__).parent / "data" / "timing_profiles.json"
    if profiles_path.exists():
        return json.loads(profiles_path.read_text())
    return {}
