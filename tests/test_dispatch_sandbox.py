"""Tests for agent dispatch mode and experimental sandbox."""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.dispatch import (
    AgentDispatcher,
    DispatchTask,
    GPUSlot,
    _print_task_header,
    _print_task_result,
)
from cortex.cli.sandbox import (
    ExperimentResult,
    Sandbox,
    SandboxTool,
)


# ── Dispatch Tests ──────────────────────────────────────────────────


class TestDispatchTask:
    def test_defaults(self):
        t = DispatchTask(id="t1", description="do stuff")
        assert t.status == "pending"
        assert t.duration == 0
        assert t.result == ""

    def test_fields(self):
        t = DispatchTask(
            id="t2", description="more", status="running", duration=3.5
        )
        assert t.status == "running"
        assert t.duration == 3.5


class TestGPUSlot:
    def test_defaults(self):
        s = GPUSlot(device_id="gpu:0")
        assert s.model_loaded == ""
        assert not s.busy

    def test_busy_flag(self):
        s = GPUSlot(device_id="cuda:0", busy=True)
        assert s.busy


class TestAgentDispatcher:
    @pytest.fixture
    def dispatcher(self):
        d = AgentDispatcher()
        d._gpu_slots = [GPUSlot(device_id="gpu:0")]
        return d

    async def test_detect_gpus_fallback(self):
        """Without Ollama, should return a single default GPU slot."""
        d = AgentDispatcher()
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://fake:9999"}):
            slots = await d._detect_gpus()
        assert len(slots) >= 1
        assert slots[0].device_id == "gpu:0"

    async def test_detect_gpus_with_ollama(self):
        """Mocked Ollama /api/ps returns GPU info."""
        d = AgentDispatcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"device": "cuda:0", "name": "qwen2.5:7b", "size": 4 * 1024**3},
                {"device": "cuda:1", "name": "qwen2.5:14b", "size": 8 * 1024**3},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            slots = await d._detect_gpus()

        assert len(slots) == 2
        assert slots[0].device_id == "cuda:0"
        assert slots[1].device_id == "cuda:1"
        assert slots[0].model_loaded == "qwen2.5:7b"

    async def test_sequential_dispatch(self, dispatcher):
        """Sequential dispatch runs tasks in order."""
        call_order = []

        async def mock_run_agent(task, model=None, max_iterations=50, **kw):
            call_order.append(task)
            return 0

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await dispatcher.dispatch(
                tasks=["first", "second", "third"],
                strategy="sequential",
            )

        assert len(results) == 3
        assert call_order == ["first", "second", "third"]
        assert all(r.status == "completed" for r in results)
        assert all(r.duration > 0 for r in results)

    async def test_sequential_dispatch_failure(self, dispatcher):
        """A failing task gets status='failed'."""
        async def mock_run_agent(task, **kw):
            if "bad" in task:
                return 1
            return 0

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await dispatcher.dispatch(
                tasks=["good", "bad task", "also good"],
                strategy="sequential",
            )

        assert results[0].status == "completed"
        assert results[1].status == "failed"
        assert results[2].status == "completed"

    async def test_sequential_dispatch_exception(self, dispatcher):
        """An exception in run_agent sets status='failed' with error message."""
        async def mock_run_agent(task, **kw):
            raise RuntimeError("boom")

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await dispatcher.dispatch(
                tasks=["explode"],
                strategy="sequential",
            )

        assert results[0].status == "failed"
        assert "boom" in results[0].result

    async def test_parallel_dispatch(self):
        """Parallel dispatch runs tasks concurrently on multiple GPUs."""
        d = AgentDispatcher()
        d._gpu_slots = [
            GPUSlot(device_id="cuda:0"),
            GPUSlot(device_id="cuda:1"),
        ]

        run_times = []

        async def mock_run_agent(task, **kw):
            run_times.append(task)
            await asyncio.sleep(0.01)
            return 0

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await d.dispatch(
                tasks=["alpha", "beta"],
                strategy="parallel",
            )

        assert len(results) == 2
        assert all(r.status == "completed" for r in results)

    async def test_auto_strategy_single_gpu(self, dispatcher):
        """Auto strategy picks sequential with one GPU."""
        async def mock_run_agent(task, **kw):
            return 0

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await dispatcher.dispatch(
                tasks=["a", "b"],
                strategy="auto",
            )

        assert len(results) == 2

    async def test_auto_strategy_multi_gpu(self):
        """Auto strategy picks parallel with 2+ GPUs."""
        d = AgentDispatcher()
        d._gpu_slots = [
            GPUSlot(device_id="cuda:0"),
            GPUSlot(device_id="cuda:1"),
        ]

        calls = []

        async def mock_run_agent(task, **kw):
            calls.append(task)
            return 0

        with patch("cortex.cli.agent.run_agent", side_effect=mock_run_agent):
            results = await d.dispatch(
                tasks=["x", "y"],
                strategy="auto",
            )

        assert len(results) == 2
        assert set(calls) == {"x", "y"}

    def test_generate_summary(self, dispatcher):
        dispatcher._tasks = [
            DispatchTask(id="task-1", description="do A", status="completed", duration=2.5),
            DispatchTask(id="task-2", description="do B", status="failed", duration=1.0),
        ]
        summary = dispatcher._generate_summary()
        assert "1 completed" in summary
        assert "1 failed" in summary
        assert "3.5s total" in summary

    async def test_initialize_without_memory(self):
        """Initialize works even when memory bridge is unavailable."""
        d = AgentDispatcher()
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://fake:9999"}):
            with patch(
                "cortex.cli.memory_bridge.MemoryBridge",
                side_effect=ImportError("no memory"),
            ):
                await d.initialize()
        assert len(d._gpu_slots) >= 1


