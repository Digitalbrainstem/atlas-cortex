"""Abstract LLM provider interface for Atlas Cortex.

Any LLM backend (Ollama, vLLM, LocalAI, LM Studio, etc.) implements this interface.
"""

from __future__ import annotations

import abc
from typing import AsyncGenerator, Any


class LLMProvider(abc.ABC):
    """Abstract interface for any LLM backend."""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]:
        """Send a chat completion request.

        Returns an async generator of text chunks when stream=True,
        or a dict with the full response when stream=False.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """Generate embeddings for *text*.

        Returns a list of floats (the embedding vector).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def list_models(self) -> list[dict[str, Any]]:
        """List available models.

        Each entry contains at minimum: ``name`` (str), ``size_bytes`` (int).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health(self) -> bool:
        """Check if the backend is reachable. Returns True when healthy."""
        raise NotImplementedError

    def supports_embeddings(self) -> bool:
        """Whether this provider can generate embeddings."""
        return False

    def supports_thinking(self) -> bool:
        """Whether models on this provider support extended thinking."""
        return False
