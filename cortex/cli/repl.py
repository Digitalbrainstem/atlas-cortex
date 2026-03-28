"""Interactive chat REPL and one-shot query mode for Atlas CLI.

Provides :class:`AtlasREPL` — a full-featured REPL with streaming,
interrupts, slash commands, sessions, memory, and model routing.
Also exposes :func:`run_repl` and :func:`run_oneshot` for backward
compatibility with the entry-point layer.
"""

# Module ownership: CLI interactive chat loop and one-shot query

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Optional rich import with fallback ──────────────────────────────
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.theme import Theme

    _THEME = Theme({
        "user": "bold cyan",
        "assistant": "bold green",
        "system": "bold yellow",
        "error": "bold red",
        "dim": "dim",
    })
    _console = Console(theme=_THEME)
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]

# ── Optional prompt_toolkit import with fallback ────────────────────
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.history import InMemoryHistory

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

# ── Constants ───────────────────────────────────────────────────────
_SLASH_COMMANDS = [
    "/help", "/quit", "/exit",
    "/session list", "/session new", "/session resume", "/session name", "/session info",
    "/memory search", "/memory recall", "/memory forget",
    "/model", "/model fast", "/model think", "/model list",
    "/tools", "/tools run",
    "/clear", "/context", "/stream on", "/stream off",
    "/code", "/file", "/diff", "/status",
    "/history",
    "/bg list", "/bg cancel",
]

_HELP_TEXT = """\
**Atlas CLI Commands**

| Command                     | Description                         |
|-----------------------------|-------------------------------------|
| `/help`                     | Show this help message              |
| `/quit`, `/exit`            | Exit (session auto-saved)           |
| `/session list`             | List all sessions                   |
| `/session new [name]`       | Start new session                   |
| `/session resume [id]`      | Resume a session                    |
| `/session name <name>`      | Rename current session              |
| `/session info`             | Show current session details        |
| `/memory search <query>`    | Search memory                       |
| `/memory recall`            | Show recall for last message        |
| `/memory forget <id>`       | Remove a memory entry               |
| `/model`                    | Show current model                  |
| `/model fast`               | Switch to fast model                |
| `/model think`              | Switch to thinking model            |
| `/model list`               | List available models               |
| `/tools`                    | List available tools                |
| `/tools run <tool> [args]`  | Run a tool directly                 |
| `/clear`                    | Clear screen (keep session)         |
| `/context`                  | Show context window size            |
| `/stream on|off`            | Toggle streaming output             |
| `/code <lang>`              | Enter multi-line code block mode    |
| `/file <path>`              | Read and include file in context    |
| `/diff`                     | Show git diff of current repo       |
| `/status`                   | Show system status                  |
| `/history`                  | Show conversation history           |
| `/bg list`                  | List background tasks               |
| `/bg cancel <id>`           | Cancel a background task            |

*Multi-line input*: type `\"\"\"` to start and end a multi-line block.
"""


# ── Output helpers ──────────────────────────────────────────────────

def _print_rich(text: str, *, style: str | None = None, markdown: bool = False) -> None:
    """Print using rich if available, plain text otherwise."""
    if _HAS_RICH and _console is not None:
        if markdown:
            _console.print(Markdown(text))
        else:
            _console.print(text, style=style)
    else:
        print(text)


def _print_error(text: str) -> None:
    _print_rich(f"[error]✗ {text}[/error]" if _HAS_RICH else f"✗ {text}", style="error")


def _print_system(text: str) -> None:
    _print_rich(f"[system]{text}[/system]" if _HAS_RICH else text, style="system")


# ── Input helpers ───────────────────────────────────────────────────

def _build_prompt_session() -> Any:
    """Build a prompt_toolkit session with history and completions."""
    if not _HAS_PROMPT_TOOLKIT:
        return None
    completer = WordCompleter(_SLASH_COMMANDS, sentence=True)
    return PromptSession(
        history=InMemoryHistory(),
        completer=completer,
    )


