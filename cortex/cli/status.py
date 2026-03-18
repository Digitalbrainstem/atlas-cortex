"""System status display for Atlas CLI.

Shows LLM provider health, database info, loaded plugins, memory stats,
and active timers/alarms.
"""

# Module ownership: CLI system health and status reporting

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Optional rich import with fallback ──────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table

    _console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]


def _ok(value: bool) -> str:
    """Return a coloured status indicator."""
    return "✓ healthy" if value else "✗ unreachable"


def _plain_row(label: str, value: str) -> None:
    """Fallback plain-text row."""
    print(f"  {label:<24} {value}")


async def print_status() -> int:
    """Print system status: provider health, loaded plugins, memory stats."""

    # ── LLM Provider ────────────────────────────────────────────
    provider_name = os.environ.get("LLM_PROVIDER", "ollama")
    model_fast = os.environ.get("MODEL_FAST", "qwen2.5:14b")
    model_thinking = os.environ.get("MODEL_THINKING", "qwen3:30b-a3b")
    provider_healthy = False

    try:
        from cortex.providers import get_provider
        provider = get_provider()
        provider_healthy = await provider.health()
    except Exception as exc:
        logger.debug("Provider health check failed: %s", exc)

    # ── Database ────────────────────────────────────────────────
    db_path = "unknown"
    table_count = 0
    memory_count = 0
    timer_count = 0

    try:
        from cortex.db import init_db, get_db
        init_db()
        conn = get_db()
        db_path = str(conn.execute("PRAGMA database_list").fetchone()[2])

        rows = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        table_count = rows[0] if rows else 0

        # Memory count (best-effort)
        try:
            rows = conn.execute("SELECT count(*) FROM memory_fts").fetchone()
            memory_count = rows[0] if rows else 0
        except Exception:
            pass

        # Active timers/alarms (best-effort — tables may not exist)
        try:
            rows = conn.execute(
                "SELECT count(*) FROM list_items li "
                "JOIN list_registry lr ON li.list_id = lr.id "
                "WHERE lr.list_type IN ('timer', 'alarm') "
                "AND li.completed = 0"
            ).fetchone()
            timer_count = rows[0] if rows else 0
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Database status check failed: %s", exc)

    # ── Plugins ─────────────────────────────────────────────────
    plugin_names: list[str] = []
    try:
        from cortex.plugins import get_registry
        registry = get_registry()
        plugin_names = [
            f"{p.display_name} ({p.plugin_id})" for p in registry.list_plugins()
        ]
    except Exception as exc:
        logger.debug("Plugin registry check failed: %s", exc)

    # ── Render ──────────────────────────────────────────────────
    if _HAS_RICH and _console is not None:
        _console.print("\n[bold cyan]Atlas Cortex — System Status[/bold cyan]\n")

        tbl = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        tbl.add_column("Component", style="bold")
        tbl.add_column("Detail")

        tbl.add_row("LLM Provider", f"{provider_name}  ({_ok(provider_healthy)})")
        tbl.add_row("Model (fast)", model_fast)
        tbl.add_row("Model (thinking)", model_thinking)
        tbl.add_row("Database", f"{db_path}  ({table_count} tables)")
        tbl.add_row("Memories", str(memory_count))
        tbl.add_row("Active timers", str(timer_count))
        tbl.add_row(
            "Plugins",
            ", ".join(plugin_names) if plugin_names else "(none loaded)",
        )

        _console.print(tbl)
        _console.print()
    else:
        print("\nAtlas Cortex — System Status\n")
        _plain_row("LLM Provider", f"{provider_name}  ({_ok(provider_healthy)})")
        _plain_row("Model (fast)", model_fast)
        _plain_row("Model (thinking)", model_thinking)
        _plain_row("Database", f"{db_path}  ({table_count} tables)")
        _plain_row("Memories", str(memory_count))
        _plain_row("Active timers", str(timer_count))
        _plain_row(
            "Plugins",
            ", ".join(plugin_names) if plugin_names else "(none loaded)",
        )
        print()

    return 0
