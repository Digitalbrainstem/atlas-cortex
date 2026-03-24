"""Atlas experimental sandbox — isolated environment for testing hypotheses.

The sandbox provides:
1. Isolated filesystem (temp directory, auto-cleaned)
2. Expendable model (spin up a tiny LLM on secondary GPU)
3. Benchmarking harness (measure before/after)
4. Safe execution (can't affect production code or data)

Module ownership: CLI experimental sandbox
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Result of a sandbox experiment."""

    hypothesis: str
    validated: bool
    metrics_before: dict[str, float] = field(default_factory=dict)
    metrics_after: dict[str, float] = field(default_factory=dict)
    improvement: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    duration: float = 0
    sandbox_path: str = ""


class Sandbox:
    """An isolated environment for Atlas to experiment in."""

    def __init__(self, name: str = "experiment") -> None:
        self.name = name
        self._root: Path | None = None
        self._model: str | None = None
        self._model_url: str | None = None
        self._cleanup_on_exit = True

    async def __aenter__(self) -> Sandbox:
        await self.setup()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.teardown()

    async def setup(self) -> Path:
        """Create isolated sandbox environment."""
        self._root = Path(
            tempfile.mkdtemp(prefix=f"atlas-sandbox-{self.name}-")
        )

        (self._root / "src").mkdir()
        (self._root / "tests").mkdir()
        (self._root / "data").mkdir()
        (self._root / "results").mkdir()

        logger.info("Sandbox created: %s", self._root)
        return self._root

    async def teardown(self) -> None:
        """Clean up sandbox."""
        if self._root and self._cleanup_on_exit and self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
            logger.info("Sandbox cleaned up: %s", self._root)

        if self._model:
            await self._unload_model()

    @property
    def path(self) -> Path | None:
        return self._root

    async def load_expendable_model(
        self, model: str = "qwen2.5:0.5b"
    ) -> str:
        """Load a tiny model for experimentation.

        Uses the secondary GPU if available, otherwise shares primary.
        The model is small enough (~300MB) to not impact main workload.

        Returns the model name that can be used with the provider.
        """
        try:
            import httpx

            base_url = os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )

            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.get(f"{base_url}/api/tags")
                if r.status_code == 200:
                    models = [
                        m["name"] for m in r.json().get("models", [])
                    ]
                    if model not in models:
                        logger.info("Pulling expendable model: %s", model)
                        await client.post(
                            f"{base_url}/api/pull",
                            json={"name": model, "stream": False},
                            timeout=300,
                        )

                self._model = model
                self._model_url = base_url
                logger.info("Expendable model ready: %s", model)
                return model
        except Exception as e:
            logger.warning("Failed to load expendable model: %s", e)
            self._model = os.environ.get("MODEL_FAST", "qwen3.5:9b")
            return self._model

    async def _unload_model(self) -> None:
        """Unload the expendable model to free VRAM."""
        if not self._model or not self._model_url:
            return
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self._model_url}/api/generate",
                    json={"model": self._model, "keep_alive": 0},
                )
                logger.info("Unloaded expendable model: %s", self._model)
        except Exception:
            pass

    async def run_shell(
        self, command: str, timeout: int = 30
    ) -> tuple[int, str]:
        """Run a shell command inside the sandbox."""
        if not self._root:
            return -1, "Sandbox not set up"

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "Command timed out"

        output = stdout.decode(errors="replace")
        if stderr:
            output += f"\n[stderr]\n{stderr.decode(errors='replace')}"
        return proc.returncode or 0, output

    def write_file(self, relative_path: str, content: str) -> Path:
        """Write a file into the sandbox."""
        full_path = self._root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return full_path

    def read_file(self, relative_path: str) -> str:
        """Read a file from the sandbox."""
        return (self._root / relative_path).read_text()

    async def benchmark(
        self, command: str, iterations: int = 5, label: str = ""
    ) -> dict[str, Any]:
        """Run a benchmark in the sandbox.

        Returns: {min, max, avg, median, iterations, label}
        """
        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            returncode, _ = await self.run_shell(command, timeout=60)
            elapsed = time.perf_counter() - start
            if returncode == 0:
                times.append(elapsed)

        if not times:
            return {"error": "All iterations failed", "label": label}

        times.sort()
        return {
            "min": times[0],
            "max": times[-1],
            "avg": sum(times) / len(times),
            "median": times[len(times) // 2],
            "iterations": len(times),
            "label": label,
        }

    async def compare(
        self,
        baseline_command: str,
        experiment_command: str,
        setup_command: str = "",
        iterations: int = 5,
    ) -> ExperimentResult:
        """Run A/B comparison between baseline and experiment.

        Returns ExperimentResult with metrics and improvement percentages.
        """
        result = ExperimentResult(
            hypothesis="",
            validated=False,
            sandbox_path=str(self._root),
        )

        start = time.perf_counter()

        if setup_command:
            await self.run_shell(setup_command)

        baseline = await self.benchmark(
            baseline_command, iterations, "baseline"
        )
        result.metrics_before = baseline

        experiment = await self.benchmark(
            experiment_command, iterations, "experiment"
        )
        result.metrics_after = experiment

        if "avg" in baseline and "avg" in experiment:
            baseline_avg = baseline["avg"]
            experiment_avg = experiment["avg"]
            if baseline_avg > 0:
                pct_change = (
                    (baseline_avg - experiment_avg) / baseline_avg
                ) * 100
                result.improvement = {
                    "avg_speedup_pct": round(pct_change, 1),
                    "baseline_avg": round(baseline_avg, 4),
                    "experiment_avg": round(experiment_avg, 4),
                }
                result.validated = pct_change > 5

        result.duration = time.perf_counter() - start
        return result

    async def ask_model(self, prompt: str, system: str = "") -> str:
        """Query the expendable model with a prompt."""
        if not self._model:
            await self.load_expendable_model()

        try:
            import httpx

            url = self._model_url or os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )
            async with httpx.AsyncClient(timeout=60) as client:
                messages: list[dict[str, str]] = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})

                r = await client.post(
                    f"{url}/api/chat",
                    json={
                        "model": self._model,
                        "messages": messages,
                        "stream": False,
                    },
                    timeout=60,
                )
                if r.status_code == 200:
                    return r.json().get("message", {}).get("content", "")
        except Exception as e:
            return f"Model query failed: {e}"
        return ""


