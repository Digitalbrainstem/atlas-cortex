"""LLM backend probe/discovery for Atlas Cortex installer.

Probes localhost (and optionally the local network) for running LLM backends
and returns connection details for the first healthy one found.

See docs/installation.md for the full provider auto-detection design.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Ordered list of backends to probe
PROBE_TARGETS = [
    {
        "name": "Ollama",
        "urls": ["http://localhost:11434", "http://127.0.0.1:11434"],
        "health_path": "/api/tags",
        "provider": "ollama",
    },
    {
        "name": "LM Studio",
        "urls": ["http://localhost:1234"],
        "health_path": "/v1/models",
        "provider": "openai_compatible",
    },
    {
        "name": "LocalAI",
        "urls": ["http://localhost:8080"],
        "health_path": "/v1/models",
        "provider": "openai_compatible",
    },
    {
        "name": "vLLM",
        "urls": ["http://localhost:8000"],
        "health_path": "/v1/models",
        "provider": "openai_compatible",
    },
    {
        "name": "text-generation-webui",
        "urls": ["http://localhost:5000", "http://localhost:5001"],
        "health_path": "/v1/models",
        "provider": "openai_compatible",
    },
    {
        "name": "koboldcpp",
        "urls": ["http://localhost:5001"],
        "health_path": "/api/v1/model",
        "provider": "openai_compatible",
    },
    {
        "name": "llama.cpp",
        "urls": ["http://localhost:8080"],
        "health_path": "/v1/models",
        "provider": "openai_compatible",
    },
]


async def probe_backend(target: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    """Probe a single backend. Returns connection info or None."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in target["urls"]:
            try:
                resp = await client.get(f"{url}{target['health_path']}")
                if resp.status_code == 200:
                    return {
                        "name": target["name"],
                        "url": url,
                        "provider": target["provider"],
                        "health_path": target["health_path"],
                    }
            except Exception:
                continue
    return None


async def discover_backends() -> list[dict[str, Any]]:
    """Probe all known backends concurrently.

    Returns a list of found backends, sorted by probe order (Ollama first).
    """
    tasks = [probe_backend(t) for t in PROBE_TARGETS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    found = []
    for result in results:
        if isinstance(result, dict):
            found.append(result)
    return found


def discover_backends_sync() -> list[dict[str, Any]]:
    """Synchronous wrapper for use in the CLI installer."""
    return asyncio.run(discover_backends())
