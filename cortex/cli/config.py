"""Atlas CLI configuration — loads from ``~/.atlas/config.yaml``.

Provides a typed, cached singleton that falls back to sensible defaults
when the config file is absent or contains invalid YAML.

Module ownership: CLI configuration loading and defaults
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(os.environ.get("ATLAS_CONFIG_DIR", Path.home() / ".atlas"))
_CONFIG_PATH = _CONFIG_DIR / "config.yaml"

_cached_config: AtlasConfig | None = None  # noqa: F821 — forward ref resolved below


@dataclass
class ModelConfig:
    fast: str = "qwen2.5:14b"
    thinking: str = "qwen3:30b-a3b"
    provider: str = "transformers"


@dataclass
class ServerConfig:
    url: str = "http://localhost:5100"
    llm_url: str = ""


@dataclass
class MemoryConfig:
    auto_recall: bool = True
    auto_archive: bool = True
    max_recall_results: int = 5


@dataclass
class CLIDisplayConfig:
    streaming: bool = True
    syntax_highlight: bool = True
    max_context_messages: int = 50
    prompt_style: str = "atlas"


@dataclass
class ToolsConfig:
    enabled: bool = True
    auto_approve: bool = False


@dataclass
class AtlasConfig:
    """Top-level CLI configuration container."""

    model: ModelConfig = field(default_factory=ModelConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    cli: CLIDisplayConfig = field(default_factory=CLIDisplayConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)


def _dict_to_dataclass(dc_cls: type, data: dict[str, Any]) -> Any:
    """Populate a dataclass from a dict, ignoring unknown keys."""
    if not isinstance(data, dict):
        return dc_cls()
    known = {f.name for f in dc_cls.__dataclass_fields__.values()}
    return dc_cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: Path | None = None) -> AtlasConfig:
    """Load configuration from *path* (or the default location).

    Returns :class:`AtlasConfig` with defaults for any missing keys.
    """
    global _cached_config  # noqa: PLW0603
    if _cached_config is not None and path is None:
        return _cached_config

    cfg_path = path or _CONFIG_PATH
    raw: dict[str, Any] = {}

    if cfg_path.exists():
        try:
            import yaml

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            logger.debug("Loaded config from %s", cfg_path)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s — using defaults", cfg_path, exc)

    config = AtlasConfig(
        model=_dict_to_dataclass(ModelConfig, raw.get("model", {})),
        server=_dict_to_dataclass(ServerConfig, raw.get("server", {})),
        memory=_dict_to_dataclass(MemoryConfig, raw.get("memory", {})),
        cli=_dict_to_dataclass(CLIDisplayConfig, raw.get("cli", {})),
        tools=_dict_to_dataclass(ToolsConfig, raw.get("tools", {})),
    )

    # Env-var overrides
    config.model.fast = os.environ.get("MODEL_FAST", config.model.fast)
    config.model.thinking = os.environ.get("MODEL_THINKING", config.model.thinking)
    config.server.llm_url = os.environ.get(
        "LLM_URL", os.environ.get("OLLAMA_BASE_URL", config.server.llm_url)
    )

    if path is None:
        _cached_config = config
    return config


def save_default_config(path: Path | None = None) -> Path:
    """Write a default ``config.yaml`` if one does not already exist.

    Returns the path written to.
    """
    cfg_path = path or _CONFIG_PATH
    if cfg_path.exists():
        return cfg_path

    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    default_yaml = """\
# Atlas CLI configuration
model:
  fast: qwen2.5:14b
  thinking: qwen3:30b-a3b
  provider: transformers

server:
  url: http://localhost:5100
  llm_url: ""

memory:
  auto_recall: true
  auto_archive: true
  max_recall_results: 5

cli:
  streaming: true
  syntax_highlight: true
  max_context_messages: 50
  prompt_style: "atlas"

tools:
  enabled: true
  auto_approve: false
"""
    cfg_path.write_text(default_yaml, encoding="utf-8")
    return cfg_path


def reset_cached_config() -> None:
    """Clear the cached config (useful for testing)."""
    global _cached_config  # noqa: PLW0603
    _cached_config = None
