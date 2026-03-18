"""System status dashboard for Atlas CLI.

Shows a comprehensive overview of all Atlas Cortex subsystems:
LLM provider, database, plugins, scheduling, satellites, media,
and TTS/STT.
"""

# Module ownership: CLI system health and status reporting

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import cortex

logger = logging.getLogger(__name__)

# ── Optional rich import with fallback ──────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    _console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]


# ── Helpers ─────────────────────────────────────────────────────────

_DOT_OK = "●"
_DOT_FAIL = "●"


def _dot(healthy: bool) -> str:
    """Green/red dot for rich output."""
    if not _HAS_RICH:
        return "●" if healthy else "○"
    colour = "green" if healthy else "red"
    return f"[{colour}]{_DOT_OK}[/{colour}]"


def _human_size(nbytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} TB"


def _plain_row(label: str, value: str) -> None:
    """Fallback plain-text row."""
    print(f"  {label:<24} {value}")


# ── Data collectors ─────────────────────────────────────────────────

async def _collect_llm_info() -> dict[str, Any]:
    """Gather LLM provider status."""
    info: dict[str, Any] = {
        "provider_name": os.environ.get("LLM_PROVIDER", "ollama"),
        "model_fast": os.environ.get("MODEL_FAST", "qwen2.5:14b"),
        "model_thinking": os.environ.get("MODEL_THINKING", "qwen3:30b-a3b"),
        "healthy": False,
        "url": "",
        "models": [],
    }
    try:
        from cortex.providers import get_provider
        provider = get_provider()
        info["url"] = getattr(provider, "_base_url", "") or getattr(
            getattr(provider, "_client", None), "base_url", ""
        )
        info["url"] = str(info["url"]).rstrip("/")
        info["healthy"] = await provider.health()
        if info["healthy"]:
            try:
                info["models"] = [
                    m.get("name", m.get("id", "?"))
                    for m in await provider.list_models()
                ]
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Provider health check failed: %s", exc)
    return info


def _collect_db_info() -> dict[str, Any]:
    """Gather database stats."""
    info: dict[str, Any] = {
        "path": "unknown",
        "size": 0,
        "table_count": 0,
        "interaction_count": 0,
        "memory_count": 0,
    }
    try:
        from cortex.db import init_db, get_db
        init_db()
        conn = get_db()
        raw_path = conn.execute("PRAGMA database_list").fetchone()[2]
        info["path"] = raw_path
        try:
            info["size"] = Path(raw_path).stat().st_size
        except Exception:
            pass

        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        info["table_count"] = row[0] if row else 0

        for tbl, key in [("interactions", "interaction_count"),
                         ("memory_fts", "memory_count")]:
            try:
                row = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()  # noqa: S608
                info[key] = row[0] if row else 0
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Database status check failed: %s", exc)
    return info


async def _collect_plugin_info() -> list[dict[str, Any]]:
    """Gather plugin id, name, and health."""
    plugins: list[dict[str, Any]] = []
    try:
        from cortex.plugins import get_registry
        for p in get_registry().list_plugins():
            healthy = False
            try:
                healthy = await p.health()
            except Exception:
                pass
            plugins.append({
                "id": p.plugin_id,
                "name": p.display_name,
                "healthy": healthy,
            })
    except Exception as exc:
        logger.debug("Plugin registry check failed: %s", exc)
    return plugins


def _collect_scheduling_info() -> dict[str, int]:
    """Count active timers, alarms, and pending reminders."""
    counts: dict[str, int] = {"timers": 0, "alarms": 0, "reminders": 0}
    try:
        from cortex.db import get_db
        conn = get_db()
        for kind, key in [("timer", "timers"), ("alarm", "alarms"),
                          ("reminder", "reminders")]:
            try:
                row = conn.execute(
                    "SELECT count(*) FROM list_items li "
                    "JOIN list_registry lr ON li.list_id = lr.id "
                    "WHERE lr.list_type = ? AND li.completed = 0",
                    (kind,),
                ).fetchone()
                counts[key] = row[0] if row else 0
            except Exception:
                pass
    except Exception:
        pass
    return counts


