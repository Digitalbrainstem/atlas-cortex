"""LoRA adapter lifecycle manager.

Discovers on-disk LoRA adapters, composes them into named Ollama models
(via Modelfile + ``/api/create``), and provides fast domain → model lookups
at inference time.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortex.middleware.lora_tools import discover_loras, get_lora_info

logger = logging.getLogger(__name__)

# Lazy import — httpx is available but we don't want a hard top-level dep
_httpx: Any = None


def _get_httpx() -> Any:
    global _httpx
    if _httpx is None:
        import httpx
        _httpx = httpx
    return _httpx


@dataclass
class ComposedModel:
    """A LoRA adapter that has been baked into a named Ollama model."""

    domain: str
    model_name: str  # e.g. "atlas-coding:latest"
    base_model: str
    adapter_path: str
    info: dict[str, Any] = field(default_factory=dict)


class LoRAManager:
    """Discover, compose, and manage LoRA-backed Ollama models.

    Ollama doesn't support dynamic adapter loading at inference time.
    Instead we create a Modelfile that bakes the adapter into a named
    model, run ``ollama create``, and then call that model by name.
    """

    def __init__(
        self,
        ollama_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._ollama_url = (
            ollama_url
            or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self._timeout = timeout
        # domain -> ComposedModel
        self._composed: dict[str, ComposedModel] = {}

    # ── Discovery ─────────────────────────────────────────────────

    def discover(self, lora_dir: str | Path) -> dict[str, Path]:
        """Scan *lora_dir* for LoRA adapters.

        Returns a map of domain name → adapter directory.
        """
        return discover_loras(lora_dir)

    async def _list_ollama_atlas_models(self) -> list[str]:
        """Query Ollama for existing ``atlas-*`` models."""
        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._ollama_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = data.get("models", [])
                return [
                    m["name"]
                    for m in models
                    if m.get("name", "").startswith("atlas-")
                ]
        except Exception as exc:
            logger.warning("Could not list Ollama models: %s", exc)
            return []

    # ── Composition ───────────────────────────────────────────────

    @staticmethod
    def build_modelfile(base_model: str, adapter_path: str) -> str:
        """Build the Modelfile content for Ollama."""
        # Ollama expects the path to the actual weights file
        p = Path(adapter_path)
        safetensors = p / "adapter_model.safetensors"
        bin_file = p / "adapter_model.bin"
        if safetensors.exists():
            weight_path = str(safetensors)
        elif bin_file.exists():
            weight_path = str(bin_file)
        else:
            # Fall back to the directory itself
            weight_path = str(p)
        return f"FROM {base_model}\nADAPTER {weight_path}\n"

    async def compose(
        self,
        domain: str,
        base_model: str,
        adapter_path: str | Path,
    ) -> ComposedModel | None:
        """Create a composed Ollama model for *domain*.

        Generates a Modelfile, calls ``/api/create``, and registers
        the result in the in-memory lookup table.
        """
        adapter_path = str(adapter_path)
        model_name = f"atlas-{domain}:latest"
        modelfile = self.build_modelfile(base_model, adapter_path)

        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/create",
                    json={"model": model_name, "modelfile": modelfile},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.error("Failed to compose %s: %s", model_name, exc)
            return None

        info = get_lora_info(adapter_path)
        composed = ComposedModel(
            domain=domain,
            model_name=model_name,
            base_model=base_model,
            adapter_path=adapter_path,
            info=info,
        )
        self._composed[domain] = composed
        logger.info("Composed LoRA model %s (domain=%s)", model_name, domain)
        return composed

    async def compose_all(
        self,
        base_model: str,
        lora_dir: str | Path,
        ollama_url: str | None = None,
    ) -> list[ComposedModel]:
        """Discover all LoRAs and compose each into an Ollama model.

        *ollama_url* is accepted for backward-compat but ignored (the
        instance URL from ``__init__`` is used).

        Returns the list of successfully composed models.
        """
        loras = self.discover(lora_dir)
        if not loras:
            logger.info("No LoRA adapters found in %s", lora_dir)

        composed: list[ComposedModel] = []
        for domain, path in loras.items():
            result = await self.compose(domain, base_model, path)
            if result:
                composed.append(result)
                self._register_in_db(result)

        # Also pick up any pre-existing atlas-* models in Ollama
        existing = await self._list_ollama_atlas_models()
        for name in existing:
            # e.g. "atlas-coding:latest" -> "coding"
            domain = name.split(":")[0].removeprefix("atlas-")
            if domain and domain not in self._composed:
                self._composed[domain] = ComposedModel(
                    domain=domain,
                    model_name=name,
                    base_model=base_model,
                    adapter_path="",
                )
                logger.info("Found pre-existing Ollama model %s", name)

        logger.info(
            "LoRA composition complete: %d composed, %d total active",
            len(composed),
            len(self._composed),
        )
        return composed

    # ── Lookup ────────────────────────────────────────────────────

    def get_model_for_domain(self, domain: str) -> str | None:
        """Return the composed model name for *domain*, or ``None``."""
        cm = self._composed.get(domain)
        return cm.model_name if cm else None

    def list_active(self) -> list[ComposedModel]:
        """Return all currently composed LoRA models."""
        return list(self._composed.values())

    # ── Removal ───────────────────────────────────────────────────

    async def remove(self, domain: str) -> bool:
        """Remove a composed LoRA model from Ollama."""
        cm = self._composed.pop(domain, None)
        if cm is None:
            return False

        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    "DELETE",
                    f"{self._ollama_url}/api/delete",
                    json={"model": cm.model_name},
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.error("Failed to remove %s: %s", cm.model_name, exc)
            # Put it back so the state stays consistent
            self._composed[domain] = cm
            return False

        logger.info("Removed composed model %s", cm.model_name)
        return True

    # ── Registry integration ──────────────────────────────────────

    @staticmethod
    def _register_in_db(cm: ComposedModel) -> None:
        """Best-effort registration of a composed model in model_registry."""
        try:
            from cortex.db import get_db

            conn = get_db()
            conn.execute(
                "INSERT OR IGNORE INTO model_registry "
                "(model_name, model_type, source, metadata) "
                "VALUES (?, 'lora', 'composed', ?)",
                (
                    cm.model_name,
                    json.dumps({
                        "domain": cm.domain,
                        "base_model": cm.base_model,
                        "adapter_path": cm.adapter_path,
                        **cm.info,
                    }),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Could not register %s in DB: %s", cm.model_name, exc)


# ── Module-level singleton ────────────────────────────────────────

_lora_manager: LoRAManager | None = None


def get_lora_manager() -> LoRAManager | None:
    """Return the global LoRA manager (``None`` if not initialized)."""
    return _lora_manager


def set_lora_manager(mgr: LoRAManager) -> None:
    """Set the global LoRA manager singleton."""
    global _lora_manager
    _lora_manager = mgr
