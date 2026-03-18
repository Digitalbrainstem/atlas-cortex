"""Session persistence for Atlas CLI.

Saves and resumes agent/chat sessions as human-readable JSON files
stored in ``~/.atlas/sessions/``.
"""

# Module ownership: CLI session save / resume
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


@dataclass
class SessionMessage:
    """A single message within a session."""

    role: str  # user, assistant, system, tool
    content: str
    timestamp: float = field(default_factory=time.time)
    tool_call: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


class SessionManager:
    """Persist and resume CLI sessions.

    Session files live as JSON in *session_dir* (default
    ``~/.atlas/sessions/``).  Each file is named ``{session_id}.json``.
    """

    def __init__(self, session_dir: str | None = None) -> None:
        self.session_dir = Path(
            session_dir or os.path.expanduser("~/.atlas/sessions")
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current_id: str | None = None
        self._messages: list[SessionMessage] = []
        self._mode: str = "chat"

    # ── lifecycle ───────────────────────────────────────────────────

    def new_session(self, mode: str = "chat") -> str:
        """Create a new session and return its ID.

        ID format: ``YYYYMMDD-HHMMSS-NNN-mode`` where NNN is a
        monotonic counter to avoid collisions within the same second.
        """
        global _session_counter  # noqa: PLW0603
        _session_counter += 1
        now = datetime.now(timezone.utc)
        session_id = now.strftime("%Y%m%d-%H%M%S") + f"-{_session_counter:03d}-{mode}"
        self._current_id = session_id
        self._messages = []
        self._mode = mode
        log.info("new session %s", session_id)
        return session_id

    def resume_session(self, session_id: str) -> None:
        """Resume a previous session by loading its messages."""
        self._messages = self.load(session_id)
        self._current_id = session_id
        # Infer mode from the session id suffix
        parts = session_id.rsplit("-", 1)
        self._mode = parts[-1] if len(parts) > 1 else "chat"
        log.info("resumed session %s (%d msgs)", session_id, len(self._messages))

    # ── messages ────────────────────────────────────────────────────

    def add_message(
        self,
        role: str,
        content: str,
        tool_call: dict[str, Any] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> None:
        """Append a message to the current session."""
        self._messages.append(
            SessionMessage(
                role=role,
                content=content,
                tool_call=tool_call,
                tool_result=tool_result,
            )
        )

    def get_history(self) -> list[dict[str, Any]]:
        """Return the current session as a list of dicts suitable for the LLM."""
        return [
            {"role": m.role, "content": m.content}
            for m in self._messages
        ]

    # ── persistence ─────────────────────────────────────────────────

    def save(self) -> None:
        """Save the current session to disk as JSON."""
        if self._current_id is None:
            log.warning("save called with no active session")
            return
        path = self.session_dir / f"{self._current_id}.json"
        data = {
            "id": self._current_id,
            "mode": self._mode,
            "created_at": (
                self._messages[0].timestamp if self._messages else time.time()
            ),
            "messages": [asdict(m) for m in self._messages],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("saved session %s (%d msgs)", self._current_id, len(self._messages))

    def load(self, session_id: str) -> list[SessionMessage]:
        """Load a session by ID. Returns its messages.

        Returns an empty list if the file is missing or corrupt.
        """
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

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent sessions with metadata.

        Returns dicts with keys: ``id``, ``mode``, ``message_count``,
        ``created_at``.  Sorted newest-first.
        """
        sessions: list[dict[str, Any]] = []
        for path in sorted(self.session_dir.glob("*.json"), reverse=True):
            if len(sessions) >= limit:
                break
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "id": data.get("id", path.stem),
                        "mode": data.get("mode", "unknown"),
                        "message_count": len(data.get("messages", [])),
                        "created_at": data.get("created_at", 0),
                    }
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                log.debug("skipping corrupt session file %s", path)
        return sessions

    # ── properties ──────────────────────────────────────────────────

    @property
    def current_session_id(self) -> str | None:
        """The active session ID, or ``None``."""
        return self._current_id
