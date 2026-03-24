"""Agent dispatch mode — coordinate multiple agent tasks.

Module ownership: Multi-agent task dispatch and coordination
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DispatchTask:
    """A task to be dispatched to an agent."""

    id: str
    description: str
    status: str = "pending"  # pending, running, completed, failed
    result: str = ""
    duration: float = 0
    agent_model: str = ""
    started_at: float = 0
    completed_at: float = 0


@dataclass
class GPUSlot:
    """Represents an available GPU for agent work."""

    device_id: str  # "cuda:0", "rocm:0", etc.
    model_loaded: str = ""
    vram_mb: int = 0
    busy: bool = False


class AgentDispatcher:
    """Coordinates multiple agent tasks across available compute."""

    def __init__(self) -> None:
        self._tasks: list[DispatchTask] = []
        self._gpu_slots: list[GPUSlot] = []
        self._memory_bridge: object | None = None

    async def initialize(self) -> None:
        """Detect available compute resources."""
        self._gpu_slots = await self._detect_gpus()

        try:
            from cortex.cli.memory_bridge import MemoryBridge

            self._memory_bridge = MemoryBridge(user_id="dispatch")
            await self._memory_bridge.initialize()
        except Exception as exc:
            logger.debug("Memory bridge unavailable for dispatch: %s", exc)

    async def _detect_gpus(self) -> list[GPUSlot]:
        """Detect available GPUs via Ollama's running-model endpoint."""
        slots: list[GPUSlot] = []

        try:
            import httpx

            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}/api/ps")
                if r.status_code == 200:
                    data = r.json()
                    for model_info in data.get("models", []):
                        slots.append(
                            GPUSlot(
                                device_id=model_info.get("device", "gpu:0"),
                                model_loaded=model_info.get("name", ""),
                                vram_mb=model_info.get("size", 0) // (1024 * 1024),
                            )
                        )
        except Exception:
            pass

        if not slots:
            slots.append(GPUSlot(device_id="gpu:0"))

        return slots

    async def dispatch(
        self,
        tasks: list[str],
        strategy: str = "auto",
        model: str | None = None,
        max_iterations: int = 50,
    ) -> list[DispatchTask]:
        """Dispatch multiple tasks.

        Strategy:
        - auto: parallel if 2+ GPUs available, sequential otherwise
        - sequential: run one at a time, shared memory between tasks
        - parallel: run simultaneously on different GPUs
        """
        self._tasks = []
        for i, desc in enumerate(tasks):
            self._tasks.append(
                DispatchTask(id=f"task-{i + 1}", description=desc)
            )

        if strategy == "auto":
            available_gpus = len([s for s in self._gpu_slots if not s.busy])
            strategy = (
                "parallel"
                if available_gpus >= 2 and len(tasks) >= 2
                else "sequential"
            )

        logger.info(
            "Dispatching %d tasks with strategy=%s (%d GPUs available)",
            len(tasks),
            strategy,
            len(self._gpu_slots),
        )

        if strategy == "parallel":
            await self._run_parallel(model, max_iterations)
        else:
            await self._run_sequential(model, max_iterations)

        summary = self._generate_summary()
        if self._memory_bridge:
            await self._memory_bridge.remember(
                f"Dispatch completed: {summary}",
                tags=["dispatch", "multi-agent"],
            )

        return self._tasks

    async def _run_sequential(
        self, model: str | None, max_iterations: int
    ) -> None:
        """Run tasks one at a time. Each task can see previous task's memory."""
        from cortex.cli.agent import run_agent

        for task in self._tasks:
            task.status = "running"
            task.started_at = time.time()

            _print_task_header(task)

            try:
                exit_code = await run_agent(
                    task=task.description,
                    model=model,
                    max_iterations=max_iterations,
                )
                task.status = "completed" if exit_code == 0 else "failed"
            except Exception as e:
                task.status = "failed"
                task.result = str(e)
                logger.error("Task %s failed: %s", task.id, e)

            task.completed_at = time.time()
            task.duration = task.completed_at - task.started_at

            _print_task_result(task)

    async def _run_parallel(
        self, model: str | None, max_iterations: int
    ) -> None:
        """Run tasks in parallel on different GPUs."""
        from cortex.cli.agent import run_agent

        gpu_count = len(self._gpu_slots)

        async def _run_on_gpu(
            task: DispatchTask, gpu_slot: GPUSlot
        ) -> None:
            task.status = "running"
            task.started_at = time.time()
            task.agent_model = gpu_slot.model_loaded or model or ""

            _print_task_header(task, gpu_slot.device_id)

            try:
                task_model = model
                if gpu_slot.device_id != self._gpu_slots[0].device_id:
                    task_model = model or os.environ.get(
                        "MODEL_FAST", "qwen3.5:9b"
                    )

                exit_code = await run_agent(
                    task=task.description,
                    model=task_model,
                    max_iterations=max_iterations,
                )
                task.status = "completed" if exit_code == 0 else "failed"
            except Exception as e:
                task.status = "failed"
                task.result = str(e)

            task.completed_at = time.time()
            task.duration = task.completed_at - task.started_at
            gpu_slot.busy = False

            _print_task_result(task)

        running: list[asyncio.Task[None]] = []
        task_queue = list(self._tasks)

        while task_queue or running:
            while task_queue and len(running) < gpu_count:
                task = task_queue.pop(0)
                slot = next(
                    (s for s in self._gpu_slots if not s.busy), None
                )
                if slot:
                    slot.busy = True
                    coro = _run_on_gpu(task, slot)
                    running.append(asyncio.create_task(coro))

            if running:
                done, running_set = await asyncio.wait(
                    running, return_when=asyncio.FIRST_COMPLETED
                )
                running = list(running_set)

    def _generate_summary(self) -> str:
        """Generate a human-readable summary of all tasks."""
        total_time = sum(t.duration for t in self._tasks)
        completed = sum(1 for t in self._tasks if t.status == "completed")
        failed = sum(1 for t in self._tasks if t.status == "failed")

        lines = [
            f"\n{'=' * 60}",
            f"Dispatch Summary: {completed} completed, {failed} failed, "
            f"{total_time:.1f}s total",
            f"{'=' * 60}",
        ]

        for task in self._tasks:
            icon = "✅" if task.status == "completed" else "❌"
            lines.append(
                f"  {icon} {task.id}: {task.description[:50]} "
                f"({task.duration:.1f}s)"
            )

        return "\n".join(lines)


def _print_task_header(task: DispatchTask, gpu: str = "") -> None:
    gpu_info = f" [{gpu}]" if gpu else ""
    print(f"\n{'=' * 60}")
    print(f"🚀 Starting {task.id}{gpu_info}: {task.description}")
    print(f"{'=' * 60}\n")


def _print_task_result(task: DispatchTask) -> None:
    icon = "✅" if task.status == "completed" else "❌"
    print(f"\n{icon} {task.id}: {task.status} ({task.duration:.1f}s)")
