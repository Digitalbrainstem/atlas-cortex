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


# ──────────────────────────────────────────────────────────────────
# Checkpoint summarization
# ──────────────────────────────────────────────────────────────────

async def summarize_checkpoint(
    history: list[dict],
    provider: Any = None,
    max_turns: int = 10,
) -> str:
    """Summarize older conversation turns into a compact checkpoint.

    If no LLM provider available, falls back to extractive summary
    (first sentence of each assistant turn).
    """
    if not history:
        return ""

    turns = history[:max_turns]

    if provider is not None:
        combined = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in turns
        )
        prompt = (
            "Summarize the following conversation into a brief paragraph "
            "capturing the key topics and conclusions:\n\n" + combined
        )
        try:
            return await provider.generate(prompt)
        except Exception:
            logger.warning("LLM summarization failed, falling back to extractive summary")

    # Extractive fallback: first sentence of each assistant turn
    sentences: list[str] = []
    for m in turns:
        if m.get("role") == "assistant":
            content = m.get("content", "").strip()
            if content:
                first = content.split(".")[0].strip()
                if first:
                    sentences.append(first + ".")
    return " ".join(sentences) if sentences else ""


# ──────────────────────────────────────────────────────────────────
# Context compaction
# ──────────────────────────────────────────────────────────────────

def compact_context(
    history: list[dict],
    limits: ContextLimits,
    checkpoints: list[str] | None = None,
) -> tuple[list[dict], str]:
    """Compact conversation history to fit within token limits.

    Returns (trimmed_history, checkpoint_summary).
    Strategy:
    1. If history fits in budget, return as-is
    2. Split: keep recent N turns, summarize older into checkpoint
    3. Inject checkpoint as system context
    """
    if not history:
        return history, ""

    max_tokens = limits.default_context
    total = sum(estimate_tokens(m.get("content", "")) for m in history)
    existing_checkpoint = " ".join(checkpoints) if checkpoints else ""

    if total <= max_tokens:
        return history, existing_checkpoint

    # Keep recent turns that fit in budget
    budget = max_tokens // 2  # reserve half for recent turns
    recent: list[dict] = []
    used = 0
    for m in reversed(history):
        t = estimate_tokens(m.get("content", ""))
        if used + t > budget and recent:
            break
        recent.insert(0, m)
        used += t

    # Summarize the older turns we're dropping
    older = history[: len(history) - len(recent)]
    sentences: list[str] = []
    for m in older:
        if m.get("role") == "assistant":
            content = m.get("content", "").strip()
            if content:
                first = content.split(".")[0].strip()
                if first:
                    sentences.append(first + ".")
    new_checkpoint = " ".join(sentences)

    if existing_checkpoint:
        new_checkpoint = existing_checkpoint + " " + new_checkpoint if new_checkpoint else existing_checkpoint

    return recent, new_checkpoint.strip()


# ──────────────────────────────────────────────────────────────────
# Overflow recovery
# ──────────────────────────────────────────────────────────────────

def recover_overflow(
    history: list[dict],
    limits: ContextLimits,
) -> list[dict]:
    """Emergency context recovery when we exceed hard limits.

    Aggressively trims to 50% of context window, keeping only
    the most recent turns. Used as last resort.
    """
    if not history:
        return history

    target = limits.default_context // 2
    return trim_history(history, target)
