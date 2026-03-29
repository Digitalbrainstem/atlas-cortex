"""Transformers-based LLM provider with CAG (Cache-Augmented Generation) KV cache support.

Runs HuggingFace transformers models directly on GPU, bypassing Ollama.
Supports pre-computed KV cache injection for zero-token knowledge injection.

Usage::

    provider = TransformersProvider(model_name="Qwen/Qwen3-4B")
    await provider.load_model()

    # Standard chat (like Ollama)
    async for token in await provider.chat(messages, stream=True):
        print(token, end="")

    # Chat with CAG KV cache
    async for token in await provider.chat(messages, stream=True, kv_cache=cache, prefix_len=128):
        print(token, end="")

Env vars:
    CAG_MODEL       — HuggingFace model ID (default: Qwen/Qwen3-4B)
    CAG_DEVICE      — Device (default: cuda)
    CAG_DTYPE       — Torch dtype (default: float16)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator

from .base import LLMProvider

logger = logging.getLogger(__name__)

_HAS_TORCH = False
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache, TextIteratorStreamer
    from threading import Thread
    _HAS_TORCH = True
except ImportError:
    pass


class TransformersProvider(LLMProvider):
    """Direct HuggingFace transformers inference with KV cache support.

    This provider loads a model directly onto the GPU, enabling:
    - Pre-computed KV cache injection (CAG)
    - Full control over generation parameters
    - Thinking mode support for Qwen3 models
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        dtype: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.model_name = model_name or os.environ.get("CAG_MODEL", "Qwen/Qwen3-4B")
        self.device = device or os.environ.get("CAG_DEVICE", "cuda")
        self._dtype_str = dtype or os.environ.get("CAG_DTYPE", "float16")
        self._model = None
        self._tokenizer = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    async def load_model(self) -> None:
        """Load model and tokenizer onto device."""
        if not _HAS_TORCH:
            raise RuntimeError("PyTorch and transformers are required for TransformersProvider")
        if self._loaded:
            return

        dtype_map = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}
        dtype = dtype_map.get(self._dtype_str, torch.float16)

        logger.info("Loading %s on %s (%s)...", self.model_name, self.device, self._dtype_str)
        loop = asyncio.get_event_loop()
        self._tokenizer, self._model = await loop.run_in_executor(
            None, self._load_sync, dtype,
        )
        self._loaded = True
        if self.device == "cuda" and torch.cuda.is_available():
            vram = torch.cuda.memory_allocated() / 1e9
            logger.info("Model loaded. VRAM: %.1fGB", vram)

    def _load_sync(self, dtype):
        tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_name, dtype=dtype,
        ).to(self.device)
        model.eval()
        return tokenizer, model

    async def unload_model(self) -> None:
        """Free GPU memory."""
        if self._model is not None:
            del self._model
            del self._tokenizer
            self._model = None
            self._tokenizer = None
            self._loaded = False
            if _HAS_TORCH and torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("Model unloaded")

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
        if not self._loaded:
            await self.load_model()

        kv_cache = kwargs.pop("kv_cache", None)
        prefix_len = kwargs.pop("prefix_len", 0)
        enable_thinking = kwargs.pop("enable_thinking", False)

        if stream:
            return self._stream_generate(
                messages, temperature, max_tokens or 1024,
                kv_cache, prefix_len, enable_thinking,
            )

        # Non-streaming
        full = []
        async for chunk in self._stream_generate(
            messages, temperature, max_tokens or 1024,
            kv_cache, prefix_len, enable_thinking,
        ):
            full.append(chunk)
        return {"content": "".join(full), "model": self.model_name, "done": True}

    async def _stream_generate(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_new_tokens: int,
        kv_cache: Any | None,
        prefix_len: int,
        enable_thinking: bool,
    ) -> AsyncGenerator[str, None]:
        """Generate tokens with optional KV cache injection."""
        text = self._tokenizer.apply_chat_template(
            messages, tokenize=False,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
        )
        input_ids = self._tokenizer(text, return_tensors="pt").input_ids.to(self.device)
        q_len = input_ids.shape[1]

        gen_kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0.01,
        }
        if temperature > 0.01:
            gen_kwargs["temperature"] = temperature

        # CAG: inject pre-computed KV cache
        if kv_cache is not None and prefix_len > 0:
            cache_clone = self._clone_cache(kv_cache)
            gen_kwargs["past_key_values"] = cache_clone
            gen_kwargs["position_ids"] = torch.arange(
                prefix_len, prefix_len + q_len, device=self.device,
            ).unsqueeze(0)
            gen_kwargs["attention_mask"] = torch.ones(
                1, prefix_len + q_len, device=self.device, dtype=torch.long,
            )

        # Stream via TextIteratorStreamer
        streamer = TextIteratorStreamer(
            self._tokenizer, skip_prompt=True, skip_special_tokens=not enable_thinking,
        )
        gen_kwargs["input_ids"] = input_ids
        gen_kwargs["streamer"] = streamer

        thread = Thread(target=self._generate_thread, args=(gen_kwargs,))
        thread.start()

        loop = asyncio.get_event_loop()
        for chunk in streamer:
            if chunk:
                # Strip thinking tags if not wanted
                if not enable_thinking and "</think>" in chunk:
                    chunk = chunk.split("</think>")[-1]
                if chunk.strip():
                    yield chunk
            await asyncio.sleep(0)

        thread.join()

    def _generate_thread(self, gen_kwargs: dict) -> None:
        """Run model.generate in a thread (for streaming)."""
        with torch.no_grad():
            self._model.generate(**gen_kwargs)

    def _clone_cache(self, cache) -> Any:
        """Deep clone a DynamicCache."""
        c = DynamicCache()
        if hasattr(cache, "layers"):
            for L in cache.layers:
                c.update(L.keys.clone(), L.values.clone(), len(c.layers))
        elif hasattr(cache, "key_cache"):
            for i in range(len(cache.key_cache)):
                if cache.key_cache[i] is not None:
                    c.update(cache.key_cache[i].clone(), cache.value_cache[i].clone(), i)
        return c

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """Embeddings not supported — use Ollama for embeddings."""
        raise NotImplementedError(
            "TransformersProvider does not support embeddings. "
            "Use OllamaProvider for embedding generation."
        )

    async def list_models(self) -> list[dict[str, Any]]:
        return [{
            "name": self.model_name,
            "size_bytes": 0,
            "loaded": self._loaded,
            "device": self.device,
            "supports_thinking": "qwen3" in self.model_name.lower(),
            "supports_kv_cache": True,
        }]

    async def health(self) -> bool:
        if not _HAS_TORCH:
            return False
        if not self._loaded:
            return True  # Not loaded yet but could be
        try:
            return self._model is not None
        except Exception:
            return False

    def supports_embeddings(self) -> bool:
        return False

    def supports_thinking(self) -> bool:
        return "qwen3" in self.model_name.lower()

    def supports_kv_cache(self) -> bool:
        """Whether this provider supports CAG (Cache-Augmented Generation) KV cache injection."""
        return True

    async def aclose(self) -> None:
        await self.unload_model()
