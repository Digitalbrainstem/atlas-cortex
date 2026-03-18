"""Atlas CLI — Terminal-based AI assistant with chat and agent modes.

Usage::

    # Interactive chat
    python -m cortex.cli chat

    # One-shot query
    python -m cortex.cli ask "what time is it?"

    # Autonomous agent
    python -m cortex.cli agent "add authentication to the API"

    # Agent with file input
    python -m cortex.cli agent --file spec.png "implement this"

    # System status
    python -m cortex.cli status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas — your local AI assistant",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging",
    )
    parser.add_argument(
        "--model", "-m", default=None,
        help="Override LLM model (default: from env or qwen2.5:14b)",
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
        "--max-iterations", type=int, default=25,
        help="Maximum agent iterations (default: 25)",
    )

    # ── status ───────────────────────────────────────────────────
    sub.add_parser("status", help="Show system status")

    # ── diagnose ─────────────────────────────────────────────────
    sub.add_parser("diagnose", help="Run system diagnostics")

    return parser


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


async def _run_chat(args: argparse.Namespace) -> int:
    from cortex.cli.repl import run_repl
    return await run_repl(model=args.model)


async def _run_ask(args: argparse.Namespace) -> int:
    from cortex.cli.repl import run_oneshot
    question = " ".join(args.question)
    return await run_oneshot(question, model=args.model)


async def _run_agent(args: argparse.Namespace) -> int:
    from cortex.cli.agent import run_agent
    task = " ".join(args.task)
    return await run_agent(
        task=task,
        files=args.file,
        model=args.model,
        max_iterations=args.max_iterations,
    )


async def _run_status(_args: argparse.Namespace) -> int:
    from cortex.cli.status import print_status
    return await print_status()


async def _run_diagnose(_args: argparse.Namespace) -> int:
    from cortex.cli.diagnose import run_diagnose
    return await run_diagnose()


def main() -> int:
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
        "status": _run_status,
        "diagnose": _run_diagnose,
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
