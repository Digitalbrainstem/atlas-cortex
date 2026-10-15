"""Shared identity and dispatch contracts for finalized conversation turns."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
import weakref
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Protocol

logger = logging.getLogger(__name__)
DEFAULT_MAX_OUTPUT_BYTES = 262144


def output_byte_limit() -> int:
    key = "CORTEX_TURN_MAX_OUTPUT_BYTES"
    try:
        limit = int(os.environ.get(key, str(DEFAULT_MAX_OUTPUT_BYTES)))
        if limit < 1:
            raise ValueError("limit must be positive")
        return limit
    except ValueError:
        logger.error("%s must be a positive integer", key)
        raise

def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Discard caller-supplied policy/credential fields before durable storage."""
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Metadata must be an object")
    private = {
        "content_tier", "forced_content_tier", "role", "is_admin", "authenticated",
        "authenticated_user_id", "permissions", "user_id", "authorization", "token",
        "chat_token", "admin_token", "access_token", "refresh_token", "password",
        "pin", "api_key", "ha_token", "device_fingerprint",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key.lower() not in private}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(metadata or {})


class PipelineGenerationError(RuntimeError):
    """Generation failed; callers must report an error, not a completed answer."""


class TurnSupersededError(PipelineGenerationError):
    """The current turn was cancelled or replaced before publication."""


class ConversationConflictError(PipelineGenerationError):
    """The requested conversation relation or identity is obsolete or invalid."""


class ConversationStorageError(PipelineGenerationError):
    """Durable admission or processing failed; acceptance must not be inferred."""


@dataclass(frozen=True)
class GenerationOptions:
    model_fast: str
    model_thinking: str
    system_prompt: str = ""
    memory_context: str = ""


class ConversationRuntime(Protocol):
    def stream(
        self, prepared: PreparedTurn, options: GenerationOptions,
    ) -> AsyncGenerator[str, None]: ...

    def queue_status(self) -> dict[str, Any]: ...


_conversation_runtimes: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ConversationRuntime] = weakref.WeakKeyDictionary()


def get_conversation_coordinator() -> ConversationRuntime | None:
    return _conversation_runtimes.get(asyncio.get_running_loop())


def register_conversation_coordinator(runtime: ConversationRuntime) -> None:
    loop = asyncio.get_running_loop()
    existing = _conversation_runtimes.get(loop)
    if existing is not None and existing is not runtime:
        raise RuntimeError("An inference coordinator already owns this event loop")
    _conversation_runtimes[loop] = runtime


def unregister_conversation_coordinator(runtime: ConversationRuntime) -> None:
    loop = asyncio.get_running_loop()
    if _conversation_runtimes.get(loop) is runtime:
        del _conversation_runtimes[loop]


class RouteStatus(str, Enum):
    NOT_MATCHED = "not_matched"
    HANDLED = "handled"
    CLARIFICATION = "clarification"
    FAILED = "failed"


class ActionOutcome(str, Enum):
    NONE = "none"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TurnIdentity:
    turn_id: str
    conversation_id: str
    user_id: str
    source: str = "pipeline"
    device_id: str | None = None
    room: str | None = None
    revision: int = 1
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        for name in ("turn_id", "conversation_id", "user_id", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                logger.error("Invalid turn identity field: %s", name)
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("device_id", "room", "idempotency_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                logger.error("Invalid optional turn identity field: %s", name)
                raise ValueError(f"{name} must be a nonempty string when provided")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            logger.error("Invalid turn revision")
            raise ValueError("revision must be a positive integer")

    @classmethod
    def from_metadata(
        cls,
        user_id: str,
        metadata: dict[str, Any] | None = None,
        satellite_id: str | None = None,
    ) -> TurnIdentity:
        data = metadata or {}
        for name in ("turn_id", "conversation_id", "source", "room", "idempotency_key"):
            if name in data and (not isinstance(data[name], str) or not data[name].strip()):
                logger.error("Invalid turn metadata field: %s", name)
                raise ValueError(f"{name} must be a nonempty string")
        revision = data.get("revision", 1)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            logger.error("Invalid turn revision")
            raise ValueError("revision must be a positive integer")
        turn_id = data.get("turn_id") or uuid.uuid4().hex
        return cls(
            turn_id=turn_id,
            conversation_id=data.get("conversation_id") or turn_id,
            user_id=user_id,
            source=data.get("source", "satellite" if satellite_id else "pipeline"),
            device_id=satellite_id,
            room=data.get("room"),
            revision=revision,
            idempotency_key=data.get("idempotency_key"),
        )


@dataclass(frozen=True)
class DispatchResult:
    status: RouteStatus
    response: str | None = None
    confidence: float = 0.0
    layer: str = "tool"
    plugin_id: str | None = None
    intent: str = ""
    entities: list[str] = field(default_factory=list)
    action_outcome: ActionOutcome = ActionOutcome.NONE


@dataclass
class PreparedTurn:
    """Fast-path work has already run; consuming this object never repeats it."""

    identity: TurnIdentity
    message: str
    context: dict[str, Any]
    dispatch: DispatchResult
    start_ms: int
    layer_times: dict[str, float]
    consumed: bool = False

    def context_snapshot(self) -> dict[str, Any]:
        """Make the shared identity JSON-safe for operational context persistence."""
        return {**self.context, "turn_identity": asdict(self.identity)}
