"""Rich terminal output helpers for Atlas CLI.

Centralises formatted output so every module gets consistent styling:
syntax-highlighted code, markdown rendering, tables, progress bars,
and coloured status indicators.

Module ownership: CLI rich output formatting
"""
from __future__ import annotations

from typing import Any

# ── Optional rich imports with graceful fallback ────────────────────
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme

    _THEME = Theme({
        "user": "bold cyan",
        "assistant": "bold green",
        "system": "bold yellow",
        "error": "bold red",
        "dim": "dim",
        "success": "green",
        "warning": "yellow",
        "info": "cyan",
    })
    console = Console(theme=_THEME, highlight=False)
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False
    console = None  # type: ignore[assignment]


# ── Convenience helpers ─────────────────────────────────────────────

def print_styled(text: str, *, style: str | None = None) -> None:
    """Print with optional rich styling."""
    if HAS_RICH and console:
        console.print(text, style=style)
    else:
        print(text)


def print_markdown(text: str) -> None:
    """Render markdown in the terminal."""
    if HAS_RICH and console:
        console.print(Markdown(text))
    else:
        print(text)


def print_error(text: str) -> None:
    print_styled(f"✗ {text}", style="error")


def print_success(text: str) -> None:
    print_styled(f"✓ {text}", style="success")


def print_warning(text: str) -> None:
    print_styled(f"⚠ {text}", style="warning")


def print_info(text: str) -> None:
    print_styled(text, style="info")


def print_system(text: str) -> None:
    print_styled(text, style="system")


def print_panel(content: str, *, title: str = "", style: str = "cyan") -> None:
    """Print text inside a bordered panel."""
    if HAS_RICH and console:
        console.print(Panel(content, title=title, border_style=style))
    else:
        border = f"── {title} " if title else "──"
        print(border + "─" * max(0, 40 - len(border)))
        print(content)
        print("─" * 40)


def print_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> None:
    """Print a formatted table."""
    if HAS_RICH and console:
        table = Table(title=title)
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*row)
        console.print(table)
    else:
        if title:
            print(f"\n{title}")
        widths = [max(len(h), *(len(r[i]) for r in rows if i < len(r)))
                  for i, h in enumerate(headers)]
        header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
        print(header_line)
        print("  ".join("─" * w for w in widths))
        for row in rows:
            print("  ".join(
                (row[i] if i < len(row) else "").ljust(w)
                for i, w in enumerate(widths)
            ))


def format_status_dot(healthy: bool) -> str:
    """Green/red dot indicator."""
    if HAS_RICH:
        colour = "green" if healthy else "red"
        return f"[{colour}]●[/{colour}]"
    return "●" if healthy else "○"
