"""Atlas CLI autonomous agent with ReAct (Think → Act → Observe) loop.

Uses text-based tool calling with ``<tool_call>`` blocks parsed from LLM
output, providing compatibility with any LLM backend regardless of native
function-calling support.

Usage::

    from cortex.cli.agent import run_agent
    exit_code = await run_agent(task="add auth to the API")
"""

# Module ownership: ReAct autonomous agent loop
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time as _time
from pathlib import Path
from typing import Any

from cortex.cli.tools import ToolRegistry, ToolResult, get_default_registry

logger = logging.getLogger(__name__)

# ── Optional rich import with fallback ──────────────────────────────
try:
    from rich.console import Console
    from rich.theme import Theme

    _THEME = Theme({
        "thinking": "dim cyan",
        "tool_name": "bold yellow",
        "tool_param": "dim",
        "result_ok": "green",
        "result_err": "bold red",
        "done": "bold green",
        "error": "bold red",
        "iter": "dim magenta",
        "header": "bold cyan",
    })
    _console = Console(theme=_THEME, highlight=False)
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]


# ── Display helpers ─────────────────────────────────────────────────

def _print(text: str, *, style: str | None = None, end: str = "\n") -> None:
    """Print with optional rich styling."""
    if _HAS_RICH and _console is not None:
        _console.print(text, style=style, end=end)
    else:
        print(text, end=end)


def _print_iteration(i: int, max_iter: int) -> None:
    _print(f"\n[{i}/{max_iter}]", style="iter")


def _print_thinking() -> None:
    _print("🤔 Thinking...", style="thinking")


def _print_tool_call(tool_id: str, params: dict[str, Any]) -> None:
    param_str = json.dumps(params, indent=2) if params else "{}"
    _print(f"🔧 {tool_id}", style="tool_name")
    if params:
        _print(f"   {_truncate(param_str, 500)}", style="tool_param")


def _print_tool_result(result: ToolResult) -> None:
    if result.success:
        _print(f"📋 {_truncate(result.output, 1000)}", style="result_ok")
    else:
        _print(
            f"❌ {result.error or 'Tool execution failed'}", style="result_err",
        )


def _print_done(summary: str) -> None:
    _print(f"\n✅ {summary}", style="done")


def _print_error(text: str) -> None:
    _print(f"❌ {text}", style="error")


def _truncate(text: str, max_len: int = 2000) -> str:
    """Truncate *text* and append a note if it exceeds *max_len* characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... ({len(text) - max_len} chars truncated)"


# ── Tool-call parsing ──────────────────────────────────────────────

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract tool calls from LLM text output.

    Parses ``<tool_call>{"tool": "...", "params": {...}}</tool_call>``
    blocks.  Returns a list of dicts with *tool* and *params* keys.
    """
    calls: list[dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "tool" in data:
                calls.append({
                    "tool": str(data["tool"]),
                    "params": data.get("params") or {},
                })
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to parse tool call: %s", match.group(1)[:200],
            )
    return calls


def extract_thinking(text: str) -> str:
    """Return the reasoning text with ``<tool_call>`` blocks stripped."""
    return _TOOL_CALL_RE.sub("", text).strip()


# ── System prompt ───────────────────────────────────────────────────

_AGENT_SYSTEM_PROMPT = """\
You are Atlas, an autonomous AI agent. You accomplish tasks by using tools.

## Available Tools

{tool_descriptions}

## How to Use Tools

To use a tool, include a tool call block in your response:

<tool_call>
{{"tool": "tool_name", "params": {{"param1": "value1"}}}}
</tool_call>

You can make multiple tool calls in one response.  After each round of \
tool calls you will see all results in the next message.

## Workflow

Think step by step:
1. Analyze the task and plan your approach
2. Gather information (read files, search code, check status)
3. Make changes (edit files, run commands)
4. Verify your work (run tests, check output)
5. When done, respond with your final summary (NO tool call)

## Rules

- Always read files before editing them
- Run tests after making code changes
- If a tool fails, adapt your approach
- Be precise with file paths
- When your task is complete, give a clear summary WITHOUT any tool calls

## Self-Learning

If you encounter a task that requires a capability none of your tools provide, \
use tool_propose to create a new tool. This is better than trying to work around \
missing tools with complex shell commands.

You can also use tool_learn to teach yourself new tools from user instructions. \
Use tool_cleanup periodically to remove tools you no longer need.
"""


