"""Atlas Cortex — standalone OpenAI-compatible server.

Exposes:
  GET  /v1/models                — list "atlas-cortex" model
  POST /v1/chat/completions      — full pipeline (streaming SSE or JSON)
  GET  /health                   — liveness check

Start with::

    python -m cortex.server
    # or
    uvicorn cortex.server:app --host 0.0.0.0 --port 5100
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from cortex.db import get_db, init_db
from cortex.pipeline import run_pipeline
from cortex.providers import get_provider

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────

app = FastAPI(title="Atlas Cortex", version="1.0.0")

_provider = None
_db_conn = None


def _get_provider():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


def _get_db():
    global _db_conn
    if _db_conn is None:
        init_db()
        _db_conn = get_db()
    return _db_conn


# ──────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "atlas-cortex"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int | None = None
    # Atlas-specific extensions
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    provider = _get_provider()
    healthy = await provider.health()
    return {"status": "ok" if healthy else "degraded", "provider_healthy": healthy}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "atlas-cortex",
                "object": "model",
                "created": 1700000000,
                "owned_by": "atlas-cortex",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw: Request):
    # Extract conversation history and the latest user message
    messages = request.messages
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Last user message is the current query
    user_message = ""
    history: list[dict[str, str]] = []
    for msg in messages:
        if msg.role == "user":
            user_message = msg.content
        history.append({"role": msg.role, "content": msg.content})

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")

    # History excludes the final user turn (it's passed separately)
    conversation_history = history[:-1]

    provider = _get_provider()
    db_conn = _get_db()

    user_id = request.user or request.metadata.get("user_id", "default")
    metadata = request.metadata

    pipeline = await run_pipeline(
        message=user_message,
        provider=provider,
        user_id=user_id,
        conversation_history=conversation_history,
        metadata=metadata,
        db_conn=db_conn,
    )

    if request.stream:
        return StreamingResponse(
            _sse_stream(pipeline, request.model),
            media_type="text/event-stream",
        )
    else:
        full = "".join([chunk async for chunk in pipeline])
        return _json_response(full, request.model)


async def _sse_stream(
    gen: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """Wrap token stream as Server-Sent Events (OpenAI SSE format)."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async for chunk in gen:
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(data)}\n\n"

    # Final chunk
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


def _json_response(content: str, model: str) -> JSONResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    host = os.environ.get("CORTEX_HOST", "0.0.0.0")
    port = int(os.environ.get("CORTEX_PORT", "5100"))
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Atlas Cortex server on %s:%d", host, port)
    uvicorn.run("cortex.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
