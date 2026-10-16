"""Tests for cortex.memory.compaction — context compaction system."""
from __future__ import annotations

import pytest

from cortex.memory.compaction import (
    CompactionResult,
    ContextCompactor,
    SUMMARY_SYSTEM_PROMPT,
    _build_compaction_prompt,
    _count_facts,
    _estimate_tokens,
    _turns_token_count,
)
from cortex.memory.sessions import Turn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _turn(role: str = "user", content: str = "hello", *, index: int = 0) -> Turn:
    return Turn(
        id=index,
        session_id="s1",
        turn_index=index,
        role=role,
        content=content,
    )


def _turns(n: int, *, prefix: str = "msg") -> list[Turn]:
    """Generate *n* alternating user/assistant turns."""
    out: list[Turn] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append(_turn(role, f"{prefix} {i}", index=i))
    return out


class _MockProvider:
    """Fake LLM provider whose chat() returns a canned summary."""

    def __init__(self, summary: str = "Summary: fact1, fact2, fact3.") -> None:
        self.summary = summary
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return {
            "choices": [
                {"message": {"content": self.summary}},
            ],
        }


class _FailingProvider:
    """Provider that always raises."""

    async def chat(self, messages, **kwargs):
        raise RuntimeError("LLM unavailable")


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_short(self):
        assert _estimate_tokens("hi") == 1  # 2 chars => max(0,1)=1

    def test_known(self):
        assert _estimate_tokens("a" * 100) == 25

    def test_turns(self):
        turns = _turns(4, prefix="word")
        total = _turns_token_count(turns)
        assert total > 0


# ---------------------------------------------------------------------------
# should_compact
# ---------------------------------------------------------------------------

class TestShouldCompact:
    @pytest.fixture()
    def compactor(self):
        return ContextCompactor()

    async def test_no_max_tokens(self, compactor):
        turns = _turns(20)
        assert await compactor.should_compact(turns, max_tokens=0) is False

    async def test_negative_max_tokens(self, compactor):
        turns = _turns(20)
        assert await compactor.should_compact(turns, max_tokens=-1) is False

    async def test_too_few_turns(self, compactor):
        turns = _turns(5)
        # Even with a tiny window, not enough turns to split
        assert await compactor.should_compact(turns, max_tokens=1, keep_recent=10) is False

    async def test_under_threshold(self, compactor):
        turns = _turns(15)
        # Very large window — usage is well under 50 %
        assert await compactor.should_compact(turns, max_tokens=100_000) is False

    async def test_over_threshold(self, compactor):
        # Each turn has ~6 chars → ~1-2 tokens, so 20 turns ≈ 30 tokens
        # With max_tokens=10, 50% threshold=5, usage (30) > 5 → True
        turns = _turns(20)
        assert await compactor.should_compact(turns, max_tokens=10) is True

    async def test_custom_threshold(self, compactor):
        turns = _turns(20)
        est = await compactor.estimate_tokens(turns)
        # Set threshold so high it won't trigger
        assert await compactor.should_compact(
            turns, max_tokens=est * 10, threshold=0.99
        ) is False

    async def test_exact_keep_recent_boundary(self, compactor):
        # Exactly keep_recent turns — nothing to compact
        turns = _turns(10)
        assert await compactor.should_compact(
            turns, max_tokens=1, keep_recent=10
        ) is False


# ---------------------------------------------------------------------------
# compact
# ---------------------------------------------------------------------------

class TestCompact:
    @pytest.fixture()
    def compactor(self):
        return ContextCompactor()

    async def test_nothing_to_compact(self, compactor):
        turns = _turns(5)
        prov = _MockProvider()
        result = await compactor.compact(turns, prov, keep_recent=10)
        assert result.summary == ""
        assert result.kept_turns == turns
        assert prov.calls == []

    async def test_basic_compaction(self, compactor):
        turns = _turns(20)
        prov = _MockProvider("User discussed weather. Temperature was 72°F.")
        result = await compactor.compact(turns, prov, keep_recent=5)

        assert result.summary != ""
        assert len(result.kept_turns) == 5  # last 5 kept
        assert result.compacted_token_count < result.original_token_count
        assert 0 < result.compression_ratio < 1.0
        assert result.facts_preserved >= 1

    async def test_recent_turns_preserved(self, compactor):
        turns = _turns(15)
        prov = _MockProvider("summary")
        result = await compactor.compact(turns, prov, keep_recent=10)

        # The last 10 turns are kept verbatim
        recent_contents = [t.content for t in result.kept_turns[-10:]]
        expected = [t.content for t in turns[-10:]]
        assert recent_contents == expected

    async def test_system_turns_preserved(self, compactor):
        sys_turn = _turn("system", "You are a helpful assistant.", index=0)
        user_turns = _turns(15)
        all_turns = [sys_turn] + user_turns
        prov = _MockProvider("summary")
        result = await compactor.compact(all_turns, prov, keep_recent=5)

        # System turn should be in kept_turns
        roles = [t.role for t in result.kept_turns]
        assert "system" in roles
        assert result.kept_turns[0].content == "You are a helpful assistant."

    async def test_llm_receives_only_older_turns(self, compactor):
        turns = _turns(20)
        prov = _MockProvider("summary")
        await compactor.compact(turns, prov, keep_recent=10)

        # The prompt should contain the older 10 turns, not the recent 10
        assert len(prov.calls) == 1
        user_msg = prov.calls[0][1]["content"]
        for t in turns[:10]:
            assert t.content in user_msg
        for t in turns[10:]:
            assert t.content not in user_msg

    async def test_provider_failure_returns_original(self, compactor):
        turns = _turns(20)
        prov = _FailingProvider()
        result = await compactor.compact(turns, prov, keep_recent=5)

        assert result.summary == ""
        assert result.kept_turns == list(turns)
        assert result.compression_ratio == 1.0

    async def test_all_system_turns_no_crash(self, compactor):
        """If all older turns are system role, nothing to summarize."""
        sys_turns = [_turn("system", f"sys {i}", index=i) for i in range(15)]
        recent = _turns(5)
        all_turns = sys_turns + recent
        prov = _MockProvider("summary")
        result = await compactor.compact(all_turns, prov, keep_recent=5)

        assert result.summary == ""
        assert prov.calls == []