def build_system_prompt(registry: ToolRegistry) -> str:
    """Build the full system prompt including tool descriptions."""
    tool_lines: list[str] = []
    for tool in registry.list_tools():
        schema = tool.parameters_schema
        param_parts: list[str] = []
        for pname, pinfo in schema.get("properties", {}).items():
            required = pname in schema.get("required", [])
            req_mark = " (required)" if required else ""
            desc = pinfo.get("description", "")
            ptype = pinfo.get("type", "string")
            param_parts.append(f"    - {pname}: {ptype}{req_mark} — {desc}")
        params_desc = "\n".join(param_parts) if param_parts else "    (none)"

        confirm = " ⚠️  REQUIRES USER CONFIRMATION" if tool.requires_confirmation else ""
        tool_lines.append(
            f"### {tool.tool_id}{confirm}\n"
            f"{tool.description}\n"
            f"Parameters:\n{params_desc}",
        )

    return _AGENT_SYSTEM_PROMPT.format(
        tool_descriptions="\n\n".join(tool_lines),
    )


# ── File-input processing ──────────────────────────────────────────

_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg",
})

_MAX_FILE_CHARS = 50_000


def _process_input_files(files: list[str]) -> str:
    """Read *files* and return context text suitable for the task prompt."""
    parts: list[str] = []
    for file_path in files:
        p = Path(file_path)
        ext = p.suffix.lower()

        if not p.exists():
            parts.append(f"[File not found: {file_path}]")
            continue

        if ext in _IMAGE_EXTENSIONS:
            parts.append(
                f"[Image file: {file_path} — describe what you need from it]",
            )
        elif ext == ".pdf":
            parts.append(
                f"[PDF file: {file_path} — use shell_exec with pdftotext to extract]",
            )
        else:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                if len(content) > _MAX_FILE_CHARS:
                    content = (
                        content[:_MAX_FILE_CHARS]
                        + f"\n... (truncated, {len(content)} total chars)"
                    )
                parts.append(f"--- File: {file_path} ---\n{content}\n---")
            except OSError as exc:
                parts.append(f"[Error reading {file_path}: {exc}]")

    return "\n\n".join(parts)


# ── Confirmation ────────────────────────────────────────────────────

def _ask_confirmation(tool_id: str, params: dict[str, Any]) -> bool:
    """Prompt the user to approve a destructive tool invocation."""
    _print(f"\n⚠️  {tool_id} wants to execute:", style="tool_name")
    param_str = json.dumps(params, indent=2)
    _print(f"   {_truncate(param_str, 500)}", style="tool_param")
    try:
        response = input("   Allow? [y/N] ")
        return response.strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ── Piped-stdin helper ──────────────────────────────────────────────

def _read_piped_stdin() -> str | None:
    """Return data from stdin when it is piped (non-interactive)."""
    try:
        if sys.stdin.isatty():
            return None
        return sys.stdin.read()
    except Exception:  # noqa: BLE001
        return None


# ── Core ReAct loop ─────────────────────────────────────────────────