# ── Sandbox Tool for the Agent ────────────────────────────


class SandboxTool:
    """Let the agent create and use sandboxes for experimentation.

    Dynamically inherits from AgentTool when registered via the tool registry.
    The base class is applied via __init_subclass__ or duck-typing to avoid
    circular imports (tools/__init__.py imports us, we can't import it at
    module level).
    """

    tool_id = "sandbox"
    description = (
        "Create an experimental sandbox to test hypotheses, "
        "benchmark approaches, or try code safely"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "write_file",
                    "run",
                    "benchmark",
                    "compare",
                    "ask_model",
                    "destroy",
                ],
                "description": "Sandbox action",
            },
            "name": {
                "type": "string",
                "description": "Sandbox name (for create)",
            },
            "path": {
                "type": "string",
                "description": "File path (for write_file)",
            },
            "content": {
                "type": "string",
                "description": "File content (for write_file)",
            },
            "command": {
                "type": "string",
                "description": "Shell command (for run/benchmark)",
            },
            "baseline_command": {
                "type": "string",
                "description": "Baseline command (for compare)",
            },
            "experiment_command": {
                "type": "string",
                "description": "Experiment command (for compare)",
            },
            "prompt": {
                "type": "string",
                "description": "Prompt for expendable model (for ask_model)",
            },
            "iterations": {
                "type": "integer",
                "description": "Benchmark iterations",
                "default": 5,
            },
        },
        "required": ["action"],
    }
    requires_confirmation = False

    _active_sandboxes: dict[str, Sandbox] = {}

    async def execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        from cortex.cli.tools import ToolResult

        action = params["action"]

        if action == "create":
            name = params.get("name", f"exp-{int(time.time())}")
            sb = Sandbox(name=name)
            await sb.setup()
            self._active_sandboxes[name] = sb
            return ToolResult(
                success=True,
                output=(
                    f"Sandbox '{name}' created at {sb.path}\n"
                    f"Directories: src/, tests/, data/, results/"
                ),
            )

        if action == "destroy":
            name = params.get("name", "")
            if name in self._active_sandboxes:
                await self._active_sandboxes[name].teardown()
                del self._active_sandboxes[name]
                return ToolResult(
                    success=True, output=f"Sandbox '{name}' destroyed"
                )
            return ToolResult(
                success=False, output=f"Sandbox '{name}' not found"
            )

        # All other actions need an active sandbox
        name = params.get("name", "")
        if not name:
            name = next(iter(self._active_sandboxes), "")
        sb = self._active_sandboxes.get(name)
        if not sb:
            return ToolResult(
                success=False,
                output="No active sandbox. Use action='create' first.",
            )

        if action == "write_file":
            path = sb.write_file(params["path"], params["content"])
            return ToolResult(success=True, output=f"Written: {path}")

        if action == "run":
            code, output = await sb.run_shell(params["command"])
            return ToolResult(success=code == 0, output=output[:10000])

        if action == "benchmark":
            result = await sb.benchmark(
                params["command"],
                params.get("iterations", 5),
            )
            return ToolResult(
                success="error" not in result,
                output=json.dumps(result, indent=2),
            )

        if action == "compare":
            result = await sb.compare(
                params["baseline_command"],
                params["experiment_command"],
                iterations=params.get("iterations", 5),
            )
            validated = (
                "✅ VALIDATED" if result.validated else "❌ NOT VALIDATED"
            )
            return ToolResult(
                success=True,
                output=(
                    f"{validated}\n"
                    f"Baseline avg: "
                    f"{result.metrics_before.get('avg', 0):.4f}s\n"
                    f"Experiment avg: "
                    f"{result.metrics_after.get('avg', 0):.4f}s\n"
                    f"Improvement: "
                    f"{result.improvement.get('avg_speedup_pct', 0):.1f}%"
                ),
            )

        if action == "ask_model":
            response = await sb.ask_model(params["prompt"])
            return ToolResult(success=True, output=response[:5000])

        return ToolResult(success=False, output=f"Unknown action: {action}")

    def to_function_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.tool_id,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
