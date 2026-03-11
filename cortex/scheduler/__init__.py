"""Centralized startup/shutdown task manager.

All background services register here. The server lifespan calls
start_all() / stop_all() once.

OWNERSHIP: This module is the ONLY place that starts or stops
background services. No other module should call asyncio.create_task()
for long-running services directly.
"""

# Module ownership: Background task management
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class StartupTask:
    """A named async task that runs during server startup."""
    def __init__(
        self,
        name: str,
        coro_fn: Callable[[], Coroutine[Any, Any, None]],
        *,
        blocking: bool = False,
        critical: bool = False,
    ) -> None:
        self.name = name
        self.coro_fn = coro_fn
        self.blocking = blocking  # If True, awaited before server starts
        self.critical = critical  # If True, failure aborts startup


class BackgroundService:
    """A named service with start/stop lifecycle."""
    def __init__(
        self,
        name: str,
        start_fn: Callable[[], Coroutine[Any, Any, None]],
        stop_fn: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self.name = name
        self.start_fn = start_fn
        self.stop_fn = stop_fn


_startup_tasks: list[StartupTask] = []
_background_services: list[BackgroundService] = []
_running_tasks: list[asyncio.Task] = []


def register_startup_task(
    name: str,
    coro_fn: Callable[[], Coroutine[Any, Any, None]],
    *,
    blocking: bool = False,
    critical: bool = False,
) -> None:
    """Register a task to run at server startup."""
    _startup_tasks.append(StartupTask(name, coro_fn, blocking=blocking, critical=critical))


def register_service(
    name: str,
    start_fn: Callable[[], Coroutine[Any, Any, None]],
    stop_fn: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Register a background service with start/stop lifecycle."""
    _background_services.append(BackgroundService(name, start_fn, stop_fn))


async def start_all() -> None:
    """Run all registered startup tasks and start background services."""
    # Run blocking tasks first (order matters)
    for task in _startup_tasks:
        if task.blocking:
            try:
                logger.info("Startup [blocking]: %s", task.name)
                await task.coro_fn()
            except Exception as e:
                if task.critical:
                    logger.error("Critical startup task failed: %s — %s", task.name, e)
                    raise
                logger.warning("Startup task failed (non-critical): %s — %s", task.name, e)

    # Fire non-blocking tasks as background coroutines
    for task in _startup_tasks:
        if not task.blocking:
            async def _run(t: StartupTask = task) -> None:
                try:
                    logger.info("Startup [background]: %s", t.name)
                    await t.coro_fn()
                    logger.info("Startup complete: %s", t.name)
                except Exception as e:
                    logger.warning("Background startup failed: %s — %s", t.name, e)
            _running_tasks.append(asyncio.create_task(_run()))

    # Start lifecycle services
    for svc in _background_services:
        try:
            logger.info("Starting service: %s", svc.name)
            await svc.start_fn()
        except Exception as e:
            logger.warning("Service start failed: %s — %s", svc.name, e)


async def stop_all() -> None:
    """Stop all background services (reverse order)."""
    for svc in reversed(_background_services):
        try:
            logger.info("Stopping service: %s", svc.name)
            await svc.stop_fn()
        except Exception as e:
            logger.warning("Service stop failed: %s — %s", svc.name, e)

    # Cancel any lingering background tasks
    for task in _running_tasks:
        if not task.done():
            task.cancel()
    _running_tasks.clear()
