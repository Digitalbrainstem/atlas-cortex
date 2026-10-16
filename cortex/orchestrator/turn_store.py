"""Durable finalized turns, independent of active inference and playback queues."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from cortex.pipeline.turns import ActionOutcome, DEFAULT_MAX_OUTPUT_BYTES, DispatchResult, RouteStatus, TurnIdentity
from cortex.plugins.base import ConfigField

logger = logging.getLogger(__name__)

_TERMINAL = ("completed", "failed", "cancelled", "superseded")
_ACTIVE = ("routing", "fastpath_running", "llm_running")


class TurnStoreError(RuntimeError):
    """A durable turn operation failed; no acceptance may be inferred."""


class TurnConflictError(TurnStoreError):
    """A claim, identity, revision or transition is no longer valid."""


class TurnCapacityError(TurnStoreError):
    """A measured storage or active-work boundary has been reached."""


def _fail(message: str, error_type: type[TurnStoreError] = TurnStoreError) -> None:
    logger.error("Turn store: %s", message)
    raise error_type(message)


@dataclass(frozen=True)
class TurnStorePolicy:
    max_active: int = 1
    prefetch: int = 8
    max_input_bytes: int = 65536
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_store_bytes: int = 64 * 1024 * 1024
    min_free_disk_bytes: int = 16 * 1024 * 1024
    retention_seconds: int = 7 * 86400
    lease_seconds: int = 120
    maintenance_seconds: int = 30

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                _fail(f"{name} must be a positive integer")

    @classmethod
    def from_env(cls) -> TurnStorePolicy:
        values: dict[str, int] = {}
        for name in cls.__dataclass_fields__:
            key = f"CORTEX_TURN_{name.upper()}"
            if key in os.environ:
                try:
                    values[name] = int(os.environ[key])
                except ValueError as exc:
                    logger.error("Invalid integer configuration: %s", key)
                    raise TurnStoreError(f"{key} must be an integer") from exc
        return cls(**values)

    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField(
                key=f"CORTEX_TURN_{name.upper()}",
                label=name.replace("_", " ").title(),
                field_type="number",
                default=value,
                help_text="Positive integer; bytes and seconds are named explicitly. Restart required.",
            )
            for name, value in asdict(self).items()
        ]


@dataclass(frozen=True)
class StoredTurn:
    identity: TurnIdentity
    sequence: int
    transcript: str
    context: dict[str, Any]
    state: str
    route_status: str | None
    relation: str
    related_turn_id: str | None
    action_outcome: str
    result: str | None
    output_validated: bool
    delivery_state: str
    playback_position: int
    lease_owner: str | None
    lease_until: float | None
    attempts: int
    error_code: str | None
    recovery_reason: str | None


class TurnStore:
    """Short atomic SQLite operations; no transactions span async work.

    Callers supply a connection using the repository's DB initialization.
    Identity and authorization are supplied by the ingress/worker, not inferred
    from arbitrary client metadata. Read and mutation methods require user scope.
    """

    def __init__(self, conn: sqlite3.Connection, policy: TurnStorePolicy | None = None):
        self.conn = conn
        self.policy = policy or TurnStorePolicy.from_env()
        self._lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("PRAGMA database_list").fetchone()
        self._db_file = Path(row["file"]) if row and row["file"] else None

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            if self.conn.in_transaction:
                _fail("Cannot nest a durable turn transaction", TurnConflictError)
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                yield
                self.conn.commit()
            except (sqlite3.Error, json.JSONDecodeError) as exc:
                self.conn.rollback()
                logger.exception("Durable turn transaction failed")
                raise TurnStoreError("Durable turn storage failed") from exc
            except BaseException:
                self.conn.rollback()
                raise

    def _row(self, turn_id: str, user_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM pending_turns WHERE turn_id = ? AND user_id = ?",
            (turn_id, user_id),
        ).fetchone()
        if row is None:
            _fail("Turn not found in user scope", TurnConflictError)
        return row

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredTurn:
        context = json.loads(row["context_json"])
        if not isinstance(context, dict):
            _fail("Persisted turn context is not a JSON object")
        return StoredTurn(
            identity=TurnIdentity(
                turn_id=row["turn_id"], conversation_id=row["conversation_id"],
                user_id=row["user_id"], source=row["source"], device_id=row["device_id"],
                room=row["room"], revision=row["revision"], idempotency_key=row["idempotency_key"],
            ),
            sequence=row["sequence"], transcript=row["transcript"],
            context=context, state=row["state"],
            route_status=row["route_status"], relation=row["relation"],
            related_turn_id=row["related_turn_id"], action_outcome=row["action_outcome"],
            result=row["result"], output_validated=bool(row["output_validated"]),
            delivery_state=row["delivery_state"], playback_position=row["playback_position"],
            lease_owner=row["lease_owner"], lease_until=row["lease_until"], attempts=row["attempts"],
            error_code=row["error_code"], recovery_reason=row["recovery_reason"],
        )

    def _usage(self) -> int:
        return self.conn.execute(
            "SELECT payload_bytes FROM pending_turn_usage WHERE id = 1",
        ).fetchone()[0]

    def _check_capacity(self, additional_bytes: int) -> None:
        if self._usage() + additional_bytes > self.policy.max_store_bytes:
            _fail("Durable turn payload budget reached; existing turns were preserved", TurnCapacityError)
        if self._db_file is not None:
            try:
                free = shutil.disk_usage(self._db_file.parent).free
            except OSError as exc:
                logger.exception("Could not check durable turn disk capacity")
                raise TurnStoreError("Could not check durable turn disk capacity") from exc
            if free < self.policy.min_free_disk_bytes + max(additional_bytes, 0):
                _fail("Insufficient free disk for durable turns", TurnCapacityError)

    def accept(
        self,
        identity: TurnIdentity,
        transcript: str,
        *,
        input_validated: bool,
        context: dict[str, Any] | None = None,
        replaces: tuple[str, int] | None = None,
        clarifies: tuple[str, int] | None = None,
        promote_accepted: bool = False,
    ) -> StoredTurn:
        """Commit a finalized, input-validated turn before acknowledging acceptance."""
        if input_validated is not True:
            _fail("Input must pass validation before durable acceptance")
        if not isinstance(transcript, str) or not transcript.strip():
            _fail("Transcript must be nonempty text")
        if not all(isinstance(value, str) and value.strip() for value in (
            identity.turn_id, identity.conversation_id, identity.user_id, identity.source,
        )):
            _fail("Turn identity and user scope must be nonempty")
        if identity.revision != 1:
            _fail("New turns must begin at revision 1")
        if context is not None and not isinstance(context, dict):
            _fail("Turn context must be a JSON object")
        try:
            context_json = json.dumps(context or {}, ensure_ascii=True, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            logger.error("Turn context is not JSON serializable")
            raise TurnStoreError("Turn context must be JSON serializable") from exc
        size = (len(transcript.encode("utf-8")) + len(context_json.encode("utf-8"))
                + len(json.dumps(asdict(identity)).encode("utf-8")))
        if size > self.policy.max_input_bytes:
            _fail("Finalized turn exceeds the configured input byte limit", TurnCapacityError)
        now = time.time()
        with self._transaction():
            existing = self.conn.execute(
                "SELECT * FROM pending_turns WHERE turn_id = ? OR "
                "(user_id = ? AND conversation_id = ? AND idempotency_key = ?)",
                (identity.turn_id, identity.user_id, identity.conversation_id, identity.idempotency_key),
            ).fetchall()
            if existing:
                row = existing[0]
                promoting = (
                    promote_accepted and len(existing) == 1 and row["state"] == "accepted"
                    and (self._stored(row).context.get("voice_ingress") is True
                         or self._stored(row).context.get("pipeline_ingress") is True)
                    and (row["turn_id"], row["user_id"], row["conversation_id"], row["transcript"],
                         row["source"], row["device_id"], row["room"], row["revision"]) ==
                        (identity.turn_id, identity.user_id, identity.conversation_id, transcript,
                         identity.source, identity.device_id, identity.room, identity.revision)
                )
                if promoting:
                    self._check_capacity(size - row["stored_bytes"])
                elif len(existing) != 1 or (
                    row["user_id"], row["conversation_id"], row["transcript"], row["context_json"],
                    row["source"], row["device_id"], row["room"], row["idempotency_key"],
                ) != (
                    identity.user_id, identity.conversation_id, transcript, context_json,
                    identity.source, identity.device_id, identity.room, identity.idempotency_key,
                ):
                    _fail("Idempotency key or turn ID was reused for different work", TurnConflictError)
                elif not promoting:
                    return self._stored(row)
            else:
                promoting = False
                self._check_capacity(size)
            related = replaces or clarifies
            if replaces is not None and clarifies is not None:
                _fail("A turn cannot both replace and clarify another turn", TurnConflictError)
            if related is not None:
                old = self._at_revision(related[0], identity.user_id, related[1])
                if (old["conversation_id"] != identity.conversation_id
                        or old["source"] != identity.source or old["device_id"] != identity.device_id
                        or old["room"] != identity.room
                        or self._stored(old).context.get("speaker_id") != (context or {}).get("speaker_id")
                        or old["action_outcome"] != "none"
                        or replaces is not None and old["state"] not in ("llm_pending", "llm_running")):
                    _fail("Replacement target is not a pending question in this scope", TurnConflictError)
            if replaces is not None:
                self.conn.execute(
                    "UPDATE pending_turns SET state='superseded', revision=revision+1, "
                    "lease_owner=NULL, lease_until=NULL, delivery_state='interrupted', updated_at=? "
                    "WHERE turn_id=?", (now, replaces[0]),
                )
            if promoting:
                self.conn.execute(
                    "UPDATE pending_turns SET context_json=?,stored_bytes=?,updated_at=? WHERE turn_id=?",
                    (context_json, size, now, identity.turn_id),
                )
            else:
                self._insert_accepted(identity, transcript, context_json, size, now)
            result = self._stored(self._row(identity.turn_id, identity.user_id))
            if related is not None:
                self.conn.execute(
                    "UPDATE pending_turns SET relation=?, related_turn_id=? WHERE turn_id=?",
                    ("replacement" if replaces else "clarification", related[0], identity.turn_id),
                )
                result = self._stored(self._row(identity.turn_id, identity.user_id))
        return result

    def _insert_accepted(
        self, identity: TurnIdentity, transcript: str, context_json: str, size: int, now: float,
    ) -> None:
        sequence = self.conn.execute(
                """INSERT INTO pending_turn_sequences (user_id, conversation_id, sequence)
                   VALUES (?, ?, 1) ON CONFLICT(user_id, conversation_id)
                   DO UPDATE SET sequence = sequence + 1 RETURNING sequence""",
                (identity.user_id, identity.conversation_id),
        ).fetchone()[0]
        self.conn.execute(
                """INSERT INTO pending_turns
                   (turn_id, conversation_id, user_id, source, device_id, room, sequence,
                    idempotency_key, transcript, context_json, input_validated,
                    stored_bytes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (identity.turn_id, identity.conversation_id, identity.user_id, identity.source,
                 identity.device_id, identity.room, sequence, identity.idempotency_key,
                 transcript, context_json, size, now, now),
        )

    def finish_voice_preparation(self, identity: TurnIdentity, owner: str) -> None:
        """Only a returned typed dispatch resolves the pessimistic preparation claim."""
        with self._transaction():
            row = self._owned(identity.turn_id, identity.user_id, owner, identity.revision)
            context = self._stored(row).context
            if not (context.get("voice_ingress") or context.get("pipeline_ingress")):
                _fail("Not an ingress preparation claim", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET state='accepted',action_outcome='none',lease_owner=NULL,"
                "lease_until=NULL,updated_at=? WHERE turn_id=?", (time.time(), identity.turn_id),
            )

    def voice_delivery_head(self, device_id: str, exclude: str = "", *, room: str | None = None) -> StoredTurn | None:
        with self._lock:
            try:
                row = self.conn.execute(
                    """SELECT * FROM pending_turns WHERE source='satellite' AND device_id=? AND turn_id!=? AND room IS ?
                       AND state NOT IN ('superseded','cancelled')
                       AND (state IN ('accepted','routing','fastpath_running','llm_pending','llm_running')
                         OR delivery_state IN ('ready','delivering','paused'))
                       ORDER BY CASE WHEN json_extract(context_json,'$.voice_control')=1
                         AND delivery_state='ready' THEN 0 ELSE 1 END,created_at,sequence LIMIT 1""",
                    (device_id, exclude, room),
                ).fetchone()
                return self._stored(row) if row else None
            except sqlite3.Error as exc:
                logger.exception("Voice delivery listing failed")
                raise TurnStoreError("Voice delivery listing failed") from exc

    def checkpoint_playback(self, turn_id: str, user_id: str, revision: int, position: int) -> None:
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if (row["delivery_state"] != "delivering" or isinstance(position, bool)
                    or not isinstance(position, int) or not row["playback_position"] <= position <= len(row["result"] or "")):
                _fail("Invalid playback receipt position", TurnConflictError)
            self.conn.execute("UPDATE pending_turns SET playback_position=?,updated_at=? WHERE turn_id=?",
                              (position, time.time(), turn_id))

    def interrupt_delivery(self, turn_id: str, user_id: str, revision: int) -> None:
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if row["delivery_state"] not in ("ready", "delivering", "paused", "awaiting_confirmation"):
                _fail("No current output to interrupt", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET delivery_state='interrupted',revision=revision+1,updated_at=? WHERE turn_id=?",
                (time.time(), turn_id),
            )

    def resume_delivery(self, turn_id: str, user_id: str, revision: int) -> None:
        """Explicit consent queues only the unacknowledged sentence suffix."""
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if not row["output_validated"] or row["delivery_state"] not in (
                "paused", "interrupted", "awaiting_confirmation",
            ):
                _fail("No validated interrupted output to resume", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET delivery_state='ready',revision=revision+1,updated_at=? WHERE turn_id=?",
                (time.time(), turn_id),
            )

    def voice_pending_targets(
        self, device_id: str, user_id: str, exclude: str, *, room: str | None = None,
    ) -> list[StoredTurn]:
        with self._lock:
            try:
                rows = self.conn.execute(
                    """SELECT * FROM pending_turns WHERE device_id=? AND user_id=? AND turn_id!=? AND room IS ?
                       AND source='satellite' AND state NOT IN ('cancelled','superseded','uncertain','failed')
                       AND COALESCE(json_extract(context_json,'$.voice_control'),0)!=1
                       AND (state!='completed' OR delivery_state IN ('ready','delivering','paused','awaiting_confirmation','interrupted'))
                       ORDER BY created_at,sequence LIMIT 2""", (device_id, user_id, exclude, room),
                ).fetchall()
                return [self._stored(row) for row in rows]
            except sqlite3.Error as exc:
                logger.exception("Voice target lookup failed")
                raise TurnStoreError("Voice target lookup failed") from exc

    def voice_processing(self, device_id: str, room: str | None) -> bool:
        with self._lock:
            try:
                return self.conn.execute(
                    """SELECT 1 FROM pending_turns WHERE source='satellite' AND device_id=? AND room IS ?
                       AND state IN ('accepted','routing','fastpath_running','llm_pending','llm_running') LIMIT 1""",
                    (device_id, room),
                ).fetchone() is not None
            except sqlite3.Error as exc:
                logger.exception("Voice activity lookup failed")
                raise TurnStoreError("Voice activity lookup failed") from exc

    def find_existing(self, identity: TurnIdentity) -> StoredTurn | None:
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT turn_id, user_id FROM pending_turns WHERE turn_id=? OR "
                    "(user_id=? AND conversation_id=? AND idempotency_key=?)",
                    (identity.turn_id, identity.user_id, identity.conversation_id, identity.idempotency_key),
                ).fetchall()
                if not rows:
                    return None
                if len(rows) != 1 or rows[0]["user_id"] != identity.user_id:
                    _fail("Turn identity conflicts with existing work", TurnConflictError)
                return self.get(rows[0]["turn_id"], identity.user_id)
            except sqlite3.Error as exc:
                logger.exception("Durable turn lookup failed")
                raise TurnStoreError("Durable turn lookup failed") from exc

    def llm_dispatch_ids(self) -> list[tuple[str, str, int]]:
        """One conversation head per page, least-recently dispatched first."""
        with self._lock:
            try:
                return [tuple(row) for row in self.conn.execute(
                    """SELECT p.turn_id, p.user_id, p.revision FROM pending_turns p
                       JOIN pending_turn_sequences s USING(user_id, conversation_id)
                       WHERE p.state IN ('accepted','llm_pending')
                       AND json_type(p.context_json, '$.generation_options')='object'
                       AND NOT EXISTS (
                         SELECT 1 FROM pending_turns earlier
                         WHERE earlier.user_id=p.user_id AND earlier.conversation_id=p.conversation_id
                         AND json_type(earlier.context_json, '$.generation_options')='object'
                         AND (earlier.state='llm_running' OR
                           (earlier.state IN ('accepted','llm_pending') AND earlier.sequence < p.sequence)))
                       ORDER BY s.last_llm_dispatch, p.created_at, p.sequence LIMIT ?""",
                    (self.policy.prefetch,),
                ).fetchall()]
            except sqlite3.Error as exc:
                logger.exception("Durable LLM dispatch listing failed")
                raise TurnStoreError("Durable LLM dispatch listing failed") from exc

    def scoped_questions(self, identity: TurnIdentity, speaker_id: str | None) -> list[StoredTurn]:
        with self._lock:
            try:
                rows = self.conn.execute(
                    """SELECT turn_id FROM pending_turns WHERE user_id=? AND conversation_id=?
                       AND source=? AND device_id IS ? AND room IS ?
                       AND COALESCE(json_extract(context_json, '$.speaker_id'), '')=?
                       AND state IN ('llm_pending','llm_running') ORDER BY sequence DESC LIMIT 2""",
                    (identity.user_id, identity.conversation_id, identity.source, identity.device_id,
                     identity.room, speaker_id or ""),
                ).fetchall()
                return [self.get(row[0], identity.user_id) for row in rows]
            except sqlite3.Error as exc:
                logger.exception("Durable question relation lookup failed")
                raise TurnStoreError("Durable question relation lookup failed") from exc

    def completed_history(self, turn: StoredTurn) -> list[dict[str, str]]:
        with self._lock:
            try:
                rows = self.conn.execute(
                    """SELECT transcript, result FROM pending_turns
                       WHERE user_id=? AND conversation_id=? AND sequence < ?
                       AND source=? AND device_id IS ? AND room IS ?
                       AND COALESCE(json_extract(context_json, '$.speaker_id'), '')=?
                       AND state='completed' AND output_validated=1
                       ORDER BY sequence DESC LIMIT ?""",
                    (turn.identity.user_id, turn.identity.conversation_id, turn.sequence,
                     turn.identity.source, turn.identity.device_id, turn.identity.room,
                     turn.context.get("speaker_id") or "", self.policy.prefetch),
                ).fetchall()
                return [
                    message for row in reversed(rows) for message in (
                        {"role": "user", "content": row["transcript"]},
                        {"role": "assistant", "content": row["result"]},
                    )
                ]
            except sqlite3.Error as exc:
                logger.exception("Durable conversation history lookup failed")
                raise TurnStoreError("Durable conversation history lookup failed") from exc

    def get(self, turn_id: str, user_id: str) -> StoredTurn:
        with self._lock:
            try:
                return self._stored(self._row(turn_id, user_id))
            except (sqlite3.Error, json.JSONDecodeError) as exc:
                logger.exception("Durable turn read failed")
                raise TurnStoreError("Durable turn read failed") from exc

    def pending_ids(self, *, user_id: str | None = None, limit: int | None = None) -> list[str]:
        """Read only a bounded page of IDs, never the full backlog of bodies."""
        return self._ids(("accepted", "llm_pending"), user_id, limit)

    def recovery_ids(self, user_id: str, *, limit: int | None = None) -> list[str]:
        if not isinstance(user_id, str) or not user_id.strip():
            _fail("Recovery listing requires user scope")
        return self._ids(("recovery_pending", "uncertain"), user_id, limit, include_stale_delivery=True)

    def user_turns(self, user_id: str, *, limit: int = 1) -> list[StoredTurn]:
        if not isinstance(user_id, str) or not user_id.strip():
            _fail("Turn listing requires user scope", TurnConflictError)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.policy.prefetch:
            _fail("Turn page limit must be within the configured prefetch", TurnConflictError)
        with self._lock:
            try:
                rows = self.conn.execute(
                    "SELECT turn_id FROM pending_turns WHERE user_id=? ORDER BY created_at DESC,turn_id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            except sqlite3.Error as exc:
                logger.exception("User turn listing failed")
                raise TurnStoreError("User turn listing failed") from exc
            return [self.get(row[0], user_id) for row in rows]

    def _ids(
        self, states: tuple[str, str], user_id: str | None, limit: int | None, *,
        include_stale_delivery: bool = False,
    ) -> list[str]:
        size = self.policy.prefetch if limit is None else limit
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= self.policy.prefetch:
            _fail("Pending page limit must be within the configured prefetch", TurnConflictError)
        with self._lock:
            try:
                rows = self.conn.execute(
                    """SELECT turn_id FROM pending_turns
                       WHERE (state IN (?, ?) OR (? AND delivery_state = 'awaiting_confirmation'))
                       AND (? IS NULL OR user_id = ?) ORDER BY created_at, turn_id LIMIT ?""",
                    (*states, include_stale_delivery, user_id, user_id, size),
                ).fetchall()
            except sqlite3.Error as exc:
                logger.exception("Durable turn listing failed")
                raise TurnStoreError("Durable turn listing failed") from exc
            return [row[0] for row in rows]

    def claim(
        self, turn_id: str, user_id: str, owner: str, *,
        revision: int = 1, stage: str = "routing",
    ) -> StoredTurn:
        target = {"routing": "routing", "fastpath": "fastpath_running", "llm": "llm_running"}.get(stage)
        if target is None or not isinstance(owner, str) or not owner.strip():
            _fail("Claim needs a valid stage and owner")
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            allowed = ("llm_pending",) if stage == "llm" else ("accepted",)
            if row["state"] not in allowed:
                _fail("Turn is not claimable at this revision", TurnConflictError)
            if stage == "llm":
                active = self.conn.execute(
                    "SELECT COUNT(*) FROM pending_turns WHERE state = 'llm_running'",
                ).fetchone()[0]
                if active >= self.policy.max_active:
                    _fail("Active inference limit reached", TurnCapacityError)
            now = time.time()
            self.conn.execute(
                "UPDATE pending_turns SET state = ?, lease_owner = ?, lease_until = ?, "
                "attempts = attempts + 1, updated_at = ? WHERE turn_id = ?",
                (target, owner, now + self.policy.lease_seconds, now, turn_id),
            )
            if stage == "llm":
                self.conn.execute(
                    "UPDATE pending_turn_sequences SET last_llm_dispatch=? "
                    "WHERE user_id=? AND conversation_id=?",
                    (now, user_id, row["conversation_id"]),
                )
            return self._stored(self._row(turn_id, user_id))

    def _at_revision(self, turn_id: str, user_id: str, revision: int) -> sqlite3.Row:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            _fail("Revision must be a positive integer", TurnConflictError)
        row = self._row(turn_id, user_id)
        if row["revision"] != revision:
            _fail("Turn revision has changed", TurnConflictError)
        return row

    def _owned(self, turn_id: str, user_id: str, owner: str, revision: int) -> sqlite3.Row:
        row = self._at_revision(turn_id, user_id, revision)
        if (row["lease_owner"] != owner
                or row["state"] not in _ACTIVE or row["lease_until"] is None
                or row["lease_until"] <= time.time()):
            _fail("Turn claim expired, changed or belongs to another worker", TurnConflictError)
        return row

    def heartbeat(self, turn_id: str, user_id: str, owner: str, revision: int) -> None:
        with self._transaction():
            self._owned(turn_id, user_id, owner, revision)
            now = time.time()
            self.conn.execute(
                "UPDATE pending_turns SET lease_until = ?, updated_at = ? WHERE turn_id = ?",
                (now + self.policy.lease_seconds, now, turn_id),
            )

    def mark_llm_pending(self, turn_id: str, user_id: str, owner: str, revision: int) -> None:
        with self._transaction():
            row = self._owned(turn_id, user_id, owner, revision)
            if row["state"] != "routing":
                _fail("Only routed fallthrough can become LLM pending", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET state = 'llm_pending', route_status = 'not_matched', "
                "lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE turn_id = ?",
                (time.time(), turn_id),
            )

    def begin_action(self, turn_id: str, user_id: str, owner: str, revision: int) -> None:
        with self._transaction():
            row = self._owned(turn_id, user_id, owner, revision)
            if row["state"] not in ("routing", "fastpath_running") or row["action_outcome"] != "none":
                _fail("Action cannot start in this state", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET state = 'fastpath_running', action_outcome = 'pending', "
                "updated_at = ? WHERE turn_id = ?", (time.time(), turn_id),
            )

    def complete(
        self, turn_id: str, user_id: str, owner: str, revision: int,
        result: DispatchResult, *, output_validated: bool,
    ) -> StoredTurn:
        if output_validated is not True or not isinstance(result.response, str) or not result.response.strip():
            _fail("A result must be output-validated before persistence and delivery")
        if result.status == RouteStatus.NOT_MATCHED:
            _fail("Fallthrough is not a completed response")
        with self._transaction():
            row = self._owned(turn_id, user_id, owner, revision)
            if row["action_outcome"] == "pending" and result.action_outcome == ActionOutcome.NONE:
                _fail("External action outcome must be resolved explicitly", TurnConflictError)
            additional = len(result.response.encode("utf-8"))
            if additional > self.policy.max_output_bytes:
                _fail("Turn result exceeds the configured output byte limit", TurnCapacityError)
            self._check_capacity(additional)
            state = "failed" if result.status == RouteStatus.FAILED else "completed"
            if result.action_outcome == ActionOutcome.UNCERTAIN:
                state = "uncertain"
            self.conn.execute(
                """UPDATE pending_turns SET state = ?, route_status = ?, action_outcome = ?,
                   result = ?, output_validated = 1, delivery_state = 'ready',
                   lease_owner = NULL, lease_until = NULL, stored_bytes = stored_bytes + ?,
                   updated_at = ? WHERE turn_id = ?""",
                (state, result.status.value, result.action_outcome.value, result.response,
                 additional, time.time(), turn_id),
            )
            return self._stored(self._row(turn_id, user_id))

    def fail(self, turn_id: str, user_id: str, owner: str, revision: int, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            _fail("Failure needs a nonempty error code")
        with self._transaction():
            row = self._owned(turn_id, user_id, owner, revision)
            uncertain = row["action_outcome"] == "pending"
            self.conn.execute(
                """UPDATE pending_turns SET state = ?, action_outcome = ?, error_code = ?,
                   lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE turn_id = ?""",
                ("uncertain" if uncertain else "failed",
                 "uncertain" if uncertain else row["action_outcome"], code, time.time(), turn_id),
            )
            logger.warning("Turn failed with code %s", code)

    def cancel(self, turn_id: str, user_id: str, revision: int) -> None:
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if row["state"] in _TERMINAL:
                _fail("Turn is no longer cancellable at this revision", TurnConflictError)
            uncertain = row["action_outcome"] in ("pending", "uncertain")
            self.conn.execute(
                """UPDATE pending_turns SET state = ?, action_outcome = ?, revision = revision + 1,
                   delivery_state = 'interrupted', lease_owner = NULL, lease_until = NULL,
                   updated_at = ? WHERE turn_id = ?""",
                ("uncertain" if uncertain else "cancelled",
                 "uncertain" if uncertain else row["action_outcome"], time.time(), turn_id),
            )

    def supersede(self, turn_id: str, user_id: str, revision: int, replacement_id: str) -> None:
        with self._transaction():
            old = self._at_revision(turn_id, user_id, revision)
            new = self._row(replacement_id, user_id)
            if (old["state"] not in ("accepted", "llm_pending", "llm_running")
                    or old["action_outcome"] != "none" or new["state"] != "accepted"
                    or new["conversation_id"] != old["conversation_id"] or turn_id == replacement_id):
                _fail("Only pending/generating questions can be superseded", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET state = 'superseded', revision = revision + 1, "
                "lease_owner = NULL, lease_until = NULL, delivery_state = 'interrupted', "
                "updated_at = ? WHERE turn_id = ?", (time.time(), turn_id),
            )
            self.conn.execute(
                "UPDATE pending_turns SET relation = 'replacement', related_turn_id = ?, "
                "updated_at = ? WHERE turn_id = ?", (turn_id, time.time(), replacement_id),
            )

    def recover(self, *, startup: bool = False) -> int:
        """Invalidate interrupted claims; all restart work requires confirmation."""
        with self._transaction():
            now = time.time()
            if startup:
                predicate = "state IN ('accepted','routing','fastpath_running','llm_pending','llm_running')"
                params: tuple[Any, ...] = ()
            else:
                predicate = "lease_owner IS NOT NULL AND lease_until <= ?"
                params = (now,)
            cursor = self.conn.execute(
                f"""UPDATE pending_turns SET
                    state = CASE WHEN action_outcome = 'pending' THEN 'uncertain' ELSE 'recovery_pending' END,
                    action_outcome = CASE WHEN action_outcome = 'pending' THEN 'uncertain' ELSE action_outcome END,
                    revision = revision + 1, lease_owner = NULL, lease_until = NULL,
                    recovery_reason = ?, delivery_state = 'awaiting_confirmation', updated_at = ?
                    WHERE {predicate}""",
                ("server_restart" if startup else "lease_expired", now, *params),
            )
            count = cursor.rowcount
            if startup:
                self.conn.execute(
                    """UPDATE pending_turns SET delivery_state = 'awaiting_confirmation', updated_at = ?
                       WHERE delivery_state IN ('ready', 'delivering', 'paused')""", (now,),
                )
            if count:
                logger.warning("Recovered %d turns; confirmation required", count)
            return count

    def confirm_recovery(
        self, turn_id: str, user_id: str, revision: int, *, retry_uncertain: bool = False,
    ) -> None:
        """Explicit user/operator consent; workers must still recheck permissions."""
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if row["state"] not in ("recovery_pending", "uncertain"):
                _fail("Turn does not await recovery at this revision", TurnConflictError)
            if row["state"] == "uncertain" and retry_uncertain is not True:
                _fail("Uncertain external action requires explicit retry confirmation", TurnConflictError)
            self.conn.execute(
                """UPDATE pending_turns SET state = 'accepted', action_outcome = 'none',
                   result = NULL, output_validated = 0, delivery_state = 'not_ready',
                   playback_position = 0, stored_bytes = stored_bytes - ?, recovery_reason = NULL,
                   error_code = NULL, error_message = NULL,
                   revision = revision + 1, updated_at = ? WHERE turn_id = ?""",
                (len((row["result"] or "").encode("utf-8")), time.time(), turn_id),
            )

    def set_delivery(
        self, turn_id: str, user_id: str, revision: int, state: str, *, position: int = 0,
        confirmed: bool = False,
    ) -> None:
        transitions = {
            "ready": ("delivering",),
            "delivering": ("delivered", "paused", "interrupted"),
            "paused": ("delivering", "interrupted"),
            "interrupted": ("delivering",),
            "awaiting_confirmation": ("delivering",),
        }
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            _fail("Playback position must be a nonnegative integer")
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if (not row["output_validated"]
                    or row["state"] not in ("completed", "failed", "uncertain")
                    or state not in transitions.get(row["delivery_state"], ())):
                _fail("Invalid or obsolete delivery transition", TurnConflictError)
            if row["delivery_state"] in ("awaiting_confirmation", "interrupted") and confirmed is not True:
                _fail("Stale playback requires explicit confirmation", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET delivery_state = ?, playback_position = ?, updated_at = ? "
                "WHERE turn_id = ?", (state, position, time.time(), turn_id),
            )

    def cleanup(self) -> int:
        """Expire only terminal, non-pending delivery; never evict accepted work."""
        with self._transaction():
            cursor = self.conn.execute(
                """DELETE FROM pending_turns WHERE turn_id IN (
                   SELECT turn_id FROM pending_turns
                   WHERE state IN ('completed','failed','cancelled','superseded')
                   AND delivery_state IN ('not_ready','delivered','interrupted')
                   AND updated_at < ? ORDER BY updated_at LIMIT ?)""",
                (time.time() - self.policy.retention_seconds, self.policy.prefetch),
            )
            return cursor.rowcount

    def acknowledge_text(self, turn_id: str, user_id: str, revision: int) -> None:
        """Record transport/client consumption, never infer it from generation."""
        with self._transaction():
            row = self._at_revision(turn_id, user_id, revision)
            if row["source"] == "satellite" or not row["output_validated"]:
                _fail("Only validated text can receive a text receipt", TurnConflictError)
            if row["state"] not in ("completed", "failed", "uncertain"):
                _fail("Obsolete text receipt", TurnConflictError)
            self.conn.execute(
                "UPDATE pending_turns SET delivery_state='delivered',updated_at=? WHERE turn_id=?",
                (time.time(), turn_id),
            )

    def delete_user(self, user_id: str) -> int:
        if not isinstance(user_id, str) or not user_id.strip():
            _fail("User deletion requires nonempty scope")
        with self._transaction():
            count = self.conn.execute("DELETE FROM pending_turns WHERE user_id = ?", (user_id,)).rowcount
            self.conn.execute("DELETE FROM pending_turn_sequences WHERE user_id = ?", (user_id,))
            return count

    def status(self) -> dict[str, Any]:
        with self._lock:
            try:
                counts = dict(self.conn.execute("SELECT state, COUNT(*) FROM pending_turns GROUP BY state"))
                used = self._usage()
                free = shutil.disk_usage(self._db_file.parent).free if self._db_file else None
            except (sqlite3.Error, OSError) as exc:
                logger.exception("Durable turn status read failed")
                raise TurnStoreError("Durable turn status read failed") from exc
            return {
                "counts": counts, "payload_bytes": used, "free_disk_bytes": free,
                "admission_available": used < self.policy.max_store_bytes and (
                    free is None or free > self.policy.min_free_disk_bytes
                ),
                "policy": asdict(self.policy),
                "config_fields": [field.to_dict() for field in self.policy.config_fields()],
            }


class TurnStoreService:
    """Maintenance only; LLM/voice dispatch is owned by later orchestration phases."""

    def __init__(self, store: TurnStore):
        self.store = store
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._maintain(), name="durable-turn-maintenance")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _maintain(self) -> None:
        while True:
            try:
                self.store.recover()
                self.store.cleanup()
            except (TurnStoreError, sqlite3.Error, OSError):
                logger.exception("Durable turn maintenance failed")
            await asyncio.sleep(self.store.policy.maintenance_seconds)
