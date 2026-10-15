"""LLM-only conversation perspective and bounded durable dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict
from typing import Any, AsyncGenerator, Protocol

from cortex.orchestrator.turn_store import (
    StoredTurn, TurnConflictError, TurnStore, TurnStoreError,
)
from cortex.pipeline.request_queue import InferenceRequest, Priority, RequestQueue
from cortex.pipeline.turns import (
    ConversationConflictError, ConversationStorageError, DispatchResult, GenerationOptions,
    PipelineGenerationError, PreparedTurn, RouteStatus, TurnSupersededError,
    get_conversation_coordinator, register_conversation_coordinator, unregister_conversation_coordinator,
)
from cortex.plugins.intent import command_scope, correction_fragment
from cortex.providers.base import LLMProvider
from cortex.safety import GuardrailResult, Severity

logger = logging.getLogger(__name__)


class ConversationSafety(Protocol):
    def check_input(self, message: str, user_id: str, metadata: dict[str, Any]) -> GuardrailResult: ...
    def build_system_prompt(self, user_id: str, metadata: dict[str, Any]) -> str: ...
    def check_output(
        self, response: str, user_id: str, last_user_message: str, system_prompt: str,
        metadata: dict[str, Any],
    ) -> GuardrailResult: ...


class _QueuedProvider(LLMProvider):
    """Layer 3 keeps model routing; RequestQueue owns actual provider admission."""

    def __init__(self, queue: RequestQueue, provider: LLMProvider, turn: StoredTurn) -> None:
        self.queue = queue
        self.provider = provider
        self.turn = turn

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, stream: bool = True,
        temperature: float = 0.7, max_tokens: int | None = None, **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]:
        request = InferenceRequest(
            id=f"{self.turn.identity.turn_id}:{self.turn.identity.revision}",
            user_id=self.turn.identity.user_id, messages=messages, model=model,
            priority=Priority.VOICE if self.turn.identity.source in ("satellite", "voice") else Priority.CHAT,
            max_tokens=max_tokens, cancellable=False,
        )

        async def tokens():
            try:
                async for token in self.queue.submit_and_stream(request, buffer_size=128):
                    yield token
            finally:
                await self.queue.release(request.id)

        return tokens()

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        return await self.provider.embed(text, model)

    async def list_models(self) -> list[dict[str, Any]]:
        return await self.provider.list_models()

    async def health(self) -> bool:
        return await self.provider.health()


class ConversationCoordinator:
    """Only IDs are prefetched; complete generation is validated before publication."""

    def __init__(
        self, store: TurnStore, provider: LLMProvider, safety: ConversationSafety,
    ) -> None:
        self.store = store
        self.provider = provider
        self.safety = safety
        self.queue = RequestQueue(
            provider, max_concurrent=store.policy.max_active,
            max_queued=store.policy.prefetch,
        )
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._changed = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None
        self._running = False
        self._fatal_error: TurnStoreError | None = None

    async def start(self) -> None:
        if self._running:
            return
        register_conversation_coordinator(self)
        try:
            await self.queue.start()
            self._running = True
            self._dispatcher = asyncio.create_task(self._dispatch(), name="conversation-dispatch")
        except BaseException:
            self._running = False
            unregister_conversation_coordinator(self)
            await self.queue.stop()
            raise

    async def stop(self) -> None:
        self._running = False
        unregister_conversation_coordinator(self)
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
            self._dispatcher = None
        tasks = list(self._jobs.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.queue.stop()
        self._signal()

    def queue_status(self) -> dict[str, Any]:
        counts = self.store.status()["counts"]
        return {
            "depth": counts.get("llm_pending", 0), "active": self.queue.active_count,
            "running": self._running and self._fatal_error is None,
            "prefetched": self.queue.queued_count,
        }

    def _signal(self) -> None:
        previous = self._changed
        self._changed = asyncio.Event()
        previous.set()

    def _check_available(self) -> None:
        if not self._running:
            raise ConversationStorageError("Conversation processing is not running")
        if self._fatal_error is not None:
            raise ConversationStorageError("Conversation storage failed; recovery is required") from self._fatal_error

    def _admit(self, prepared: PreparedTurn, options: GenerationOptions) -> StoredTurn:
        identity = prepared.identity
        context = prepared.context_snapshot()
        metadata = context.get("metadata", {})
        fingerprint = hashlib.sha256(json.dumps({
            "message": prepared.message, "conversation_id": identity.conversation_id,
            "user_id": identity.user_id, "source": identity.source,
            "device_id": identity.device_id, "room": identity.room,
            "speaker_id": context.get("speaker_id"), "idempotency_key": identity.idempotency_key,
            "history": context.get("conversation_history", []),
            "relation": metadata.get("relation"), "related_turn_id": metadata.get("related_turn_id"),
            "related_revision": metadata.get("related_revision"),
            "models": (options.model_fast, options.model_thinking),
        }, sort_keys=True, allow_nan=False).encode()).hexdigest()
        existing = self.store.find_existing(identity)
        if existing is not None:
            if existing.context.get("request_fingerprint") != fingerprint:
                raise TurnConflictError("Turn identity was reused for different work")
            return existing

        relation = metadata.get("relation", "independent")
        if relation not in ("independent", "correction", "replacement", "clarification"):
            raise TurnConflictError("Unknown conversation relation")
        target_id = metadata.get("related_turn_id")
        requested_correction = relation in ("correction", "replacement") or (
            "relation" not in metadata and re.match(
                r"^\s*(?:actually|no|i meant|correction)[,\s]+", prepared.message, re.IGNORECASE,
            ) is not None
        )
        if target_id is not None and (not isinstance(target_id, str) or not target_id.strip()):
            raise TurnConflictError("Related turn ID must be nonempty text")
        if target_id is not None and relation == "independent":
            raise TurnConflictError("A related turn ID requires an explicit relation")
        candidates = self.store.scoped_questions(identity, context.get("speaker_id"))
        target = None
        clarification = None
        if target_id is not None:
            target = self.store.get(target_id, identity.user_id)
            if (target.identity.conversation_id != identity.conversation_id
                    or target.identity.source != identity.source
                    or target.identity.device_id != identity.device_id
                    or target.identity.room != identity.room
                    or target.context.get("speaker_id") != context.get("speaker_id")):
                raise TurnConflictError("Related question is not in this conversation scope")
            revision = metadata.get("related_revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision != target.identity.revision:
                raise TurnConflictError("Related question revision is obsolete")
        elif requested_correction:
            if command_scope(prepared.context) is not None and len(candidates) == 1:
                target = candidates[0]
            elif not candidates and "relation" not in metadata and correction_fragment(prepared.message) is None:
                requested_correction = False
            else:
                clarification = "Which question are you correcting? Please restate the complete question."
        if requested_correction and target is not None:
            if target.state not in ("llm_pending", "llm_running"):
                raise TurnConflictError("Only a pending or generating question can be replaced")
            context["turn_relation"] = {
                "kind": "replacement", "target_turn_id": target.identity.turn_id,
                "target_revision": target.identity.revision, "target_sequence": target.sequence,
                "original_question": target.transcript,
            }
        elif relation == "clarification":
            if target is None:
                raise TurnConflictError("Clarification requires an explicit related question")
            context["turn_relation"] = {
                "kind": "clarification", "target_turn_id": target.identity.turn_id,
                "target_revision": target.identity.revision, "original_question": target.transcript,
            }
        context["request_fingerprint"] = fingerprint
        context["generation_options"] = asdict(options)
        turn = self.store.accept(
            identity, prepared.message, input_validated=True, context=context,
            replaces=(target.identity.turn_id, target.identity.revision)
            if requested_correction and target is not None else None,
            clarifies=(target.identity.turn_id, target.identity.revision)
            if relation == "clarification" and target is not None else None,
        )
        owner = f"route:{identity.turn_id}"
        self.store.claim(identity.turn_id, identity.user_id, owner)
        if clarification:
            checked = self._validated_output(clarification, turn, options.system_prompt)
            self.store.complete(
                identity.turn_id, identity.user_id, owner, 1,
                DispatchResult(RouteStatus.CLARIFICATION, checked), output_validated=True,
            )
        else:
            self.store.mark_llm_pending(identity.turn_id, identity.user_id, owner, 1)
        if requested_correction and target is not None:
            task = self._jobs.get(target.identity.turn_id)
            if task is not None:
                task.cancel()
        self._signal()
        return self.store.get(identity.turn_id, identity.user_id)

    async def stream(
        self, prepared: PreparedTurn, options: GenerationOptions,
    ) -> AsyncGenerator[str, None]:
        self._check_available()
        metadata = prepared.context.get("metadata", {})
        checked = self.safety.check_input(prepared.message, prepared.identity.user_id, metadata)
        if checked.severity >= Severity.SOFT_BLOCK:
            prepared.context["perspective_status"] = RouteStatus.FAILED
            yield checked.suggested_response or "I'm not able to help with that request."
            return
        turn = None
        try:
            turn = self._admit(prepared, options)
            while True:
                self._check_available()
                current = self.store.get(turn.identity.turn_id, turn.identity.user_id)
                if current.identity.revision != turn.identity.revision or current.state in (
                    "superseded", "cancelled",
                ):
                    raise TurnSupersededError("This question was replaced or cancelled.")
                if current.state in ("uncertain", "recovery_pending") or current.delivery_state == "awaiting_confirmation":
                    raise PipelineGenerationError("This question or its delivery requires recovery confirmation.")
                if current.result is not None and current.output_validated:
                    prepared.context["perspective_status"] = RouteStatus(
                        current.route_status or "handled",
                    )
                    yield current.result
                    return
                if current.state == "failed":
                    raise PipelineGenerationError(
                        "The question could not complete or requires recovery confirmation.",
                    )
                changed = self._changed
                try:
                    await asyncio.wait_for(changed.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
        except TurnConflictError as exc:
            raise ConversationConflictError(str(exc)) from exc
        except TurnStoreError as exc:
            raise ConversationStorageError(str(exc)) from exc

    def cancel(self, turn_id: str, user_id: str, revision: int) -> None:
        self.store.cancel(turn_id, user_id, revision)
        task = self._jobs.get(turn_id)
        if task is not None:
            task.cancel()
        self._signal()

    def _validated_output(self, text: str, turn: StoredTurn, prompt: str) -> str:
        checked = self.safety.check_output(
            text, turn.identity.user_id, turn.transcript, prompt, turn.context.get("metadata", {}),
        )
        return (
            checked.suggested_response or "I'm not able to provide that response."
            if checked.severity >= Severity.SOFT_BLOCK else text
        )

    async def _dispatch(self) -> None:
        while self._running:
            try:
                changed = self._changed
                if self._fatal_error is None:
                    for turn_id, user_id, revision in self.store.llm_dispatch_ids():
                        if len(self._jobs) >= self.store.policy.max_active:
                            break
                        if turn_id in self._jobs:
                            continue
                        current = self.store.get(turn_id, user_id)
                        if current.state == "accepted":
                            route_owner = f"route:{turn_id}:{revision}"
                            self.store.claim(turn_id, user_id, route_owner, revision=revision)
                            self.store.mark_llm_pending(turn_id, user_id, route_owner, revision)
                        owner = f"llm:{turn_id}:{revision}"
                        turn = self.store.claim(turn_id, user_id, owner, revision=revision, stage="llm")
                        task = asyncio.create_task(self._generate(turn, owner), name=f"turn-{turn_id}")
                        self._jobs[turn_id] = task
                        task.add_done_callback(lambda done, tid=turn_id: self._job_done(tid, done))
                try:
                    await asyncio.wait_for(changed.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
            except TurnConflictError:
                logger.warning("Conversation dispatch claim changed; refreshing")
            except TurnStoreError as exc:
                logger.exception("Conversation dispatch storage failed")
                self._fatal_error = exc
                self._signal()

    def _job_done(self, turn_id: str, task: asyncio.Task[None]) -> None:
        self._jobs.pop(turn_id, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error("Conversation worker failed", exc_info=(type(error), error, error.__traceback__))
        self._signal()

    async def _generate(self, turn: StoredTurn, owner: str) -> None:
        from cortex.pipeline.layer3_llm import stream_llm_response

        identity = turn.identity
        worker = asyncio.current_task()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(max(0.1, self.store.policy.lease_seconds / 3))
                try:
                    self.store.heartbeat(identity.turn_id, identity.user_id, owner, identity.revision)
                except TurnConflictError:
                    logger.exception("Conversation lease could not be renewed")
                    if worker is not None:
                        worker.cancel()
                    return
                except TurnStoreError as exc:
                    logger.exception("Conversation lease storage failed")
                    self._fatal_error = exc
                    self._signal()
                    if worker is not None:
                        worker.cancel()
                    return

        lease = asyncio.create_task(heartbeat())
        try:
            options = GenerationOptions(**turn.context["generation_options"])
            context = {**turn.context, "turn_identity": identity}
            context["conversation_turn"] = {
                "turn_id": identity.turn_id, "revision": identity.revision,
                "sequence": turn.sequence, "relation": turn.relation,
            }
            history = self.store.completed_history(turn)
            context["conversation_turn"]["history_source"] = "durable" if history else "client_supplied"
            context["conversation_history"] = history or context.get("conversation_history", [])
            context["conversation_length"] = len(context["conversation_history"])
            metadata = context.get("metadata", {})
            checked = self.safety.check_input(turn.transcript, identity.user_id, metadata)
            safety_prompt = self.safety.build_system_prompt(identity.user_id, metadata)
            prompt = options.system_prompt
            if safety_prompt and safety_prompt not in prompt:
                prompt = f"{prompt}\n\n{safety_prompt}".strip()
            if checked.severity >= Severity.SOFT_BLOCK:
                response = checked.suggested_response or "I'm not able to help with that request."
                status = RouteStatus.FAILED
            else:
                parts: list[str] = []
                size = 0
                generation = stream_llm_response(
                    turn.transcript, context, _QueuedProvider(self.queue, self.provider, turn),
                    options.model_fast, options.model_thinking, options.memory_context, prompt,
                )
                try:
                    async for chunk in generation:
                        size += len(chunk.encode("utf-8"))
                        if size > self.store.policy.max_output_bytes:
                            raise PipelineGenerationError("Generated response exceeded the output byte limit")
                        parts.append(chunk)
                finally:
                    await generation.aclose()
                response = "".join(parts)
                status = RouteStatus.HANDLED
            if not response.strip():
                raise PipelineGenerationError("The language model returned no response")
            response = self._validated_output(response, turn, prompt)
            self.store.complete(
                identity.turn_id, identity.user_id, owner, identity.revision,
                DispatchResult(status, response, layer="llm"), output_validated=True,
            )
        except asyncio.CancelledError:
            raise
        except TurnConflictError:
            logger.info("Discarded obsolete conversation result: %s", identity.turn_id)
        except Exception:
            logger.exception("Conversation generation failed")
            try:
                current = self.store.get(identity.turn_id, identity.user_id)
                if current.state == "llm_running" and current.identity.revision == identity.revision:
                    self.store.fail(identity.turn_id, identity.user_id, owner, identity.revision, "generation_failed")
            except TurnStoreError as exc:
                logger.exception("Could not persist conversation failure")
                self._fatal_error = exc
        finally:
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
