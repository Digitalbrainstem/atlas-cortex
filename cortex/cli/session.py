"""Persistent session management for Atlas CLI.

Sessions survive terminal disconnects, sleep, and reconnects.  Each
session is a JSON file in ``~/.atlas/sessions/{id}.json`` containing
the full conversation history and metadata.

Module ownership: CLI session save / resume
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_session_counter: int = 0

log = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class SessionMessage:
    """A single message within a session."""

    role: str  # user, assistant, system, tool
    content: str
    timestamp: float = field(default_factory=time.time)
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


@dataclass
class SessionInfo:
    """Lightweight summary of a session (for listing).

    Supports dict-style access (``info["id"]``, ``"id" in info``) for
    backward compatibility with code that expects ``list_sessions`` to
    return plain dicts.
    """

    id: str
    name: str
    mode: str
    message_count: int
    created_at: float
    updated_at: float

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and hasattr(self, key)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None


# ── Session ─────────────────────────────────────────────────────────

class Session:
    """A single persistent conversation session."""

    def __init__(
        self,
        session_id: str,
        *,
        name: str = "",
        mode: str = "chat",
        session_dir: Path | None = None,
    ) -> None:
        self.id = session_id
        self.name = name or session_id
        self.mode = mode
        self.created_at: float = time.time()
        self.updated_at: float = self.created_at
        self.messages: list[SessionMessage] = []
        self.metadata: dict[str, Any] = {}
        self._dir = session_dir or Path(
            os.environ.get("ATLAS_SESSIONS_DIR", Path.home() / ".atlas" / "sessions")
        )
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._dir / f"{self.id}.json"

    # ── Messages ────────────────────────────────────────────────────

    def add_message(
        self,
        role: str,
        content: str,
        tool_call: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> None:
        """Append a message and update the timestamp."""
        self.messages.append(SessionMessage(
            role=role,
            content=content,
            tool_call=tool_call,
            tool_result=tool_result,
        ))
        self.updated_at = time.time()

    def get_history(self) -> list[dict[str, Any]]:
        """Return messages as plain dicts for the LLM."""
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def truncate(self, keep_last: int = 50) -> None:
        """Drop old messages keeping only the most recent *keep_last*."""
        if len(self.messages) > keep_last:
            self.messages = self.messages[-keep_last:]

    # ── Persistence ─────────────────────────────────────────────────

    def save(self) -> None:
        """Persist the session to a JSON file."""
        data = {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "messages": [asdict(m) for m in self.messages],
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("saved session %s (%d msgs)", self.id, len(self.messages))

    def load(self) -> None:
        """Load session data from its JSON file."""
        if not self.path.exists():
            log.warning("session file not found: %s", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.name = data.get("name", self.id)
            self.mode = data.get("mode", "chat")
            self.created_at = data.get("created_at", self.created_at)
            self.updated_at = data.get("updated_at", self.updated_at)
            self.metadata = data.get("metadata", {})
            self.messages = [
                SessionMessage(
                    role=m["role"],
                    content=m["content"],
                    timestamp=m.get("timestamp", 0.0),
                    tool_call=m.get("tool_call"),
                    tool_result=m.get("tool_result"),
                )
                for m in data.get("messages", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("corrupt session %s: %s", self.id, exc)


# ── Session Manager ─────────────────────────────────────────────────

class SessionManager:
    """Manages persistent chat sessions stored on disk.

    Session files live as JSON in *session_dir* (default
    ``~/.atlas/sessions/``).  Each file is named ``{session_id}.json``.
    """

    SESSIONS_DIR = Path(os.environ.get(
        "ATLAS_SESSIONS_DIR", Path.home() / ".atlas" / "sessions",
    ))

    def __init__(self, session_dir: str | Path | None = None) -> None:
        self.session_dir = Path(session_dir) if session_dir else self.SESSIONS_DIR
        self.session_dir.mkdir(parents=True, exist_ok=True)
        # Legacy compat — kept for modules that access _current_id directly
        self._current_id: str | None = None
        self._messages: list[SessionMessage] = []
        self._mode: str = "chat"

    # ── Create / resume ─────────────────────────────────────────────

    def create_session(self, name: str | None = None, mode: str = "chat") -> Session:
        """Create a brand-new session and return it."""
        global _session_counter  # noqa: PLW0603
        _session_counter += 1
        now = datetime.now(timezone.utc)
        session_id = now.strftime("%Y%m%d-%H%M%S") + f"-{_session_counter:03d}-{mode}"
        session = Session(session_id, name=name or "", mode=mode, session_dir=self.session_dir)
        session.save()  # create the file immediately
        # Legacy compat
        self._current_id = session_id
        self._messages = session.messages
        self._mode = mode
        log.info("new session %s", session_id)
        return session

    def resume_session(self, session_id: str | None = None) -> Session:
        """Resume a session by ID, or the most recent one if *session_id* is None."""
        if session_id is None:
            sessions = self.list_sessions(limit=1)
            if not sessions:
                return self.create_session()
            session_id = sessions[0].id

        session = Session(session_id, session_dir=self.session_dir)
        session.load()
        # Legacy compat
        self._current_id = session_id
        self._messages = session.messages
        self._mode = session.mode
        log.info("resumed session %s (%d msgs)", session_id, len(session.messages))
        return session

    def get_session(self, session_id: str) -> Session:
        """Load and return a session by ID."""
        session = Session(session_id, session_dir=self.session_dir)
        session.load()
        return session

    def list_sessions(self, limit: int = 20) -> list[SessionInfo]:
        """List recent sessions, newest first."""
        infos: list[SessionInfo] = []
        for path in sorted(self.session_dir.glob("*.json"), reverse=True):
            if len(infos) >= limit:
                break
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                infos.append(SessionInfo(
                    id=data.get("id", path.stem),
                    name=data.get("name", path.stem),
                    mode=data.get("mode", "unknown"),
                    message_count=len(data.get("messages", [])),
                    created_at=data.get("created_at", 0),
                    updated_at=data.get("updated_at", 0),
                ))
            except (json.JSONDecodeError, KeyError, TypeError):
                log.debug("skipping corrupt session file %s", path)
        return infos

    # ── Legacy API (backward compat with existing callers) ──────────

    def new_session(self, mode: str = "chat") -> str:
        """Create a new session and return its ID (legacy API)."""
        session = self.create_session(mode=mode)
        return session.id

    def resume_session_legacy(self, session_id: str) -> None:
        """Resume a previous session by loading its messages (legacy API)."""
        session = self.resume_session(session_id)
        self._messages = session.messages
        self._current_id = session.id
        self._mode = session.mode

    def add_message(
        self,
        role: str,
        content: str,
        tool_call: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> None:
        """Append a message to the current session (legacy API)."""
        self._messages.append(SessionMessage(
            role=role, content=content,
            tool_call=tool_call, tool_result=tool_result,
        ))

    def get_history(self) -> list[dict[str, Any]]:
        """Return the current session as a list of dicts (legacy API)."""
        return [{"role": m.role, "content": m.content} for m in self._messages]

    def save(self) -> None:
        """Save the current session to disk (legacy API)."""
        if self._current_id is None:
            log.warning("save called with no active session")
            return
        path = self.session_dir / f"{self._current_id}.json"
        data = {
            "id": self._current_id,
            "name": self._current_id,
            "mode": self._mode,
            "created_at": (
                self._messages[0].timestamp if self._messages else time.time()
            ),
            "updated_at": time.time(),
            "metadata": {},
            "messages": [asdict(m) for m in self._messages],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("saved session %s (%d msgs)", self._current_id, len(self._messages))

    def load(self, session_id: str) -> list[SessionMessage]:
        """Load a session by ID. Returns its messages (legacy API)."""
        path = self.session_dir / f"{session_id}.json"
        if not path.exists():
            log.warning("session file not found: %s", path)
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [
                SessionMessage(
                    role=m["role"],
                    content=m["content"],
                    timestamp=m.get("timestamp", 0.0),
                    tool_call=m.get("tool_call"),
                    tool_result=m.get("tool_result"),
                )
                for m in data.get("messages", [])
            ]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("corrupt session %s: %s", session_id, exc)
            return []

    @property
    def current_session_id(self) -> str | None:
        """The active session ID, or ``None``."""
        return self._current_id
