"""Bridge connecting the CLI agent/REPL to Atlas persistent memory.

Handles:
1. Auto-recall: Search memory before each LLM call
2. Auto-archive: Summarize + store old turns when history grows
3. Auto-remember: Store key tool results and decisions
4. Context assembly: Build optimal prompt from recent turns + memory hits

Module ownership: CLI memory integration
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Token estimation: ~4 chars per token
def estimate_tokens(text: str) -> int:
    return len(text) // 4

def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


class MemoryBridge:
    """Manages the conversation window with memory-backed overflow."""

    def __init__(
        self,
        max_context_tokens: int = 6000,   # Leave room for response in 8K window
        archive_threshold: int = 10,       # Archive after this many turns
        archive_keep_recent: int = 4,      # Keep this many recent turns after archive
        memory_recall_top_k: int = 5,      # How many memory hits to inject
        user_id: str = "cli_user",
    ):
        self.max_context_tokens = max_context_tokens
        self.archive_threshold = archive_threshold
        self.archive_keep_recent = archive_keep_recent
        self.memory_recall_top_k = memory_recall_top_k
        self.user_id = user_id
        self._db_conn = None
        self._memory_system = None
        self._provider = None  # For summarization

    async def initialize(self) -> None:
        """Set up DB connection and memory system."""
        try:
            from cortex.db import init_db, get_db
            init_db()
            self._db_conn = get_db()
        except Exception as e:
            logger.debug("DB init failed (memory will be limited): %s", e)

        try:
            from cortex.memory.controller import get_memory_system
            self._memory_system = get_memory_system()
            if self._memory_system:
                await self._memory_system.start()
        except Exception as e:
            logger.debug("Memory system unavailable: %s", e)

    async def recall(self, query: str) -> str:
        """Search memory for relevant context. Returns formatted string or empty."""
        if not self._db_conn:
            return ""
        try:
            from cortex.memory.hot import hot_query, format_memory_context
            hits = hot_query(query, self.user_id, self._db_conn, top_k=self.memory_recall_top_k)
            if hits:
                context = format_memory_context(hits, max_chars=1500)
                logger.debug("Memory recall: %d hits for '%s'", len(hits), query[:50])
                return context
        except Exception as e:
            logger.debug("Memory recall failed: %s", e)
        return ""

    async def remember(self, text: str, tags: list[str] | None = None) -> None:
        """Store text to persistent memory (async, non-blocking)."""
        if not self._memory_system:
            return
        try:
            await self._memory_system.remember(text, self.user_id, tags=tags)
        except Exception as e:
            logger.debug("Memory store failed: %s", e)

    async def remember_tool_result(self, tool_id: str, params: dict, result: str) -> None:
        """Store a tool execution result to memory."""
        # Only store meaningful results (skip empty, errors, huge outputs)
        if not result or len(result) < 10:
            return
        # Truncate large results for memory storage
        text = result[:2000] if len(result) > 2000 else result
        summary = f"Tool '{tool_id}' executed: {text}"
        await self.remember(summary, tags=["tool", tool_id])

    async def remember_decision(self, decision: str) -> None:
        """Store a key decision or learning."""
        await self.remember(f"Decision: {decision}", tags=["decision"])

    async def build_messages_with_memory(
        self,
        system_prompt: str,
        current_message: str,
        history: list[dict],
    ) -> list[dict]:
        """Build the optimal message list for the LLM.

        1. Search memory for context relevant to current message
        2. If history is too long, archive old turns to memory
        3. Assemble: system + memory context + recent history + current message
        """
        # Step 1: Auto-archive if history is too long
        if len(history) > self.archive_threshold:
            history = await self._archive_old_turns(history)

        # Step 2: Recall relevant memory
        memory_context = await self.recall(current_message)

        # Step 3: Build system prompt with memory
        system_parts = [system_prompt]
        if memory_context:
            system_parts.append(
                f"\n[RELEVANT CONTEXT FROM MEMORY]\n{memory_context}\n[/RELEVANT CONTEXT]"
            )

        # Step 4: Assemble messages
        messages = [{"role": "system", "content": "\n".join(system_parts)}]

        # Add recent history (respect token budget)
        remaining_tokens = self.max_context_tokens - estimate_tokens(messages[0]["content"])
        remaining_tokens -= estimate_tokens(current_message) + 100  # Reserve for current msg

        # Add history from most recent, working backwards
        included_history = []
        for msg in reversed(history):
            msg_tokens = estimate_tokens(msg.get("content", ""))
            if remaining_tokens - msg_tokens < 0:
                break
            included_history.insert(0, msg)
            remaining_tokens -= msg_tokens

        messages.extend(included_history)
        messages.append({"role": "user", "content": current_message})

        total_tokens = estimate_messages_tokens(messages)
        logger.debug(
            "Context assembly: %d history turns (of %d), %d memory chars, ~%d tokens",
            len(included_history), len(history), len(memory_context), total_tokens,
        )

        return messages

    async def _archive_old_turns(self, history: list[dict]) -> list[dict]:
        """Archive old conversation turns to memory, keep recent ones."""
        if len(history) <= self.archive_keep_recent:
            return history

        to_archive = history[:-self.archive_keep_recent]
        kept = history[-self.archive_keep_recent:]

        # Build a summary of archived turns
        archive_text_parts = []
        for msg in to_archive:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "..."
            archive_text_parts.append(f"{role}: {content}")

        archive_text = "\n".join(archive_text_parts)

        # Try to summarize with LLM if available, otherwise store raw
        summary = None
        if self._provider and len(archive_text) > 200:
            try:
                summary_response = ""
                async for chunk in self._provider.chat(
                    messages=[
                        {"role": "system", "content": "Summarize this conversation in 2-3 concise sentences. Focus on decisions made, key information exchanged, and current task state."},
                        {"role": "user", "content": archive_text[:3000]},
                    ],
                    stream=True,
                ):
                    summary_response += chunk
                summary = summary_response.strip()
            except Exception as e:
                logger.debug("Summary generation failed: %s", e)

        # Store to memory
        stored_text = summary or archive_text[:2000]
        await self.remember(
            f"Conversation archive ({len(to_archive)} turns): {stored_text}",
            tags=["conversation", "archive"],
        )

        logger.info(
            "Archived %d turns to memory (kept %d recent), summary=%s",
            len(to_archive), len(kept), bool(summary),
        )

        return kept

    def set_provider(self, provider: Any) -> None:
        """Set the LLM provider for summarization."""
        self._provider = provider

    async def shutdown(self) -> None:
        """Clean shutdown — flush memory writer."""
        if self._memory_system:
            try:
                await self._memory_system.stop()
            except Exception:
                pass