class TestPrintHelpers:
    def test_print_task_header(self, capsys):
        t = DispatchTask(id="task-1", description="test task")
        _print_task_header(t)
        out = capsys.readouterr().out
        assert "task-1" in out
        assert "test task" in out

    def test_print_task_header_with_gpu(self, capsys):
        t = DispatchTask(id="task-1", description="test task")
        _print_task_header(t, "cuda:0")
        out = capsys.readouterr().out
        assert "[cuda:0]" in out

    def test_print_task_result_completed(self, capsys):
        t = DispatchTask(id="task-1", description="x", status="completed", duration=1.5)
        _print_task_result(t)
        out = capsys.readouterr().out
        assert "✅" in out
        assert "1.5s" in out

    def test_print_task_result_failed(self, capsys):
        t = DispatchTask(id="task-1", description="x", status="failed", duration=0.5)
        _print_task_result(t)
        out = capsys.readouterr().out
        assert "❌" in out


# ── Sandbox Tests ───────────────────────────────────────────────────


class TestSandbox:
    async def test_setup_creates_directories(self):
        sb = Sandbox(name="test-dirs")
        path = await sb.setup()
        try:
            assert path.exists()
            assert (path / "src").is_dir()
            assert (path / "tests").is_dir()
            assert (path / "data").is_dir()
            assert (path / "results").is_dir()
        finally:
            await sb.teardown()

    async def test_teardown_removes_directory(self):
        sb = Sandbox(name="test-cleanup")
        path = await sb.setup()
        assert path.exists()
        await sb.teardown()
        assert not path.exists()

    async def test_context_manager(self):
        async with Sandbox(name="ctx") as sb:
            assert sb.path is not None
            assert sb.path.exists()
            saved_path = sb.path
        assert not saved_path.exists()

    async def test_write_and_read_file(self):
        async with Sandbox(name="files") as sb:
            sb.write_file("src/hello.py", "print('hello')")
            content = sb.read_file("src/hello.py")
            assert content == "print('hello')"

    async def test_write_file_creates_parents(self):
        async with Sandbox(name="parents") as sb:
            sb.write_file("deep/nested/dir/file.txt", "data")
            assert sb.read_file("deep/nested/dir/file.txt") == "data"

    async def test_run_shell_success(self):
        async with Sandbox(name="shell") as sb:
            code, output = await sb.run_shell("echo hello")
            assert code == 0
            assert "hello" in output

    async def test_run_shell_failure(self):
        async with Sandbox(name="shell-fail") as sb:
            code, output = await sb.run_shell("false")
            # 'false' returns exit code 1
            assert code != 0 or "false" in output or output == ""

    async def test_run_shell_cwd_is_sandbox(self):
        async with Sandbox(name="cwd") as sb:
            code, output = await sb.run_shell("pwd")
            assert code == 0
            assert str(sb.path) in output

    async def test_run_shell_timeout(self):
        async with Sandbox(name="timeout") as sb:
            code, output = await sb.run_shell("sleep 60", timeout=1)
            assert code == -1
            assert "timed out" in output.lower()

    async def test_run_shell_without_setup(self):
        sb = Sandbox(name="no-setup")
        code, output = await sb.run_shell("echo hi")
        assert code == -1
        assert "not set up" in output.lower()

    async def test_benchmark(self):
        async with Sandbox(name="bench") as sb:
            result = await sb.benchmark("echo done", iterations=3, label="echo-test")
            assert "error" not in result
            assert result["iterations"] == 3
            assert result["label"] == "echo-test"
            assert result["min"] <= result["avg"] <= result["max"]
            assert result["median"] > 0

    async def test_benchmark_all_fail(self):
        async with Sandbox(name="bench-fail") as sb:
            result = await sb.benchmark(
                "exit 1", iterations=3, label="fail-test"
            )
            assert "error" in result

    async def test_compare(self):
        async with Sandbox(name="compare") as sb:
            result = await sb.compare(
                baseline_command="echo baseline",
                experiment_command="echo experiment",
                iterations=3,
            )
            assert isinstance(result, ExperimentResult)
            assert result.duration > 0
            assert "avg" in result.metrics_before
            assert "avg" in result.metrics_after

    async def test_compare_with_setup(self):
        async with Sandbox(name="compare-setup") as sb:
            result = await sb.compare(
                baseline_command="cat data.txt",
                experiment_command="cat data.txt",
                setup_command="echo 'hello' > data.txt",
                iterations=2,
            )
            assert result.duration > 0

    async def test_path_property(self):
        sb = Sandbox()
        assert sb.path is None
        await sb.setup()
        try:
            assert sb.path is not None
        finally:
            await sb.teardown()

    async def test_load_expendable_model_fallback(self):
        """Without Ollama, falls back to MODEL_FAST env var."""
        async with Sandbox(name="model") as sb:
            with patch.dict(os.environ, {
                "OLLAMA_BASE_URL": "http://fake:9999",
                "MODEL_FAST": "tiny-test:latest",
            }):
                model = await sb.load_expendable_model()
            assert model == "tiny-test:latest"

    async def test_ask_model_without_server(self):
        """ask_model returns error message when no server available."""
        async with Sandbox(name="ask") as sb:
            sb._model = "test:latest"
            sb._model_url = "http://fake:9999"
            result = await sb.ask_model("hello")
            assert "failed" in result.lower() or result == ""

    async def test_unload_model_noop_without_model(self):
        """Unload does nothing when no model loaded."""
        sb = Sandbox()
        sb._model = None
        await sb._unload_model()  # Should not raise


