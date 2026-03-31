"""Never-Blocking Architecture — priority request queue for LLM inference.

Ensures the user can ALWAYS interact: no request ever blocks Chat, CLI, or voice.
Requests are prioritised (voice > chat > CLI > background) and higher-priority
requests can preempt lower-priority background tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Priority tiers
# ---------------------------------------------------------------------------

class Priority(IntEnum):
    """Request priority — lower numeric value = higher priority."""

    VOICE = 0
    CHAT = 1
    CLI = 2
    BACKGROUND = 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class InferenceRequest:
    """A queued LLM inference request."""

    id: str
    user_id: str
    messages: list[dict]
    priority: int = Priority.CHAT
    model: str | None = None
    max_tokens: int | None = None
    stream: bool = True
    cancellable: bool = True


@dataclass
class InferenceResult:
    """The outcome of an inference request."""

    request_id: str
    content: str
    model_used: str
    tokens: int
    elapsed: float
    cancelled: bool = False
    partial: bool = False


# ---------------------------------------------------------------------------
# Internal bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class _QueueEntry:
    """Wrapper used inside the asyncio.PriorityQueue.

    Ordering: (priority, sequence) guarantees FIFO within the same priority
    tier and prevents dataclass comparison issues.
    """

    priority: int
    sequence: int
    request: InferenceRequest = field(compare=False)

    def __lt__(self, other: _QueueEntry) -> bool:  # type: ignore[override]
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.sequence < other.sequence


class _RequestState:
    """Mutable state attached to each in-flight request."""

    __slots__ = (
        "request",
        "cancel_event",
        "result_future",
        "status",
        "partial_content",
        "token_count",
        "start_time",
        "processing_task",
        "token_sink",
    )

    def __init__(self, request: InferenceRequest) -> None:
        self.request = request
        self.cancel_event = asyncio.Event()
        self.result_future: asyncio.Future[InferenceResult] = (
            asyncio.get_running_loop().create_future()
        )
        self.status: str = "queued"  # queued → processing → completed/cancelled/failed
        self.partial_content: str = ""
        self.token_count: int = 0
        self.start_time: float = 0.0
        self.processing_task: asyncio.Task[None] | None = None
        self.token_sink: asyncio.Queue[str | None] | None = None


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class _ProviderLike:
    """Structural typing helper — any object with an async ``chat()``."""

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# RequestQueue
# ---------------------------------------------------------------------------

class RequestQueue:
    """Priority-based request queue with cancellation and preemption.

    Parameters
    ----------
    provider:
        An LLM provider instance that exposes an async ``chat()`` method
        compatible with :class:`cortex.providers.base.LLMProvider`.
    max_concurrent:
        Number of inference slots (mirrors ``n_parallel`` in llama-server).
    max_queued:
        Maximum pending requests before the queue starts rejecting.
    """

    def __init__(
        self,
        provider: Any,
        *,
        max_concurrent: int = 4,
        max_queued: int = 256,
    ) -> None:
        self._provider = provider
        self._max_concurrent = max_concurrent
        self._max_queued = max_queued

        self._queue: asyncio.PriorityQueue[_QueueEntry] = asyncio.PriorityQueue(
            maxsize=max_queued,
        )
        self._seq: int = 0
        self._states: dict[str, _RequestState] = {}
        self._active_count: int = 0
        self._slot_available = asyncio.Event()
        self._slot_available.set()
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background dispatcher loop."""
        if self._running:
            return
        self._running = True
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher(), name="request-queue-dispatcher",
        )
        log.info("RequestQueue started (slots=%d)", self._max_concurrent)

    async def stop(self) -> None:
        """Gracefully shut down — cancel queued work and wait for active slots."""
        self._running = False
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
        # Cancel everything still tracked.
        for rid in list(self._states):
            self._cancel_state(rid, reason="shutdown")
        log.info("RequestQueue stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, request: InferenceRequest) -> str:
        """Enqueue a request.  Returns the ``request.id``.

        Raises ``asyncio.QueueFull`` if the queue is at capacity.
        """
        if not request.id:
            request.id = uuid.uuid4().hex[:12]

        state = _RequestState(request)
        self._states[request.id] = state

        entry = _QueueEntry(
            priority=request.priority,
            sequence=self._next_seq(),
            request=request,
        )
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            del self._states[request.id]
            raise

        log.debug(
            "Submitted request %s  priority=%d  user=%s",
            request.id, request.priority, request.user_id,
        )
        return request.id

    async def wait(
        self,
        request_id: str,
        timeout: float | None = None,
    ) -> InferenceResult:
        """Block until *request_id* completes or times out.

        Raises ``KeyError`` if the id is unknown and ``asyncio.TimeoutError``
        on timeout.
        """
        state = self._states.get(request_id)
        if state is None:
            raise KeyError(request_id)
        return await asyncio.wait_for(state.result_future, timeout=timeout)

    async def submit_and_stream(
        self,
        request: InferenceRequest,
    ) -> AsyncGenerator[str, None]:
        """Submit and yield tokens as they arrive.

        Cancellation-aware: if the request is cancelled the generator closes
        cleanly, yielding whatever partial content was produced.
        """
        request.stream = True
        rid = self.submit(request)
        state = self._states[rid]
        token_queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Stash a token sink so _process_request can push tokens.
        state.token_sink = token_queue

        try:
            while True:
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=120)
                except asyncio.TimeoutError:
                    break
                if token is None:
                    break
                yield token
        finally:
            # Ensure state is cleaned up even if consumer stops reading.
            if state.status == "processing":
                self._cancel_state(rid, reason="stream-closed")

    def cancel_request(self, request_id: str) -> bool:
        """Cancel a single request.  Returns ``True`` if it was still active."""
        return self._cancel_state(request_id, reason="user-cancel")

    def cancel_for_user(self, user_id: str) -> int:
        """Cancel every pending/processing request belonging to *user_id*.

        Returns the number of requests cancelled.
        """
        cancelled = 0
        for rid, st in list(self._states.items()):
            if st.request.user_id == user_id and st.status in ("queued", "processing"):
                if self._cancel_state(rid, reason="user-interrupt"):
                    cancelled += 1
        return cancelled

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    def status(self, request_id: str) -> str | None:
        """Return current status string or ``None`` if unknown."""
        st = self._states.get(request_id)
        return st.status if st else None

    # ------------------------------------------------------------------
    # Dispatcher loop
    # ------------------------------------------------------------------

    async def _dispatcher(self) -> None:
        """Continuously pull entries from the queue and assign them to slots."""
        while self._running:
            try:
                # Get next entry (blocks until one is available).
                try:
                    entry = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                state = self._states.get(entry.request.id)
                if state is None or state.cancel_event.is_set():
                    # Already cancelled while queued.
                    continue

                # Acquire a slot — preempt a lower-priority task if needed.
                acquired = False
                while not acquired:
                    if self._active_count < self._max_concurrent:
                        acquired = True
                    elif self._try_preempt(entry.request.priority):
                        # Preempted — give the cancelled task a moment to
                        # release its slot via its finally block.
                        await asyncio.sleep(0.02)
                    else:
                        # Can't preempt and no free slot — wait.
                        self._slot_available.clear()
                        await self._slot_available.wait()

                async with self._lock:
                    self._active_count += 1
                    if self._active_count >= self._max_concurrent:
                        self._slot_available.clear()

                state.processing_task = asyncio.create_task(
                    self._process_request(state),
                    name=f"infer-{entry.request.id}",
                )
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("Dispatcher loop error")
                await asyncio.sleep(0.1)

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def _process_request(self, state: _RequestState) -> None:
        """Run one inference request through the provider."""
        request = state.request
        state.status = "processing"
        state.start_time = time.monotonic()
        token_sink = state.token_sink

        content_parts: list[str] = []
        model_used = request.model or ""

        try:
            gen = await self._provider.chat(
                messages=request.messages,
                model=request.model,
                stream=request.stream,
                **({"max_tokens": request.max_tokens} if request.max_tokens else {}),
            )

            if request.stream and hasattr(gen, "__aiter__"):
                async for chunk in gen:
                    if state.cancel_event.is_set():
                        break
                    content_parts.append(chunk)
                    state.token_count += 1
                    state.partial_content = "".join(content_parts)
                    if token_sink is not None:
                        await token_sink.put(chunk)
            else:
                # Non-streaming: gen is a dict.
                body = gen if isinstance(gen, dict) else {}
                text = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", str(body))
                )
                content_parts.append(text)
                state.token_count = body.get("usage", {}).get("total_tokens", 1)
                model_used = body.get("model", model_used)

            elapsed = time.monotonic() - state.start_time
            cancelled = state.cancel_event.is_set()

            result = InferenceResult(
                request_id=request.id,
                content="".join(content_parts),
                model_used=model_used,
                tokens=state.token_count,
                elapsed=elapsed,
                cancelled=cancelled,
                partial=cancelled and len(content_parts) > 0,
            )
            state.status = "cancelled" if cancelled else "completed"
            if not state.result_future.done():
                state.result_future.set_result(result)

        except Exception as exc:
            elapsed = time.monotonic() - state.start_time
            state.status = "failed"
            result = InferenceResult(
                request_id=request.id,
                content=state.partial_content,
                model_used=model_used,
                tokens=state.token_count,
                elapsed=elapsed,
                cancelled=False,
                partial=bool(state.partial_content),
            )
            if not state.result_future.done():
                state.result_future.set_exception(exc)
            log.error("Inference failed for %s: %s", request.id, exc)
        finally:
            if token_sink is not None:
                try:
                    token_sink.put_nowait(None)  # signal end-of-stream
                except asyncio.QueueFull:
                    pass
            async with self._lock:
                self._active_count -= 1
                self._slot_available.set()

    # ------------------------------------------------------------------
    # Preemption
    # ------------------------------------------------------------------

    def _try_preempt(self, incoming_priority: int) -> bool:
        """Cancel the lowest-priority cancellable active request if it has
        strictly lower priority (higher numeric value) than *incoming_priority*.

        Returns ``True`` if a slot was freed.
        """
        worst_rid: str | None = None
        worst_priority: int = -1

        for rid, st in self._states.items():
            if (
                st.status == "processing"
                and st.request.cancellable
                and st.request.priority > incoming_priority
                and st.request.priority > worst_priority
            ):
                worst_rid = rid
                worst_priority = st.request.priority

        if worst_rid is not None:
            log.info(
                "Preempting request %s (priority %d) for incoming priority %d",
                worst_rid, worst_priority, incoming_priority,
            )
            self._cancel_state(worst_rid, reason="preempted")
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cancel_state(self, request_id: str, *, reason: str = "") -> bool:
        state = self._states.get(request_id)
        if state is None:
            return False
        if state.status in ("completed", "cancelled", "failed"):
            return False

        state.cancel_event.set()
        prev = state.status
        state.status = "cancelled"

        if state.processing_task is not None and not state.processing_task.done():
            state.processing_task.cancel()

        # Resolve the future so waiters don't hang.
        if not state.result_future.done():
            elapsed = (time.monotonic() - state.start_time) if state.start_time else 0.0
            state.result_future.set_result(
                InferenceResult(
                    request_id=request_id,
                    content=state.partial_content,
                    model_used=state.request.model or "",
                    tokens=state.token_count,
                    elapsed=elapsed,
                    cancelled=True,
                    partial=bool(state.partial_content),
                ),
            )

        log.debug("Cancelled %s (%s → cancelled) reason=%s", request_id, prev, reason)
        return True

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq
