"""OpenAI mock wire compatibility without live services or simulated delays."""

from __future__ import annotations

import json

import httpx
import pytest

from mocks.mock_llm_server import app


@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setattr("mocks.mock_llm_server._RESPONSES", {
        "fixture": {
            "response_text": "Hello world.",
            "pipeline_total_ms": 0, "ttft_ms": 0, "filler_text": "",
        },
    })


async def test_openai_models_and_nonstreaming(recorded):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock") as client:
        models = await client.get("/v1/models")
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "qwen2.5:7b"
        response = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "fixture"}], "stream": False,
        })
        assert response.json()["choices"][0]["message"]["content"] == "Hello world."
        assert response.json()["choices"][0]["finish_reason"] == "stop"


async def test_openai_streaming_retains_tokens_and_completion_id(recorded):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock") as client:
        response = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "fixture"}], "stream": True,
        })
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks) == "Hello world."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


async def test_legacy_ollama_endpoint_remains_compatible(recorded):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock") as client:
        response = await client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "fixture"}], "stream": False,
        })
    assert response.json()["message"]["content"] == "Hello world."
