"""Atlas CLI — Terminal-based AI assistant with chat and agent modes.

Usage::

    # Interactive chat (default)
    atlas

    # Interactive chat with session resume
    atlas --session <id>

    # Start a new session
    atlas --new

    # One-shot query
    atlas ask "what time is it?"

    # Autonomous agent
    atlas agent "add authentication to the API"

    # Agent with file input
    atlas agent --file spec.png "implement this"

    # List sessions
    atlas sessions

    # System status
    atlas status
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _build_parser():
    """Build argparse-based CLI parser (retained for backward compat)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas — your local AI assistant",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging",
    )
    parser.add_argument(
        "--model", "-m", default=None,
        help="Override LLM model (default: from env or config)",
    )
    parser.add_argument(
        "--session", "-s", default=None,
        help="Resume session by ID",
    )
    parser.add_argument(
        "--new", "-n", action="store_true",
        help="Start a new session",
    )

    sub = parser.add_subparsers(dest="command")

    # ── chat ─────────────────────────────────────────────────────
    sub.add_parser("chat", help="Interactive chat mode")

    # ── ask ──────────────────────────────────────────────────────
    ask_p = sub.add_parser("ask", help="One-shot question")
    ask_p.add_argument("question", nargs="+", help="Your question")

    # ── agent ────────────────────────────────────────────────────
    agent_p = sub.add_parser("agent", help="Autonomous agent mode")
    agent_p.add_argument("task", nargs="+", help="Task description")
    agent_p.add_argument(
        "--file", "-f", action="append", default=[],
        help="Attach file(s) for context (images, PDFs, code, logs)",
    )
    agent_p.add_argument(
        "--max-iterations", type=int, default=50,
        help="Maximum agent iterations (default: 50)",
    )
    agent_p.add_argument(
        "--dispatch", action="store_true",
        help="Dispatch mode: run multiple tasks (provide multiple task args)",
    )
    agent_p.add_argument(
        "--parallel", action="store_true",
        help="Force parallel execution (requires multiple GPUs)",
    )
    agent_p.add_argument(
        "--sequential", action="store_true",
        help="Force sequential execution",
    )

    # ── sessions ─────────────────────────────────────────────────
    sub.add_parser("sessions", help="List all sessions")

    # ── status ───────────────────────────────────────────────────
    sub.add_parser("status", help="Show system status")

    # ── diagnose ─────────────────────────────────────────────────
    sub.add_parser("diagnose", help="Run system diagnostics")

    # ── reflect ──────────────────────────────────────────────────
    sub.add_parser("reflect", help="Atlas reflects on recent work and suggests improvements")

    # ── daemon ───────────────────────────────────────────────────
    daemon_p = sub.add_parser("daemon", help="Manage the workspace daemon")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_action")
    daemon_sub.add_parser("start", help="Start the daemon")
    daemon_sub.add_parser("stop", help="Stop the daemon")
    daemon_sub.add_parser("status", help="Check daemon status")
    daemon_sub.add_parser("restart", help="Restart the daemon")

    # ── workspace ────────────────────────────────────────────────
    sub.add_parser("workspace", help="Open workspace TUI (connects to daemon)")

    # ── send ─────────────────────────────────────────────────────
    send_p = sub.add_parser("send", help="Send a message to the running daemon")
    send_p.add_argument("message", nargs="+", help="Message to send")

    # ── vscode-bridge ────────────────────────────────────────────
    sub.add_parser("vscode-bridge", help="Start VS Code integration bridge")

    return parser


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


async def _run_chat(args) -> int:
    from cortex.cli.repl import run_repl
    return await run_repl(
        model=args.model,
        session_id=getattr(args, "session", None),
        new_session=getattr(args, "new", False),
    )


async def _run_ask(args) -> int:
    from cortex.cli.repl import run_oneshot
    question = " ".join(args.question)
    return await run_oneshot(question, model=args.model)


async def _run_agent(args) -> int:
    if getattr(args, "dispatch", False):
        # Dispatch mode — multiple independent tasks
        from cortex.cli.dispatch import AgentDispatcher

        dispatcher = AgentDispatcher()
        await dispatcher.initialize()

        strategy = "auto"
        if getattr(args, "parallel", False):
            strategy = "parallel"
        elif getattr(args, "sequential", False):
            strategy = "sequential"

        results = await dispatcher.dispatch(
            tasks=args.task,
            strategy=strategy,
            model=args.model,
            max_iterations=args.max_iterations,
        )

        failed = sum(1 for r in results if r.status == "failed")
        return 1 if failed else 0

    # Single task mode (existing behavior)
    from cortex.cli.agent import run_agent
    task = " ".join(args.task)
    return await run_agent(
        task=task,
        files=args.file,
        model=args.model,
        max_iterations=args.max_iterations,
    )


