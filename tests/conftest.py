"""pytest configuration for Atlas Cortex tests."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time

import httpx
import pytest


# Configure asyncio mode for pytest-asyncio
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


@pytest.fixture(scope="module")
def browser_server(tmp_path_factory):
    """Own an isolated server, data directory, port and non-blocking log file."""
    from mocks.conftest import _free_port
    directory = tmp_path_factory.mktemp("browser-server")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "CORTEX_DATA_DIR": str(directory / "data"),
        "CORTEX_CONFIG_DIR": str(directory / "config"),
        "SNAPSHOT_DIR": str(directory / "snapshots"),
        "SNAPSHOT_NAS_ENABLED": "false",
        "CORTEX_JWT_SECRET": secrets.token_urlsafe(32),
        "CORTEX_HOST": "127.0.0.1", "CORTEX_PORT": str(port),
        "LLM_PROVIDER": "openai_compatible", "LLM_URL": "http://127.0.0.1:1",
        "TTS_PROVIDER": "kokoro", "KOKORO_HOST": "127.0.0.1", "KOKORO_PORT": "1",
        "STT_HOST": "127.0.0.1", "STT_PORT": "1",
    }
    with (directory / "server.log").open("w+") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "cortex.server"], env=env,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(60):
                if proc.poll() is not None:
                    break
                try:
                    if httpx.get(f"{base_url}/live", timeout=1).status_code == 200:
                        yield base_url
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            log.seek(0)
            pytest.fail(f"Cortex failed to start:\n{log.read()[-4000:]}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