def _read_input(prompt_session: Any) -> str | None:
    """Read a line from the user. Returns None on EOF/Ctrl-D."""
    try:
        if prompt_session is not None:
            return prompt_session.prompt("atlas> ")
        return input("atlas> ")
    except EOFError:
        return None
    except KeyboardInterrupt:
        return None


def _read_multiline() -> str:
    """Collect lines until a closing triple-quote is entered."""
    lines: list[str] = []
    while True:
        try:
            line = input("...   ")
        except (EOFError, KeyboardInterrupt):
            break
        if '"""' in line:
            idx = line.index('"""')
            if idx > 0:
                lines.append(line[:idx])
            break
        lines.append(line)
    return "\n".join(lines)


def _read_code_block(lang: str = "") -> str:
    """Read a multi-line code block (end with a blank line)."""
    _print_system(f"Enter {lang + ' ' if lang else ''}code (blank line to finish):")
    lines: list[str] = []
    while True:
        try:
            line = input("│ ")
        except (EOFError, KeyboardInterrupt):
            break
        if line == "":
            break
        lines.append(line)
    code = "\n".join(lines)
    if lang:
        return f"```{lang}\n{code}\n```"
    return f"```\n{code}\n```"


# ── Slash-command parsing ───────────────────────────────────────────

def parse_slash_command(text: str) -> tuple[str, str]:
    """Parse a slash command into (command, argument).

    Returns ``("", "")`` if the text is not a slash command.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return ("", "")
    parts = stripped.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    return (cmd, arg)


# ── Legacy slash command handler (kept for backward compat) ─────────

async def _handle_slash_command(
    cmd: str,
    arg: str,
    history: list[dict[str, str]],
    current_model: str,
    db_conn: Any,
) -> tuple[bool, str]:
    """Handle a slash command. Returns (should_quit, updated_model)."""
    if cmd in ("/quit", "/exit"):
        return True, current_model

    if cmd == "/help":
        _print_rich(_HELP_TEXT, markdown=True)
        return False, current_model

    if cmd == "/clear":
        history.clear()
        _print_system("Conversation history cleared.")
        return False, current_model

    if cmd == "/history":
        if not history:
            _print_system("No conversation history yet.")
        else:
            for msg in history:
                role = msg["role"]
                content = msg["content"]
                prefix = "🧑 You" if role == "user" else "🤖 Atlas"
                _print_rich(f"\n**{prefix}**: {content}", markdown=True)
        return False, current_model

    if cmd == "/model":
        if not arg:
            _print_system(f"Current model: {current_model}")
        else:
            current_model = arg.strip()
            _print_system(f"Model switched to: {current_model}")
        return False, current_model

    if cmd == "/memory":
        if not arg:
            _print_system("Usage: /memory <search query>")
            return False, current_model
        await _search_memory(arg, db_conn)
        return False, current_model

    _print_error(f"Unknown command: {cmd}. Type /help for available commands.")
    return False, current_model


async def _search_memory(query: str, db_conn: Any) -> None:
    """Search memory and display results."""
    try:
        from cortex.memory.hot import hot_query
        results = hot_query(query, user_id="cli_user", conn=db_conn)
        if not results:
            _print_system("No memories found.")
            return
        _print_system(f"Found {len(results)} memory hit(s):\n")
        for i, hit in enumerate(results, 1):
            score = f"{hit.score:.3f}" if hit.score else "–"
            _print_rich(f"  {i}. [{score}] {hit.text[:120]}")
    except Exception as exc:
        _print_error(f"Memory search failed: {exc}")


# ── Session logging ─────────────────────────────────────────────────

def _log_interaction(
    db_conn: Any,
    user_input: str,
    assistant_response: str,
) -> None:
    """Best-effort log of the interaction to the database."""
    if db_conn is None:
        return
    try:
        db_conn.execute(
            "INSERT INTO interactions (user_id, raw_text, final_response) "
            "VALUES (?, ?, ?)",
            ("cli_user", user_input, assistant_response),
        )
        db_conn.commit()
    except Exception:
        logger.debug("Failed to log interaction to DB", exc_info=True)


# ── Welcome banner ──────────────────────────────────────────────────

def _show_banner(model: str, session_id: str | None = None) -> None:
    """Display the welcome banner with model and session info."""
    banner = (
        "\n╭─────────────────────────────────────╮\n"
        "│         🧠  Atlas Cortex CLI        │\n"
        "╰─────────────────────────────────────╯\n"
    )
    if _HAS_RICH and _console is not None:
        _console.print(banner, style="bold cyan")
        _console.print(f"  Model: [bold]{model}[/bold]")
        if session_id:
            _console.print(f"  Session: [bold]{session_id}[/bold]")
        _console.print("  Type [bold]/help[/bold] for commands, "
                        "[bold]/quit[/bold] or Ctrl+C to exit.\n")
    else:
        print(banner)
        print(f"  Model: {model}")
        if session_id:
            print(f"  Session: {session_id}")
        print("  Type /help for commands, /quit or Ctrl+C to exit.\n")


# ── AtlasREPL class ────────────────────────────────────────────────

class AtlasREPL:
    """Interactive REPL with streaming, interrupts, and slash commands."""

    def __init__(
        self,
        *,
        model: str | None = None,
        session_id: str | None = None,
        new_session: bool = False,
        streaming: bool = True,
    ) -> None:
        from cortex.cli.config import load_config
        self._config = load_config()
        self._model_override = model
        self._requested_session_id = session_id
        self._new_session = new_session
        self._streaming = streaming
        self._running = True
        self._current_stream: Any = None
        self._session: Any = None  # Session object
        self._provider: Any = None
        self._bridge: Any = None  # MemoryBridge
        self._router: Any = None  # ModelRouter
        self._bg_runner: Any = None  # BackgroundRunner
        self._db_conn: Any = None
        self._prompt_session: Any = None
        self._force_model: str | None = None  # "fast" or "think" override

    async def _initialize(self) -> bool:
        """Set up all subsystems. Returns False if critical init fails."""
        from cortex.cli.session import SessionManager
        from cortex.cli.streaming import StreamingOutput
        from cortex.cli.background import BackgroundRunner
        from cortex.cli.model_router import ModelRouter

        # Config-driven model
        self._current_model = (
            self._model_override
            or os.environ.get("MODEL_FAST")
            or self._config.model.fast
        )

        # Database (best-effort)
        try:
            from cortex.db import init_db, get_db
            init_db()
            self._db_conn = get_db()
        except Exception as exc:
            _print_error(f"Database unavailable: {exc}")

        # LLM provider
        try:
            from cortex.providers import get_provider
            self._provider = get_provider()
        except Exception as exc:
            _print_error(f"LLM provider unavailable: {exc}")
            _print_system("Start your LLM backend and try again.")
            return False

        # Memory bridge
        try:
            from cortex.cli.memory_bridge import MemoryBridge
            self._bridge = MemoryBridge(user_id="cli_user")
            await self._bridge.initialize()
            self._bridge.set_provider(self._provider)
        except Exception as exc:
            logger.debug("Memory bridge init failed: %s", exc)

        # Session
        mgr = SessionManager()
        if self._new_session:
            self._session = mgr.create_session()
        elif self._requested_session_id:
            self._session = mgr.resume_session(self._requested_session_id)
        else:
            self._session = mgr.create_session()

        # Sub-components
        self._streamer = StreamingOutput(syntax_highlight=self._config.cli.syntax_highlight)
        self._bg_runner = BackgroundRunner()
        self._router = ModelRouter(self._config)
        self._prompt_session = _build_prompt_session()

        return True

    async def run(self) -> int:
        """Main REPL loop — returns an exit code."""
        if not await self._initialize():
            return 1

        _show_banner(self._current_model, self._session.id if self._session else None)

        history: list[dict[str, str]] = []
        # Pre-load history from resumed session
        if self._session and self._session.messages:
            history = self._session.get_history()

        while self._running:
            try:
                raw = _read_input(self._prompt_session)
                if raw is None:
                    print()
                    _print_system("Goodbye! 👋")
                    break

                text = raw.strip()
                if not text:
                    continue

                # Multi-line mode
                if text.startswith('"""'):
                    remainder = text[3:]
                    if '"""' in remainder:
                        text = remainder[:remainder.index('"""')]
                    else:
                        first_part = remainder
                        rest = _read_multiline()
                        text = (first_part + "\n" + rest).strip() if first_part else rest.strip()
                    if not text:
                        continue

                # Slash commands
                cmd, arg = parse_slash_command(text)
                if cmd:
                    should_quit = await self._handle_slash(cmd, arg, history)
                    if should_quit:
                        _print_system("Goodbye! 👋")
                        break
                    continue

                # Chat
                await self._chat(text, history)

            except KeyboardInterrupt:
                if self._current_stream:
                    self._current_stream.cancel()
                    self._current_stream = None
                    print("\n[interrupted]")
                else:
                    print("\nUse /quit to exit")

        # Cleanup
        if self._session:
            self._session.save()
        if self._bridge:
            await self._bridge.shutdown()
        return 0

    async def _chat(self, message: str, history: list[dict[str, str]]) -> None:
        """Send a message, stream the response, auto-save."""
        from cortex.pipeline import run_pipeline

        history.append({"role": "user", "content": message})
        if self._session:
            self._session.add_message("user", message)

        # Auto-recall memory
        memory_context = ""
        if self._bridge:
            memory_context = await self._bridge.recall(message)

        # Model selection
        model = self._router.select_model(
            message,
            force=self._force_model,
            message_count=len(history),
        ) if self._router else self._current_model

        if _HAS_RICH and _console is not None:
            _console.print("\n[assistant]Atlas[/assistant]:", end=" ")
        else:
            print("\nAtlas:", end=" ")

        response_chunks: list[str] = []
        try:
            from cortex.cli.streaming import StreamingOutput
            self._current_stream = StreamingOutput()

            async def _token_gen():
                async for chunk in run_pipeline(
                    message=message,
                    provider=self._provider,
                    user_id="cli_user",
                    conversation_history=history[:-1],
                    model_fast=model,
                    memory_context=memory_context,
                    db_conn=self._db_conn,
                ):
                    yield chunk

            full_response = await self._current_stream.stream(_token_gen())
            self._current_stream = None
        except Exception as exc:
            self._current_stream = None
            _print_error(f"\nPipeline error: {exc}")
            history.pop()
            if self._session and self._session.messages:
                self._session.messages.pop()
            return

        history.append({"role": "assistant", "content": full_response})
        if self._session:
            self._session.add_message("assistant", full_response)
            self._session.save()

        # Auto-archive long conversations
        if self._bridge and len(history) > self._bridge.archive_threshold:
            history[:] = await self._bridge._archive_old_turns(history)

        # Store meaningful interactions to memory
        if self._bridge and len(full_response) > 100:
            await self._bridge.remember(
                f"User asked: {message[:200]}\nAtlas answered: {full_response[:500]}",
                tags=["conversation"],
            )

        _log_interaction(self._db_conn, message, full_response)

    # ── Slash command dispatch ──────────────────────────────────────

    async def _handle_slash(
        self, cmd: str, arg: str, history: list[dict[str, str]],
    ) -> bool:
        """Handle a slash command. Returns True if the REPL should exit."""

        # ── Exit ────────────────────────────────────────────────
        if cmd in ("/quit", "/exit"):
            return True

        # ── Help ────────────────────────────────────────────────
        if cmd == "/help":
            _print_rich(_HELP_TEXT, markdown=True)
            return False

        # ── Session management ──────────────────────────────────
        if cmd == "/session":
            await self._handle_session_cmd(arg)
            return False

        # ── Memory ──────────────────────────────────────────────
        if cmd == "/memory":
            await self._handle_memory_cmd(arg)
            return False

        # ── Model ───────────────────────────────────────────────
        if cmd == "/model":
            self._handle_model_cmd(arg)
            return False

        # ── Tools ───────────────────────────────────────────────
        if cmd == "/tools":
            await self._handle_tools_cmd(arg)
            return False

        # ── Clear ───────────────────────────────────────────────
        if cmd == "/clear":
            if self._bridge and history:
                await self._bridge._archive_old_turns(history)
            history.clear()
            if _HAS_RICH and _console:
                _console.clear()
            _print_system("Screen cleared (session preserved).")
            return False

        # ── Context ─────────────────────────────────────────────
        if cmd == "/context":
            msg_count = len(self._session.messages) if self._session else 0
            total_chars = sum(len(m.content) for m in self._session.messages) if self._session else 0
            _print_system(f"Messages: {msg_count}, ~{total_chars // 4} tokens")
            return False

        # ── Streaming toggle ────────────────────────────────────
        if cmd == "/stream":
            if arg.lower() == "off":
                self._streaming = False
                _print_system("Streaming disabled.")
            else:
                self._streaming = True
                _print_system("Streaming enabled.")
            return False

        # ── Code block ──────────────────────────────────────────
        if cmd == "/code":
            code = _read_code_block(arg)
            if code:
                history.append({"role": "user", "content": code})
                if self._session:
                    self._session.add_message("user", code)
                await self._chat(code, history)
            return False

        # ── File include ────────────────────────────────────────
        if cmd == "/file":
            self._handle_file_cmd(arg, history)
            return False

        # ── Git diff ────────────────────────────────────────────
        if cmd == "/diff":
            self._handle_diff_cmd(history)
            return False

        # ── Status ──────────────────────────────────────────────
        if cmd == "/status":
            await self._handle_status_cmd()
            return False

        # ── History ─────────────────────────────────────────────
        if cmd == "/history":
            if not history:
                _print_system("No conversation history yet.")
            else:
                for msg in history:
                    role = msg["role"]
                    content = msg["content"]
                    prefix = "🧑 You" if role == "user" else "🤖 Atlas"
                    _print_rich(f"\n**{prefix}**: {content}", markdown=True)
            return False

        # ── Background tasks ────────────────────────────────────
        if cmd == "/bg":
            self._handle_bg_cmd(arg)
            return False

        _print_error(f"Unknown command: {cmd}. Type /help for available commands.")
        return False

    # ── Sub-handlers ────────────────────────────────────────────────

    async def _handle_session_cmd(self, arg: str) -> None:
        from cortex.cli.session import SessionManager
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else "info"
        sub_arg = parts[1] if len(parts) > 1 else ""
        mgr = SessionManager()

        if sub == "list":
            sessions = mgr.list_sessions()
            if not sessions:
                _print_system("No sessions found.")
                return
            _print_system("Recent sessions:")
            for s in sessions:
                current = " ← current" if (self._session and s.id == self._session.id) else ""
                name = f" ({s.name})" if s.name and s.name != s.id else ""
                ts = datetime.datetime.fromtimestamp(s.created_at).strftime("%Y-%m-%d %H:%M")
                _print_rich(f"  {s.id}{name}  [{s.message_count} msgs, {ts}]{current}")

        elif sub == "new":
            self._session = mgr.create_session(name=sub_arg or None)
            _print_system(f"New session: {self._session.id}")

        elif sub == "resume":
            if sub_arg:
                self._session = mgr.resume_session(sub_arg)
            else:
                self._session = mgr.resume_session()
            _print_system(
                f"Resumed session: {self._session.id} "
                f"({len(self._session.messages)} messages)"
            )

        elif sub == "name":
            if self._session and sub_arg:
                self._session.name = sub_arg.strip()
                self._session.save()
                _print_system(f"Session renamed to: {sub_arg.strip()}")
            else:
                _print_system("Usage: /session name <name>")

        elif sub == "info":
            if self._session:
                _print_system(f"Session ID: {self._session.id}")
                _print_system(f"Name: {self._session.name}")
                _print_system(f"Messages: {len(self._session.messages)}")
                ts = datetime.datetime.fromtimestamp(self._session.created_at).strftime("%Y-%m-%d %H:%M")
                _print_system(f"Created: {ts}")
            else:
                _print_system("No active session.")
        else:
            _print_system("Usage: /session list|new|resume|name|info")

    async def _handle_memory_cmd(self, arg: str) -> None:
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""
        sub_arg = parts[1] if len(parts) > 1 else ""

        if sub == "search":
            if sub_arg:
                await _search_memory(sub_arg, self._db_conn)
            else:
                _print_system("Usage: /memory search <query>")
        elif sub == "recall":
            if self._session and self._session.messages:
                last_user = ""
                for m in reversed(self._session.messages):
                    if m.role == "user":
                        last_user = m.content
                        break
                if last_user and self._bridge:
                    ctx = await self._bridge.recall(last_user)
                    _print_system(ctx or "No memory recalled.")
                else:
                    _print_system("No user message to recall for.")
            else:
                _print_system("No messages in session.")
        elif sub == "forget":
            _print_system("Memory forget is not yet implemented.")
        elif sub:
            # Legacy: treat bare /memory <query> as /memory search <query>
            await _search_memory(arg, self._db_conn)
        else:
            _print_system("Usage: /memory search|recall|forget")

    def _handle_model_cmd(self, arg: str) -> None:
        sub = arg.strip().lower()
        if not sub:
            _print_system(f"Current model: {self._current_model}")
            if self._force_model:
                _print_system(f"Mode: {self._force_model}")
        elif sub == "fast":
            self._force_model = "fast"
            self._current_model = self._router.fast_model if self._router else self._config.model.fast
            _print_system(f"Switched to fast model: {self._current_model}")
        elif sub in ("think", "thinking"):
            self._force_model = "think"
            self._current_model = self._router.thinking_model if self._router else self._config.model.thinking
            _print_system(f"Switched to thinking model: {self._current_model}")
        elif sub == "list":
            _print_system(f"  fast: {self._config.model.fast}")
            _print_system(f"  thinking: {self._config.model.thinking}")
        elif sub == "auto":
            self._force_model = None
            _print_system("Model selection set to auto (router picks based on query).")
        else:
            self._current_model = sub
            self._force_model = None
            _print_system(f"Model set to: {self._current_model}")

    async def _handle_tools_cmd(self, arg: str) -> None:
        try:
            from cortex.cli.tools import get_default_registry
            registry = get_default_registry()
        except Exception:
            _print_system("Tool registry not available.")
            return

        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else ""

        if sub == "run":
            _print_system("Direct tool execution is not yet supported in the REPL.")
        elif sub:
            _print_system(f"Unknown tools sub-command: {sub}")
        else:
            tools = registry.list_tools()
            _print_system(f"{len(tools)} tools available:")
            for t in tools:
                _print_rich(f"  {t.tool_id:25s} {t.description[:50]}")

    def _handle_file_cmd(self, arg: str, history: list[dict[str, str]]) -> None:
        path_str = arg.strip()
        if not path_str:
            _print_system("Usage: /file <path>")
            return
        p = Path(path_str).expanduser()
        if not p.exists():
            _print_error(f"File not found: {p}")
            return
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50_000:
                content = content[:50_000] + f"\n... (truncated, {len(content)} total chars)"
            file_msg = f"[File: {p.name}]\n```\n{content}\n```"
            history.append({"role": "user", "content": file_msg})
            if self._session:
                self._session.add_message("user", file_msg)
            _print_system(f"Included {p.name} ({len(content)} chars)")
        except OSError as exc:
            _print_error(f"Error reading {p}: {exc}")

    def _handle_diff_cmd(self, history: list[dict[str, str]]) -> None:
        try:
            result = subprocess.run(
                ["git", "diff"], capture_output=True, text=True, timeout=10,
            )
            diff = result.stdout.strip()
            if not diff:
                _print_system("No uncommitted changes.")
                return
            diff_msg = f"[Git Diff]\n```diff\n{diff[:20_000]}\n```"
            history.append({"role": "user", "content": diff_msg})
            if self._session:
                self._session.add_message("user", diff_msg)
            _print_system(f"Included git diff ({len(diff)} chars)")
        except Exception as exc:
            _print_error(f"Git diff failed: {exc}")

    async def _handle_status_cmd(self) -> None:
        _print_system("Atlas System Status:")
        _print_rich(f"  Provider: {type(self._provider).__name__ if self._provider else 'None'}")
        _print_rich(f"  Model: {self._current_model}")
        _print_rich(f"  Database: {'connected' if self._db_conn else 'disconnected'}")
        _print_rich(f"  Memory: {'active' if self._bridge else 'inactive'}")
        _print_rich(f"  Session: {self._session.id if self._session else 'none'}")
        if self._bg_runner:
            tasks = self._bg_runner.list_tasks()
            running = sum(1 for t in tasks if t.status == "running")
            _print_rich(f"  Background tasks: {running} running / {len(tasks)} total")

    def _handle_bg_cmd(self, arg: str) -> None:
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else "list"
        sub_arg = parts[1].strip() if len(parts) > 1 else ""

        if not self._bg_runner:
            _print_system("Background runner not available.")
            return

        if sub == "list":
            tasks = self._bg_runner.list_tasks()
            if not tasks:
                _print_system("No background tasks.")
                return
            for t in tasks:
                _print_rich(f"  [{t.id}] {t.name} — {t.status}")
        elif sub == "cancel":
            if sub_arg:
                asyncio.ensure_future(self._bg_runner.cancel(sub_arg))
                _print_system(f"Cancelling {sub_arg}...")
            else:
                _print_system("Usage: /bg cancel <task-id>")
        else:
            _print_system("Usage: /bg list|cancel")


