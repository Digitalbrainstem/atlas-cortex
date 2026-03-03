#!/usr/bin/env python3
"""Start all mock servers for GPU-free Atlas Cortex development.

Launches mock LLM (Ollama), STT (Whisper), and TTS (Kokoro) servers
on localhost with realistic timing from benchmark data.

Usage:
    # Start all mocks (background, ctrl-C to stop):
    python -m mocks.run

    # Start specific mocks:
    python -m mocks.run --only llm,tts

    # Custom ports:
    python -m mocks.run --llm-port 11434 --stt-port 10300 --tts-port 8880

Environment variables set for Atlas Cortex:
    LLM_URL=http://localhost:11434
    STT_HOST=localhost
    STT_PORT=10300
    TTS_PROVIDER=kokoro
    KOKORO_URL=http://localhost:8880
    MODEL_FAST=qwen2.5:7b
    MODEL_THINKING=qwen2.5:7b
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mocks")


class MockServer:
    """Manages a single mock server process."""

    def __init__(self, name: str, app_module: str, port: int):
        self.name = name
        self.app_module = app_module
        self.port = port
        self._server: uvicorn.Server | None = None

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app_module,
            host="0.0.0.0",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        logger.info("Starting %s on port %d", self.name, self.port)
        await self._server.serve()

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True


async def run_all(args: argparse.Namespace) -> None:
    """Start all requested mock servers."""

    servers: list[MockServer] = []
    enabled = set(args.only.split(",")) if args.only else {"llm", "stt", "tts"}

    if "llm" in enabled:
        servers.append(MockServer("Mock LLM (Ollama)", "mocks.mock_llm_server:app", args.llm_port))
    if "stt" in enabled:
        servers.append(MockServer("Mock STT (Whisper)", "mocks.mock_stt_server:app", args.stt_port))
    if "tts" in enabled:
        servers.append(MockServer("Mock TTS (Kokoro)", "mocks.mock_tts_server:app", args.tts_port))

    if not servers:
        logger.error("No servers to start")
        return

    # Set environment variables for Atlas Cortex
    os.environ["LLM_URL"] = f"http://localhost:{args.llm_port}"
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["STT_HOST"] = "localhost"
    os.environ["STT_PORT"] = str(args.stt_port)
    os.environ["TTS_PROVIDER"] = "kokoro"
    os.environ["KOKORO_URL"] = f"http://localhost:{args.tts_port}"
    os.environ["MODEL_FAST"] = "qwen2.5:7b"
    os.environ["MODEL_THINKING"] = "qwen2.5:7b"

    # Print connection info
    print("\n" + "=" * 60)
    print("ATLAS CORTEX — MOCK DEVELOPMENT ENVIRONMENT")
    print("=" * 60)
    for s in servers:
        print(f"  {s.name:30s}  http://localhost:{s.port}")
    print()
    print("Environment variables set:")
    print(f"  LLM_URL=http://localhost:{args.llm_port}")
    print(f"  STT_HOST=localhost  STT_PORT={args.stt_port}")
    print(f"  KOKORO_URL=http://localhost:{args.tts_port}")
    print(f"  MODEL_FAST=qwen2.5:7b  MODEL_THINKING=qwen2.5:7b")
    print()
    print("Press Ctrl-C to stop all servers.")
    print("=" * 60 + "\n")

    # Handle graceful shutdown
    def shutdown(sig, frame):
        logger.info("Shutting down mock servers...")
        for s in servers:
            s.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Run all servers concurrently
    tasks = [asyncio.create_task(s.start()) for s in servers]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Start Atlas Cortex mock servers for GPU-free development"
    )
    parser.add_argument("--llm-port", type=int, default=11434,
                        help="Mock LLM (Ollama) port (default: 11434)")
    parser.add_argument("--stt-port", type=int, default=10300,
                        help="Mock STT (Whisper) port (default: 10300)")
    parser.add_argument("--tts-port", type=int, default=8880,
                        help="Mock TTS (Kokoro) port (default: 8880)")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of servers: llm,stt,tts")
    args = parser.parse_args()

    asyncio.run(run_all(args))


if __name__ == "__main__":
    main()