# ---------------------------------------------------------------------------
# Compression ratio calculation
# ---------------------------------------------------------------------------

class TestCompressionRatio:
    async def test_ratio_shrinks(self):
        compactor = ContextCompactor()
        # 30 turns with long content
        turns = [_turn("user", "x" * 200, index=i) for i in range(30)]
        prov = _MockProvider("Short summary.")
        result = await compactor.compact(turns, prov, keep_recent=5)

        # Summary is much shorter than original
        assert result.compression_ratio < 0.5
        assert result.original_token_count > result.compacted_token_count

    async def test_ratio_is_one_when_no_compaction(self):
        compactor = ContextCompactor()
        turns = _turns(3)
        prov = _MockProvider()
        result = await compactor.compact(turns, prov, keep_recent=10)
        assert result.compression_ratio == 1.0


# ---------------------------------------------------------------------------
# merge_summaries
# ---------------------------------------------------------------------------

class TestMergeSummaries:
    @pytest.fixture()
    def compactor(self):
        return ContextCompactor()

    def test_both_present(self, compactor):
        merged = compactor.merge_summaries("old facts", "new facts")
        assert "old facts" in merged
        assert "new facts" in merged
        assert "---" in merged

    def test_old_empty(self, compactor):
        assert compactor.merge_summaries("", "new") == "new"

    def test_new_empty(self, compactor):
        assert compactor.merge_summaries("old", "") == "old"

    def test_both_empty(self, compactor):
        assert compactor.merge_summaries("", "") == ""


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

class TestBuildCompactionPrompt:
    def test_system_message(self):
        msgs = _build_compaction_prompt([_turn("user", "hello")])
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SUMMARY_SYSTEM_PROMPT

    def test_conversation_in_user_message(self):
        turns = [
            _turn("user", "What is the capital of France?"),
            _turn("assistant", "The capital of France is Paris."),
        ]
        msgs = _build_compaction_prompt(turns)
        body = msgs[1]["content"]
        assert "[user]: What is the capital of France?" in body
        assert "[assistant]: The capital of France is Paris." in body

    def test_empty_content_skipped(self):
        turns = [_turn("user", ""), _turn("assistant", "reply")]
        msgs = _build_compaction_prompt(turns)
        body = msgs[1]["content"]
        assert "[user]:" not in body
        assert "[assistant]: reply" in body


# ---------------------------------------------------------------------------
# Fact counting
# ---------------------------------------------------------------------------

class TestCountFacts:
    def test_bullets(self):
        text = "Facts:\n- fact one\n- fact two\n- fact three"
        assert _count_facts(text) == 3

    def test_sentences(self):
        text = "Fact one. Fact two. Fact three."
        assert _count_facts(text) == 3

    def test_empty(self):
        assert _count_facts("") == 0

    def test_no_bullets_or_periods(self):
        assert _count_facts("just some text") == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.fixture()
    def compactor(self):
        return ContextCompactor()

    async def test_empty_turns(self, compactor):
        prov = _MockProvider()
        result = await compactor.compact([], prov, keep_recent=10)
        assert result.summary == ""
        assert result.kept_turns == []

    async def test_single_turn(self, compactor):
        turns = [_turn("user", "hello")]
        prov = _MockProvider()
        result = await compactor.compact(turns, prov, keep_recent=10)
        assert result.summary == ""
        assert len(result.kept_turns) == 1

    async def test_keep_recent_equals_total(self, compactor):
        turns = _turns(10)
        prov = _MockProvider()
        result = await compactor.compact(turns, prov, keep_recent=10)
        assert result.summary == ""
        assert len(result.kept_turns) == 10


# ---------------------------------------------------------------------------
# Idempotent compaction
# ---------------------------------------------------------------------------

class TestIdempotentCompaction:
    async def test_compact_already_compacted(self):
        """Compacting a summary turn should refine it, not crash."""
        compactor = ContextCompactor()
        # Simulate a previously compacted conversation:
        # a summary turn + 15 more turns since then
        summary_turn = _turn(
            "assistant",
            "Previous summary: User's name is Alice. She prefers tea.",
            index=0,
        )
        new_turns = _turns(15)
        all_turns = [summary_turn] + new_turns

        prov = _MockProvider("Refined: Alice prefers tea. Also likes cats.")
        result = await compactor.compact(all_turns, prov, keep_recent=10)

        assert result.summary != ""
        assert result.facts_preserved >= 1
        # The old summary content was fed to the LLM
        call_content = prov.calls[0][1]["content"]
        assert "Alice" in call_content