async def _run_sessions(_args) -> int:
    """List all saved sessions."""
    from cortex.cli.session import SessionManager
    mgr = SessionManager()
    sessions = mgr.list_sessions(limit=50)
    if not sessions:
        print("No sessions found.")
        return 0
    print(f"{'ID':<40} {'Name':<20} {'Messages':>8}  {'Created'}")
    print("─" * 85)
    import datetime
    for s in sessions:
        ts = datetime.datetime.fromtimestamp(s.created_at).strftime("%Y-%m-%d %H:%M")
        name = s.name[:18] if s.name != s.id else ""
        print(f"{s.id:<40} {name:<20} {s.message_count:>8}  {ts}")
    return 0


async def _run_status(_args) -> int:
    from cortex.cli.status import print_status
    return await print_status()


async def _run_diagnose(_args) -> int:
    from cortex.cli.diagnose import run_diagnose
    return await run_diagnose()


async def _run_reflect(_args) -> int:
    from cortex.curiosity import CuriosityEngine
    engine = CuriosityEngine()
    await engine.initialize()
    report = await engine.reflect()
    print(report)
    return 0


async def _run_daemon(args) -> int:
    from cortex.cli.daemon import (
        get_daemon_pid,
        is_daemon_running,
        start_daemon,
        stop_daemon,
    )

    action = getattr(args, "daemon_action", None) or "start"

    if action == "start":
        if is_daemon_running():
            print(f"Daemon already running (PID {get_daemon_pid()})")
            return 0
        print("Starting Atlas workspace daemon...")
        await start_daemon(model=args.model)
    elif action == "stop":
        if stop_daemon():
            print("Daemon stopped")
        else:
            print("Daemon not running")
    elif action == "status":
        if is_daemon_running():
            print(f"✅ Daemon running (PID {get_daemon_pid()})")
        else:
            print("❌ Daemon not running")
    elif action == "restart":
        stop_daemon()
        await asyncio.sleep(1)
        print("Starting Atlas workspace daemon...")
        await start_daemon(model=args.model)
    return 0


async def _run_workspace(_args) -> int:
    from cortex.cli.daemon import is_daemon_running

    if not is_daemon_running():
        print("Daemon not running. Starting it first...")
        import subprocess

        subprocess.Popen(
            [sys.executable, "-m", "cortex.cli", "daemon", "start"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(3)

    from cortex.cli.workspace import run_workspace_tui

    return await run_workspace_tui()


async def _run_send(args) -> int:
    from cortex.cli.workspace import WorkspaceClient

    client = WorkspaceClient()
    if not await client.connect():
        print("❌ Daemon not running")
        return 1

    # Consume the welcome message
    await client.receive()

    message = " ".join(args.message)
    await client.send_message(message)

    while True:
        msg = await client.receive()
        if not msg:
            break
        if msg.get("type") == "token":
            print(msg.get("text", ""), end="", flush=True)
        elif msg.get("type") == "response_end":
            print()
            break
        elif msg.get("type") == "error":
            print(f"❌ {msg.get('message', '')}")
            break

    await client.disconnect()
    return 0


async def _run_vscode_bridge(args) -> int:
    """Start the VS Code integration bridge."""
    from cortex.cli.vscode import VSCodeBridge

    provider = None
    try:
        from cortex.providers import get_provider
        provider = get_provider()
    except Exception as exc:
        logger.warning("LLM provider unavailable for VS Code bridge: %s", exc)

    bridge = VSCodeBridge()
    print(f"Starting VS Code bridge on {bridge.SOCKET_PATH}...")
    await bridge.start_server(provider)
    return 0


def main() -> int:
    # Check deps before importing anything that needs them
    from cortex.cli import _check_cli_deps
    if not _check_cli_deps():
        return 1

    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        # Default to chat mode if no command given
        args.command = "chat"

    _setup_logging(args.verbose)

    handlers = {
        "chat": _run_chat,
        "ask": _run_ask,
        "agent": _run_agent,
        "sessions": _run_sessions,
        "status": _run_status,
        "diagnose": _run_diagnose,
        "reflect": _run_reflect,
        "daemon": _run_daemon,
        "workspace": _run_workspace,
        "send": _run_send,
        "vscode-bridge": _run_vscode_bridge,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        print("\n\nGoodbye! 👋")
        return 0


if __name__ == "__main__":
    sys.exit(main())
