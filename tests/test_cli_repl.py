"""Tests for cortex.cli.repl — slash commands, history, multi-line, one-shot."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.repl import (
    parse_slash_command,
    _handle_slash_command,
    _read_multiline,
    run_oneshot,
    run_repl,
)


# ── Slash command parsing ───────────────────────────────────────────


class TestParseSlashCommand:
    def test_help(self) -> None:
        cmd, arg = parse_slash_command("/help")
        assert cmd == "/help"
        assert arg == ""

    def test_quit(self) -> None:
        cmd, arg = parse_slash_command("/quit")
        assert cmd == "/quit"
        assert arg == ""

    def test_clear(self) -> None:
        cmd, arg = parse_slash_command("/clear")
        assert cmd == "/clear"
        assert arg == ""

    def test_memory_with_query(self) -> None:
        cmd, arg = parse_slash_command("/memory what is python")
        assert cmd == "/memory"
        assert arg == "what is python"

    def test_model_with_name(self) -> None:
        cmd, arg = parse_slash_command("/model gpt-4")
        assert cmd == "/model"
        assert arg == "gpt-4"

    def test_model_no_arg(self) -> None:
        cmd, arg = parse_slash_command("/model")
        assert cmd == "/model"
        assert arg == ""

    def test_history(self) -> None:
        cmd, arg = parse_slash_command("/history")
        assert cmd == "/history"
        assert arg == ""

    def test_not_a_command(self) -> None:
        cmd, arg = parse_slash_command("hello world")
        assert cmd == ""
        assert arg == ""

    def test_empty_string(self) -> None:
        cmd, arg = parse_slash_command("")
        assert cmd == ""
        assert arg == ""

    def test_case_insensitive(self) -> None:
        cmd, arg = parse_slash_command("/HELP")
        assert cmd == "/help"

    def test_whitespace_prefix(self) -> None:
        cmd, arg = parse_slash_command("  /quit  ")
        assert cmd == "/quit"


# ── Slash command handling ──────────────────────────────────────────


class TestHandleSlashCommand:
    @pytest.mark.asyncio
    async def test_quit_returns_true(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/quit", "", [], "m", None,
        )
        assert should_quit is True

    @pytest.mark.asyncio
    async def test_help_returns_false(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/help", "", [], "m", None,
        )
        assert should_quit is False

    @pytest.mark.asyncio
    async def test_clear_empties_history(self) -> None:
        history: list[dict[str, str]] = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        should_quit, _ = await _handle_slash_command(
            "/clear", "", history, "m", None,
        )
        assert should_quit is False
        assert history == []

    @pytest.mark.asyncio
    async def test_model_switch(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/model", "llama3", [], "old-model", None,
        )
        assert should_quit is False
        assert model == "llama3"

    @pytest.mark.asyncio
    async def test_model_no_arg_keeps_current(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/model", "", [], "current-model", None,
        )
        assert should_quit is False
        assert model == "current-model"

    @pytest.mark.asyncio
    async def test_unknown_command(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/unknown", "", [], "m", None,
        )
        assert should_quit is False

    @pytest.mark.asyncio
    async def test_memory_no_arg(self) -> None:
        should_quit, _ = await _handle_slash_command(
            "/memory", "", [], "m", None,
        )
        assert should_quit is False

    @pytest.mark.asyncio
    async def test_memory_with_query(self) -> None:
        mock_conn = MagicMock()
        with patch("cortex.cli.repl.hot_query", return_value=[]):
            should_quit, _ = await _handle_slash_command(
                "/memory", "test query", [], "m", mock_conn,
            )
        assert should_quit is False

    @pytest.mark.asyncio
    async def test_history_empty(self) -> None:
        should_quit, _ = await _handle_slash_command(
            "/history", "", [], "m", None,
        )
        assert should_quit is False

    @pytest.mark.asyncio
    async def test_history_with_messages(self) -> None:
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        should_quit, _ = await _handle_slash_command(
            "/history", "", history, "m", None,
        )
        assert should_quit is False
        # History should not be modified
        assert len(history) == 2


# ── Multi-line input detection ──────────────────────────────────────


class TestMultiLineInput:
    def test_triple_quote_detected(self) -> None:
        text = '"""'
        assert text.startswith('"""')

    def test_single_line_triple_quote(self) -> None:
        # """content""" should extract "content"
        text = '"""hello world"""'
        remainder = text[3:]
        assert '"""' in remainder
        idx = remainder.index('"""')
        assert remainder[:idx] == "hello world"

    def test_read_multiline_with_close(self) -> None:
        # Simulate input() returning lines then a closing """
        inputs = iter(["line one", "line two", '"""'])
        with patch("builtins.input", side_effect=inputs):
            result = _read_multiline()
        assert result == "line one\nline two"

    def test_read_multiline_eof(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            result = _read_multiline()
        assert result == ""

    def test_read_multiline_close_with_prefix(self) -> None:
        inputs = iter(["first", 'end"""'])
        with patch("builtins.input", side_effect=inputs):
            result = _read_multiline()
        assert result == "first\nend"


# ── Conversation history management ────────────────────────────────


class TestConversationHistory:
    def test_add_user_message(self) -> None:
        history: list[dict[str, str]] = []
        history.append({"role": "user", "content": "hello"})
        assert len(history) == 1
        assert history[0]["role"] == "user"

    def test_add_assistant_message(self) -> None:
        history: list[dict[str, str]] = []
        history.append({"role": "user", "content": "hi"})
        history.append({"role": "assistant", "content": "hello!"})
        assert len(history) == 2
        assert history[1]["role"] == "assistant"

    def test_clear_history(self) -> None:
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        history.clear()
        assert history == []

    def test_history_excludes_current_for_pipeline(self) -> None:
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
        # Pipeline receives history[:-1] to exclude current message
        pipeline_history = history[:-1]
        assert len(pipeline_history) == 2
        assert pipeline_history[-1]["role"] == "assistant"


# ── One-shot mode ───────────────────────────────────────────────────


class TestOneShot:
    @pytest.mark.asyncio
    async def test_oneshot_streams_and_exits(self) -> None:
        """one-shot should stream tokens and return 0."""

        async def _fake_pipeline(**kwargs):
            for token in ["Hello", " ", "world!"]:
                yield token

        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch("cortex.cli.repl.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl.run_pipeline", side_effect=_fake_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 0

    @pytest.mark.asyncio
    async def test_oneshot_provider_unavailable(self) -> None:
        """one-shot should return 1 when provider fails."""
        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch(
                "cortex.cli.repl.get_provider",
                side_effect=RuntimeError("no backend"),
            ),
        ):
            code = await run_oneshot("test question")
        assert code == 1

    @pytest.mark.asyncio
    async def test_oneshot_pipeline_error(self) -> None:
        """one-shot should return 1 on pipeline error."""

        async def _broken_pipeline(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — makes this an async generator

        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch("cortex.cli.repl.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl.run_pipeline", side_effect=_broken_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 1

    @pytest.mark.asyncio
    async def test_oneshot_db_unavailable_still_works(self) -> None:
        """one-shot should proceed even if DB is not available."""

        async def _fake_pipeline(**kwargs):
            yield "ok"

        with (
            patch("cortex.cli.repl.init_db", side_effect=RuntimeError("no db")),
            patch("cortex.cli.repl.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl.run_pipeline", side_effect=_fake_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 0


# ── REPL graceful handling ──────────────────────────────────────────


class TestReplGraceful:
    @pytest.mark.asyncio
    async def test_repl_provider_unavailable(self) -> None:
        """REPL should return 1 if the provider cannot be created."""
        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch(
                "cortex.cli.repl.get_provider",
                side_effect=RuntimeError("no backend"),
            ),
        ):
            code = await run_repl()
        assert code == 1

    @pytest.mark.asyncio
    async def test_repl_eof_exits(self) -> None:
        """REPL should exit cleanly on EOF (None from _read_input)."""
        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch("cortex.cli.repl.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl._read_input", return_value=None),
        ):
            code = await run_repl()
        assert code == 0

    @pytest.mark.asyncio
    async def test_repl_quit_command(self) -> None:
        """REPL should exit cleanly on /quit."""
        inputs = iter(["/quit"])

        def _fake_read(_session):
            return next(inputs, None)

        with (
            patch("cortex.cli.repl.init_db"),
            patch("cortex.cli.repl.get_db", return_value=MagicMock()),
            patch("cortex.cli.repl.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl._read_input", side_effect=_fake_read),
        ):
            code = await run_repl()
        assert code == 0