async def run_agent(
    task: str,
    files: list[str] | None = None,
    model: str | None = None,
    max_iterations: int = 25,
) -> int:
    """Run the autonomous agent on a task.

    Returns 0 on success, 1 on failure.
    """
    from cortex.providers import get_provider
    from cortex.cli.memory_bridge import MemoryBridge
    from cortex.curiosity import CuriosityEngine

    current_model = model or os.environ.get("MODEL_FAST", "qwen2.5:14b")

    # ── Provider ────────────────────────────────────────────────
    try:
        provider = get_provider()
    except Exception as exc:
        _print_error(f"LLM provider unavailable: {exc}")
        return 1

    # ── Memory bridge ──────────────────────────────────────────
    bridge = MemoryBridge(user_id="cli_agent")
    await bridge.initialize()
    bridge.set_provider(provider)

    # ── Curiosity engine ───────────────────────────────────────
    curiosity = CuriosityEngine()
    await curiosity.initialize()

    # ── Tools ───────────────────────────────────────────────────
    registry = get_default_registry()
    system_prompt = build_system_prompt(registry)
    system_prompt += curiosity.get_system_prompt_addition()

    # ── Task context ────────────────────────────────────────────
    task_parts: list[str] = [task]

    if files:
        file_ctx = _process_input_files(files)
        if file_ctx:
            task_parts.append(f"\n\nAttached files:\n{file_ctx}")

    piped = _read_piped_stdin()
    if piped:
        task_parts.append(f"\n\nPiped input:\n{piped}")

    cwd = os.getcwd()
    task_parts.append(f"\n\nWorking directory: {cwd}")

    full_task = "".join(task_parts)

    # ── Conversation (memory-managed) ──────────────────────────
    history: list[dict[str, str]] = []
    tool_context: dict[str, Any] = {"cwd": cwd}

    messages = await bridge.build_messages_with_memory(
        system_prompt=system_prompt,
        current_message=full_task,
        history=[],
    )

    # ── Banner ──────────────────────────────────────────────────
    _print("\n╭─────────────────────────────────────╮", style="header")
    _print("│       🤖 Atlas Agent Mode           │", style="header")
    _print("╰─────────────────────────────────────╯", style="header")
    _print(f"  Model: {current_model}", style="thinking")
    _print(f"  Tools: {len(registry.list_tools())}", style="thinking")
    _print(f"  Max iterations: {max_iterations}", style="thinking")
    _print(f"  Memory: {'connected' if bridge._db_conn else 'offline'}\n",
           style="thinking")

    # ── ReAct loop ──────────────────────────────────────────────
    _agent_start = _time.monotonic()
    for iteration in range(1, max_iterations + 1):
        _print_iteration(iteration, max_iterations)
        _print_thinking()

        # ── LLM call (streaming) ────────────────────────────────
        try:
            response = await provider.chat(
                messages=messages,
                model=current_model,
                stream=True,
                temperature=0.3,
            )
        except Exception as exc:
            _print_error(f"LLM request failed: {exc}")
            await bridge.shutdown()
            return 1

        full_response = ""
        try:
            if isinstance(response, dict):
                # Provider returned a non-streaming dict
                full_response = response.get("content", "")
                print(full_response, flush=True)
            else:
                async for chunk in response:
                    full_response += chunk
                    print(chunk, end="", flush=True)
                print()  # trailing newline after streamed output
        except Exception as exc:
            _print_error(f"\nStreaming error: {exc}")
            await bridge.shutdown()
            return 1

        # ── Parse tool calls ────────────────────────────────────
        tool_calls = parse_tool_calls(full_response)

        if not tool_calls:
            # No tool calls → agent finished (final summary)
            history.append({"role": "assistant", "content": full_response})
            await bridge.remember_decision(f"Task completed: {task[:200]}")

            # Curiosity: record task completion and save state
            try:
                curiosity.on_task_complete(
                    task[:200],
                    _time.monotonic() - _agent_start,
                    iteration,
                )
                reflection = await curiosity.reflect()
                if reflection and "No notable patterns" not in reflection:
                    await bridge.remember_decision(reflection)
                await curiosity.save_state()
            except Exception:  # noqa: BLE001
                pass

            await bridge.shutdown()
            _print_done("Task complete")
            return 0

        # ── Execute tool calls ──────────────────────────────────
        history.append({"role": "assistant", "content": full_response})

        results_parts: list[str] = []
        for call in tool_calls:
            tool_id = call["tool"]
            params = call["params"]
            _print_tool_call(tool_id, params)

            tool = registry.get(tool_id)
            if tool is None:
                result = ToolResult(
                    success=False, output="",
                    error=f"Unknown tool: {tool_id}",
                )
            elif tool.requires_confirmation and not _ask_confirmation(
                tool_id, params,
            ):
                result = ToolResult(
                    success=False, output="", error="User denied execution",
                )
            else:
                _t0 = _time.monotonic()
                try:
                    result = await registry.execute(tool_id, params, tool_context)
                except Exception as exc:  # noqa: BLE001
                    result = ToolResult(
                        success=False, output="",
                        error=f"Tool error: {exc}",
                    )
                _elapsed = _time.monotonic() - _t0

            # Curiosity: observe tool execution (best-effort)
            try:
                curiosity.on_tool_executed(tool_id, params, result, _elapsed)
                if not result.success:
                    curiosity.on_error(
                        result.error.split(":")[0] if result.error else "unknown",
                        f"{tool_id}: {result.error}",
                    )
            except Exception:  # noqa: BLE001
                pass

            _print_tool_result(result)

            output = result.output if result.success else f"ERROR: {result.error}"
            output = _truncate(output, 10_000)
            results_parts.append(
                f"Tool: {tool_id}\nSuccess: {result.success}\nOutput:\n{output}",
            )

            # Store tool result to memory (best-effort)
            await bridge.remember_tool_result(
                tool_id, params, result.output if result.success else "",
            )

        results_message = "\n\n---\n\n".join(results_parts)
        tool_result_text = f"Tool results:\n\n{results_message}"
        history.append({"role": "user", "content": tool_result_text})

        # Rebuild messages through bridge (handles archival + memory recall)
        messages = await bridge.build_messages_with_memory(
            system_prompt=system_prompt,
            current_message=f"Continue working on the task. {tool_result_text}",
            history=history,
        )

    # ── Max iterations reached ──────────────────────────────────
    _print_error(f"Reached maximum iterations ({max_iterations})")
    _print(
        "The agent made progress but did not complete the task.",
        style="thinking",
    )

    # Curiosity: record even incomplete tasks
    try:
        curiosity.on_task_complete(
            task[:200], _time.monotonic() - _agent_start, max_iterations,
        )
        await curiosity.save_state()
    except Exception:  # noqa: BLE001
        pass

    await bridge.shutdown()
    return 1
