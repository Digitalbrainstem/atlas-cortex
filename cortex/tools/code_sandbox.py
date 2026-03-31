"""Sandboxed Python code execution for Atlas Cortex.

Allows Atlas to generate code, run it safely in a subprocess with resource
limits, inspect results, and self-correct via an LLM feedback loop.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import allow / block lists
# ---------------------------------------------------------------------------

BLOCKED_MODULES: frozenset[str] = frozenset({
    "os", "subprocess", "sys", "shutil", "pathlib",
    "socket", "requests", "urllib", "http", "ftplib",
    "smtplib", "imaplib", "poplib", "telnetlib",
    "ctypes", "multiprocessing", "signal", "importlib",
    "code", "codeop", "compileall", "runpy",
    "webbrowser", "antigravity", "turtle",
    "pickle", "shelve", "marshal",
})

ALLOWED_MODULES: frozenset[str] = frozenset({
    "math", "random", "collections", "itertools", "functools",
    "dataclasses", "datetime", "json", "re", "typing",
    "statistics", "decimal", "fractions", "string",
    "operator", "enum", "copy", "pprint", "textwrap",
    "numbers", "cmath", "bisect", "heapq", "array",
    "hashlib", "hmac", "base64", "binascii",
    "struct", "io", "abc", "contextlib",
    "uuid", "time",
})

# Defaults -------------------------------------------------------------------

DEFAULT_TIMEOUT: int = 30
DEFAULT_MEMORY_MB: int = 256

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    """Outcome of a sandboxed code execution."""

    success: bool
    stdout: str
    stderr: str
    return_value: Any
    elapsed: float
    timed_out: bool = False


@dataclass
class SyntaxResult:
    """Outcome of a syntax validation pass."""

    valid: bool
    error: str | None = None
    line: int | None = None


@dataclass
class ImportResult:
    """Outcome of an import-safety check."""

    allowed: bool
    blocked: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


@dataclass
class CodeResult:
    """Combined result from the generate-and-verify loop."""

    code: str
    execution: ExecutionResult
    attempts: int
    syntax_valid: bool


# ---------------------------------------------------------------------------
# Sandbox implementation
# ---------------------------------------------------------------------------


class CodeSandbox:
    """Execute Python code in a restricted subprocess."""

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        memory_mb: int = DEFAULT_MEMORY_MB,
    ) -> None:
        self.timeout = timeout
        self.memory_mb = memory_mb

    # -- static checks ------------------------------------------------------

    @staticmethod
    def validate_syntax(code: str) -> SyntaxResult:
        """Parse *code* with :func:`ast.parse` and report any syntax error."""
        try:
            ast.parse(code)
            return SyntaxResult(valid=True)
        except SyntaxError as exc:
            return SyntaxResult(
                valid=False,
                error=str(exc.msg) if exc.msg else str(exc),
                line=exc.lineno,
            )

    @staticmethod
    def check_imports(code: str) -> ImportResult:
        """Walk the AST and verify every import against the allow/block lists."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ImportResult(allowed=False, blocked=[], unknown=["<syntax error>"])

        blocked: list[str] = []
        unknown: list[str] = []

        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names = [node.module.split(".")[0]]

            for name in names:
                if name in BLOCKED_MODULES:
                    blocked.append(name)
                elif name not in ALLOWED_MODULES:
                    unknown.append(name)

        return ImportResult(
            allowed=len(blocked) == 0,
            blocked=sorted(set(blocked)),
            unknown=sorted(set(unknown)),
        )

    # -- execution ----------------------------------------------------------

    async def execute(
        self,
        code: str,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Run *code* in a sandboxed subprocess and return the result.

        The subprocess is given resource limits (memory, timeout) and has
        dangerous standard-library modules disabled.
        """
        timeout = timeout if timeout is not None else self.timeout

        # Pre-flight checks
        syn = self.validate_syntax(code)
        if not syn.valid:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"SyntaxError at line {syn.line}: {syn.error}",
                return_value=None,
                elapsed=0.0,
            )

        imp = self.check_imports(code)
        if not imp.allowed:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Blocked imports: {', '.join(imp.blocked)}",
                return_value=None,
                elapsed=0.0,
            )

        wrapper = _build_wrapper(code, self.memory_mb)

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", wrapper,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = time.monotonic() - start
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="Execution timed out",
                    return_value=None,
                    elapsed=elapsed,
                    timed_out=True,
                )

            elapsed = time.monotonic() - start
            stdout_text = raw_stdout.decode(errors="replace")
            stderr_text = raw_stderr.decode(errors="replace")

            return_value: Any = None
            # The wrapper writes a JSON sentinel on the last line of stdout.
            lines = stdout_text.rstrip("\n").split("\n")
            sentinel_prefix = "\x00__SANDBOX_RESULT__:"
            if lines and lines[-1].startswith(sentinel_prefix):
                payload = lines.pop(-1)[len(sentinel_prefix):]
                stdout_text = "\n".join(lines)
                if stdout_text and not stdout_text.endswith("\n"):
                    stdout_text += "\n"
                elif not lines:
                    stdout_text = ""
                try:
                    return_value = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    pass

            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout_text,
                stderr=stderr_text,
                return_value=return_value,
                elapsed=elapsed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sandbox execution failed unexpectedly")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Sandbox error: {exc}",
                return_value=None,
                elapsed=time.monotonic() - start,
            )

    # -- LLM self-correction loop -------------------------------------------

    async def generate_and_verify(
        self,
        provider: Any,
        messages: list[dict[str, str]],
        *,
        max_attempts: int = 3,
        timeout: int | None = None,
    ) -> CodeResult:
        """Ask an LLM to produce code, then verify and self-correct.

        1. Call *provider.chat()* with *messages* (non-streaming).
        2. Extract the first Python code block from the response.
        3. Validate syntax → check imports → execute.
        4. If execution fails, append the error to the conversation and retry
           (up to *max_attempts*).
        """
        conversation = list(messages)
        last_code = ""
        last_result = ExecutionResult(
            success=False,
            stdout="",
            stderr="No code generated",
            return_value=None,
            elapsed=0.0,
        )
        syntax_ok = False

        for attempt in range(1, max_attempts + 1):
            # -- generate code from LLM ------------------------------------
            response = await provider.chat(
                conversation, stream=False, temperature=0.3,
            )
            content: str = response.get("content", "") if isinstance(response, dict) else str(response)

            code = _extract_python_code(content)
            if not code:
                conversation.append({"role": "assistant", "content": content})
                conversation.append({
                    "role": "user",
                    "content": (
                        "Your response did not contain a Python code block. "
                        "Please provide the code inside a ```python ... ``` block."
                    ),
                })
                last_code = ""
                continue

            last_code = code

            # -- validate syntax -------------------------------------------
            syn = self.validate_syntax(code)
            syntax_ok = syn.valid
            if not syn.valid:
                conversation.append({"role": "assistant", "content": content})
                conversation.append({
                    "role": "user",
                    "content": (
                        f"The code has a syntax error on line {syn.line}: "
                        f"{syn.error}\nPlease fix it."
                    ),
                })
                last_result = ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"SyntaxError at line {syn.line}: {syn.error}",
                    return_value=None,
                    elapsed=0.0,
                )
                continue

            # -- check imports ---------------------------------------------
            imp = self.check_imports(code)
            if not imp.allowed:
                conversation.append({"role": "assistant", "content": content})
                conversation.append({
                    "role": "user",
                    "content": (
                        f"The code uses blocked imports: {', '.join(imp.blocked)}. "
                        "Please rewrite without those modules."
                    ),
                })
                last_result = ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Blocked imports: {', '.join(imp.blocked)}",
                    return_value=None,
                    elapsed=0.0,
                )
                continue

            # -- execute ---------------------------------------------------
            last_result = await self.execute(code, timeout=timeout)
            if last_result.success:
                break

            # Feed error back for self-correction
            conversation.append({"role": "assistant", "content": content})
            conversation.append({
                "role": "user",
                "content": (
                    f"The code failed with this error:\n"
                    f"```\n{last_result.stderr}\n```\n"
                    "Please fix the code and try again."
                ),
            })

        return CodeResult(
            code=last_code,
            execution=last_result,
            attempts=attempt,  # type: ignore[possibly-undefined]
            syntax_valid=syntax_ok,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_CODE_BLOCK_MARKERS = ("```python", "```py")


def _extract_python_code(text: str) -> str:
    """Pull the first Python fenced code block out of *text*."""
    lower = text.lower()
    start = -1
    for marker in _CODE_BLOCK_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and (start == -1 or idx < start):
            start = idx

    if start == -1:
        return ""

    # Skip the marker line
    code_start = text.index("\n", start) + 1
    end = text.find("```", code_start)
    if end == -1:
        return text[code_start:].strip()
    return text[code_start:end].strip()


def _build_wrapper(code: str, memory_mb: int) -> str:
    """Return a self-contained Python script that executes *code* with limits.

    The wrapper:
    1. Sets an ``RLIMIT_AS`` memory cap (soft == hard).
    2. Executes *code* via ``exec()`` in a restricted namespace.
    3. Writes the last expression value as a JSON sentinel to stdout.
    """
    escaped = code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

    return textwrap.dedent(f"""\
        import resource, json, sys, io

        # Memory limit
        _mem = {memory_mb} * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (_mem, _mem))
        except (ValueError, resource.error):
            pass

        _code = '{escaped}'
        _ns = {{}}
        _result = None
        try:
            _tree = compile(_code, "<sandbox>", "exec")
            exec(_tree, _ns)
            # Try to capture the last expression value
            try:
                _expr = compile(_code, "<sandbox>", "eval")
                _result = eval(_expr, _ns)
            except SyntaxError:
                pass
        except SystemExit:
            pass
        except BaseException as _exc:
            print(str(_exc), file=sys.stderr)
            sys.exit(1)

        # Sentinel for return value
        try:
            _payload = json.dumps(_result)
        except (TypeError, ValueError):
            _payload = "null"
        print("\\x00__SANDBOX_RESULT__:" + _payload)
    """)
