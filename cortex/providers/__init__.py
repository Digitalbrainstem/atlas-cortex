"""Provider factory for Atlas Cortex.

Usage::

    from cortex.providers import get_provider
    provider = get_provider()          # auto from environment (default: transformers)
    provider = get_provider("ollama")
    provider = get_provider("openai_compatible")
    provider = get_provider("transformers")  # direct GPU with KV cache support
"""

# Module ownership: LLM providers: Transformers (default), Ollama, OpenAI-compatible

from __future__ import annotations

import logging
import os
from typing import Any

from .base import LLMProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

# Transformers provider is optional (requires torch)
try:
    from .transformers_provider import TransformersProvider
    _HAS_TRANSFORMERS = True
except ImportError:
    TransformersProvider = None  # type: ignore[misc, assignment]
    _HAS_TRANSFORMERS = False

__all__ = ["LLMProvider", "OllamaProvider", "OpenAICompatibleProvider", "get_provider"]
if _HAS_TRANSFORMERS:
    __all__.append("TransformersProvider")

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}
if _HAS_TRANSFORMERS:
    _PROVIDERS["transformers"] = TransformersProvider


def get_provider(
    provider_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """Return a configured LLMProvider.

    If *provider_name* is omitted, reads ``LLM_PROVIDER`` from the environment
    (default: ``"transformers"``).  Falls back to Ollama when torch is not
    installed.
    """
    if provider_name is None:
        provider_name = os.environ.get("LLM_PROVIDER", "transformers")

    provider_name = provider_name.lower().replace("-", "_")
    cls = _PROVIDERS.get(provider_name)

    # Fallback: if transformers requested but not available, fall back to ollama
    if cls is None and provider_name == "transformers":
        logger.warning(
            "TransformersProvider not available (torch not installed). "
            "Falling back to Ollama."
        )
        cls = _PROVIDERS.get("ollama")

    if cls is None:
        raise ValueError(
            f"Unknown provider '{provider_name}'. "
            f"Choose from: {list(_PROVIDERS)}"
        )

    init_kwargs: dict[str, Any] = {}
    if base_url is not None:
        init_kwargs["base_url"] = base_url
    elif "LLM_URL" in os.environ:
        init_kwargs["base_url"] = os.environ["LLM_URL"]

    if api_key is not None:
        init_kwargs["api_key"] = api_key
    elif "LLM_API_KEY" in os.environ:
        init_kwargs["api_key"] = os.environ["LLM_API_KEY"]

    init_kwargs.update(kwargs)
    return cls(**init_kwargs)
