"""Tests for the sandboxed code execution engine."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cortex.tools.code_sandbox import (
    ALLOWED_MODULES,
    BLOCKED_MODULES,
    CodeResult,
    CodeSandbox,
    ExecutionResult,
    ImportResult,
    SyntaxResult,
    _extract_python_code,
)


@pytest.fixture
def sandbox() -> CodeSandbox:
    return CodeSandbox(timeout=10, memory_mb=128)


# ── Syntax validation ─────────────────────────────────────────────────────


class TestValidateSyntax:
    def test_valid_code(self) -> None:
        r = CodeSandbox.validate_syntax("x = 1 + 2\nprint(x)")
        assert r.valid
        assert r.error is None
        assert r.line is None

    def test_empty_code(self) -> None:
        r = CodeSandbox.validate_syntax("")
        assert r.valid

    def test_syntax_error(self) -> None:
        r = CodeSandbox.validate_syntax("def foo(\n")
        assert not r.valid
        assert r.error is not None
        assert r.line is not None

    def test_syntax_error_line_number(self) -> None:
        code = "x = 1\ny = )\n"
        r = CodeSandbox.validate_syntax(code)
        assert not r.valid
        assert r.line == 2

    def test_multiline_valid(self) -> None:
        code = "for i in range(10):\n    print(i)\n"
        r = CodeSandbox.validate_syntax(code)
        assert r.valid


# ── Import checking ───────────────────────────────────────────────────────


class TestCheckImports:
    def test_allowed_imports(self) -> None:
        code = "import math\nimport json\nfrom collections import Counter"
        r = CodeSandbox.check_imports(code)
        assert r.allowed
        assert r.blocked == []

    def test_blocked_import(self) -> None:
        code = "import os"
        r = CodeSandbox.check_imports(code)
        assert not r.allowed
        assert "os" in r.blocked

    def test_blocked_subprocess(self) -> None:
        code = "import subprocess"
        r = CodeSandbox.check_imports(code)
        assert not r.allowed
        assert "subprocess" in r.blocked

    def test_blocked_from_import(self) -> None:
        code = "from os.path import join"
        r = CodeSandbox.check_imports(code)
        assert not r.allowed
        assert "os" in r.blocked

    def test_multiple_blocked(self) -> None:
        code = "import os\nimport subprocess\nimport socket"
        r = CodeSandbox.check_imports(code)
        assert not r.allowed
        assert set(r.blocked) == {"os", "subprocess", "socket"}

    def test_unknown_import(self) -> None:
        code = "import numpy"
        r = CodeSandbox.check_imports(code)
        assert r.allowed  # unknown != blocked
        assert "numpy" in r.unknown

    def test_no_imports(self) -> None:
        code = "x = 1 + 2"
        r = CodeSandbox.check_imports(code)
        assert r.allowed
        assert r.blocked == []
        assert r.unknown == []

    def test_syntax_error_returns_not_allowed(self) -> None:
        r = CodeSandbox.check_imports("def (")
        assert not r.allowed

    def test_allowed_set_nonempty(self) -> None:
        assert len(ALLOWED_MODULES) > 10

    def test_blocked_set_nonempty(self) -> None:
        assert len(BLOCKED_MODULES) > 5


# ── Code execution ────────────────────────────────────────────────────────


class TestExecute:
    async def test_simple_print(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("print('hello')")
        assert r.success
        assert "hello" in r.stdout
        assert r.stderr == ""

    async def test_math_expression(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("result = 6 * 7\nprint(result)")
        assert r.success
        assert "42" in r.stdout

    async def test_return_value(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("2 + 2")
        assert r.success
        assert r.return_value == 4

    async def test_multiline(self, sandbox: CodeSandbox) -> None:
        code = "total = 0\nfor i in range(5):\n    total += i\nprint(total)"
        r = await sandbox.execute(code)
        assert r.success
        assert "10" in r.stdout

    async def test_import_math(self, sandbox: CodeSandbox) -> None:
        code = "import math\nprint(math.pi)"
        r = await sandbox.execute(code)
        assert r.success
        assert "3.14" in r.stdout

    async def test_runtime_error(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("1 / 0")
        assert not r.success
        assert "division by zero" in r.stderr.lower()

    async def test_name_error(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("print(undefined_variable)")
        assert not r.success
        assert "undefined_variable" in r.stderr

    async def test_syntax_error_caught(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("def (")
        assert not r.success
        assert "SyntaxError" in r.stderr

    async def test_blocked_import_caught(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("import os\nos.listdir('.')")
        assert not r.success
        assert "Blocked imports" in r.stderr

    async def test_timeout(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("while True: pass", timeout=2)
        assert not r.success
        assert r.timed_out

    async def test_elapsed_tracked(self, sandbox: CodeSandbox) -> None:
        r = await sandbox.execute("x = 1")
        assert r.success
        assert r.elapsed >= 0

    async def test_stderr_captured(self, sandbox: CodeSandbox) -> None:
        code = "import sys\nprint('oops', file=sys.stderr)"
        # sys is blocked, so this should fail pre-flight
        r = await sandbox.execute(code)
        assert not r.success

    async def test_json_import(self, sandbox: CodeSandbox) -> None:
        code = "import json\nprint(json.dumps({'a': 1}))"
        r = await sandbox.execute(code)
        assert r.success
        assert '"a"' in r.stdout

    async def test_collections_counter(self, sandbox: CodeSandbox) -> None:
        code = "from collections import Counter\nc = Counter('aabbc')\nprint(c.most_common(1))"
        r = await sandbox.execute(code)
        assert r.success
        assert "a" in r.stdout


# ── Timeout enforcement ───────────────────────────────────────────────────


class TestTimeout:
    async def test_infinite_loop_killed(self) -> None:
        sb = CodeSandbox(timeout=2)
        r = await sb.execute("while True: pass")
        assert r.timed_out
        assert not r.success
        assert r.elapsed >= 1.5

    async def test_sleep_within_limit(self) -> None:
        sb = CodeSandbox(timeout=10)
        r = await sb.execute("import time\nprint('done')")
        # time is allowed
        assert r.success


# ── Self-correction loop ─────────────────────────────────────────────────


class TestGenerateAndVerify:
    async def test_success_first_attempt(self, sandbox: CodeSandbox) -> None:
        """LLM returns correct code on the first try."""
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value={
            "content": "```python\nprint('hello')\n```",
        })
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "print hello"}],
        )
        assert result.execution.success
        assert result.attempts == 1
        assert result.syntax_valid
        assert "hello" in result.execution.stdout

    async def test_self_correction(self, sandbox: CodeSandbox) -> None:
        """LLM fails first, then succeeds on retry."""
        provider = AsyncMock()
        # First call: bad code (NameError), second call: fixed code
        provider.chat = AsyncMock(side_effect=[
            {"content": "```python\nprint(undefined_var)\n```"},
            {"content": "```python\nprint('fixed')\n```"},
        ])
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "print something"}],
        )
        assert result.execution.success
        assert result.attempts == 2
        assert "fixed" in result.execution.stdout

    async def test_syntax_correction(self, sandbox: CodeSandbox) -> None:
        """LLM produces invalid syntax first, then fixes it."""
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=[
            {"content": "```python\ndef foo(\n```"},
            {"content": "```python\ndef foo():\n    return 42\nprint(foo())\n```"},
        ])
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "define a function"}],
        )
        assert result.execution.success
        assert result.attempts == 2
        assert result.syntax_valid

    async def test_blocked_import_correction(self, sandbox: CodeSandbox) -> None:
        """LLM uses a blocked import first, then rewrites without it."""
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=[
            {"content": "```python\nimport os\nprint(os.getcwd())\n```"},
            {"content": "```python\nprint('safe code')\n```"},
        ])
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "show current directory"}],
        )
        assert result.execution.success
        assert result.attempts == 2

    async def test_max_attempts_exhausted(self, sandbox: CodeSandbox) -> None:
        """All attempts fail — returns last failure."""
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value={
            "content": "```python\n1/0\n```",
        })
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "divide by zero"}],
            max_attempts=2,
        )
        assert not result.execution.success
        assert result.attempts == 2

    async def test_no_code_block(self, sandbox: CodeSandbox) -> None:
        """LLM doesn't provide a code block — asks for one, then gets it."""
        provider = AsyncMock()
        provider.chat = AsyncMock(side_effect=[
            {"content": "Sure! Just use print('hello')."},
            {"content": "```python\nprint('hello')\n```"},
        ])
        result = await sandbox.generate_and_verify(
            provider,
            [{"role": "user", "content": "print hello"}],
        )
        assert result.execution.success
        assert result.attempts == 2


