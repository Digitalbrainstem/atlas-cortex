"""Tests for the never-blocking request queue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from cortex.pipeline.request_queue import (
    InferenceRequest,
    InferenceResult,
    Priority,
    RequestQueue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(
    *,
    user: str = "u1",
    priority: int = Priority.CHAT,
    rid: str | None = None,
    stream: bool = True,
    cancellable: bool = True,
    model: str | None = None,
) -> InferenceRequest:
    return InferenceRequest(
        id=rid or "",
        user_id=user,
        messages=[{"role": "user", "content": "hello"}],
        priority=priority,
        model=model,
        stream=stream,
        cancellable=cancellable,
    )


class FakeProvider:
    """A mock LLM provider with configurable latency and output."""

    def __init__(
        self,
        tokens: list[str] | None = None,
        delay: float = 0.0,
        non_stream_response: dict | None = None,
    ) -> None:
        self.tokens = tokens or ["Hello", " world"]
        self.delay = delay
        self.non_stream_response = non_stream_response
        self.call_count = 0
        self.cancel_aware = True

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]:
        self.call_count += 1
        if not stream and self.non_stream_response is not None:
            return self.non_stream_response

        async def _gen() -> AsyncGenerator[str, None]:
            for tok in self.tokens:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield tok

        return _gen()


class SlowProvider(FakeProvider):
    """Provider that yields tokens slowly — useful for cancellation tests."""

    def __init__(self, token_count: int = 50, delay: float = 0.05) -> None:
        super().__init__(tokens=[f"t{i}" for i in range(token_count)], delay=delay)


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_voice_before_chat_before_cli(self):
        """Higher-priority requests are processed first."""
        order: list[str] = []
        processed = asyncio.Event()
        expected = 3

        class OrderTracker(FakeProvider):
            async def chat(self, messages, model=None, stream=True, **kw):
                rid = messages[0]["content"]
                order.append(rid)
                if len(order) >= expected:
                    processed.set()
                return await super().chat(messages, model=model, stream=stream, **kw)

        provider = OrderTracker(delay=0.01)
        q = RequestQueue(provider, max_concurrent=1)

        # Submit in reverse priority order while queue isn't processing yet.
        q.submit(_req(rid="cli", priority=Priority.CLI,
                       user="u1"))
        q._states["cli"].request.messages = [{"role": "user", "content": "cli"}]

        q.submit(_req(rid="chat", priority=Priority.CHAT,
                       user="u1"))
        q._states["chat"].request.messages = [{"role": "user", "content": "chat"}]

        q.submit(_req(rid="voice", priority=Priority.VOICE,
                       user="u1"))
        q._states["voice"].request.messages = [{"role": "user", "content": "voice"}]

        await q.start()
        await asyncio.wait_for(processed.wait(), timeout=5)
        await q.stop()

        assert order == ["voice", "chat", "cli"]

    @pytest.mark.asyncio
    async def test_fifo_within_same_priority(self):
        """Requests at the same priority are served FIFO."""
        order: list[str] = []
        processed = asyncio.Event()

        class OrderTracker(FakeProvider):
            async def chat(self, messages, model=None, stream=True, **kw):
                rid = messages[0]["content"]
                order.append(rid)
                if len(order) >= 3:
                    processed.set()
                return await super().chat(messages, model=model, stream=stream, **kw)

        provider = OrderTracker(delay=0.01)
        q = RequestQueue(provider, max_concurrent=1)

        for i in range(3):
            r = _req(rid=f"r{i}", priority=Priority.CHAT)
            r.messages = [{"role": "user", "content": f"r{i}"}]
            q.submit(r)

        await q.start()
        await asyncio.wait_for(processed.wait(), timeout=5)
        await q.stop()

        assert order == ["r0", "r1", "r2"]


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_pending_request(self):
        """A queued (not yet processing) request can be cancelled."""
        provider = SlowProvider(token_count=100, delay=0.1)
        q = RequestQueue(provider, max_concurrent=1)

        # Fill the slot.
        q.submit(_req(rid="blocking", priority=Priority.VOICE))
        # This one will queue behind it.
        q.submit(_req(rid="victim", priority=Priority.CHAT))

        await q.start()
        await asyncio.sleep(0.05)

        assert q.cancel_request("victim") is True
        result = await q.wait("victim", timeout=2)
        assert result.cancelled is True
        await q.stop()

    @pytest.mark.asyncio
    async def test_cancel_in_progress_request(self):
        """An actively processing request can be cancelled mid-stream."""
        provider = SlowProvider(token_count=100, delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)
        q.submit(_req(rid="active", priority=Priority.CHAT))

        await q.start()
        await asyncio.sleep(0.15)  # let a few tokens through

        assert q.cancel_request("active") is True
        result = await q.wait("active", timeout=2)
        assert result.cancelled is True

        await q.stop()

    @pytest.mark.asyncio
    async def test_cancel_returns_partial(self):
        """Cancelled in-progress requests return partial content."""
        provider = SlowProvider(token_count=100, delay=0.03)
        q = RequestQueue(provider, max_concurrent=1)
        q.submit(_req(rid="partial", priority=Priority.CHAT))

        await q.start()
        await asyncio.sleep(0.15)

        q.cancel_request("partial")
        result = await q.wait("partial", timeout=2)
        assert result.cancelled is True
        assert result.partial is True
        assert len(result.content) > 0
        await q.stop()

    @pytest.mark.asyncio
    async def test_cancel_unknown_id(self):
        provider = FakeProvider()
        q = RequestQueue(provider)
        assert q.cancel_request("nonexistent") is False

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self):
        """Cancelling a completed request is a no-op."""
        provider = FakeProvider(delay=0.0)
        q = RequestQueue(provider, max_concurrent=1)
        q.submit(_req(rid="done"))

        await q.start()
        await asyncio.sleep(0.3)

        result = await q.wait("done", timeout=2)
        assert result.cancelled is False
        assert q.cancel_request("done") is False
        await q.stop()


# ---------------------------------------------------------------------------
# User interruption
# ---------------------------------------------------------------------------

class TestUserInterruption:
    @pytest.mark.asyncio
    async def test_cancel_for_user(self):
        """cancel_for_user removes all pending/processing for that user."""
        provider = SlowProvider(token_count=50, delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="a1", user="alice", priority=Priority.CHAT))
        q.submit(_req(rid="a2", user="alice", priority=Priority.CLI))
        q.submit(_req(rid="b1", user="bob", priority=Priority.CHAT))

        await q.start()
        await asyncio.sleep(0.05)

        count = q.cancel_for_user("alice")
        assert count == 2

        # Bob's request should still be alive.
        bob_state = q._states.get("b1")
        assert bob_state is not None
        assert bob_state.status in ("queued", "processing")

        await q.stop()

    @pytest.mark.asyncio
    async def test_cancel_for_user_no_match(self):
        provider = FakeProvider()
        q = RequestQueue(provider)
        assert q.cancel_for_user("ghost") == 0


# ---------------------------------------------------------------------------
# Concurrent slot limiting
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_respects_max_concurrent(self):
        """No more than max_concurrent requests process simultaneously."""
        peak = 0
        current = 0
        lock = asyncio.Lock()

        class ConcurrencyTracker(FakeProvider):
            async def chat(self, messages, model=None, stream=True, **kw):
                nonlocal peak, current
                async with lock:
                    current += 1
                    if current > peak:
                        peak = current
                await asyncio.sleep(0.1)
                async with lock:
                    current -= 1
                return await super().chat(messages, model=model, stream=stream, **kw)

        provider = ConcurrencyTracker()
        q = RequestQueue(provider, max_concurrent=2)

        for i in range(6):
            q.submit(_req(rid=f"c{i}", priority=Priority.CHAT))

        await q.start()
        # Give enough time for all to complete.
        await asyncio.sleep(1.0)
        await q.stop()

        assert peak <= 2

    @pytest.mark.asyncio
    async def test_single_slot(self):
        """With max_concurrent=1, requests serialize."""
        order: list[str] = []

        class OrderTracker(FakeProvider):
            async def chat(self, messages, model=None, stream=True, **kw):
                rid = messages[0]["content"]
                order.append(rid)
                return await super().chat(messages, model=model, stream=stream, **kw)

        provider = OrderTracker(delay=0.01)
        q = RequestQueue(provider, max_concurrent=1)

        for i in range(3):
            r = _req(rid=f"s{i}", priority=Priority.CHAT)
            r.messages = [{"role": "user", "content": f"s{i}"}]
            q.submit(r)

        await q.start()
        await asyncio.sleep(0.5)
        await q.stop()

        assert len(order) == 3


# ---------------------------------------------------------------------------
# Streaming with cancellation
# ---------------------------------------------------------------------------

class TestStreaming:
    @pytest.mark.asyncio
    async def test_stream_all_tokens(self):
        provider = FakeProvider(tokens=["a", "b", "c"], delay=0.01)
        q = RequestQueue(provider, max_concurrent=1)
        await q.start()

        collected: list[str] = []
        async for tok in q.submit_and_stream(
            _req(rid="stream1", priority=Priority.CHAT),
        ):
            collected.append(tok)

        await q.stop()
        assert collected == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_stream_cancelled_midway(self):
        """Cancelling mid-stream yields partial tokens then stops."""
        provider = SlowProvider(token_count=50, delay=0.03)
        q = RequestQueue(provider, max_concurrent=1)
        await q.start()

        collected: list[str] = []
        async for tok in q.submit_and_stream(
            _req(rid="streamcancel", priority=Priority.CHAT),
        ):
            collected.append(tok)
            if len(collected) >= 3:
                q.cancel_request("streamcancel")

        await q.stop()
        assert 3 <= len(collected) <= 10  # got some but not all 50


# ---------------------------------------------------------------------------
# Queue overflow
# ---------------------------------------------------------------------------

class TestOverflow:
    @pytest.mark.asyncio
    async def test_queue_full_raises(self):
        provider = SlowProvider(token_count=10, delay=0.1)
        q = RequestQueue(provider, max_concurrent=1, max_queued=2)

        q.submit(_req(rid="o1"))
        q.submit(_req(rid="o2"))

        with pytest.raises(asyncio.QueueFull):
            q.submit(_req(rid="o3"))


# ---------------------------------------------------------------------------
# Background preemption
# ---------------------------------------------------------------------------

class TestPreemption:
    @pytest.mark.asyncio
    async def test_voice_preempts_background(self):
        """A voice request preempts a running background task."""
        provider = SlowProvider(token_count=200, delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="bg", priority=Priority.BACKGROUND, cancellable=True))
        await q.start()
        await asyncio.sleep(0.1)  # let bg start processing

        assert q._states["bg"].status == "processing"

        # Now submit a voice request.
        q.submit(_req(rid="vox", priority=Priority.VOICE))
        await asyncio.sleep(0.5)

        bg_result = await q.wait("bg", timeout=2)
        assert bg_result.cancelled is True

        await q.stop()

    @pytest.mark.asyncio
    async def test_no_preempt_same_priority(self):
        """Same-priority requests do NOT preempt each other."""
        provider = SlowProvider(token_count=200, delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="first", priority=Priority.CHAT, cancellable=True))
        await q.start()
        await asyncio.sleep(0.1)

        q.submit(_req(rid="second", priority=Priority.CHAT))
        await asyncio.sleep(0.15)

        # first should NOT be cancelled by second (same priority).
        first_state = q._states["first"]
        assert first_state.status == "processing"

        await q.stop()

    @pytest.mark.asyncio
    async def test_non_cancellable_not_preempted(self):
        """A non-cancellable background task cannot be preempted."""
        provider = SlowProvider(token_count=200, delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(
            _req(rid="protected", priority=Priority.BACKGROUND, cancellable=False),
        )
        await q.start()
        await asyncio.sleep(0.1)

        q.submit(_req(rid="vox2", priority=Priority.VOICE))
        await asyncio.sleep(0.15)

        assert q._states["protected"].status == "processing"
        await q.stop()


# ---------------------------------------------------------------------------
# Introspection & edge cases
# ---------------------------------------------------------------------------

class TestIntrospection:
    @pytest.mark.asyncio
    async def test_status_lifecycle(self):
        provider = FakeProvider(tokens=["ok"], delay=0.05)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="lc"))
        assert q.status("lc") == "queued"

        await q.start()
        await asyncio.sleep(0.3)

        assert q.status("lc") == "completed"
        assert q.status("unknown") is None
        await q.stop()

    @pytest.mark.asyncio
    async def test_wait_unknown_id_raises(self):
        provider = FakeProvider()
        q = RequestQueue(provider)
        with pytest.raises(KeyError):
            await q.wait("nope", timeout=0.1)

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        provider = SlowProvider(token_count=200, delay=0.1)
        q = RequestQueue(provider, max_concurrent=1)
        q.submit(_req(rid="slow"))
        await q.start()

        with pytest.raises(asyncio.TimeoutError):
            await q.wait("slow", timeout=0.05)

        await q.stop()

    @pytest.mark.asyncio
    async def test_queued_count(self):
        provider = SlowProvider(token_count=100, delay=0.1)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="q1"))
        q.submit(_req(rid="q2"))
        q.submit(_req(rid="q3"))
        assert q.queued_count == 3

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        provider = FakeProvider()
        q = RequestQueue(provider)
        await q.start()
        await q.start()  # should not raise
        await q.stop()

    @pytest.mark.asyncio
    async def test_non_stream_response(self):
        resp = {
            "choices": [{"message": {"content": "non-stream reply"}}],
            "model": "test-model",
            "usage": {"total_tokens": 42},
        }
        provider = FakeProvider(non_stream_response=resp)
        q = RequestQueue(provider, max_concurrent=1)

        q.submit(_req(rid="ns", stream=False))
        await q.start()

        result = await q.wait("ns", timeout=3)
        assert result.content == "non-stream reply"
        assert result.model_used == "test-model"
        assert result.tokens == 42
        assert result.cancelled is False
        await q.stop()


# ---------------------------------------------------------------------------
# Priority enum
# ---------------------------------------------------------------------------

class TestPriorityEnum:
    def test_ordering(self):
        assert Priority.VOICE < Priority.CHAT < Priority.CLI < Priority.BACKGROUND

    def test_values(self):
        assert Priority.VOICE == 0
        assert Priority.CHAT == 1
        assert Priority.CLI == 2
        assert Priority.BACKGROUND == 3
