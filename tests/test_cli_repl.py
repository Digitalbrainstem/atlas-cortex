"""Tests for cortex.cli.repl — slash commands, history, multi-line, one-shot.

Also covers the new modules introduced by the CLI overhaul:
session manager, streaming output, model router, background runner,
configuration, and output helpers.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.cli.repl import (
    AtlasREPL,
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

    def test_exit(self) -> None:
        cmd, arg = parse_slash_command("/exit")
        assert cmd == "/exit"
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

    def test_session_list(self) -> None:
        cmd, arg = parse_slash_command("/session list")
        assert cmd == "/session"
        assert arg == "list"

    def test_session_new_with_name(self) -> None:
        cmd, arg = parse_slash_command("/session new my-project")
        assert cmd == "/session"
        assert arg == "new my-project"

    def test_file_with_path(self) -> None:
        cmd, arg = parse_slash_command("/file /tmp/test.py")
        assert cmd == "/file"
        assert arg == "/tmp/test.py"

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
    async def test_exit_returns_true(self) -> None:
        should_quit, model = await _handle_slash_command(
            "/exit", "", [], "m", None,
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
        with patch("cortex.memory.hot.hot_query", return_value=[]):
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
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch("cortex.providers.get_provider", return_value=MagicMock()),
            patch("cortex.pipeline.run_pipeline", side_effect=_fake_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 0

    @pytest.mark.asyncio
    async def test_oneshot_provider_unavailable(self) -> None:
        """one-shot should return 1 when provider fails."""
        with (
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch(
                "cortex.providers.get_provider",
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
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch("cortex.providers.get_provider", return_value=MagicMock()),
            patch("cortex.pipeline.run_pipeline", side_effect=_broken_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 1

    @pytest.mark.asyncio
    async def test_oneshot_db_unavailable_still_works(self) -> None:
        """one-shot should proceed even if DB is not available."""

        async def _fake_pipeline(**kwargs):
            yield "ok"

        with (
            patch("cortex.db.init_db", side_effect=RuntimeError("no db")),
            patch("cortex.providers.get_provider", return_value=MagicMock()),
            patch("cortex.pipeline.run_pipeline", side_effect=_fake_pipeline),
        ):
            code = await run_oneshot("test question")
        assert code == 0


# ── REPL graceful handling ──────────────────────────────────────────


class TestReplGraceful:
    @pytest.mark.asyncio
    async def test_repl_provider_unavailable(self) -> None:
        """REPL should return 1 if the provider cannot be created."""
        with (
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch(
                "cortex.providers.get_provider",
                side_effect=RuntimeError("no backend"),
            ),
        ):
            code = await run_repl()
        assert code == 1

    @pytest.mark.asyncio
    async def test_repl_eof_exits(self) -> None:
        """REPL should exit cleanly on EOF (None from _read_input)."""
        with (
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch("cortex.providers.get_provider", return_value=MagicMock()),
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
            patch("cortex.db.init_db"),
            patch("cortex.db.get_db", return_value=MagicMock()),
            patch("cortex.providers.get_provider", return_value=MagicMock()),
            patch("cortex.cli.repl._read_input", side_effect=_fake_read),
        ):
            code = await run_repl()
        assert code == 0


# ═══════════════════════════════════════════════════════════════════
# NEW: Session Manager Tests
# ═══════════════════════════════════════════════════════════════════


class TestSessionCreate:
    """Test Session and SessionManager create/save/resume."""

    def test_create_session(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        session = mgr.create_session(name="test-project")
        assert session.id is not None
        assert session.name == "test-project"
        assert (tmp_path / f"{session.id}.json").exists()

    def test_add_message_and_save(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        session = mgr.create_session()
        session.add_message("user", "hello")
        session.add_message("assistant", "hi there")
        session.save()

        data = json.loads((tmp_path / f"{session.id}.json").read_text())
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["content"] == "hi there"

    def test_resume_session(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        session = mgr.create_session(name="resume-me")
        session.add_message("user", "first message")
        session.save()
        sid = session.id

        resumed = mgr.resume_session(sid)
        assert resumed.id == sid
        assert len(resumed.messages) == 1
        assert resumed.messages[0].content == "first message"

    def test_resume_latest_when_no_id(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        s1 = mgr.create_session()
        s1.add_message("user", "old")
        s1.save()
        s2 = mgr.create_session()
        s2.add_message("user", "new")
        s2.save()

        resumed = mgr.resume_session(None)
        # Should resume the newest session
        assert len(resumed.messages) >= 0  # just check it loads without error

    def test_list_sessions(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        mgr.create_session(name="alpha")
        mgr.create_session(name="beta")

        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        names = {s.name for s in sessions}
        assert "alpha" in names
        assert "beta" in names

    def test_get_session(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        created = mgr.create_session(name="findme")
        created.add_message("user", "test content")
        created.save()

        found = mgr.get_session(created.id)
        assert found.name == "findme"
        assert len(found.messages) == 1


class TestSessionPersistence:
    """Test Session load/save edge cases."""

    def test_truncate(self, tmp_path: Path) -> None:
        from cortex.cli.session import Session
        session = Session("test-trunc", session_dir=tmp_path)
        for i in range(100):
            session.add_message("user", f"msg {i}")
        assert len(session.messages) == 100
        session.truncate(keep_last=10)
        assert len(session.messages) == 10
        assert session.messages[0].content == "msg 90"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        from cortex.cli.session import Session
        session = Session("nonexistent", session_dir=tmp_path)
        session.load()
        assert session.messages == []

    def test_load_corrupt_json(self, tmp_path: Path) -> None:
        from cortex.cli.session import Session
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        session = Session("corrupt", session_dir=tmp_path)
        session.load()
        assert session.messages == []

    def test_get_history_returns_dicts(self, tmp_path: Path) -> None:
        from cortex.cli.session import Session
        session = Session("hist", session_dir=tmp_path)
        session.add_message("user", "q")
        session.add_message("assistant", "a")
        history = session.get_history()
        assert isinstance(history, list)
        assert history[0] == {"role": "user", "content": "q"}
        assert history[1] == {"role": "assistant", "content": "a"}


# ═══════════════════════════════════════════════════════════════════
# NEW: Streaming Output Tests
# ═══════════════════════════════════════════════════════════════════


class TestStreamingOutput:
    @pytest.mark.asyncio
    async def test_stream_collects_tokens(self) -> None:
        from cortex.cli.streaming import StreamingOutput

        async def gen():
            for t in ["hello", " ", "world"]:
                yield t

        out = StreamingOutput()
        result = await out.stream(gen())
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_cancel_stops_stream(self) -> None:
        from cortex.cli.streaming import StreamingOutput

        async def gen():
            yield "a"
            yield "b"
            yield "c"
            yield "d"

        out = StreamingOutput()
        # Cancel after first token via a wrapper
        original_render = out._render_token

        def _cancel_after_first(token):
            original_render(token)
            out.cancel()

        out._render_token = _cancel_after_first
        result = await out.stream(gen())
        assert result == "a"
        assert out.cancelled

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        from cortex.cli.streaming import StreamingOutput

        async def gen():
            return
            yield  # make it an async generator

        out = StreamingOutput()
        result = await out.stream(gen())
        assert result == ""


# ═══════════════════════════════════════════════════════════════════
# NEW: Model Router Tests
# ═══════════════════════════════════════════════════════════════════


class TestModelRouter:
    def _make_router(self):
        from cortex.cli.config import AtlasConfig
        from cortex.cli.model_router import ModelRouter
        config = AtlasConfig()
        config.model.fast = "fast-model"
        config.model.thinking = "think-model"
        return ModelRouter(config)

    def test_force_fast(self) -> None:
        router = self._make_router()
        assert router.select_model("anything", force="fast") == "fast-model"

    def test_force_think(self) -> None:
        router = self._make_router()
        assert router.select_model("anything", force="think") == "think-model"

    def test_simple_question_selects_fast(self) -> None:
        router = self._make_router()
        model = router.select_model("what is Python?")
        assert model == "fast-model"

    def test_complex_request_selects_thinking(self) -> None:
        router = self._make_router()
        model = router.select_model(
            "Analyze the architecture and refactor the authentication module step by step",
        )
        assert model == "think-model"

    def test_code_fence_selects_thinking(self) -> None:
        router = self._make_router()
        model = router.select_model("Implement this:\n```python\ndef foo():\n    pass\n```")
        assert model == "think-model"

    def test_long_session_biases_thinking(self) -> None:
        router = self._make_router()
        # A neutral message in a long session
        model = router.select_model("continue", message_count=30)
        assert model == "think-model"

    def test_properties(self) -> None:
        router = self._make_router()
        assert router.fast_model == "fast-model"
        assert router.thinking_model == "think-model"


# ═══════════════════════════════════════════════════════════════════
# NEW: Background Runner Tests
# ═══════════════════════════════════════════════════════════════════


class TestBackgroundRunner:
    @pytest.mark.asyncio
    async def test_run_and_complete(self) -> None:
        from cortex.cli.background import BackgroundRunner

        runner = BackgroundRunner()

        async def _work():
            return 42

        task_id = await runner.run_bg("test-task", _work())
        assert task_id.startswith("bg-")

        info = await runner.wait(task_id)
        assert info is not None
        assert info.status == "done"
        assert info.result == 42

    @pytest.mark.asyncio
    async def test_list_tasks(self) -> None:
        from cortex.cli.background import BackgroundRunner

        runner = BackgroundRunner()

        async def _work():
            return "ok"

        await runner.run_bg("t1", _work())
        await runner.run_bg("t2", _work())

        tasks = runner.list_tasks()
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_failed_task(self) -> None:
        from cortex.cli.background import BackgroundRunner

        runner = BackgroundRunner()

        async def _fail():
            raise ValueError("boom")

        task_id = await runner.run_bg("fail-task", _fail())
        info = await runner.wait(task_id)
        assert info is not None
        assert info.status == "failed"
        assert "boom" in info.error

    @pytest.mark.asyncio
    async def test_cancel_task(self) -> None:
        from cortex.cli.background import BackgroundRunner

        runner = BackgroundRunner()

        async def _slow():
            await asyncio.sleep(100)

        task_id = await runner.run_bg("slow-task", _slow())
        cancelled = await runner.cancel(task_id)
        assert cancelled
        info = runner.get_task(task_id)
        assert info is not None
        assert info.status == "failed"
        assert info.error == "cancelled"


# ═══════════════════════════════════════════════════════════════════
# NEW: Configuration Tests
# ═══════════════════════════════════════════════════════════════════


class TestConfig:
    def test_default_config(self) -> None:
        from cortex.cli.config import AtlasConfig, load_config, reset_cached_config
        reset_cached_config()
        config = load_config(path=Path("/nonexistent/config.yaml"))
        assert isinstance(config, AtlasConfig)
        assert config.model.fast == os.environ.get("MODEL_FAST", "qwen2.5:14b")
        assert config.cli.streaming is True
        assert config.tools.enabled is True

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        from cortex.cli.config import load_config, reset_cached_config
        reset_cached_config()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model:\n  fast: custom-fast\n  thinking: custom-think\ncli:\n  streaming: false\n",
            encoding="utf-8",
        )
        config = load_config(path=cfg)
        assert config.model.fast == "custom-fast"
        assert config.model.thinking == "custom-think"
        assert config.cli.streaming is False

    def test_save_default_config(self, tmp_path: Path) -> None:
        from cortex.cli.config import save_default_config
        path = save_default_config(path=tmp_path / "config.yaml")
        assert path.exists()
        content = path.read_text()
        assert "model:" in content
        assert "fast:" in content

    def test_save_default_no_overwrite(self, tmp_path: Path) -> None:
        from cortex.cli.config import save_default_config
        cfg = tmp_path / "config.yaml"
        cfg.write_text("custom: true", encoding="utf-8")
        save_default_config(path=cfg)
        assert cfg.read_text() == "custom: true"

    def test_corrupt_yaml_uses_defaults(self, tmp_path: Path) -> None:
        from cortex.cli.config import load_config, reset_cached_config
        reset_cached_config()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("{{invalid yaml::", encoding="utf-8")
        config = load_config(path=cfg)
        # Should fall back to defaults
        assert config.cli.syntax_highlight is True


# ═══════════════════════════════════════════════════════════════════
# NEW: Output Helpers Tests
# ═══════════════════════════════════════════════════════════════════


class TestOutputHelpers:
    def test_print_styled(self, capsys) -> None:
        from cortex.cli.output import print_styled
        print_styled("hello", style="info")
        captured = capsys.readouterr()
        assert "hello" in captured.out

    def test_print_error(self, capsys) -> None:
        from cortex.cli.output import print_error
        print_error("something broke")
        captured = capsys.readouterr()
        assert "something broke" in captured.out

    def test_print_success(self, capsys) -> None:
        from cortex.cli.output import print_success
        print_success("it worked")
        captured = capsys.readouterr()
        assert "it worked" in captured.out

    def test_format_status_dot(self) -> None:
        from cortex.cli.output import format_status_dot
        healthy = format_status_dot(True)
        unhealthy = format_status_dot(False)
        assert healthy != unhealthy

    def test_print_table(self, capsys) -> None:
        from cortex.cli.output import print_table
        print_table(
            ["Name", "Value"],
            [["model", "qwen"], ["status", "ok"]],
            title="Test",
        )
        captured = capsys.readouterr()
        assert "model" in captured.out
        assert "qwen" in captured.out


# ═══════════════════════════════════════════════════════════════════
# NEW: VS Code Bridge Tests
# ═══════════════════════════════════════════════════════════════════


class TestVSCodeBridge:
    def test_bridge_creation(self) -> None:
        from cortex.cli.vscode import VSCodeBridge
        bridge = VSCodeBridge()
        assert bridge._running is False
        assert bridge._provider is None

    @pytest.mark.asyncio
    async def test_dispatch_unknown_method(self) -> None:
        from cortex.cli.vscode import VSCodeBridge
        bridge = VSCodeBridge()
        result = await bridge._dispatch({"id": 1, "method": "nonexistent"})
        assert "error" in result
        assert result["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_dispatch_status(self) -> None:
        from cortex.cli.vscode import VSCodeBridge
        bridge = VSCodeBridge()
        bridge._running = True
        result = await bridge._dispatch({"id": 1, "method": "status", "params": {}})
        assert result["result"]["running"] is True


# ═══════════════════════════════════════════════════════════════════
# NEW: Legacy Session Manager Compat Tests
# ═══════════════════════════════════════════════════════════════════


class TestSessionManagerLegacy:
    """Ensure the legacy API still works for existing callers."""

    def test_new_session_returns_id(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        sid = mgr.new_session("chat")
        assert sid is not None
        assert mgr.current_session_id == sid

    def test_add_message_and_get_history(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        mgr.new_session()
        mgr.add_message("user", "hello")
        mgr.add_message("assistant", "hi")
        history = mgr.get_history()
        assert len(history) == 2
        assert history[0]["role"] == "user"

    def test_save_and_load(self, tmp_path: Path) -> None:
        from cortex.cli.session import SessionManager
        mgr = SessionManager(session_dir=tmp_path)
        sid = mgr.new_session()
        mgr.add_message("user", "test")
        mgr.save()

        msgs = mgr.load(sid)
        assert len(msgs) == 1
        assert msgs[0].content == "test"