def _collect_satellite_info() -> list[dict[str, str]]:
    """List connected satellites."""
    sats: list[dict[str, str]] = []
    try:
        from cortex.satellite.discovery import SatelliteDiscovery
        disc = SatelliteDiscovery()
        for s in disc.get_announced():
            sats.append({
                "ip": s.ip_address,
                "hostname": s.hostname,
                "room": s.properties.get("room", "unknown"),
            })
    except Exception:
        pass
    return sats


def _collect_media_info() -> dict[str, str]:
    """Check media provider configuration."""
    info: dict[str, str] = {}
    info["plex"] = "configured" if os.environ.get("PLEX_URL") else "not configured"
    info["audiobookshelf"] = (
        "configured" if os.environ.get("AUDIOBOOKSHELF_URL") else "not configured"
    )
    info["youtube_music"] = (
        "configured" if os.environ.get("YOUTUBE_MUSIC_COOKIE") else "not configured"
    )
    return info


def _collect_voice_info() -> dict[str, str]:
    """Check TTS/STT provider config."""
    return {
        "tts_provider": os.environ.get("TTS_PROVIDER", "kokoro"),
        "stt_backend": os.environ.get("STT_BACKEND", "whisper_cpp"),
        "stt_host": os.environ.get("STT_HOST", "localhost"),
        "stt_port": os.environ.get("STT_PORT", "10300"),
    }


# ── Renderers ───────────────────────────────────────────────────────

