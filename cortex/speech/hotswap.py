"""TTS hot-swap manager — GPU model coordination for Atlas Cortex.

Manages TTS model loading/unloading on the RTX 4060 (8GB VRAM).
Only ONE large TTS model fits at a time. Coordinates swaps so
conversational TTS falls back during story generation.

Owner: cortex.speech
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPU slot descriptor
# ---------------------------------------------------------------------------

@dataclass
class GPUSlot:
    """Represents a GPU slot that can hold one large TTS model."""

    gpu_id: str = "cuda:0"
    current_model: str | None = None
    vram_mb: int = 8192  # RTX 4060 default


# ---------------------------------------------------------------------------
# Provider hierarchy
# ---------------------------------------------------------------------------

# Primary providers (GPU-bound, mutually exclusive)
PROVIDER_QWEN3_TTS = "qwen3_tts"
PROVIDER_FISH_S2 = "fish_audio_s2"

# Fallbacks (CPU or secondary GPU)
PROVIDER_ORPHEUS = "orpheus"
PROVIDER_KOKORO = "kokoro"
PROVIDER_PIPER = "piper"

FALLBACK_ORDER = (PROVIDER_ORPHEUS, PROVIDER_KOKORO, PROVIDER_PIPER)


# ---------------------------------------------------------------------------
# HotSwapManager
# ---------------------------------------------------------------------------

class HotSwapManager:
    """Manages TTS model loading/unloading on the RTX 4060.

    Only ONE large TTS model fits at a time (8GB VRAM).
    Coordinates swaps so conversational TTS falls back during story generation.
    """

    def __init__(self) -> None:
        self._gpu = GPUSlot(
            gpu_id=os.environ.get("TTS_GPU_ID", "cuda:0"),
            vram_mb=int(os.environ.get("TTS_VRAM_MB", "8192")),
        )
        self._current_provider: str = PROVIDER_QWEN3_TTS
        self._gpu.current_model = PROVIDER_QWEN3_TTS
        self._swap_lock = asyncio.Lock()
        self._is_swapping: bool = False
        self._swap_count: int = 0
        self._last_swap_at: float = 0.0
        self._fallback_provider: str = PROVIDER_ORPHEUS

        # Pluggable callbacks for actual GPU model management.
        # Each receives the provider_id and returns True on success.
        self._load_fn = _stub_load
        self._unload_fn = _stub_unload
        self._health_fn = _stub_health

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_provider(self, purpose: str = "conversation") -> str:
        """Return the appropriate TTS provider for *purpose*.

        ``purpose="conversation"`` → current GPU provider (or fallback while
        swapping).
        ``purpose="story"`` → triggers swap to Fish Audio S2 if not loaded.
        """
        if self._is_swapping:
            fb = await self.get_fallback_provider()
            logger.info("Swap in progress — returning fallback %s", fb)
            return fb

        if purpose == "story":
            if self._current_provider != PROVIDER_FISH_S2:
                ok = await self.swap_to_story_mode()
                if not ok:
                    logger.warning("Swap to story mode failed; using fallback")
                    return await self.get_fallback_provider()
            return PROVIDER_FISH_S2

        # conversation (default)
        return self._current_provider

    async def swap_to_story_mode(self) -> bool:
        """Unload Qwen3-TTS → Load Fish Audio S2.

        Sets fallback for conversational TTS during swap.
        Returns ``True`` on success.
        """
        async with self._swap_lock:
            if self._current_provider == PROVIDER_FISH_S2:
                logger.debug("Already in story mode")
                return True

            self._is_swapping = True
            previous = self._current_provider
            try:
                logger.info(
                    "Hot-swap: %s → %s (GPU %s)",
                    previous, PROVIDER_FISH_S2, self._gpu.gpu_id,
                )

                # 1. Unload current model
                if not await self._unload_fn(previous):
                    logger.error("Failed to unload %s", previous)
                    return False
                self._gpu.current_model = None

                # 2. Load Fish Audio S2
                if not await self._load_fn(PROVIDER_FISH_S2):
                    logger.error("Failed to load %s — reloading %s",
                                 PROVIDER_FISH_S2, previous)
                    await self._load_fn(previous)
                    self._gpu.current_model = previous
                    return False

                self._current_provider = PROVIDER_FISH_S2
                self._gpu.current_model = PROVIDER_FISH_S2
                self._swap_count += 1
                self._last_swap_at = time.time()
                logger.info("Hot-swap complete → %s", PROVIDER_FISH_S2)
                return True
            finally:
                self._is_swapping = False

    async def swap_to_conversation_mode(self) -> bool:
        """Unload Fish Audio S2 → Reload Qwen3-TTS.

        Restores normal conversational TTS.  Returns ``True`` on success.
        """
        async with self._swap_lock:
            if self._current_provider == PROVIDER_QWEN3_TTS:
                logger.debug("Already in conversation mode")
                return True

            self._is_swapping = True
            previous = self._current_provider
            try:
                logger.info(
                    "Hot-swap: %s → %s (GPU %s)",
                    previous, PROVIDER_QWEN3_TTS, self._gpu.gpu_id,
                )

                if not await self._unload_fn(previous):
                    logger.error("Failed to unload %s", previous)
                    return False
                self._gpu.current_model = None

                if not await self._load_fn(PROVIDER_QWEN3_TTS):
                    logger.error("Failed to load %s", PROVIDER_QWEN3_TTS)
                    return False

                self._current_provider = PROVIDER_QWEN3_TTS
                self._gpu.current_model = PROVIDER_QWEN3_TTS
                self._swap_count += 1
                self._last_swap_at = time.time()
                logger.info("Hot-swap complete → %s", PROVIDER_QWEN3_TTS)
                return True
            finally:
                self._is_swapping = False

    async def get_fallback_provider(self) -> str:
        """During swap, return Orpheus, Kokoro, or Piper as fallback."""
        for provider in FALLBACK_ORDER:
            if await self._health_fn(provider):
                self._fallback_provider = provider
                return provider
        logger.warning("No healthy fallback found — returning %s", PROVIDER_PIPER)
        return PROVIDER_PIPER

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_swapping(self) -> bool:
        return self._is_swapping

    @property
    def current_provider(self) -> str:
        return self._current_provider

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Return current GPU state, loaded model, swap status."""
        return {
            "gpu_id": self._gpu.gpu_id,
            "vram_mb": self._gpu.vram_mb,
            "current_model": self._gpu.current_model,
            "current_provider": self._current_provider,
            "is_swapping": self._is_swapping,
            "fallback_provider": self._fallback_provider,
            "swap_count": self._swap_count,
            "last_swap_at": self._last_swap_at,
        }


# ---------------------------------------------------------------------------
# Stub GPU operations (pluggable)
# ---------------------------------------------------------------------------

async def _stub_load(provider_id: str) -> bool:
    """Stub: logs what a real implementation would do."""
    logger.info("[STUB] Would load TTS model: %s onto GPU", provider_id)
    return True


async def _stub_unload(provider_id: str) -> bool:
    """Stub: logs what a real implementation would do."""
    logger.info("[STUB] Would unload TTS model: %s from GPU", provider_id)
    return True


async def _stub_health(provider_id: str) -> bool:
    """Stub: all providers report healthy."""
    logger.debug("[STUB] Health check for %s → True", provider_id)
    return True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: HotSwapManager | None = None


def get_hotswap_manager() -> HotSwapManager:
    """Return the global HotSwapManager singleton."""
    global _manager
    if _manager is None:
        _manager = HotSwapManager()
    return _manager


def reset_hotswap_manager() -> None:
    """Reset the singleton (useful for testing)."""
    global _manager
    _manager = None
