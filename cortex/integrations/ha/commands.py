"""Scoped command context and execution ordering, independent of LLM admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from cortex.plugins.base import CommandResult
from cortex.plugins.intent import command_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HACommand:
    domain: str
    service: str
    target: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not isinstance(self.domain, str) or not isinstance(self.service, str)
                or not self.service or not isinstance(self.target, str)
                or not self.target.strip() or not isinstance(self.data, dict)):
            raise ValueError("Invalid structured device command")


@dataclass
class ActionRequest:
    command: HACommand
    execution_id: str
    scope: tuple[str, ...] | None
    turn_id: str | None = None
    previous: ActionRequest | None = None
    replaced: bool = False
    started: bool = False
    outcome: str = "queued"
    done: threading.Event = field(default_factory=threading.Event)


class CommandContext:
    """Recent intent is not a replay queue; interrupted effects are uncertain."""

    def __init__(self, conn: sqlite3.Connection | None) -> None:
        self.conn = conn
        self._guard = threading.RLock()
        self._pending: dict[tuple[str, ...], ActionRequest] = {}
        self._resources = [threading.Lock() for _ in range(32)]

    @staticmethod
    def scope(context: dict[str, Any]) -> tuple[str, ...] | None:
        return command_scope(context)

    def recent(self, context: dict[str, Any]) -> tuple[HACommand, str] | None:
        scope = self.scope(context)
        if scope is None or self.conn is None:
            return None
        with self._guard:
            pending = self._pending.get(scope)
            if pending is not None:
                if (pending.previous is not None and not pending.previous.replaced
                        and not pending.previous.done.is_set()
                        and pending.previous.command.target != pending.command.target):
                    return pending.command, "ambiguous"
                return pending.command, pending.outcome
            row = self.conn.execute(
                "SELECT command_json, outcome, updated_at FROM ha_command_context "
                "WHERE user_id=? AND conversation_id=? AND source=? AND device_id=? AND room=? AND speaker_id=? "
                "ORDER BY id DESC LIMIT 1",
                scope,
            ).fetchone()
            if row is None or row["updated_at"] < time.time() - 120:
                return None
            data = json.loads(row["command_json"])
            command = HACommand(**data)
            # No live request owns a persisted in-progress record after restart.
            outcome = row["outcome"]
            if outcome not in ("queued", "running", "succeeded", "failed", "uncertain",
                               "superseded", "clarification", "none"):
                raise ValueError("Invalid persisted command outcome")
            return command, "uncertain" if outcome in ("queued", "running") else outcome

    def remember_clarification(self, command: HACommand, context: dict[str, Any]) -> None:
        with self._guard:
            scope = self.scope(context)
            if scope is not None and scope not in self._pending:
                self._persist(ActionRequest(
                    command, uuid.uuid4().hex, scope, context["turn_identity"].turn_id,
                ), "clarification")

    def enqueue(
        self, command: HACommand, context: dict[str, Any], *, correction: bool,
    ) -> ActionRequest:
        scope = self.scope(context)
        identity = context.get("turn_identity")
        request = ActionRequest(
            command, uuid.uuid4().hex, scope, identity.turn_id if identity is not None else None,
        )
        with self._guard:
            previous = self._pending.get(scope) if scope is not None else None
            if previous is not None:
                request.previous = previous
            # Commit the new target before invalidating its queued predecessor.
            replace = previous if correction and previous is not None and not previous.started else None
            self._persist(request, "queued", replace=replace)
            if correction and previous is not None and not previous.started:
                previous.replaced = True
            if scope is not None:
                self._pending[scope] = request
        return request

    def _persist(
        self, request: ActionRequest, outcome: str, *, replace: ActionRequest | None = None,
    ) -> None:
        if request.scope is None or self.conn is None:
            return
        if self.conn.in_transaction:
            raise RuntimeError("Cannot nest a command-context transaction")
        with self.conn:
            self.conn.execute(
                """INSERT INTO ha_command_context
                   (user_id, conversation_id, source, device_id, room, speaker_id,
                    execution_id, turn_id, command_json, outcome, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*request.scope, request.execution_id, request.turn_id,
                 json.dumps(asdict(request.command), allow_nan=False), outcome, time.time()),
            )
            if replace is not None:
                self.conn.execute(
                    "UPDATE ha_command_context SET outcome='superseded', updated_at=? "
                    "WHERE execution_id=? AND outcome='queued'",
                    (time.time(), replace.execution_id),
                )
            self.conn.execute(
                "DELETE FROM ha_command_context WHERE updated_at < ? "
                "AND outcome NOT IN ('queued','running','uncertain')",
                (time.time() - 7 * 86400,),
            )

    def finish(self, request: ActionRequest, outcome: str) -> None:
        with self._guard:
            request.outcome = outcome
            try:
                if request.scope is not None and self.conn is not None:
                    with self.conn:
                        self.conn.execute(
                            "UPDATE ha_command_context SET outcome=?, updated_at=? "
                            "WHERE execution_id=?",
                            (outcome, time.time(), request.execution_id),
                        )
            finally:
                if request.scope is not None and self._pending.get(request.scope) is request:
                    del self._pending[request.scope]
                request.done.set()

    async def execute(
        self, request: ActionRequest, operation: Callable[[], Awaitable[CommandResult]],
    ) -> CommandResult:
        key = f"{request.command.domain}:{request.command.target.casefold()}"
        stripe = int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "big") % 32
        lock = self._resources[stripe]
        acquired = False
        try:
            if request.previous is not None:
                while not request.previous.done.is_set():
                    await asyncio.sleep(0.01)
            while not acquired:
                with self._guard:
                    if request.replaced:
                        self.finish(request, "superseded")
                        return CommandResult(
                            True, "That queued command was replaced before it ran.",
                            metadata={"action_outcome": "none"},
                        )
                    acquired = lock.acquire(blocking=False)
                    if acquired:
                        # This commit is the boundary after which replacement is unsafe.
                        self._persist_running(request)
                        request.started = True
                if not acquired:
                    await asyncio.sleep(0.01)
            result = await operation()
            outcome = result.metadata.get(
                "action_outcome", "succeeded" if result.success else "failed",
            )
            self.finish(request, outcome)
            return result
        except BaseException:
            logger.exception("HA command execution interrupted")
            self.finish(request, "uncertain" if request.started else "failed")
            raise
        finally:
            if acquired:
                lock.release()

    def _persist_running(self, request: ActionRequest) -> None:
        if request.scope is not None and self.conn is not None:
            if self.conn.in_transaction:
                raise RuntimeError("Cannot nest a command-context transaction")
            with self.conn:
                self.conn.execute(
                    "UPDATE ha_command_context SET outcome='running', updated_at=? "
                    "WHERE execution_id=?", (time.time(), request.execution_id),
                )
        request.outcome = "running"