class TestExperimentResult:
    def test_defaults(self):
        r = ExperimentResult(hypothesis="test", validated=False)
        assert r.metrics_before == {}
        assert r.improvement == {}
        assert r.duration == 0


# ── SandboxTool Tests ───────────────────────────────────────────────


class TestSandboxTool:
    @pytest.fixture
    def tool(self):
        t = SandboxTool()
        t._active_sandboxes = {}
        return t

    async def test_create_and_destroy(self, tool):
        result = await tool.execute({"action": "create", "name": "test-sb"})
        assert result.success
        assert "test-sb" in result.output
        assert "test-sb" in tool._active_sandboxes

        result = await tool.execute({"action": "destroy", "name": "test-sb"})
        assert result.success
        assert "test-sb" not in tool._active_sandboxes

    async def test_destroy_nonexistent(self, tool):
        result = await tool.execute({"action": "destroy", "name": "nope"})
        assert not result.success

    async def test_write_file_and_run(self, tool):
        await tool.execute({"action": "create", "name": "wr"})
        try:
            result = await tool.execute({
                "action": "write_file",
                "name": "wr",
                "path": "src/hello.sh",
                "content": "#!/bin/sh\necho hello world",
            })
            assert result.success

            result = await tool.execute({
                "action": "run",
                "name": "wr",
                "command": "sh src/hello.sh",
            })
            assert result.success
            assert "hello world" in result.output
        finally:
            await tool.execute({"action": "destroy", "name": "wr"})

    async def test_benchmark_action(self, tool):
        await tool.execute({"action": "create", "name": "bench"})
        try:
            result = await tool.execute({
                "action": "benchmark",
                "name": "bench",
                "command": "echo done",
                "iterations": 2,
            })
            assert result.success
            data = json.loads(result.output)
            assert data["iterations"] == 2
        finally:
            await tool.execute({"action": "destroy", "name": "bench"})

    async def test_compare_action(self, tool):
        await tool.execute({"action": "create", "name": "cmp"})
        try:
            result = await tool.execute({
                "action": "compare",
                "name": "cmp",
                "baseline_command": "echo a",
                "experiment_command": "echo b",
                "iterations": 2,
            })
            assert result.success
            assert "Baseline avg" in result.output
        finally:
            await tool.execute({"action": "destroy", "name": "cmp"})

    async def test_no_sandbox_error(self, tool):
        result = await tool.execute({"action": "run", "command": "echo"})
        assert not result.success
        assert "No active sandbox" in result.output

    async def test_unknown_action(self, tool):
        await tool.execute({"action": "create", "name": "unk"})
        try:
            result = await tool.execute({"action": "nope", "name": "unk"})
            assert not result.success
            assert "Unknown action" in result.output
        finally:
            await tool.execute({"action": "destroy", "name": "unk"})

    async def test_auto_selects_sandbox(self, tool):
        """When no name given, uses the first active sandbox."""
        await tool.execute({"action": "create", "name": "auto-sb"})
        try:
            result = await tool.execute({
                "action": "run",
                "command": "echo auto",
            })
            assert result.success
            assert "auto" in result.output
        finally:
            await tool.execute({"action": "destroy", "name": "auto-sb"})

    def test_to_function_schema(self, tool):
        schema = tool.to_function_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "sandbox"
        assert "properties" in schema["function"]["parameters"]


