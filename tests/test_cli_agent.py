"""Tests for the Atlas CLI autonomous agent — ReAct loop, tool parsing, etc."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.agent import (
    _process_input_files,
    _read_piped_stdin,
    _truncate,
    build_system_prompt,
    extract_thinking,
    parse_tool_calls,
    run_agent,
)
from cortex.cli.tools import AgentTool, ToolRegistry, ToolResult


# ── Helpers ─────────────────────────────────────────────────────────


class _DummyTool(AgentTool):
    """Minimal tool for prompt-building tests."""

    tool_id = "dummy"
    description = "A dummy tool for testing"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "arg1": {
                "type": "string",
                "description": "First argument",
            },
        },
        "required": ["arg1"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(success=True, output=f"dummy ran with {params}")


class _ConfirmTool(AgentTool):
    """Tool that requires confirmation."""

    tool_id = "danger"
    description = "A dangerous tool"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Target"},
        },
        "required": ["target"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(success=True, output="danger executed")


async def _mock_stream(*chunks: str) -> AsyncGenerator[str, None]:
    """Return an async generator that yields *chunks*."""
    for c in chunks:
        yield c


def _make_provider(responses: list[str]) -> MagicMock:
    """Create a mock LLM provider that returns *responses* in order.

    Each call to ``chat()`` returns an async generator yielding the next
    response as a single chunk.
    """
    provider = MagicMock()
    call_count = {"n": 0}

    async def _chat(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        idx = call_count["n"]
        call_count["n"] += 1
        text = responses[idx] if idx < len(responses) else "Done."
        return _mock_stream(text)

    provider.chat = _chat
    return provider


# ── parse_tool_calls ────────────────────────────────────────────────


class TestParseToolCalls:
    """Tests for the ``<tool_call>`` parser."""

    def test_single_call(self):
        text = textwrap.dedent("""\
            I will read the file.
            <tool_call>
            {"tool": "file_read", "params": {"path": "foo.py"}}
            </tool_call>
        """)
        calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["tool"] == "file_read"
        assert calls[0]["params"] == {"path": "foo.py"}

    def test_multiple_calls(self):
        text = (
            '<tool_call>{"tool": "a", "params": {"x": 1}}</tool_call>\n'
            'some text\n'
            '<tool_call>{"tool": "b", "params": {}}</tool_call>'
        )
        calls = parse_tool_calls(text)
        assert len(calls) == 2
        assert calls[0]["tool"] == "a"
        assert calls[1]["tool"] == "b"

    def test_no_calls(self):
        text = "This is just a plain response with no tool calls."
        assert parse_tool_calls(text) == []

    def test_malformed_json_skipped(self):
        text = '<tool_call>{not valid json}</tool_call>'
        assert parse_tool_calls(text) == []

    def test_missing_tool_key_skipped(self):
        text = '<tool_call>{"action": "read"}</tool_call>'
        assert parse_tool_calls(text) == []

    def test_missing_params_defaults_to_empty_dict(self):
        text = '<tool_call>{"tool": "file_list"}</tool_call>'
        calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["params"] == {}

    def test_multiline_json(self):
        text = textwrap.dedent("""\
            <tool_call>
            {
                "tool": "file_read",
                "params": {
                    "path": "src/main.py",
                    "start_line": 10,
                    "end_line": 20
                }
            }
            </tool_call>
        """)
        calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["params"]["start_line"] == 10

    def test_params_null_treated_as_empty(self):
        text = '<tool_call>{"tool": "file_list", "params": null}</tool_call>'
        calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["params"] == {}


# ── extract_thinking ────────────────────────────────────────────────


class TestExtractThinking:
    def test_strips_tool_calls(self):
        text = (
            'Let me read the file.\n'
            '<tool_call>{"tool": "file_read", "params": {"path": "x"}}</tool_call>\n'
            'And also this.'
        )
        result = extract_thinking(text)
        assert "<tool_call>" not in result
        assert "Let me read the file." in result
        assert "And also this." in result

    def test_plain_text_unchanged(self):
        text = "No tools needed here."
        assert extract_thinking(text) == text

    def test_only_tool_call_returns_empty(self):
        text = '<tool_call>{"tool": "x", "params": {}}</tool_call>'
        assert extract_thinking(text) == ""


# ── _truncate ───────────────────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "a" * 200
        result = _truncate(text, 50)
        assert result.startswith("a" * 50)
        assert "150 chars truncated" in result

    def test_exact_limit_unchanged(self):
        text = "b" * 100
        assert _truncate(text, 100) == text


# ── build_system_prompt ─────────────────────────────────────────────


class TestBuildSystemPrompt:
    def test_includes_tool_id(self):
        reg = ToolRegistry()
        reg.register(_DummyTool())
        prompt = build_system_prompt(reg)
        assert "dummy" in prompt
        assert "A dummy tool for testing" in prompt

    def test_includes_all_registered_tools(self):
        reg = ToolRegistry()
        reg.register(_DummyTool())
        reg.register(_ConfirmTool())
        prompt = build_system_prompt(reg)
        assert "dummy" in prompt
        assert "danger" in prompt
        assert "REQUIRES USER CONFIRMATION" in prompt

    def test_includes_parameter_descriptions(self):
        reg = ToolRegistry()
        reg.register(_DummyTool())
        prompt = build_system_prompt(reg)
        assert "arg1" in prompt
        assert "(required)" in prompt

    def test_default_registry_all_tools_listed(self):
        reg = ToolRegistry()
        reg.register(_DummyTool())
        prompt = build_system_prompt(reg)
        # Check structural elements are present
        assert "<tool_call>" in prompt
        assert "tool_name" in prompt


# ── _process_input_files ────────────────────────────────────────────


class TestProcessInputFiles:
    def test_text_file(self, tmp_path: Path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello, world!\n")
        result = _process_input_files([str(f)])
        assert "Hello, world!" in result
        assert f"--- File: {f} ---" in result

    def test_missing_file(self):
        result = _process_input_files(["/nonexistent/file.txt"])
        assert "[File not found:" in result

    def test_image_file_metadata(self, tmp_path: Path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG")
        result = _process_input_files([str(img)])
        assert "[Image file:" in result

    def test_pdf_file_metadata(self, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        result = _process_input_files([str(pdf)])
        assert "[PDF file:" in result
        assert "pdftotext" in result

    def test_large_file_truncated(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 100_000)
        result = _process_input_files([str(f)])
        assert "truncated" in result

    def test_multiple_files(self, tmp_path: Path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("# file a\n")
        b.write_text("# file b\n")
        result = _process_input_files([str(a), str(b)])
        assert "file a" in result
        assert "file b" in result

    def test_empty_list(self):
        assert _process_input_files([]) == ""


# ── _read_piped_stdin ───────────────────────────────────────────────


class TestReadPipedStdin:
    def test_returns_none_when_tty(self):
        with patch("cortex.cli.agent.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = True
            assert _read_piped_stdin() is None

    def test_reads_piped_data(self):
        with patch("cortex.cli.agent.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = False
            mock_sys.stdin.read.return_value = "piped data\n"
            assert _read_piped_stdin() == "piped data\n"

    def test_exception_returns_none(self):
        with patch("cortex.cli.agent.sys") as mock_sys:
            mock_sys.stdin.isatty.side_effect = OSError("broken")
            assert _read_piped_stdin() is None


# ── run_agent — full ReAct loop ─────────────────────────────────────


class TestRunAgent:
    """Integration tests for the ReAct loop with mocked LLM provider."""

    @pytest.mark.asyncio
    async def test_agent_completes_single_turn(self):
        """LLM gives a plain-text answer with no tool calls → done."""
        provider = _make_provider(["The answer is 42."])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
        ):
            code = await run_agent(task="What is the answer?", max_iterations=5)

        assert code == 0

    @pytest.mark.asyncio
    async def test_agent_uses_tool_then_completes(self):
        """LLM makes a tool call, gets result, then gives final answer."""
        tool_call_resp = (
            'Let me read the file.\n'
            '<tool_call>\n'
            '{"tool": "file_list", "params": {}}\n'
            '</tool_call>'
        )
        final_resp = "The directory contains several files. All done."

        provider = _make_provider([tool_call_resp, final_resp])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
        ):
            reg = ToolRegistry()
            reg.register(_DummyTool())
            from cortex.cli.tools.files import FileListTool
            reg.register(FileListTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="List files", max_iterations=5)

        assert code == 0

    @pytest.mark.asyncio
    async def test_max_iterations_stops_agent(self):
        """Agent keeps calling tools until max_iterations is hit."""
        tool_resp = '<tool_call>{"tool": "dummy", "params": {"arg1": "x"}}</tool_call>'
        provider = _make_provider([tool_resp] * 5)

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
        ):
            reg = ToolRegistry()
            reg.register(_DummyTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="loop forever", max_iterations=3)

        assert code == 1

    @pytest.mark.asyncio
    async def test_provider_failure_returns_1(self):
        """When the LLM provider cannot be initialised, return 1."""
        with (
            patch(
                "cortex.providers.get_provider",
                side_effect=RuntimeError("no backend"),
            ),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
        ):
            code = await run_agent(task="anything")

        assert code == 1

    @pytest.mark.asyncio
    async def test_streaming_error_returns_1(self):
        """If the streaming generator raises, the agent returns 1."""
        provider = MagicMock()

        async def _bad_stream() -> AsyncGenerator[str, None]:
            yield "start"
            raise RuntimeError("stream broke")

        async def _chat(*a: Any, **kw: Any) -> AsyncGenerator[str, None]:
            return _bad_stream()

        provider.chat = _chat

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
        ):
            code = await run_agent(task="boom", max_iterations=2)

        assert code == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_reports_error(self):
        """Calling a tool not in the registry results in an error result."""
        tool_resp = (
            '<tool_call>{"tool": "nonexistent", "params": {}}</tool_call>'
        )
        final_resp = "Tool was unknown, done."

        provider = _make_provider([tool_resp, final_resp])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
        ):
            reg = ToolRegistry()
            reg.register(_DummyTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="use bad tool", max_iterations=5)

        assert code == 0

    @pytest.mark.asyncio
    async def test_confirmation_denied(self):
        """When user denies confirmation, the tool is not executed."""
        tool_resp = (
            '<tool_call>{"tool": "danger", "params": {"target": "/"}}</tool_call>'
        )
        final_resp = "User denied, I'll stop."

        provider = _make_provider([tool_resp, final_resp])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
            patch("builtins.input", return_value="n"),
        ):
            reg = ToolRegistry()
            reg.register(_ConfirmTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="do something dangerous", max_iterations=5)

        assert code == 0

    @pytest.mark.asyncio
    async def test_confirmation_approved(self):
        """When user approves confirmation, the tool executes."""
        tool_resp = (
            '<tool_call>{"tool": "danger", "params": {"target": "ok"}}</tool_call>'
        )
        final_resp = "Done with the dangerous operation."

        provider = _make_provider([tool_resp, final_resp])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
            patch("builtins.input", return_value="y"),
        ):
            reg = ToolRegistry()
            reg.register(_ConfirmTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="do it safely", max_iterations=5)

        assert code == 0

    @pytest.mark.asyncio
    async def test_file_context_passed_to_task(self, tmp_path: Path):
        """When --file is given, its contents appear in the task context."""
        f = tmp_path / "spec.md"
        f.write_text("# Spec\nBuild a widget.\n")

        captured: list[Any] = []
        provider = MagicMock()

        async def _chat(
            messages: list[dict[str, str]], **kw: Any,
        ) -> AsyncGenerator[str, None]:
            captured.append(messages)
            return _mock_stream("All done, widget built.")

        provider.chat = _chat

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
        ):
            code = await run_agent(
                task="implement this", files=[str(f)], max_iterations=3,
            )

        assert code == 0
        user_msg = captured[0][1]["content"]
        assert "Build a widget." in user_msg

    @pytest.mark.asyncio
    async def test_dict_response_handled(self):
        """Provider returning a dict (non-streaming) is handled gracefully."""
        provider = MagicMock()

        async def _chat(*a: Any, **kw: Any) -> dict[str, Any]:
            return {"content": "Non-streamed response. Done."}

        provider.chat = _chat

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
        ):
            code = await run_agent(task="test dict", max_iterations=3)

        assert code == 0

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_one_response(self):
        """LLM returns multiple tool calls in a single response."""
        multi_call = (
            'I need to check two things.\n'
            '<tool_call>{"tool": "dummy", "params": {"arg1": "a"}}</tool_call>\n'
            '<tool_call>{"tool": "dummy", "params": {"arg1": "b"}}</tool_call>'
        )
        final_resp = "Both calls done. Summary complete."

        provider = _make_provider([multi_call, final_resp])

        with (
            patch("cortex.providers.get_provider", return_value=provider),
            patch("cortex.cli.agent._read_piped_stdin", return_value=None),
            patch("cortex.cli.agent.get_default_registry") as mock_reg_fn,
        ):
            reg = ToolRegistry()
            reg.register(_DummyTool())
            mock_reg_fn.return_value = reg

            code = await run_agent(task="do two things", max_iterations=5)

        assert code == 0
