"""Streaming output with interrupt support for Atlas CLI.

Renders LLM tokens to the terminal one-by-one with Ctrl+C cancellation.
When cancelled, the partial response is returned — the REPL stays alive.

Module ownership: CLI streaming output rendering
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# Optional rich import
try:
    from rich.console import Console
    from rich.text import Text

    _console = Console(highlight=False)
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]


class StreamingOutput:
    """Handles token-by-token output with Ctrl+C interrupt support."""

    def __init__(self, *, syntax_highlight: bool = True) -> None:
        self._cancelled = False
        self._buffer = ""
        self._in_code_block = False
        self._syntax_highlight = syntax_highlight

    async def stream(self, generator: AsyncGenerator[str, None]) -> str:
        """Stream tokens to stdout. Returns the full response text.

        Ctrl+C sets the cancelled flag — the caller keeps running.
        """
        self._cancelled = False
        self._buffer = ""
        try:
            async for token in generator:
                if self._cancelled:
                    break
                self._buffer += token
                self._render_token(token)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Streaming error: %s", exc)
        # Final newline only when we printed something
        if self._buffer:
            print()
        return self._buffer

    def cancel(self) -> None:
        """Signal the stream to stop after the current token."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def buffer(self) -> str:
        return self._buffer

    def _render_token(self, token: str) -> None:
        """Write a token to stdout."""
        sys.stdout.write(token)
        sys.stdout.flush()
