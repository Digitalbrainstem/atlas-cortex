"""OpenAI-compatible provider for Atlas Cortex.

Works with vLLM, LocalAI, LM Studio, llama.cpp server, text-generation-webui, etc.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx

from .base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """Speaks the OpenAI /v1/chat/completions API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: str = "sk-no-key",
        model: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
        )

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
            "model": model or self.default_model or "default",
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        if stream:
            return self._stream_chat(payload)
        else:
            resp = await self._client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"content": content, "model": data.get("model", model), "done": True}

    async def _stream_chat(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        async with self._client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                delta = data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        payload = {"input": text, "model": model or "text-embedding-ada-002"}
        resp = await self._client.post("/v1/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [{}])[0].get("embedding", [])

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            resp = await self._client.get("/v1/models", timeout=5.0)
            resp.raise_for_status()
            models = resp.json().get("data", [])
            return [
                {"name": m.get("id", ""), "size_bytes": 0, "supports_thinking": False}
                for m in models
            ]
        except Exception:
            return []

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/v1/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def supports_embeddings(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
