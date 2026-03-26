"""Background task runner for the Atlas CLI.

Allows long-running async coroutines to execute in the background while
the REPL stays interactive.  Task output is collected and can be
retrieved later.

Module ownership: CLI background task execution
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Coroutine
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    """Metadata for a background task."""

    id: str
    name: str
    status: str = "running"  # running | done | failed
    result: Any = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class BackgroundRunner:
    """Run async tasks in the background and track them."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._asyncio_tasks: dict[str, asyncio.Task[Any]] = {}

    async def run_bg(self, name: str, coro: Coroutine[Any, Any, Any]) -> str:
        """Start *coro* in the background. Returns a task ID."""
        task_id = f"bg-{uuid4().hex[:8]}"
        info = TaskInfo(id=task_id, name=name)
        self._tasks[task_id] = info

        async def _wrapper() -> None:
            try:
                info.result = await coro
                info.status = "done"
            except Exception as exc:
                info.status = "failed"
                info.error = str(exc)
                logger.debug("Background task %s failed: %s", task_id, exc)
            finally:
                info.finished_at = time.time()
                elapsed = info.finished_at - info.started_at
                print(f"\n[{task_id}] {name} — {info.status} ({elapsed:.1f}s)")

        at = asyncio.create_task(_wrapper())
        self._asyncio_tasks[task_id] = at
        print(f"[{task_id}] {name} started")
        return task_id

    def list_tasks(self) -> list[TaskInfo]:
        """Return all tracked tasks (running and completed)."""
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> TaskInfo | None:
        """Retrieve a task by ID."""
        return self._tasks.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        at = self._asyncio_tasks.get(task_id)
        info = self._tasks.get(task_id)
        if at and info and info.status == "running":
            at.cancel()
            info.status = "failed"
            info.error = "cancelled"
            info.finished_at = time.time()
            return True
        return False

    async def wait(self, task_id: str) -> TaskInfo | None:
        """Block until a task finishes, then return it."""
        at = self._asyncio_tasks.get(task_id)
        if at:
            await at
        return self._tasks.get(task_id)