# ── CLI Integration ─────────────────────────────────────────────────


class TestCLIDispatchArgs:
    """Verify argparse accepts dispatch flags."""

    def test_dispatch_flag_parsed(self):
        from cortex.cli.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "agent", "--dispatch", "task1", "task2",
        ])
        assert args.dispatch is True
        assert args.task == ["task1", "task2"]

    def test_parallel_flag(self):
        from cortex.cli.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "agent", "--dispatch", "--parallel", "a", "b",
        ])
        assert args.parallel is True
        assert args.sequential is False

    def test_sequential_flag(self):
        from cortex.cli.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args([
            "agent", "--dispatch", "--sequential", "a", "b",
        ])
        assert args.sequential is True
        assert args.parallel is False

    def test_single_task_no_dispatch(self):
        from cortex.cli.__main__ import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["agent", "single task"])
        assert args.dispatch is False


class TestRunAgentDispatchIntegration:
    """Test _run_agent routes to dispatch when --dispatch is set."""

    async def test_dispatch_route(self):
        from cortex.cli.__main__ import _run_agent

        mock_dispatcher = MagicMock()
        mock_dispatcher.initialize = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=[
            DispatchTask(id="t1", description="a", status="completed"),
        ])

        ns = MagicMock()
        ns.dispatch = True
        ns.parallel = False
        ns.sequential = False
        ns.task = ["task1", "task2"]
        ns.model = None
        ns.max_iterations = 50

        with patch(
            "cortex.cli.dispatch.AgentDispatcher",
            return_value=mock_dispatcher,
        ):
            code = await _run_agent(ns)

        assert code == 0
        mock_dispatcher.dispatch.assert_awaited_once()

    async def test_single_task_route(self):
        from cortex.cli.__main__ import _run_agent

        ns = MagicMock()
        ns.dispatch = False
        ns.task = ["hello", "world"]
        ns.file = []
        ns.model = None
        ns.max_iterations = 50

        with patch(
            "cortex.cli.agent.run_agent", new_callable=AsyncMock, return_value=0
        ) as mock_agent:
            code = await _run_agent(ns)

        assert code == 0
        mock_agent.assert_awaited_once_with(
            task="hello world",
            files=[],
            model=None,
            max_iterations=50,
        )