# ── Code extraction ──────────────────────────────────────────────────────


class TestExtractPythonCode:
    def test_basic_block(self) -> None:
        text = "Here is code:\n```python\nprint('hi')\n```\nDone."
        assert _extract_python_code(text) == "print('hi')"

    def test_py_alias(self) -> None:
        text = "```py\nx = 1\n```"
        assert _extract_python_code(text) == "x = 1"

    def test_no_code_block(self) -> None:
        assert _extract_python_code("just some text") == ""

    def test_multiple_blocks_returns_first(self) -> None:
        text = "```python\nfirst\n```\n```python\nsecond\n```"
        assert _extract_python_code(text) == "first"

    def test_unclosed_block(self) -> None:
        text = "```python\nprint('open')\n"
        assert _extract_python_code(text) == "print('open')"


# ── Dataclass smoke tests ────────────────────────────────────────────────


class TestDataclasses:
    def test_execution_result_defaults(self) -> None:
        r = ExecutionResult(
            success=True, stdout="", stderr="",
            return_value=None, elapsed=0.0,
        )
        assert r.timed_out is False

    def test_syntax_result_defaults(self) -> None:
        r = SyntaxResult(valid=True)
        assert r.error is None
        assert r.line is None

    def test_import_result_defaults(self) -> None:
        r = ImportResult(allowed=True)
        assert r.blocked == []
        assert r.unknown == []

    def test_code_result_fields(self) -> None:
        er = ExecutionResult(
            success=True, stdout="ok", stderr="",
            return_value=42, elapsed=0.1,
        )
        cr = CodeResult(code="x=1", execution=er, attempts=1, syntax_valid=True)
        assert cr.attempts == 1
        assert cr.execution.return_value == 42
