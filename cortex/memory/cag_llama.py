"""CAG engine backed by llama-server's built-in prefix caching.

llama-server (llama.cpp) automatically caches the KV state for shared prompt
prefixes.  We exploit this by placing all knowledge text into the system
prompt.  The first request pays full processing cost (~2 000 tok/s); every
subsequent request that re-uses the same system prefix hits the cache and
only processes the new user/assistant tokens.

Usage:
    engine = LlamaCagEngine("http://localhost:8080")
    await engine.load_knowledge("Medical reference: NeoCardiol ...")
    await engine.warm_cache()               # optional pre-warm

    async for tok in await engine.query("What is the dosing?", stream=True):
        print(tok, end="")

    print(engine.get_cache_stats())

The class is a drop-in replacement for :class:`CAGEngine` when the backend
is llama-server instead of HuggingFace Transformers.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # rough heuristic shared with compaction.py


@dataclass
class CacheStats:
    """Snapshot of prefix-cache statistics."""

    knowledge_tokens: int = 0
    total_queries: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_prompt_ms: float = 0.0
    avg_generation_ms: float = 0.0
    knowledge_hash: str = ""

    @property
    def hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.cache_hits / self.total_queries


# ── Module-level singleton ───────────────────────────────────────────

_llama_cag_engine: LlamaCagEngine | None = None


def get_llama_cag_engine() -> LlamaCagEngine | None:
    return _llama_cag_engine


def set_llama_cag_engine(engine: LlamaCagEngine | None) -> None:
    global _llama_cag_engine
    _llama_cag_engine = engine


class LlamaCagEngine:
    """CAG engine using llama-server's built-in prefix caching.

    Knowledge text is placed in the system prompt so that llama-server's
    prompt-prefix cache retains the KV state across requests.  Only new
    user/assistant tokens are processed after the first (warm-up) request.
    """

    def __init__(
        self,
        llm_url: str = "http://localhost:8080",
        model: str | None = None,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
    ) -> None:
        self.url = llm_url.rstrip("/")
        self.model = model
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens

        self._knowledge_prefix: str = ""
        self._prefix_token_count: int = 0
        self._knowledge_hash: str = ""

        # Timing / stats accumulators
        self._total_queries: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._prompt_ms_accum: float = 0.0
        self._generation_ms_accum: float = 0.0

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.url,
            timeout=None,
        )

    # ── Knowledge management ─────────────────────────────────────────

    async def load_knowledge(self, knowledge_text: str) -> None:
        """Set the knowledge base as system prompt prefix.

        llama-server caches this after the first request that uses it.
        Call :meth:`warm_cache` afterwards to pre-populate the cache.
        """
        import hashlib

        self._knowledge_prefix = knowledge_text
        self._prefix_token_count = max(1, len(knowledge_text) // CHARS_PER_TOKEN)
        self._knowledge_hash = hashlib.sha256(
            knowledge_text.encode()
        ).hexdigest()[:16]
        logger.info(
            "Loaded knowledge prefix: ~%d tokens (hash=%s)",
            self._prefix_token_count,
            self._knowledge_hash,
        )

    async def load_knowledge_from_tree(self, tree: Any) -> None:
        """Load knowledge from a :class:`KnowledgeTree`.

        Concatenates all root-level branch contents into the knowledge
        prefix.  This is the primary integration point between the
        KnowledgeTree and the llama-server CAG layer.
        """
        roots = tree.get_roots()
        if not roots:
            await self.load_knowledge("")
            return

        parts: list[str] = []
        for root in roots:
            content = tree.get_branch_content(root.id, max_depth=2)
            if content:
                parts.append(content)

        await self.load_knowledge("\n\n".join(parts))

    async def warm_cache(self) -> float:
        """Send a minimal request to warm the prefix cache.

        Returns the prompt processing time reported by the server (ms).
        Raises :class:`httpx.HTTPStatusError` on server errors.
        """
        if not self._knowledge_prefix:
            return 0.0

        messages = self._build_messages("Hello", conversation=None)
        payload = self._build_payload(messages, stream=False, max_tokens=1)

        t0 = time.monotonic()
        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        elapsed_ms = (time.monotonic() - t0) * 1000

        data = resp.json()
        prompt_ms = _extract_timing(data, "prompt")
        self._cache_misses += 1
        self._total_queries += 1
        self._prompt_ms_accum += prompt_ms or elapsed_ms

        logger.info("Cache warmed in %.1f ms (prompt: %.1f ms)", elapsed_ms, prompt_ms or elapsed_ms)
        return prompt_ms or elapsed_ms

    async def query(
        self,
        message: str,
        conversation: list[dict[str, str]] | None = None,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None] | str:
        """Query with knowledge-augmented context.

        The system prompt (knowledge) is cached by llama-server;
        only new user/assistant tokens cost processing.

        When *stream* is ``True`` (default), returns an async generator
        yielding text chunks.  When ``False``, returns the complete text.
        """
        messages = self._build_messages(message, conversation)
        payload = self._build_payload(
            messages,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        self._total_queries += 1

        if stream:
            return self._stream_query(payload)
        else:
            return await self._non_stream_query(payload)

    async def update_knowledge(self, new_text: str) -> None:
        """Update knowledge base.  Invalidates cache — next request re-caches."""
        old_hash = self._knowledge_hash
        await self.load_knowledge(new_text)
        if old_hash and old_hash != self._knowledge_hash:
            logger.info(
                "Knowledge updated (old=%s new=%s) — cache invalidated",
                old_hash,
                self._knowledge_hash,
            )

    def get_cache_stats(self) -> CacheStats:
        """Return a snapshot of cache hit-rate, prefix size, etc."""
        avg_prompt = (
            self._prompt_ms_accum / self._total_queries
            if self._total_queries
            else 0.0
        )
        avg_gen = (
            self._generation_ms_accum / self._total_queries
            if self._total_queries
            else 0.0
        )
        return CacheStats(
            knowledge_tokens=self._prefix_token_count,
            total_queries=self._total_queries,
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
            avg_prompt_ms=avg_prompt,
            avg_generation_ms=avg_gen,
            knowledge_hash=self._knowledge_hash,
        )

    # ── Compatibility shims (CAGEngine interface) ────────────────────

    @property
    def mode(self) -> str:
        """Compatibility with :class:`CAGEngine`."""
        return "llama_server"

    async def init(self, db_conn: Any = None) -> None:
        """No-op — llama-server needs no local database."""

    async def format_context(self, query: str, top_k: int = 3) -> str:
        """Return the loaded knowledge prefix.

        Unlike :class:`CAGEngine`, which searches FTS, this returns the
        full cached prefix since llama-server handles the caching itself.
        """
        return self._knowledge_prefix

    async def get_stats(self) -> dict[str, Any]:
        """Return stats in the same dict shape as :class:`CAGEngine`."""
        cs = self.get_cache_stats()
        return {
            "mode": "llama_server",
            "banks": 1 if self._knowledge_prefix else 0,
            "total_tokens": self._prefix_token_count,
            "caches_built": 1 if self._knowledge_prefix else 0,
            "cache_dir": "(server-managed)",
            "torch_available": False,
            "hit_rate": cs.hit_rate,
            "total_queries": cs.total_queries,
        }

    async def health(self) -> bool:
        """Check if llama-server is reachable."""
        try:
            resp = await self._client.get("/v1/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ── Private helpers ──────────────────────────────────────────────

    def _build_messages(
        self,
        message: str,
        conversation: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        """Assemble the messages list with the knowledge system prompt."""
        msgs: list[dict[str, str]] = []
        if self._knowledge_prefix:
            msgs.append({"role": "system", "content": self._knowledge_prefix})
        if conversation:
            msgs.extend(conversation)
        msgs.append({"role": "user", "content": message})
        return msgs

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "stream": stream,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "cache_prompt": True,
        }
        if self.model:
            payload["model"] = self.model

        effective_max = max_tokens or self.default_max_tokens
        if effective_max is not None:
            payload["max_tokens"] = effective_max

        return payload

    async def _non_stream_query(self, payload: dict[str, Any]) -> str:
        t0 = time.monotonic()
        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        elapsed_ms = (time.monotonic() - t0) * 1000

        data = resp.json()
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )

        self._record_timing(data, elapsed_ms)
        return content

    async def _stream_query(
        self, payload: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        t0 = time.monotonic()
        async with self._client.stream(
            "POST", "/v1/chat/completions", json=payload,
        ) as resp:
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
                tok = delta.get("content", "")
                if tok:
                    yield tok

        elapsed_ms = (time.monotonic() - t0) * 1000
        self._record_timing({}, elapsed_ms)

    def _record_timing(self, data: dict[str, Any], elapsed_ms: float) -> None:
        """Update internal timing accumulators from a server response."""
        prompt_ms = _extract_timing(data, "prompt")
        gen_ms = _extract_timing(data, "generation")

        if prompt_ms is not None and prompt_ms < elapsed_ms * 0.3:
            self._cache_hits += 1
        else:
            self._cache_misses += 1

        self._prompt_ms_accum += prompt_ms or elapsed_ms * 0.1
        self._generation_ms_accum += gen_ms or elapsed_ms * 0.9


def _extract_timing(data: dict[str, Any], key: str) -> float | None:
    """Pull a timing value from the llama-server response.

    llama-server may include ``timings`` in the response body with keys
    like ``prompt_ms``, ``generation_ms``, etc.
    """
    timings = data.get("timings") or data.get("usage", {})
    if not timings:
        return None
    # llama-server uses "prompt_ms" / "predicted_ms" in /completion
    # and occasionally "prompt_eval_duration" in other endpoints.
    for suffix in (f"{key}_ms", f"{key}_eval_duration", f"predicted_ms"):
        val = timings.get(suffix)
        if val is not None:
            return float(val)
    return None
