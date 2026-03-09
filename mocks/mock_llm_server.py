"""Mock LLM server matching the Ollama /api/chat endpoint.

Serves pre-recorded responses from benchmark data with realistic timing
(simulated TTFT and token streaming delays). No GPU required.

Usage:
    python -m mocks.mock_llm_server --port 11434
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock Ollama (Atlas Cortex)")

# Load benchmark data for response lookup
_DATA_DIR = Path(__file__).parent / "data"
_RESPONSES: dict[str, dict] = {}
_TIMING: dict = {}


def _load_data() -> None:
    results_path = _DATA_DIR / "benchmark_results.json"
    profiles_path = _DATA_DIR / "timing_profiles.json"

    if results_path.exists():
        results = json.loads(results_path.read_text())
        for r in results:
            _RESPONSES[r["question_text"].lower().strip()] = r
        logger.info("Loaded %d benchmark responses", len(_RESPONSES))

    if profiles_path.exists():
        global _TIMING
        _TIMING = json.loads(profiles_path.read_text())
        logger.info("Loaded timing profiles")


@app.on_event("startup")
async def startup():
    _load_data()


def _find_response(message: str) -> dict | None:
    """Find a pre-recorded response or generate a default."""
    msg_lower = message.lower().strip()

    # Exact match
    if msg_lower in _RESPONSES:
        return _RESPONSES[msg_lower]

    # Fuzzy: check if any recorded question is a substring
    for key, resp in _RESPONSES.items():
        if key in msg_lower or msg_lower in key:
            return resp

    return None


def _default_response(message: str) -> dict:
    """Generate a default mock response with average timing."""
    msg_lower = message.lower()
    # Give a real joke for joke requests
    if "joke" in msg_lower or "funny" in msg_lower:
        return {
            "response_text": "Why don't scientists trust atoms? Because they make up everything!",
            "filler_text": "",
            "pipeline_total_ms": 2000,
            "ttft_ms": 150,
            "response_word_count": 11,
        }
    return {
        "response_text": f"I understand you're asking about: {message}. "
                         "This is a mock response for development.",
        "filler_text": "Let me think.",
        "pipeline_total_ms": 4200,
        "ttft_ms": 200,
        "response_word_count": 15,
    }


@app.post("/api/chat")
async def chat(request: Request):
    """Mock Ollama /api/chat endpoint."""
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", True)
    model = body.get("model", "qwen2.5:7b")

    # Get the last user message
    user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_msg = msg.get("content", "")
            break

    # Find pre-recorded response
    recorded = _find_response(user_msg) or _default_response(user_msg)
    full_response = recorded.get("response_text", "Mock response.")
    filler = recorded.get("filler_text", "")
    if filler:
        full_response = f"{filler}\n\n{full_response}"

    pipeline_ms = recorded.get("pipeline_total_ms", 4200)
    ttft_ms = recorded.get("ttft_ms", 200)

    if not stream:
        # Simulate pipeline latency
        if pipeline_ms > 100:
            await asyncio.sleep(pipeline_ms / 1000)

        return JSONResponse({
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "message": {"role": "assistant", "content": full_response},
            "done": True,
            "done_reason": "stop",
            "total_duration": int(pipeline_ms * 1_000_000),
            "load_duration": 0,
            "prompt_eval_count": len(user_msg.split()) * 2,
            "prompt_eval_duration": int(ttft_ms * 1_000_000),
            "eval_count": len(full_response.split()),
            "eval_duration": int((pipeline_ms - ttft_ms) * 1_000_000),
        })

    # Streaming mode
    async def stream_tokens():
        # Simulate TTFT delay
        if ttft_ms > 0:
            await asyncio.sleep(ttft_ms / 1000)

        # Stream tokens (word by word with realistic delays)
        words = full_response.split()
        remaining_ms = max(0, pipeline_ms - ttft_ms)
        per_token_ms = remaining_ms / max(len(words), 1)

        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            chunk = json.dumps({
                "model": model,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "message": {"role": "assistant", "content": token},
                "done": False,
            })
            yield chunk + "\n"
            if per_token_ms > 5:
                await asyncio.sleep(per_token_ms / 1000)

        # Final done message
        yield json.dumps({
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "total_duration": int(pipeline_ms * 1_000_000),
            "eval_count": len(words),
        }) + "\n"

    return StreamingResponse(stream_tokens(), media_type="application/x-ndjson")


@app.get("/api/tags")
async def list_models():
    """Mock model listing."""
    return {
        "models": [{
            "name": "qwen2.5:7b",
            "model": "qwen2.5:7b",
            "size": 4683087332,
            "details": {
                "format": "gguf",
                "family": "qwen2",
                "parameter_size": "7.6B",
                "quantization_level": "Q4_K_M",
            }
        }]
    }


@app.get("/")
async def health():
    return {"status": "ok", "mock": True}


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
