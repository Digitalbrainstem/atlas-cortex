"""Context compaction — lossless conversation summarization.

When conversation context grows large, older turns are summarized by
the LLM into a dense factual summary that preserves every concrete
detail (names, dates, numbers, decisions) while discarding filler and
repetition.  The summary replaces the original turns, dramatically
reducing token usage without losing information.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from cortex.memory.sessions import Turn

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4  # rough heuristic — no tokenizer dependency

DEFAULT_KEEP_RECENT = 10
DEFAULT_THRESHOLD = 0.50  # compact when usage exceeds 50 % of window

SUMMARY_SYSTEM_PROMPT = (
    "You are summarizing a conversation for memory preservation. "
    "Extract and preserve ALL:\n"
    "- Names, dates, times, appointments\n"
    "- Numbers, IDs, codes, passwords\n"
    "- Preferences, decisions made\n"
    "- Tasks assigned or completed\n"
    "- Medical information (medications, vitals, allergies)\n"
    "- Technical details discussed\n"
    "- Questions asked and answers given\n\n"
    "Format as a dense factual summary. Do not add opinions or "
    "interpretations.\n"
    "Preserve exact values (don't round numbers, don't paraphrase names)."
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompactionResult:
    """Outcome of a compaction pass."""

    summary: str
    kept_turns: list[Turn]
    original_token_count: int
    compacted_token_count: int
    facts_preserved: int
    compression_ratio: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate — ``len(text) // 4``."""
    return max(len(text) // _CHARS_PER_TOKEN, 1) if text else 0


def _turns_token_count(turns: list[Turn]) -> int:
    return sum(_estimate_tokens(t.content) for t in turns)


def _count_facts(summary: str) -> int:
    """Heuristic fact counter — counts bullet points and sentences."""
    if not summary:
        return 0
    bullets = summary.count("\n- ") + summary.count("\n* ")
    # count sentences (period followed by space or end-of-string)
    sentences = sum(
        1
        for i, ch in enumerate(summary)
        if ch == "." and (i + 1 == len(summary) or summary[i + 1] in " \n")
    )
    return max(bullets, sentences, 1)


def _build_compaction_prompt(turns: list[Turn]) -> list[dict[str, str]]:
    """Build the messages list sent to the LLM for summarization."""
    conversation_text = "\n".join(
        f"[{t.role}]: {t.content}" for t in turns if t.content
    )
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Summarize the following conversation. "
                "Preserve every factual detail.\n\n"
                f"{conversation_text}"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# ContextCompactor
# ---------------------------------------------------------------------------


class ContextCompactor:
    """Lossless conversation compaction via LLM summarization."""

    async def estimate_tokens(self, turns: list[Turn]) -> int:
        """Return a rough token estimate for *turns*."""
        return _turns_token_count(turns)

    async def should_compact(
        self,
        turns: list[Turn],
        max_tokens: int = 0,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> bool:
        """Decide whether *turns* warrant compaction.

        Returns ``True`` when the estimated token count exceeds
        *threshold* × *max_tokens* **and** there are enough turns to
        compact (i.e. more than *keep_recent*).

        If *max_tokens* is 0 or negative, compaction is always skipped.
        """
        if max_tokens <= 0:
            return False
        if len(turns) <= keep_recent:
            return False
        usage = await self.estimate_tokens(turns)
        return usage > max_tokens * threshold

    async def compact(
        self,
        turns: list[Turn],
        provider: Any,
        *,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> CompactionResult:
        """Compact *turns* by summarizing older ones via *provider*.

        The most recent *keep_recent* turns are preserved as-is.
        Older turns (excluding any leading system turn) are sent to
        the LLM for summarization.  If the LLM call fails the
        original turns are returned unchanged.

        Parameters
        ----------
        turns:
            Full conversation history.
        provider:
            An ``LLMProvider`` instance (must expose ``chat()``).
        keep_recent:
            Number of trailing turns to keep verbatim.
        """
        original_tokens = _turns_token_count(turns)

        # Nothing to compact
        if len(turns) <= keep_recent:
            return CompactionResult(
                summary="",
                kept_turns=list(turns),
                original_token_count=original_tokens,
                compacted_token_count=original_tokens,
                facts_preserved=0,
                compression_ratio=1.0,
            )

        # Split: system prompt | older turns | recent turns
        older: list[Turn] = []
        system_turns: list[Turn] = []
        recent = turns[-keep_recent:]

        for t in turns[:-keep_recent]:
            if t.role == "system":
                system_turns.append(t)
            else:
                older.append(t)

        # If there's nothing to summarize (all system), skip
        if not older:
            return CompactionResult(
                summary="",
                kept_turns=list(turns),
                original_token_count=original_tokens,
                compacted_token_count=original_tokens,
                facts_preserved=0,
                compression_ratio=1.0,
            )

        # Ask the LLM to summarize
        try:
            summary = await self._call_llm(provider, older)
        except Exception:
            log.warning("compaction: LLM call failed — skipping", exc_info=True)
            return CompactionResult(
                summary="",
                kept_turns=list(turns),
                original_token_count=original_tokens,
                compacted_token_count=original_tokens,
                facts_preserved=0,
                compression_ratio=1.0,
            )

        facts = _count_facts(summary)
        kept = system_turns + list(recent)
        compacted_tokens = _turns_token_count(kept) + _estimate_tokens(summary)
        ratio = compacted_tokens / original_tokens if original_tokens else 1.0

        return CompactionResult(
            summary=summary,
            kept_turns=kept,
            original_token_count=original_tokens,
            compacted_token_count=compacted_tokens,
            facts_preserved=facts,
            compression_ratio=ratio,
        )

    def merge_summaries(self, old_summary: str, new_summary: str) -> str:
        """Merge two summaries for incremental compaction.

        Concatenates with a divider.  A subsequent compaction pass can
        re-summarize the merged text to keep it tight.
        """
        if not old_summary:
            return new_summary
        if not new_summary:
            return old_summary
        return f"{old_summary}\n\n---\n\n{new_summary}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _call_llm(provider: Any, turns: list[Turn]) -> str:
        """Send older turns to the LLM and return a summary string."""
        messages = _build_compaction_prompt(turns)
        result = await provider.chat(
            messages,
            stream=False,
            temperature=0.3,
            max_tokens=2048,
        )
        # provider.chat(stream=False) returns a dict
        if isinstance(result, dict):
            # Standard OpenAI-style response
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            # Fallback: some providers put content at top level
            return result.get("content", "")
        # AsyncGenerator fallback — drain it
        chunks: list[str] = []
        async for chunk in result:  # type: ignore[union-attr]
            chunks.append(chunk)
        return "".join(chunks)
