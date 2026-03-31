"""LoRA GGUF conversion and management for llama.cpp integration.

Converts safetensors LoRA adapters to GGUF format and manages an inventory
of converted adapters for use with llama-server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Environment configuration ────────────────────────────────────────

_LORA_DIR = os.getenv("LORA_DIR", "/mnt/fastpool/models/atlas/loras")
_LORA_GGUF_DIR = os.getenv("LORA_GGUF_DIR", "")
_LLAMACPP_DIR = os.getenv("LLAMACPP_DIR", "")

# ── Base model identifiers ───────────────────────────────────────────

_BASE_MODELS: dict[str, str] = {
    "core-4b-h100": "Qwen3.5-4B",
    "ultra-9b-v2": "Qwen3.5-9B",
    "focused-9b": "Qwen3.5-9B",
}

_MODEL_SIZE_GROUPS: dict[str, list[str]] = {
    "4b": ["core-4b-h100"],
    "9b": ["ultra-9b-v2", "focused-9b"],
}

# ── Domain mapping ───────────────────────────────────────────────────

DOMAIN_MAP: dict[str, list[str]] = {
    "coding": ["core-4b-h100/coding", "ultra-9b-v2/coding"],
    "medical": ["core-4b-h100/medicine", "ultra-9b-v2/medicine"],
    "math": ["core-4b-h100/math_reasoning", "ultra-9b-v2/math_reasoning"],
    "science": ["core-4b-h100/biology_biomed", "ultra-9b-v2/physics_chemistry"],
    "creative": ["core-4b-h100/creative_arts", "ultra-9b-v2/creative_arts"],
    "engineering": ["core-4b-h100/engineering", "ultra-9b-v2/engineering"],
    "ai_ml": ["core-4b-h100/ai_ml", "ultra-9b-v2/ai_ml"],
    "game_dev": ["focused-9b/game_dev"],
    "history": ["focused-9b/history"],
    "music": ["focused-9b/music"],
    "networking": ["focused-9b/network_engineering"],
    "robotics": ["focused-9b/robotics_iot"],
    "electronics": ["focused-9b/electronics"],
}


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class LoRAAdapter:
    """A single LoRA adapter, possibly with a converted GGUF variant."""

    name: str
    group: str
    base_model: str
    safetensors_path: str
    gguf_path: str | None = None
    size_bytes: int = 0
    domain: str = ""


# ── Manager ──────────────────────────────────────────────────────────


class LoRAGGUFManager:
    """Manages LoRA discovery, GGUF conversion, and llama-server integration."""

    def __init__(
        self,
        lora_dir: str | None = None,
        gguf_dir: str | None = None,
        llamacpp_dir: str | None = None,
    ) -> None:
        self.lora_dir = Path(lora_dir or _LORA_DIR)
        self.gguf_dir = Path(gguf_dir or _LORA_GGUF_DIR or self.lora_dir / "gguf")
        self.llamacpp_dir = Path(llamacpp_dir or _LLAMACPP_DIR) if (llamacpp_dir or _LLAMACPP_DIR) else None
        self._inventory: dict[str, LoRAAdapter] = {}

    # ── Discovery ─────────────────────────────────────────────────

    def discover_loras(self, lora_dir: str | None = None) -> list[LoRAAdapter]:
        """Scan a directory tree for safetensors LoRA adapters.

        Expects structure like ``<group>/<domain>/adapter_model.safetensors``.
        Returns a list of discovered :class:`LoRAAdapter` instances and
        updates the internal inventory.
        """
        base = Path(lora_dir) if lora_dir else self.lora_dir
        adapters: list[LoRAAdapter] = []

        if not base.exists():
            logger.warning("LoRA directory does not exist: %s", base)
            return adapters

        for safetensors_file in sorted(base.rglob("adapter_model.safetensors")):
            adapter_dir = safetensors_file.parent
            group_dir = adapter_dir.parent

            # Only consider adapters nested as <group>/<domain>/
            if group_dir == base or group_dir.parent != base:
                continue

            group = group_dir.name
            domain = adapter_dir.name
            base_model = _BASE_MODELS.get(group, "unknown")

            try:
                size_bytes = safetensors_file.stat().st_size
            except OSError:
                size_bytes = 0

            key = f"{group}/{domain}"
            gguf_path = self._gguf_path_for(group, domain)
            existing_gguf = str(gguf_path) if gguf_path.exists() else None

            adapter = LoRAAdapter(
                name=domain,
                group=group,
                base_model=base_model,
                safetensors_path=str(adapter_dir),
                gguf_path=existing_gguf,
                size_bytes=size_bytes,
                domain=domain,
            )
            adapters.append(adapter)
            self._inventory[key] = adapter
            logger.debug("Discovered LoRA: %s (%s)", key, base_model)

        logger.info("Discovered %d LoRA adapters", len(adapters))
        return adapters

    def get_available_gguf_loras(self) -> list[LoRAAdapter]:
        """Return only adapters that have already been converted to GGUF."""
        if not self._inventory:
            self.discover_loras()
        return [a for a in self._inventory.values() if a.gguf_path is not None]

    def get_lora_for_domain(
        self, domain: str, model_size: str = "4b"
    ) -> LoRAAdapter | None:
        """Find the best LoRA adapter for a given task domain.

        Prefers adapters matching *model_size* (``"4b"`` or ``"9b"``).
        Falls back to any available adapter for the domain.
        """
        candidates = DOMAIN_MAP.get(domain)
        if not candidates:
            return None

        if not self._inventory:
            self.discover_loras()

        preferred_groups = _MODEL_SIZE_GROUPS.get(model_size, [])

        # First pass: match preferred model size
        for key in candidates:
            group = key.split("/", 1)[0]
            if group in preferred_groups and key in self._inventory:
                return self._inventory[key]

        # Second pass: any available adapter
        for key in candidates:
            if key in self._inventory:
                return self._inventory[key]

        return None

    # ── Conversion ────────────────────────────────────────────────

    async def convert_lora(
        self,
        adapter_path: str,
        output_path: str | None = None,
        base_model: str = "",
    ) -> str:
        """Convert a safetensors LoRA adapter to GGUF format.

        Uses llama.cpp's ``convert_lora_to_gguf.py`` script.  The converted
        file is cached — subsequent calls with the same *adapter_path* return
        the cached result immediately.

        Returns the path to the converted GGUF file.
        """
        adapter_dir = Path(adapter_path)
        if not adapter_dir.exists():
            raise FileNotFoundError(f"Adapter directory not found: {adapter_dir}")

        safetensors_file = adapter_dir / "adapter_model.safetensors"
        if not safetensors_file.exists():
            raise FileNotFoundError(
                f"No adapter_model.safetensors in {adapter_dir}"
            )

        # Determine output path
        if output_path:
            out = Path(output_path)
        else:
            group = adapter_dir.parent.name
            domain = adapter_dir.name
            out = self._gguf_path_for(group, domain)

        # Return cached conversion
        if out.exists() and out.stat().st_size > 0:
            logger.info("GGUF already exists, skipping conversion: %s", out)
            self._update_inventory(adapter_dir, str(out))
            return str(out)

        # Find the convert script
        script = self._find_convert_script()

        out.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            str(script),
            str(adapter_dir),
            "--outfile",
            str(out),
        ]
        if base_model:
            cmd.extend(["--base", base_model])

        logger.info("Converting LoRA to GGUF: %s → %s", adapter_dir, out)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            detail = (stderr or stdout or b"").decode(errors="replace")[-2000:]
            logger.error("LoRA GGUF conversion failed:\n%s", detail)
            raise RuntimeError(f"LoRA GGUF conversion failed (rc={proc.returncode})")

        if not out.exists():
            raise RuntimeError(f"Conversion finished but output not found: {out}")

        logger.info(
            "LoRA GGUF created: %s (%.1f MB)",
            out,
            out.stat().st_size / 1e6,
        )

        self._update_inventory(adapter_dir, str(out))

        return str(out)

    async def convert_all(
        self, model_size: str = "4b"
    ) -> list[tuple[str, str | Exception]]:
        """Convert all discovered adapters matching *model_size*.

        Returns a list of ``(key, gguf_path_or_exception)`` tuples.
        """
        if not self._inventory:
            self.discover_loras()

        groups = _MODEL_SIZE_GROUPS.get(model_size, [])
        results: list[tuple[str, str | Exception]] = []

        for key, adapter in self._inventory.items():
            if adapter.group not in groups:
                continue
            if adapter.gguf_path is not None:
                results.append((key, adapter.gguf_path))
                continue
            try:
                path = await self.convert_lora(
                    adapter.safetensors_path, base_model=adapter.base_model
                )
                results.append((key, path))
            except Exception as exc:
                logger.error("Failed to convert %s: %s", key, exc)
                results.append((key, exc))

        return results

    # ── Server integration ────────────────────────────────────────

    def get_server_args(
        self,
        domain: str,
        model_size: str = "4b",
        scale: float | None = None,
    ) -> list[str]:
        """Return llama-server CLI arguments to load the LoRA for *domain*.

        Uses ``--lora <path>`` or ``--lora-scaled <path> <scale>`` depending
        on whether *scale* is provided.

        Returns an empty list if no converted GGUF is available.
        """
        adapter = self.get_lora_for_domain(domain, model_size=model_size)
        if adapter is None or adapter.gguf_path is None:
            return []

        if scale is not None:
            return ["--lora-scaled", adapter.gguf_path, str(scale)]
        return ["--lora", adapter.gguf_path]

    # ── Helpers ───────────────────────────────────────────────────

    def _update_inventory(self, adapter_dir: Path, gguf_path: str) -> None:
        """Update the inventory entry for an adapter with its GGUF path."""
        group = adapter_dir.parent.name
        domain = adapter_dir.name
        key = f"{group}/{domain}"
        if key in self._inventory:
            self._inventory[key].gguf_path = gguf_path

    def _gguf_path_for(self, group: str, domain: str) -> Path:
        """Return the expected GGUF output path for a given adapter."""
        return self.gguf_dir / group / f"{domain}.gguf"

    def _find_convert_script(self) -> Path:
        """Locate llama.cpp's ``convert_lora_to_gguf.py``."""
        candidates: list[Path] = []
        if self.llamacpp_dir:
            candidates.append(self.llamacpp_dir / "convert_lora_to_gguf.py")
        candidates.extend([
            Path("llama.cpp") / "convert_lora_to_gguf.py",
            Path.home() / "llama.cpp" / "convert_lora_to_gguf.py",
            Path("/opt/llama.cpp/convert_lora_to_gguf.py"),
        ])

        for c in candidates:
            if c.exists():
                logger.debug("Using convert script: %s", c)
                return c

        raise FileNotFoundError(
            "llama.cpp convert_lora_to_gguf.py not found. "
            "Set LLAMACPP_DIR or place llama.cpp in the working directory."
        )


# ── Module singleton ─────────────────────────────────────────────────

_manager: LoRAGGUFManager | None = None


def get_lora_gguf_manager() -> LoRAGGUFManager | None:
    """Return the global LoRA GGUF manager (``None`` if not initialized)."""
    return _manager


def set_lora_gguf_manager(mgr: LoRAGGUFManager | None) -> None:
    """Set the global LoRA GGUF manager singleton."""
    global _manager  # noqa: PLW0603
    _manager = mgr
