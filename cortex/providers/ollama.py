"""Ollama LLM provider for Atlas Cortex."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Talks to Ollama's /api/chat and /api/embeddings endpoints."""

    def __init__(self, base_url: str = "http://localhost:11434", api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or "qwen2.5:14b",
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        payload["options"].update(kwargs)

        if stream:
            return self._stream_chat(payload)
        else:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "content": data.get("message", {}).get("content", ""),
                "model": data.get("model", model),
                "done": True,
            }

    async def _stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    break

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        payload = {"model": model or "nomic-embed-text", "prompt": text}
        resp = await self._client.post("/api/embeddings", json=payload)
        resp.raise_for_status()
        return resp.json().get("embedding", [])

    async def list_models(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return [
            {
                "name": m.get("name", ""),
                "size_bytes": m.get("size", 0),
                "supports_thinking": "qwen3" in m.get("name", "").lower(),
            }
            for m in models
        ]

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def supports_embeddings(self) -> bool:
        return True

    def supports_thinking(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
