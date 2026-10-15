"""Shared identity and dispatch contracts for finalized conversation turns."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PipelineGenerationError(RuntimeError):
    """Generation failed; callers must report an error, not a completed answer."""


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
