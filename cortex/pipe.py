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
import asyncio
import atexit
import threading
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Late imports to avoid issues when Open WebUI pre-scans the file
_pipeline_ready = False
_provider = None
_db_conn = None
_worker_lock = threading.Lock()
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None
_worker_coordinator = None
_worker_ready: threading.Event | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop, _worker_thread, _worker_coordinator, _worker_ready
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive() and (
            _worker_ready is None or not _worker_ready.is_set()
        ):
            raise RuntimeError("Pipe conversation worker startup is still pending")
        if (_worker_loop is not None and not _worker_loop.is_closed()
                and _worker_thread is not None and _worker_thread.is_alive()):
            return _worker_loop
        ready = threading.Event()
        _worker_ready = ready
        errors: list[BaseException] = []

        def run() -> None:
            global _worker_loop, _worker_coordinator
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _worker_loop = loop
            try:
                from cortex.orchestrator.conversation import ConversationCoordinator
                from cortex.orchestrator.turn_store import TurnStore
                from cortex.safety.middleware import PipelineSafetyMiddleware
                store = TurnStore(_db_conn)
                store.recover(startup=True)
                _worker_coordinator = ConversationCoordinator(
                    store, _provider, PipelineSafetyMiddleware(db_conn=_db_conn),
                )
                loop.run_until_complete(_worker_coordinator.start())
            except Exception as exc:
                logger.exception("Pipe conversation worker could not start")
                errors.append(exc)
                _worker_loop = None
                ready.set()
                loop.close()
                return
            ready.set()
            loop.run_forever()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

        _worker_thread = threading.Thread(target=run, name="atlas-pipe", daemon=True)
        _worker_thread.start()
        if not ready.wait(30):
            raise RuntimeError("Pipe conversation worker did not start in time")
        if errors:
            raise RuntimeError("Pipe conversation worker failed to start") from errors[0]
        if _worker_loop is None:
            raise RuntimeError("Pipe conversation worker has no event loop")
        return _worker_loop


def _stop_worker() -> None:
    global _worker_loop, _worker_thread, _worker_coordinator, _worker_ready
    with _worker_lock:
        loop = _worker_loop
        if loop is not None and not loop.is_closed():
            try:
                if _worker_coordinator is not None:
                    asyncio.run_coroutine_threadsafe(_worker_coordinator.stop(), loop).result(timeout=10)
                loop.call_soon_threadsafe(loop.stop)
                if _worker_thread is not None:
                    _worker_thread.join(timeout=10)
            except Exception:
                logger.exception("Pipe conversation worker shutdown failed")
                return
            if _worker_thread is not None and _worker_thread.is_alive():
                logger.error("Pipe conversation worker did not stop")
                return
        _worker_loop = None
        _worker_thread = None
        _worker_coordinator = None
        _worker_ready = None


atexit.register(_stop_worker)


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
        LLM_PROVIDER: str = "transformers"
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

        with _worker_lock:
            _ensure_ready()

        import asyncio
        from cortex.pipeline import run_pipeline

        messages: list[dict[str, str]] = body.get("messages", [])
        user_id: str = body.get("user", {}).get("id", "default") if isinstance(body.get("user"), dict) else "default"
        metadata: dict[str, Any] = dict(body.get("metadata") or {})
        metadata["source"] = "pipe"
        if body.get("chat_id"):
            metadata.setdefault("conversation_id", body["chat_id"])
        from cortex.pipeline.turns import TurnIdentity
        identity = TurnIdentity.from_metadata(user_id, metadata)
        metadata.update(turn_id=identity.turn_id, conversation_id=identity.conversation_id)

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
            pipeline = run_pipeline(
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
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if _db_conn is not None:
                future = asyncio.run_coroutine_threadsafe(_collect(), _get_worker_loop())
                tokens = future.result(timeout=120)
            elif loop is not None and loop.is_running():
                # Inside an async context — run in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _collect())
                    tokens = future.result(timeout=120)
            elif loop is not None:
                tokens = loop.run_until_complete(_collect())
            else:
                tokens = asyncio.run(_collect())
        except Exception as exc:
            logger.exception("Pipe error: %s", exc)
            yield f"Error: {exc}"
            return

        for token in tokens:
            yield token
        if _db_conn is not None:
            async def delivered():
                from cortex.pipeline.turns import get_conversation_coordinator
                coordinator = get_conversation_coordinator()
                if coordinator is not None:
                    coordinator.acknowledge_text(identity)
            asyncio.run_coroutine_threadsafe(delivered(), _get_worker_loop()).result(timeout=10)
