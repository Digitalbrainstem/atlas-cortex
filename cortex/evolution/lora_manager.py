"""LoRA adapter lifecycle manager.

Discovers on-disk LoRA adapters, registers them for dynamic loading via
the ``peft`` library, and provides fast domain → model lookups at
inference time.
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


@dataclass
class ComposedModel:
    """A registered LoRA adapter for dynamic loading via peft."""

    domain: str
    model_name: str  # e.g. "atlas-coding"
    base_model: str
    adapter_path: str
    info: dict[str, Any] = field(default_factory=dict)


class LoRAManager:
    """Discover, register, and manage LoRA adapters.

    Adapters are registered in-memory and loaded dynamically at
    inference time via the ``peft`` library.  No model baking or
    external HTTP calls are required.
    """

    def __init__(self, timeout: float = 120.0, **_kw: Any) -> None:
        self._timeout = timeout
        # domain -> ComposedModel
        self._composed: dict[str, ComposedModel] = {}

    # ── Discovery ─────────────────────────────────────────────────

    def discover(self, lora_dir: str | Path) -> dict[str, Path]:
        """Scan *lora_dir* for LoRA adapters.

        Returns a map of domain name → adapter directory.
        """
        return discover_loras(lora_dir)

    async def _list_registered_models(self) -> list[str]:
        """List currently registered atlas-* models from the DB."""
        try:
            from cortex.db import get_db

            db = get_db()
            rows = db.execute(
                "SELECT model_name FROM model_registry WHERE model_name LIKE 'atlas-%'"
            ).fetchall()
            return [r["model_name"] for r in rows]
        except Exception as exc:
            logger.warning("Could not list registered models: %s", exc)
            return []

    # ── Composition ───────────────────────────────────────────────

    async def compose(
        self,
        domain: str,
        base_model: str,
        adapter_path: str | Path,
    ) -> ComposedModel | None:
        """Register a LoRA adapter for a domain.

        Unlike the old Ollama workflow, we don't bake the adapter into
        a separate model.  Instead, we register the adapter path and
        load it dynamically at inference time using the peft library.
        """
        adapter_path = str(adapter_path)
        model_name = f"atlas-{domain}"

        # Verify adapter exists
        p = Path(adapter_path)
        if not (p / "adapter_config.json").exists():
            logger.error("No adapter_config.json in %s", adapter_path)
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
        logger.info("Registered LoRA adapter %s (domain=%s)", model_name, domain)
        return composed

    async def compose_all(
        self,
        base_model: str,
        lora_dir: str | Path,
        **_kw: Any,
    ) -> list[ComposedModel]:
        """Discover all LoRAs and register each as a peft adapter.

        Returns the list of successfully registered models.
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

        # Also pick up any pre-existing atlas-* models from the DB
        existing = await self._list_registered_models()
        for name in existing:
            # e.g. "atlas-coding" -> "coding"
            domain = name.split(":")[0].removeprefix("atlas-")
            if domain and domain not in self._composed:
                self._composed[domain] = ComposedModel(
                    domain=domain,
                    model_name=name,
                    base_model=base_model,
                    adapter_path="",
                )
                logger.info("Found pre-existing registered model %s", name)

        logger.info(
            "LoRA registration complete: %d registered, %d total active",
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
        """Return all currently registered LoRA models."""
        return list(self._composed.values())

    # ── Removal ───────────────────────────────────────────────────

    async def remove(self, domain: str) -> bool:
        """Unregister a LoRA adapter."""
        cm = self._composed.pop(domain, None)
        if cm is None:
            return False
        logger.info("Unregistered LoRA adapter %s", cm.model_name)
        return True

    # ── Registry integration ──────────────────────────────────────

    async def discover_and_register(self, lora_dir: str | Path) -> dict[str, dict[str, Any]]:
        """Discover LoRAs on disk and register them in the DB.

        Catalogs what adapters are available so the admin UI can
        display them.  Actual loading happens at inference time via
        peft when :meth:`get_model_for_domain` is used.

        Returns a dict of domain → adapter info.
        """
        loras = self.discover(lora_dir)
        if not loras:
            logger.info("No LoRA adapters found in %s", lora_dir)
            return {}

        registered: dict[str, dict[str, Any]] = {}
        for domain, path in loras.items():
            info = get_lora_info(path)
            self._register_discovered_in_db(domain, str(path), info)
            registered[domain] = {"path": str(path), **info}
            logger.debug("Registered LoRA: %s at %s", domain, path)

        # Also pick up any pre-existing atlas-* models from the DB
        existing = await self._list_registered_models()
        for name in existing:
            domain = name.split(":")[0].removeprefix("atlas-")
            if domain and domain not in self._composed:
                self._composed[domain] = ComposedModel(
                    domain=domain,
                    model_name=name,
                    base_model="",
                    adapter_path="",
                )
                logger.info("Found pre-existing registered model %s", name)

        logger.info(
            "LoRA discovery complete: %d on disk, %d registered",
            len(registered),
            len(self._composed),
        )
        return registered

    @staticmethod
    def _register_discovered_in_db(domain: str, adapter_path: str, info: dict[str, Any]) -> None:
        """Register a discovered (not yet composed) LoRA in model_registry."""
        try:
            from cortex.db import get_db

            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO model_registry "
                "(model_name, model_type, source, status, metadata) "
                "VALUES (?, 'lora', 'local', 'available', ?)",
                (
                    f"lora-{domain}",
                    json.dumps({
                        "domain": domain,
                        "adapter_path": adapter_path,
                        **info,
                    }),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Could not register lora-%s in DB: %s", domain, exc)

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
