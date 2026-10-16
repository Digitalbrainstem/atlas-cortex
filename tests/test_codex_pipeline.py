"""Tests for the Codex validation pipeline."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock

import pytest

from cortex.tools.codex_pipeline import (
    CodexPipeline,
    CodexResult,
    CodexReview,
    extract_python_code,
    run_static_analysis,
)
from cortex.tools.code_sandbox import CodeSandbox


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline() -> CodexPipeline:
    return CodexPipeline()


@pytest.fixture()
def mock_provider() -> AsyncMock:
    """LLM provider mock that returns 'LGTM' for reviews by default."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value={"content": "LGTM"})
    return provider


# ---------------------------------------------------------------------------
# Stage 1 — Syntax check
# ---------------------------------------------------------------------------


class TestSyntaxCheck:
    async def test_valid_code_passes(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("x = 1 + 2\nprint(x)")
        assert "syntax" in result.stages_passed

    async def test_syntax_error_caught(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("def f(\n")
        assert not result.valid
        assert "syntax" in result.stages_failed
        assert any("Syntax error" in w for w in result.warnings)

    async def test_syntax_error_includes_line(self, pipeline: CodexPipeline) -> None:
        code = "x = 1\ny = )\n"
        result = await pipeline.validate(code)
        assert not result.valid
        assert any("line" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Stage 2 — Import check
# ---------------------------------------------------------------------------


class TestImportCheck:
    async def test_allowed_imports_pass(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("import math\nprint(math.pi)")
        assert "imports" in result.stages_passed

    async def test_blocked_import_caught(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("import os\nos.listdir('.')")
        assert not result.valid
        assert "imports" in result.stages_failed
        assert any("os" in w for w in result.warnings)

    async def test_unknown_import_warns(self, pipeline: CodexPipeline) -> None:
        code = "import some_nonexistent_lib\nprint('hi')"
        result = await pipeline.validate(code)
        # Unknown imports are warnings, not failures
        assert "imports" in result.stages_passed
        assert any("Unknown imports" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Stage 3 — Static analysis
# ---------------------------------------------------------------------------


class TestStaticAnalysis:
    def test_unused_import_warning(self) -> None:
        code = "import math\nx = 1"
        warnings = run_static_analysis(code)
        assert any("unused" in w.lower() for w in warnings)

    def test_unreachable_code_warning(self) -> None:
        code = textwrap.dedent("""\
            def f():
                return 1
                x = 2
        """)
        warnings = run_static_analysis(code)
        assert any("unreachable" in w.lower() for w in warnings)

    def test_stub_function_warning(self) -> None:
        code = "def placeholder():\n    pass\n"
        warnings = run_static_analysis(code)
        assert any("stub" in w.lower() for w in warnings)

    def test_missing_return_type_hint_warning(self) -> None:
        code = "def add(a, b):\n    return a + b\n"
        warnings = run_static_analysis(code)
        assert any("return type hint" in w.lower() for w in warnings)

    def test_no_warnings_for_clean_code(self) -> None:
        code = textwrap.dedent("""\
            import math
            def area(r: float) -> float:
                return math.pi * r * r
        """)
        warnings = run_static_analysis(code)
        assert warnings == []

    async def test_static_analysis_in_pipeline(self, pipeline: CodexPipeline) -> None:
        """Warnings surface but don't fail the pipeline."""
        code = "import json\nx = 1"
        result = await pipeline.validate(code)
        # Should still pass — static is warning-only
        assert result.valid
        assert "static" in result.stages_passed

    def test_syntax_error_skips_analysis(self) -> None:
        warnings = run_static_analysis("def (oops")
        assert warnings == []

    def test_ellipsis_stub_detected(self) -> None:
        code = "def todo():\n    ...\n"
        warnings = run_static_analysis(code)
        assert any("stub" in w.lower() for w in warnings)

    def test_docstring_plus_pass_is_stub(self) -> None:
        code = textwrap.dedent('''\
            def todo():
                """Will implement later."""
                pass
        ''')
        warnings = run_static_analysis(code)
        assert any("stub" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Stage 4 — Sandbox execution
# ---------------------------------------------------------------------------


class TestSandboxExecution:
    async def test_runnable_script_executes(self, pipeline: CodexPipeline) -> None:
        code = "print('hello')"
        result = await pipeline.validate(code)
        assert result.valid
        assert "execute" in result.stages_passed

    async def test_execution_failure_caught(self, pipeline: CodexPipeline) -> None:
        code = "x = 1 / 0"
        result = await pipeline.validate(code)
        assert not result.valid
        assert "execute" in result.stages_failed
        assert any("Execution failed" in w for w in result.warnings)

    async def test_library_module_skips_execution(
        self, pipeline: CodexPipeline,
    ) -> None:
        code = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b

            def sub(a: int, b: int) -> int:
                return a - b

            class Calculator:
                pass
        """)
        result = await pipeline.validate(code)
        assert result.valid
        # execution should be skipped — no "execute" in either list
        assert "execute" not in result.stages_passed
        assert "execute" not in result.stages_failed


# ---------------------------------------------------------------------------
# Multi-round fix (validate_and_fix)
# ---------------------------------------------------------------------------


class TestValidateAndFix:
    async def test_already_valid_code(
        self,
        pipeline: CodexPipeline,
        mock_provider: AsyncMock,
    ) -> None:
        result = await pipeline.validate_and_fix(
            "x = 1 + 2\nprint(x)", mock_provider,
        )
        assert result.valid
        assert result.rounds == 1

    async def test_fix_syntax_error(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        broken = "def f(\n"
        fixed = "def f():\n    return 1\n"

        provider = AsyncMock()
        # First chat call: fix round returns corrected code
        # Second chat call: review returns LGTM
        provider.chat = AsyncMock(
            side_effect=[
                {"content": f"```python\n{fixed}\n```"},
                {"content": "LGTM"},
            ],
        )

        result = await pipeline.validate_and_fix(broken, provider)
        assert result.valid
        assert result.original_code == broken
        assert len(result.changes_made) >= 1

    async def test_fix_blocked_import(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        broken = "import os\nos.listdir('.')"
        fixed = "import json\nprint(json.dumps([1,2,3]))"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            side_effect=[
                {"content": f"```python\n{fixed}\n```"},
                {"content": "LGTM"},
            ],
        )

        result = await pipeline.validate_and_fix(broken, provider)
        assert result.valid
        assert "os" not in result.code

    async def test_max_rounds_respected(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        broken = "def f(\n"

        provider = AsyncMock()
        # LLM keeps returning broken code
        provider.chat = AsyncMock(
            return_value={"content": "```python\ndef f(\n```"},
        )

        result = await pipeline.validate_and_fix(
            broken, provider, max_rounds=2,
        )
        assert not result.valid
        assert result.rounds <= 2

    async def test_llm_returns_no_code_block(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        broken = "def f(\n"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value={"content": "I can't fix that, sorry."},
        )

        result = await pipeline.validate_and_fix(broken, provider)
        assert not result.valid


# ---------------------------------------------------------------------------
# Stage 5 — LLM review
# ---------------------------------------------------------------------------


class TestLLMReview:
    async def test_lgtm_means_no_changes(
        self,
        pipeline: CodexPipeline,
        mock_provider: AsyncMock,
    ) -> None:
        code = "x = 1 + 2"
        review = await pipeline.review_with_llm(code, mock_provider)
        assert not review.changed
        assert review.reviewed_code == code

    async def test_review_returns_corrected_code(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        original = "x = 1 + 2"
        corrected = "x: int = 1 + 2"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value={
                "content": (
                    f"```python\n{corrected}\n```\n"
                    "- Added type annotation\n"
                ),
            },
        )

        review = await pipeline.review_with_llm(original, provider)
        assert review.changed
        assert review.reviewed_code == corrected
        assert len(review.suggestions) >= 1

    async def test_review_with_context(
        self,
        pipeline: CodexPipeline,
        mock_provider: AsyncMock,
    ) -> None:
        await pipeline.review_with_llm(
            "x = 1", mock_provider, context="This is a test",
        )
        call_args = mock_provider.chat.call_args
        messages = call_args[0][0]
        user_msg = messages[1]["content"]
        assert "This is a test" in user_msg

    async def test_review_integrated_in_validate_and_fix(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        code = "x = 1 + 2\nprint(x)"
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value={"content": "LGTM"})

        result = await pipeline.validate_and_fix(code, provider)
        assert result.valid
        assert "review" in result.stages_passed

    async def test_review_changes_revalidated(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        code = "print('hello')"
        reviewed = "print('hello world')"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value={
                "content": f"```python\n{reviewed}\n```\n- improved message",
            },
        )

        result = await pipeline.validate_and_fix(code, provider)
        assert result.valid
        assert result.code == reviewed
        assert "review" in result.stages_passed

    async def test_bad_review_keeps_original(
        self,
        pipeline: CodexPipeline,
    ) -> None:
        """If the LLM review introduces a bug, keep the pre-review code."""
        code = "print('hello')"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            return_value={
                "content": "```python\ndef f(\n```\n- broke it",
            },
        )

        result = await pipeline.validate_and_fix(code, provider)
        assert result.valid
        assert result.code == code
        assert any("failed validation" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


class TestResultTracking:
    async def test_stages_tracked(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("print(42)")
        assert "syntax" in result.stages_passed
        assert "imports" in result.stages_passed
        assert "static" in result.stages_passed

    async def test_original_code_preserved(
        self,
        pipeline: CodexPipeline,
        mock_provider: AsyncMock,
    ) -> None:
        code = "x = 1 + 2\nprint(x)"
        result = await pipeline.validate_and_fix(code, mock_provider)
        assert result.original_code == code

    async def test_changes_recorded(self, pipeline: CodexPipeline) -> None:
        broken = "import os\nos.listdir('.')"
        fixed = "print('safe')"

        provider = AsyncMock()
        provider.chat = AsyncMock(
            side_effect=[
                {"content": f"```python\n{fixed}\n```"},
                {"content": "LGTM"},
            ],
        )

        result = await pipeline.validate_and_fix(broken, provider)
        assert len(result.changes_made) >= 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_code(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("")
        # Empty string is valid Python but has nothing to execute
        assert result.valid

    async def test_just_comments(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate("# just a comment\n# nothing here\n")
        assert result.valid

    async def test_partial_code(self, pipeline: CodexPipeline) -> None:
        # A standalone expression is valid Python
        result = await pipeline.validate("1 + 2")
        assert result.valid

    async def test_multiline_string_only(self, pipeline: CodexPipeline) -> None:
        result = await pipeline.validate('"""A docstring."""')
        assert result.valid


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------


class TestCodeExtraction:
    def test_extract_python_block(self) -> None:
        text = "Here is code:\n```python\nx = 1\n```\nDone."
        assert extract_python_code(text) == "x = 1"

    def test_extract_py_alias(self) -> None:
        text = "```py\ny = 2\n```"
        assert extract_python_code(text) == "y = 2"

    def test_no_block_returns_empty(self) -> None:
        assert extract_python_code("no code here") == ""

    def test_unclosed_block(self) -> None:
        text = "```python\nx = 1"
        assert extract_python_code(text) == "x = 1"


# ---------------------------------------------------------------------------
# Dataclass smoke tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_codex_result_defaults(self) -> None:
        r = CodexResult(code="x=1", valid=True)
        assert r.stages_passed == []
        assert r.stages_failed == []
        assert r.warnings == []
        assert r.rounds == 1
        assert r.original_code == ""
        assert r.changes_made == []

    def test_codex_review_defaults(self) -> None:
        r = CodexReview(reviewed_code="x=1")
        assert r.suggestions == []
        assert not r.changed
