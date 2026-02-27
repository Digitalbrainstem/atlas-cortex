"""Provider factory for Atlas Cortex.

Usage::

    from cortex.providers import get_provider
    provider = get_provider()          # auto from environment
    provider = get_provider("ollama")
    provider = get_provider("openai_compatible", base_url="http://...")
"""

from __future__ import annotations

import os
from typing import Any

from .base import LLMProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

__all__ = ["LLMProvider", "OllamaProvider", "OpenAICompatibleProvider", "get_provider"]

_PROVIDERS = {
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def get_provider(
    provider_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """Return a configured LLMProvider.

    If *provider_name* is omitted, reads ``LLM_PROVIDER`` from the environment
    (default: ``"ollama"``).
    """
    if provider_name is None:
        provider_name = os.environ.get("LLM_PROVIDER", "ollama")

    provider_name = provider_name.lower().replace("-", "_")
    cls = _PROVIDERS.get(provider_name)
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
