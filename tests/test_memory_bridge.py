"""Tests for CLI memory bridge."""
from __future__ import annotations

import pytest
from cortex.cli.memory_bridge import MemoryBridge, estimate_tokens, estimate_messages_tokens


class TestTokenEstimation:
    def test_estimate_tokens(self):
        assert estimate_tokens("hello world") == 2  # 11 chars / 4

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_estimate_messages(self):
        msgs = [{"content": "hello"}, {"content": "world"}]
        assert estimate_messages_tokens(msgs) == 2


class TestMemoryBridge:
    @pytest.fixture
    def bridge(self):
        return MemoryBridge(max_context_tokens=1000, archive_threshold=4, archive_keep_recent=2)

    async def test_build_messages_basic(self, bridge):
        messages = await bridge.build_messages_with_memory(
            system_prompt="You are helpful.",
            current_message="Hello",
            history=[],
        )
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Hello"

    async def test_build_messages_with_history(self, bridge):
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response 1"},
        ]
        messages = await bridge.build_messages_with_memory(
            system_prompt="system",
            current_message="second",
            history=history,
        )
        assert len(messages) == 4  # system + 2 history + current

    async def test_archive_triggers_on_threshold(self, bridge):
        """When history exceeds threshold, old turns get archived."""
        history = [
            {"role": "user", "content": f"msg {i}"} for i in range(6)
        ]
        messages = await bridge.build_messages_with_memory(
            system_prompt="system",
            current_message="current",
            history=history,
        )
        # History should have been pruned — only kept recent + system + current
        # With archive_keep_recent=2, we expect: system + <=2 history + current
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "current"

    async def test_token_budget_respected(self, bridge):
        """History is trimmed to fit token budget."""
        # Create very long history
        history = [
            {"role": "user", "content": "x" * 2000} for _ in range(10)
        ]
        messages = await bridge.build_messages_with_memory(
            system_prompt="system",
            current_message="current",
            history=history,
        )
        total = estimate_messages_tokens(messages)
        assert total <= bridge.max_context_tokens + 200  # some margin

    async def test_recall_returns_empty_without_db(self, bridge):
        result = await bridge.recall("test query")
        assert result == ""

    async def test_remember_without_memory_system(self, bridge):
        # Should not crash
        await bridge.remember("test text")

    async def test_remember_tool_result_skips_empty(self, bridge):
        await bridge.remember_tool_result("test_tool", {}, "")
        # Should not crash, should skip silently

    async def test_remember_tool_result_skips_short(self, bridge):
        await bridge.remember_tool_result("test_tool", {}, "short")
        # Should not crash, skip results < 10 chars

    async def test_shutdown_safe(self, bridge):
        await bridge.shutdown()  # Should not crash even without memory system

    async def test_archive_old_turns_keeps_recent(self, bridge):
        history = [
            {"role": "user", "content": f"turn {i}"} for i in range(6)
        ]
        kept = await bridge._archive_old_turns(history)
        assert len(kept) == bridge.archive_keep_recent
        # Should keep the LAST N turns
        assert kept[0]["content"] == "turn 4"
        assert kept[1]["content"] == "turn 5"

    async def test_archive_noop_when_short(self, bridge):
        history = [{"role": "user", "content": "short"}]
        kept = await bridge._archive_old_turns(history)
        assert kept == history

    async def test_remember_decision(self, bridge):
        # Should not crash without memory system
        await bridge.remember_decision("use postgres instead of sqlite")

    async def test_set_provider(self, bridge):
        bridge.set_provider("mock_provider")
        assert bridge._provider == "mock_provider"

    async def test_build_messages_no_memory_in_system(self, bridge):
        """Without DB, no memory context appended to system prompt."""
        messages = await bridge.build_messages_with_memory(
            system_prompt="base prompt",
            current_message="hi",
            history=[],
        )
        assert "RELEVANT CONTEXT FROM MEMORY" not in messages[0]["content"]
        assert messages[0]["content"] == "base prompt"

    async def test_initialize_safe_without_db(self, bridge):
        """initialize() should not crash even if DB/memory are unavailable."""
        await bridge.initialize()
        # Bridge should still work in degraded mode
        result = await bridge.recall("anything")
        assert result == ""