def _render_rich(  # noqa: PLR0913
    llm: dict[str, Any],
    db: dict[str, Any],
    plugins: list[dict[str, Any]],
    sched: dict[str, int],
    sats: list[dict[str, str]],
    media: dict[str, str],
    voice: dict[str, str],
) -> None:
    assert _console is not None

    # Header
    header = Text.assemble(
        ("Atlas Cortex", "bold cyan"),
        (f" v{cortex.__version__}", "cyan"),
        ("  │  ", "dim"),
        (f"Python {sys.version.split()[0]}", ""),
        ("  │  ", "dim"),
        (f"Data: {db['path']}", "dim"),
    )
    _console.print(Panel(header, expand=True))
    _console.print()

    # LLM Provider
    tbl = Table(title="LLM Provider", show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    status_str = f"{_dot(llm['healthy'])} {'healthy' if llm['healthy'] else 'unreachable'}"
    tbl.add_row("Provider", f"{llm['provider_name']}  ({llm['url'] or 'default'})")
    tbl.add_row("Status", status_str)
    tbl.add_row("Model (fast)", llm["model_fast"])
    tbl.add_row("Model (thinking)", llm["model_thinking"])
    if llm["models"]:
        tbl.add_row("Available", ", ".join(llm["models"][:10]))
    _console.print(Panel(tbl, expand=True))
    _console.print()

    # Database
    tbl = Table(title="Database", show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    tbl.add_row("Path", str(db["path"]))
    tbl.add_row("Size", _human_size(db["size"]))
    tbl.add_row("Tables", str(db["table_count"]))
    tbl.add_row("Interactions", str(db["interaction_count"]))
    tbl.add_row("Memories", str(db["memory_count"]))
    _console.print(Panel(tbl, expand=True))
    _console.print()

    # Plugins
    if plugins:
        cols: list[str] = []
        for p in plugins:
            cols.append(f"{_dot(p['healthy'])} {p['name']}")
        plugin_str = "  ".join(cols)
        _console.print(Panel(
            plugin_str,
            title=f"Plugins ({len(plugins)} registered)",
            expand=True,
        ))
    else:
        _console.print(Panel("(none loaded)", title="Plugins", expand=True))
    _console.print()

    # Scheduling
    tbl = Table(title="Scheduling", show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    tbl.add_row("Active timers", str(sched["timers"]))
    tbl.add_row("Active alarms", str(sched["alarms"]))
    tbl.add_row("Pending reminders", str(sched["reminders"]))
    _console.print(Panel(tbl, expand=True))
    _console.print()

    # Satellites
    if sats:
        sat_lines = [f"{_dot(True)} {s['hostname']} ({s['room']}) — {s['ip']}" for s in sats]
        _console.print(Panel(
            "\n".join(sat_lines),
            title=f"Satellites ({len(sats)} connected)",
            expand=True,
        ))
    else:
        _console.print(Panel("(none discovered)", title="Satellites", expand=True))
    _console.print()

    # Media
    tbl = Table(title="Media Providers", show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    tbl.add_row("Plex", media["plex"])
    tbl.add_row("Audiobookshelf", media["audiobookshelf"])
    tbl.add_row("YouTube Music", media["youtube_music"])
    _console.print(Panel(tbl, expand=True))
    _console.print()

    # TTS/STT
    tbl = Table(title="TTS / STT", show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="bold")
    tbl.add_column("Value")
    tbl.add_row("TTS provider", voice["tts_provider"])
    tbl.add_row("STT backend", voice["stt_backend"])
    tbl.add_row("STT endpoint", f"{voice['stt_host']}:{voice['stt_port']}")
    _console.print(Panel(tbl, expand=True))
    _console.print()


def _render_plain(
    llm: dict[str, Any],
    db: dict[str, Any],
    plugins: list[dict[str, Any]],
    sched: dict[str, int],
    sats: list[dict[str, str]],
    media: dict[str, str],
    voice: dict[str, str],
) -> None:
    status = "healthy" if llm["healthy"] else "unreachable"
    print(f"\nAtlas Cortex v{cortex.__version__}  |  Python {sys.version.split()[0]}")
    print(f"Data: {db['path']}\n")

    print("── LLM Provider ──")
    _plain_row("Provider", f"{llm['provider_name']}  ({llm['url'] or 'default'})")
    _plain_row("Status", status)
    _plain_row("Model (fast)", llm["model_fast"])
    _plain_row("Model (thinking)", llm["model_thinking"])

    print("\n── Database ──")
    _plain_row("Path", str(db["path"]))
    _plain_row("Size", _human_size(db["size"]))
    _plain_row("Tables", str(db["table_count"]))
    _plain_row("Interactions", str(db["interaction_count"]))
    _plain_row("Memories", str(db["memory_count"]))

    print(f"\n── Plugins ({len(plugins)} registered) ──")
    for p in plugins:
        dot = "●" if p["healthy"] else "○"
        print(f"  {dot} {p['name']}")
    if not plugins:
        print("  (none loaded)")

    print("\n── Scheduling ──")
    _plain_row("Active timers", str(sched["timers"]))
    _plain_row("Active alarms", str(sched["alarms"]))
    _plain_row("Pending reminders", str(sched["reminders"]))

    print(f"\n── Satellites ({len(sats)}) ──")
    for s in sats:
        print(f"  ● {s['hostname']} ({s['room']}) — {s['ip']}")
    if not sats:
        print("  (none discovered)")

    print("\n── Media Providers ──")
    _plain_row("Plex", media["plex"])
    _plain_row("Audiobookshelf", media["audiobookshelf"])
    _plain_row("YouTube Music", media["youtube_music"])

    print("\n── TTS / STT ──")
    _plain_row("TTS provider", voice["tts_provider"])
    _plain_row("STT backend", voice["stt_backend"])
    _plain_row("STT endpoint", f"{voice['stt_host']}:{voice['stt_port']}")
    print()


# ── Public API ──────────────────────────────────────────────────────

async def print_status() -> int:
    """Print full system status dashboard."""

    llm = await _collect_llm_info()
    db = _collect_db_info()
    plugins = await _collect_plugin_info()
    sched = _collect_scheduling_info()
    sats = _collect_satellite_info()
    media = _collect_media_info()
    voice = _collect_voice_info()

    if _HAS_RICH and _console is not None:
        _render_rich(llm, db, plugins, sched, sats, media, voice)
    else:
        _render_plain(llm, db, plugins, sched, sats, media, voice)

    return 0
