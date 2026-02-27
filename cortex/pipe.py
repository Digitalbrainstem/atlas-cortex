"""Atlas Cortex — Open WebUI Pipe function.

Drop this file into Open WebUI's functions directory, or reference it via the
Pipe manifest.  The class ``Pipe`` implements the Open WebUI Pipe interface.

Open WebUI calls:
  ``pipe(body) → str | Generator[str, None, None]``
  ``pipes() → list[dict]``          (optional — declares available models)

The full Atlas Cortex pipeline (Layers 0-3) runs inside the pipe, so all
features (instant answers, sentiment, filler streaming, memory, logging) are
available without a separate server.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Late imports to avoid issues when Open WebUI pre-scans the file
_pipeline_ready = False
_provider = None
_db_conn = None


def _ensure_ready() -> None:
    global _pipeline_ready, _provider, _db_conn
    if _pipeline_ready:
        return
    from cortex.db import get_db, init_db
    from cortex.providers import get_provider
    init_db()
    _db_conn = get_db()
    _provider = get_provider()
    _pipeline_ready = True


class Pipe:
    """Open WebUI Pipe function for Atlas Cortex."""

    class Valves:
        """User-configurable knobs shown in Open WebUI settings."""
        LLM_PROVIDER: str = "ollama"
        LLM_URL: str = "http://localhost:11434"
        LLM_API_KEY: str = ""
        MODEL_FAST: str = "qwen2.5:14b"
        MODEL_THINKING: str = "qwen3:30b-a3b"
        CORTEX_DATA_DIR: str = "/data"

    def __init__(self) -> None:
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        """Declare the 'atlas-cortex' model in Open WebUI."""
        return [{"id": "atlas-cortex", "name": "Atlas Cortex"}]

    def pipe(self, body: dict[str, Any]) -> Generator[str, None, None]:
        """Main pipe entry point — called by Open WebUI for each message."""
        # Apply valves to environment so providers/db pick them up
        os.environ.setdefault("LLM_PROVIDER", self.valves.LLM_PROVIDER)
        os.environ.setdefault("LLM_URL", self.valves.LLM_URL)
        os.environ.setdefault("CORTEX_DATA_DIR", self.valves.CORTEX_DATA_DIR)

        _ensure_ready()

        import asyncio
        from cortex.pipeline import run_pipeline

        messages: list[dict[str, str]] = body.get("messages", [])
        user_id: str = body.get("user", {}).get("id", "default") if isinstance(body.get("user"), dict) else "default"
        metadata: dict[str, Any] = body.get("metadata", {})

        if not messages:
            yield "No message provided."
            return

        # Extract last user message
        user_message = ""
        conversation_history: list[dict[str, str]] = []
        for msg in messages:
            conversation_history.append({"role": msg["role"], "content": msg["content"]})
            if msg["role"] == "user":
                user_message = msg["content"]

        if not user_message:
            yield "No user message found."
            return

        history = conversation_history[:-1]  # exclude the current turn

        async def _collect() -> list[str]:
            tokens: list[str] = []
            pipeline = await run_pipeline(
                message=user_message,
                provider=_provider,
                user_id=user_id,
                conversation_history=history,
                metadata=metadata,
                model_fast=self.valves.MODEL_FAST,
                model_thinking=self.valves.MODEL_THINKING,
                db_conn=_db_conn,
            )
            async for chunk in pipeline:
                tokens.append(chunk)
            return tokens

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an async context — run in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _collect())
                    tokens = future.result(timeout=120)
            else:
                tokens = loop.run_until_complete(_collect())
        except Exception as exc:
            logger.exception("Pipe error: %s", exc)
            yield f"Error: {exc}"
            return

        for token in tokens:
            yield token
