"""Context window management for Atlas Cortex.

Handles:
  - Dynamic context sizing based on hardware
  - Token budget allocation
  - Context compaction (checkpoints + summaries)
  - Transparent overflow recovery

See docs/context-management.md for full design.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Token budget
# ──────────────────────────────────────────────────────────────────

@dataclass
class TokenBudget:
    """Allocation of a context window across message components."""
    total: int
    system_tokens: int = 0
    memory_tokens: int = 0
    checkpoint_tokens: int = 0
    active_message_tokens: int = 0
    generation_reserve: int = 2048

    @property
    def available_for_history(self) -> int:
        used = (
            self.system_tokens
            + self.memory_tokens
            + self.checkpoint_tokens
            + self.generation_reserve
        )
        return max(0, self.total - used)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (good enough for budgeting)."""
    return max(1, len(text) // 4)


def compute_budget(
    system_prompt: str,
    memory_context: str,
    checkpoint_summary: str,
    context_window: int = 16384,
    generation_reserve: int = 2048,
) -> TokenBudget:
    """Compute token budget for a single request."""
    budget = TokenBudget(
        total=context_window,
        generation_reserve=generation_reserve,
    )
    budget.system_tokens = estimate_tokens(system_prompt)
    budget.memory_tokens = estimate_tokens(memory_context)
    budget.checkpoint_tokens = estimate_tokens(checkpoint_summary)
    return budget


# ──────────────────────────────────────────────────────────────────
# Conversation compaction
# ──────────────────────────────────────────────────────────────────

def trim_history(
    history: list[dict[str, str]],
    max_tokens: int,
) -> list[dict[str, str]]:
    """Trim oldest history turns to fit within *max_tokens*.

    Always keeps the most recent turns (never drops the current question).
    """
    if not history:
        return history

    total = sum(estimate_tokens(m["content"]) for m in history)
    if total <= max_tokens:
        return history

    # Drop oldest turns until we fit
    trimmed = list(history)
    while trimmed and sum(estimate_tokens(m["content"]) for m in trimmed) > max_tokens:
        trimmed.pop(0)

    return trimmed


# ──────────────────────────────────────────────────────────────────
# Hardware-aware context limits
# ──────────────────────────────────────────────────────────────────

@dataclass
class ContextLimits:
    default_context: int = 16384
    thinking_context: int = 32768
    max_model_size_mb: int = 12000
    recommended_model_class: str = "14B-30B"


def limits_from_hardware(hardware: dict[str, Any]) -> ContextLimits:
    """Derive context limits from detected hardware.

    *hardware* is the dict returned by :func:`cortex.install.hardware.detect_hardware`.
    """
    limits = ContextLimits()
    gpus = hardware.get("gpus", [])
    best = None
    for gpu in gpus:
        if not gpu.get("is_igpu", False):
            if best is None or gpu["vram_mb"] > best["vram_mb"]:
                best = gpu
    if best is None and gpus:
        best = gpus[0]

    if best:
        vram = best["vram_mb"]
        if vram >= 24000:
            limits.default_context = 32768
            limits.thinking_context = 65536
            limits.recommended_model_class = "30B-70B"
        elif vram >= 16000:
            limits.default_context = 16384
            limits.thinking_context = 32768
            limits.recommended_model_class = "14B-30B"
        elif vram >= 8000:
            limits.default_context = 8192
            limits.thinking_context = 16384
            limits.recommended_model_class = "7B-14B"
        elif vram >= 4000:
            limits.default_context = 4096
            limits.thinking_context = 8192
            limits.recommended_model_class = "1B-7B"
        else:
            limits.default_context = 2048
            limits.thinking_context = 4096
            limits.recommended_model_class = "1B-3B"
    else:
        # CPU-only
        limits.default_context = 4096
        limits.thinking_context = 8192
        limits.recommended_model_class = "3B-7B (Q4)"

    return limits
