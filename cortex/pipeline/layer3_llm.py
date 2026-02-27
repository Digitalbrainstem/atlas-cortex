"""Layer 3: Filler streaming + LLM background call.

The key insight: start talking before you have the answer, then seamlessly
blend in the real response.

Timeline (per request):
  0 ms  → select filler phrase
  1 ms  → stream filler tokens to client
  2 ms  → fire LLM request in background thread
  ...ms → first real token arrives
  ...ms → seamlessly continue streaming real response
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from cortex.filler import select_filler
from cortex.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Model selection heuristics (rule-based, no LLM round trip)
# ──────────────────────────────────────────────────────────────────

_THINKING_KEYWORDS = frozenset([
    "explain in detail", "analyze", "compare", "write code", "debug",
    "refactor", "design", "architecture", "step by step", "why does",
])

_FAST_KEYWORDS = frozenset([
    "what is", "who is", "when was", "where is", "how many", "define",
])


def select_model(
    message: str,
    conversation_length: int = 0,
    model_fast: str = "qwen2.5:14b",
    model_thinking: str = "qwen3:30b-a3b",
) -> str:
    """Rule-based model selection.

    Uses the fast model for short factual questions, the thinking model for
    complex reasoning tasks.
    """
    lower = message.lower()
    # Short factual → fast
    if len(message) < 80 and any(kw in lower for kw in _FAST_KEYWORDS):
        return model_fast
    # Explicit thinking cues → thinking model
    if any(kw in lower for kw in _THINKING_KEYWORDS):
        return model_thinking
    # Long message or deep conversation → thinking model
    if len(message) > 200 or conversation_length > 10:
        return model_thinking
    return model_fast


# ──────────────────────────────────────────────────────────────────
# Filler injection into system prompt
# ──────────────────────────────────────────────────────────────────

_FILLER_INJECTION_TEMPLATE = (
    "You already started your response with: \"{filler}\"\n"
    "Continue naturally from that point. "
    "Do NOT repeat the greeting or acknowledgment."
)


def build_messages(
    message: str,
    context: dict[str, Any],
    filler: str,
    memory_context: str = "",
    system_prompt: str = "",
) -> list[dict[str, str]]:
    """Assemble the message list to send to the LLM."""
    system_parts = []

    # Core personality / grounding rules
    base_system = system_prompt or (
        "You are Atlas Cortex, a helpful AI assistant with a warm, direct "
        "personality. You are honest about what you know and don't know. "
        "You never hallucinate facts. If you are uncertain, say so."
    )
    system_parts.append(base_system)

    # Filler injection
    if filler:
        system_parts.append(_FILLER_INJECTION_TEMPLATE.format(filler=filler.strip()))

    # Memory context
    if memory_context:
        system_parts.append(f"[RELEVANT CONTEXT]\n{memory_context}\n[/RELEVANT CONTEXT]")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": "\n\n".join(system_parts)}
    ]

    # Prior conversation turns
    for turn in context.get("conversation_history", []):
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": message})
    return messages


# ──────────────────────────────────────────────────────────────────
# Layer 3 streaming generator
# ──────────────────────────────────────────────────────────────────

async def stream_llm_response(
    message: str,
    context: dict[str, Any],
    provider: LLMProvider,
    model_fast: str = "qwen2.5:14b",
    model_thinking: str = "qwen3:30b-a3b",
    memory_context: str = "",
    system_prompt: str = "",
    confidence: float = 1.0,
) -> AsyncGenerator[str, None]:
    """Yield tokens: filler first, then real LLM response.

    This generator is designed to be consumed by server.py or pipe.py
    to stream tokens back to the client.
    """
    sentiment = context.get("effective_sentiment", context.get("sentiment", "casual"))
    user_id = context.get("user_id", "default")
    is_follow_up = context.get("is_follow_up", False)

    # Select and yield filler
    filler = select_filler(
        sentiment=sentiment,
        confidence=confidence,
        user_id=user_id,
        is_follow_up=is_follow_up,
    )
    if filler:
        yield filler

    # Choose model
    conv_len = context.get("conversation_length", 0)
    model = select_model(
        message,
        conversation_length=conv_len,
        model_fast=model_fast,
        model_thinking=model_thinking,
    )

    # Build message list
    messages = build_messages(message, context, filler, memory_context, system_prompt)

    # Stream real response
    try:
        stream = await provider.chat(messages=messages, model=model, stream=True)
        if asyncio.iscoroutine(stream):
            stream = await stream
        async for chunk in stream:
            if chunk:
                yield chunk
    except Exception as exc:
        logger.error("LLM stream error: %s", exc)
        yield "\n\n(Error: could not reach the language model. Please check your provider settings.)"
