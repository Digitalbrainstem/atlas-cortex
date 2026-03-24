"""LoRA management utilities.

Tools for loading, switching, and projecting LoRA adapters across
models. Supports cross-dimension projection for transferring
adapters between models of different sizes.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def discover_loras(base_path: str | Path) -> dict[str, Path]:
    """Discover LoRA adapters in a directory tree.

    Scans for directories containing ``adapter_config.json``.
    Returns a map of domain name to adapter path.
    """
    base = Path(base_path)
    loras = {}
    if not base.exists():
        return loras

    for config_file in base.rglob("adapter_config.json"):
        adapter_dir = config_file.parent
        domain = adapter_dir.name
        loras[domain] = adapter_dir
        logger.debug("Found LoRA: %s at %s", domain, adapter_dir)

    return loras


def get_lora_info(adapter_path: str | Path) -> dict[str, Any]:
    """Read adapter config and return key metadata."""
    config_file = Path(adapter_path) / "adapter_config.json"
    if not config_file.exists():
        return {}

    with open(config_file) as f:
        config = json.load(f)

    return {
        "rank": config.get("r", 0),
        "alpha": config.get("lora_alpha", 0),
        "target_modules": config.get("target_modules", []),
        "base_model": config.get("base_model_name_or_path", ""),
        "dropout": config.get("lora_dropout", 0),
    }


def compute_projection_scale(
    source_dim: int,
    target_dim: int,
    method: str = "sqrt",
) -> float:
    """Compute scaling factor for cross-dimension LoRA projection.

    Methods:
      - sqrt: scale = sqrt(target_dim / source_dim)
      - linear: scale = target_dim / source_dim
      - norm_preserving: scale = sqrt(target_dim / source_dim)
        (same as sqrt, explicitly norm-preserving)

    Returns the scalar multiplier to apply after dimension projection.
    """
    if source_dim == target_dim:
        return 1.0
    if source_dim <= 0 or target_dim <= 0:
        raise ValueError(f"Dimensions must be positive: {source_dim}, {target_dim}")

    if method in ("sqrt", "norm_preserving"):
        return math.sqrt(target_dim / source_dim)
    elif method == "linear":
        return target_dim / source_dim
    else:
        raise ValueError(f"Unknown projection method: {method}")


def list_available_models(models_path: str | Path) -> dict[str, dict]:
    """List base models and their LoRA adapters.

    Expects structure::

        models_path/
        ├── base/
        │   └── model-name/ -> model files
        └── loras/
            └── group-name/
                └── domain/ -> adapter files
    """
    base = Path(models_path)
    result = {}

    # Find base models
    base_dir = base / "base"
    if base_dir.exists():
        for model_dir in base_dir.iterdir():
            if model_dir.is_dir() or model_dir.is_symlink():
                result[model_dir.name] = {
                    "path": str(model_dir),
                    "loras": {},
                }

    # Find LoRA groups
    lora_dir = base / "loras"
    if lora_dir.exists():
        for group_dir in sorted(lora_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            group_name = group_dir.name
            loras = discover_loras(group_dir)
            for domain, path in loras.items():
                info = get_lora_info(path)
                # Associate with base model by config
                for model_name, model_info in result.items():
                    model_info["loras"][f"{group_name}/{domain}"] = {
                        "path": str(path),
                        "group": group_name,
                        **info,
                    }

    return result
