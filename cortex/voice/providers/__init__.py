"""TTS provider factory and module-level singleton (C11.1, C11.2)."""

from __future__ import annotations

import os

from cortex.voice.base import TTSProvider

# Registry of provider names → factory callables
_PROVIDER_REGISTRY: dict[str, type[TTSProvider]] = {}


def register_provider(name: str, cls: type[TTSProvider]) -> None:
    """Register a TTS provider class under *name*."""
    _PROVIDER_REGISTRY[name] = cls


def get_tts_provider(config: dict | None = None) -> TTSProvider:
    """Return the configured TTS provider, instantiating it on first call.

    Provider selection order:
      1. ``TTS_PROVIDER`` env-var (or config key)
      2. If Orpheus-FastAPI URL is set → orpheus
      3. Default → orpheus
    """
    cfg = config or _env_config()
    name = cfg.get("TTS_PROVIDER", "orpheus").lower()
    if name not in _PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown TTS provider '{name}'. "
            f"Available: {sorted(_PROVIDER_REGISTRY)}"
        )
    return _PROVIDER_REGISTRY[name](cfg)


def _env_config() -> dict:
    """Build a config dict from environment variables."""
    keys = (
        "TTS_PROVIDER",
        "ORPHEUS_URL",
        "ORPHEUS_MODEL",
        "ORPHEUS_FASTAPI_URL",
        "PIPER_URL",
    )
    return {k: v for k in keys if (v := os.environ.get(k))}


# ---------------------------------------------------------------------------
# Register built-in providers
# ---------------------------------------------------------------------------

from cortex.voice.providers.orpheus import OrpheusTTSProvider  # noqa: E402
from cortex.voice.providers.piper import PiperTTSProvider      # noqa: E402

register_provider("orpheus", OrpheusTTSProvider)
register_provider("piper", PiperTTSProvider)

__all__ = [
    "TTSProvider",
    "OrpheusTTSProvider",
    "PiperTTSProvider",
    "get_tts_provider",
    "register_provider",
]
