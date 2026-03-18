"""Interactive chat REPL and one-shot query mode for Atlas CLI.

Provides :func:`run_repl` for interactive chat with streaming output,
and :func:`run_oneshot` for single-question mode.
"""

# Module ownership: CLI interactive chat loop and one-shot query

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sys
from typing import Any

from cortex.db import init_db, get_db
from cortex.memory.hot import hot_query
from cortex.pipeline import run_pipeline
from cortex.providers import get_provider

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
_SLASH_COMMANDS = ["/help", "/clear", "/memory", "/quit", "/history", "/model"]

_HELP_TEXT = """\
**Atlas CLI Commands**

| Command           | Description                        |
|-------------------|------------------------------------|
| `/help`           | Show this help message             |
| `/clear`          | Clear conversation history         |
| `/memory <query>` | Search your memory for a topic     |
| `/history`        | Show conversation so far           |
| `/model <name>`   | Switch LLM model                   |
| `/quit`           | Exit the chat                      |

*Multi-line input*: type `\"\"\"` to start and end a multi-line block.
*Exit*: Ctrl+C or Ctrl+D also quit.
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
            # Include text before the closing triple-quote
            idx = line.index('"""')
            if idx > 0:
                lines.append(line[:idx])
            break
        lines.append(line)
    return "\n".join(lines)


# ── Slash-command dispatch ──────────────────────────────────────────

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


async def _handle_slash_command(
    cmd: str,
    arg: str,
    history: list[dict[str, str]],
    current_model: str,
    db_conn: Any,
) -> tuple[bool, str]:
    """Handle a slash command. Returns (should_quit, updated_model)."""
    if cmd == "/quit":
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

def _show_banner(model: str) -> None:
    """Display the welcome banner with model info."""
    banner = (
        "\n╭─────────────────────────────────────╮\n"
        "│         🧠  Atlas Cortex CLI        │\n"
        "╰─────────────────────────────────────╯\n"
    )
    if _HAS_RICH and _console is not None:
        _console.print(banner, style="bold cyan")
        _console.print(f"  Model: [bold]{model}[/bold]")
        _console.print("  Type [bold]/help[/bold] for commands, "
                        "[bold]/quit[/bold] or Ctrl+C to exit.\n")
    else:
        print(banner)
        print(f"  Model: {model}")
        print("  Type /help for commands, /quit or Ctrl+C to exit.\n")


# ── Core REPL ───────────────────────────────────────────────────────

async def run_repl(model: str | None = None) -> int:
    """Interactive chat loop with streaming output."""
    current_model = model or os.environ.get("MODEL_FAST", "qwen2.5:14b")

    # Initialise database (best-effort)
    db_conn: Any = None
    try:
        init_db()
        db_conn = get_db()
    except Exception as exc:
        _print_error(f"Database unavailable: {exc}")

    # Initialise LLM provider
    try:
        provider = get_provider()
    except Exception as exc:
        _print_error(f"LLM provider unavailable: {exc}")
        _print_system("Start your LLM backend and try again.")
        return 1

    _show_banner(current_model)

    prompt_session = _build_prompt_session()
    history: list[dict[str, str]] = []

    while True:
        # ── Read input ──────────────────────────────────────────
        raw = _read_input(prompt_session)
        if raw is None:
            # EOF / Ctrl-D / Ctrl-C
            print()
            _print_system("Goodbye! 👋")
            break

        text = raw.strip()
        if not text:
            continue

        # ── Multi-line mode ─────────────────────────────────────
        if text.startswith('"""'):
            remainder = text[3:]
            if '"""' in remainder:
                # Single-line triple-quote: """content"""
                text = remainder[:remainder.index('"""')]
            else:
                # Multi-line block
                first_part = remainder
                rest = _read_multiline()
                text = (first_part + "\n" + rest).strip() if first_part else rest.strip()
            if not text:
                continue

        # ── Slash commands ──────────────────────────────────────
        cmd, arg = parse_slash_command(text)
        if cmd:
            should_quit, current_model = await _handle_slash_command(
                cmd, arg, history, current_model, db_conn,
            )
            if should_quit:
                _print_system("Goodbye! 👋")
                break
            continue

        # ── Send to pipeline ────────────────────────────────────
        history.append({"role": "user", "content": text})
        response_chunks: list[str] = []

        if _HAS_RICH and _console is not None:
            _console.print("\n[assistant]Atlas[/assistant]:", end=" ")
        else:
            print("\nAtlas:", end=" ")

        try:
            async for chunk in run_pipeline(
                message=text,
                provider=provider,
                user_id="cli_user",
                conversation_history=history[:-1],
                model_fast=current_model,
                db_conn=db_conn,
            ):
                print(chunk, end="", flush=True)
                response_chunks.append(chunk)
        except Exception as exc:
            _print_error(f"\nPipeline error: {exc}")
            history.pop()  # remove failed user message
            continue

        print()  # newline after streamed response

        full_response = "".join(response_chunks)
        history.append({"role": "assistant", "content": full_response})

        # Best-effort DB logging
        _log_interaction(db_conn, text, full_response)

    return 0


async def run_oneshot(question: str, model: str | None = None) -> int:
    """Single question, streaming answer, then exit."""
    current_model = model or os.environ.get("MODEL_FAST", "qwen2.5:14b")

    # Initialise database (best-effort)
    db_conn: Any = None
    try:
        init_db()
        db_conn = get_db()
    except Exception as exc:
        logger.debug("Database unavailable: %s", exc)

    # Initialise LLM provider
    try:
        provider = get_provider()
    except Exception as exc:
        _print_error(f"LLM provider unavailable: {exc}")
        return 1

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