# ── Core REPL (backward-compatible entry point) ────────────────────

async def run_repl(
    model: str | None = None,
    session_id: str | None = None,
    new_session: bool = False,
) -> int:
    """Interactive chat loop with streaming output."""
    repl = AtlasREPL(model=model, session_id=session_id, new_session=new_session)
    return await repl.run()


async def run_oneshot(question: str, model: str | None = None) -> int:
    """Single question, streaming answer, then exit."""
    current_model = model or os.environ.get("MODEL_FAST", "qwen2.5:14b")

    # Initialise database (best-effort)
    db_conn: Any = None
    try:
        from cortex.db import init_db, get_db
        init_db()
        db_conn = get_db()
    except Exception as exc:
        logger.debug("Database unavailable: %s", exc)

    # Initialise LLM provider
    try:
        from cortex.providers import get_provider
        provider = get_provider()
    except Exception as exc:
        _print_error(f"LLM provider unavailable: {exc}")
        return 1

    from cortex.pipeline import run_pipeline

    response_chunks: list[str] = []
    try:
        async for chunk in run_pipeline(
            message=question,
            provider=provider,
            user_id="cli_user",
            model_fast=current_model,
            db_conn=db_conn,
        ):
            print(chunk, end="", flush=True)
            response_chunks.append(chunk)
    except Exception as exc:
        _print_error(f"Pipeline error: {exc}")
        return 1

    print()  # trailing newline

    # Best-effort DB logging
    full_response = "".join(response_chunks)
    _log_interaction(db_conn, question, full_response)

    return 0
