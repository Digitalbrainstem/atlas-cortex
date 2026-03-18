"""System diagnostics for Atlas CLI.

Runs a suite of pass/warn/fail checks against every Atlas Cortex
subsystem and prints a colour-coded report.
"""

# Module ownership: CLI diagnostic checks

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# ── Optional rich import with fallback ──────────────────────────────
try:
    from rich.console import Console

    _console = Console()
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False
    _console = None  # type: ignore[assignment]

# ── Result type ─────────────────────────────────────────────────────
# Each check returns (status, detail) where status is "pass", "warn", or "fail".

CheckResult = tuple[str, str]
CheckFn = Callable[[], Coroutine[Any, Any, CheckResult]]

# Timeout applied to every individual check
_CHECK_TIMEOUT = 5.0


# ═══════════════════════════════════════════════════════════════════
# Check functions
# ═══════════════════════════════════════════════════════════════════

# ── Core ───────────────────────────────────────────────────────────

async def check_python_version() -> CheckResult:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return "pass", f"{version_str}"
    return "fail", f"{version_str} (need >= 3.11)"


async def check_db_writable() -> CheckResult:
    try:
        from cortex.db import init_db, get_db
        init_db()
        conn = get_db()
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _diag_test (id INTEGER PRIMARY KEY)"
        )
        conn.execute("DROP TABLE IF EXISTS _diag_test")
        conn.commit()
        return "pass", str(db_path)
    except Exception as exc:
        return "fail", str(exc)


async def check_db_schema() -> CheckResult:
    try:
        from cortex.db import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        count = row[0] if row else 0
        if count > 0:
            return "pass", f"{count} tables"
        return "warn", "no tables found"
    except Exception as exc:
        return "fail", str(exc)


# ── LLM ───────────────────────────────────────────────────────────

async def check_ollama_reachable() -> CheckResult:
    try:
        import httpx
        provider_name = os.environ.get("LLM_PROVIDER", "ollama")
        if provider_name == "ollama":
            url = os.environ.get(
                "LLM_URL",
                os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            )
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                r = await client.get(f"{url}/api/version")
                if r.status_code == 200:
                    version = r.json().get("version", "unknown")
                    return "pass", f"Ollama {version} at {url}"
                return "fail", f"Ollama returned HTTP {r.status_code}"
        else:
            # For non-Ollama providers, try the health() method
            from cortex.providers import get_provider
            provider = get_provider()
            healthy = await provider.health()
            if healthy:
                return "pass", f"{provider_name} healthy"
            return "fail", f"{provider_name} unreachable"
    except ImportError:
        return "warn", "httpx not installed"
    except Exception as exc:
        return "fail", f"Cannot reach LLM provider: {exc}"


async def check_model_available() -> CheckResult:
    model = os.environ.get("MODEL_FAST", "qwen2.5:14b")
    try:
        from cortex.providers import get_provider
        provider = get_provider()
        models = await provider.list_models()
        names = [m.get("name", m.get("id", "")) for m in models]
        if any(model in n for n in names):
            return "pass", model
        return "warn", f"{model} not found in {len(names)} available models"
    except Exception as exc:
        return "warn", f"Cannot list models: {exc}"


async def check_embedding_available() -> CheckResult:
    embed_model = os.environ.get("MODEL_EMBEDDING", os.environ.get("EMBED_MODEL", ""))
    if not embed_model:
        return "warn", "Embedding model not configured (set MODEL_EMBEDDING)"
    try:
        from cortex.providers import get_provider
        provider = get_provider()
        if not provider.supports_embeddings():
            return "warn", "Provider does not support embeddings"
        models = await provider.list_models()
        names = [m.get("name", m.get("id", "")) for m in models]
        if any(embed_model in n for n in names):
            return "pass", embed_model
        return "warn", f"{embed_model} not found"
    except Exception as exc:
        return "warn", f"Cannot check embedding model: {exc}"


# ── TTS / STT ─────────────────────────────────────────────────────

async def check_tts_available() -> CheckResult:
    tts_provider = os.environ.get("TTS_PROVIDER", "kokoro")
    try:
        import httpx
        if tts_provider == "kokoro":
            host = os.environ.get("KOKORO_HOST", "localhost")
            port = os.environ.get("KOKORO_PORT", "8880")
            url = f"http://{host}:{port}"
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                r = await client.get(f"{url}/v1/audio/voices")
                if r.status_code == 200:
                    return "pass", f"Kokoro at {url}"
                return "fail", f"Kokoro returned HTTP {r.status_code}"
        elif tts_provider == "orpheus":
            url = os.environ.get("ORPHEUS_FASTAPI_URL", "http://localhost:8880")
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    return "pass", f"Orpheus at {url}"
                return "fail", f"Orpheus returned HTTP {r.status_code}"
        elif tts_provider == "piper":
            url = os.environ.get("PIPER_URL", "http://localhost:5002")
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    return "pass", f"Piper at {url}"
                return "fail", f"Piper returned HTTP {r.status_code}"
        else:
            return "warn", f"Unknown TTS provider: {tts_provider}"
    except ImportError:
        return "warn", "httpx not installed"
    except Exception as exc:
        return "fail", f"TTS provider not available ({tts_provider}): {exc}"


async def check_stt_available() -> CheckResult:
    host = os.environ.get("STT_HOST", "localhost")
    port = os.environ.get("STT_PORT", "10300")
    try:
        import httpx
        url = f"http://{host}:{port}"
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
            r = await client.get(url)
            return "pass", f"STT at {url}"
    except ImportError:
        return "warn", "httpx not installed"
    except Exception:
        return "fail", f"STT not reachable at {host}:{port}"


# ── Home Assistant ─────────────────────────────────────────────────

async def check_ha_connected() -> CheckResult:
    ha_url = os.environ.get("HA_URL", "")
    ha_token = os.environ.get("HA_TOKEN", "")
    if not ha_url or not ha_token:
        return "warn", "Not configured (set HA_URL and HA_TOKEN)"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
            r = await client.get(
                f"{ha_url.rstrip('/')}/api/",
                headers={"Authorization": f"Bearer {ha_token}"},
            )
            if r.status_code == 200:
                return "pass", ha_url
            return "fail", f"HA returned HTTP {r.status_code}"
    except ImportError:
        return "warn", "httpx not installed"
    except Exception as exc:
        return "fail", f"Cannot reach Home Assistant: {exc}"


# ── Network ────────────────────────────────────────────────────────

async def check_port_available() -> CheckResult:
    port = 5100
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            if result != 0:
                return "pass", f"Port {port} is free"
            return "warn", f"Port {port} already in use (Atlas may be running)"
    except Exception as exc:
        return "warn", f"Cannot check port {port}: {exc}"


async def check_port_conflicts() -> CheckResult:
    ports = {5100: "Atlas API", 8880: "TTS", 10300: "STT"}
    conflicts: list[str] = []
    for port, label in ports.items():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    conflicts.append(f"{port} ({label})")
        except Exception:
            pass
    if not conflicts:
        return "pass", "No conflicts"
    return "warn", f"Ports in use: {', '.join(conflicts)}"


# ── System ─────────────────────────────────────────────────────────

async def check_disk_space() -> CheckResult:
    try:
        data_dir = os.environ.get("CORTEX_DATA_DIR", "./data")
        path = Path(data_dir)
        if not path.exists():
            path = Path(".")
        usage = shutil.disk_usage(str(path))
        free_gb = usage.free / (1024**3)
        if free_gb > 1.0:
            return "pass", f"{free_gb:.1f}GB free"
        return "fail", f"Only {free_gb:.2f}GB free (need > 1GB)"
    except Exception as exc:
        return "warn", f"Cannot check disk space: {exc}"


async def check_gpu_detected() -> CheckResult:
    # Check for AMD ROCm
    try:
        rocm_path = Path("/opt/rocm")
        if rocm_path.exists():
            # Try to find GPU info
            try:
                proc = await asyncio.create_subprocess_exec(
                    "rocm-smi", "--showproductname",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
                if proc.returncode == 0 and stdout:
                    lines = stdout.decode().strip().split("\n")
                    for line in lines:
                        if "GPU" in line or "gfx" in line or "Radeon" in line:
                            return "pass", line.strip()
                    return "pass", "AMD GPU (ROCm)"
            except Exception:
                return "pass", "AMD GPU (ROCm detected)"
    except Exception:
        pass
    # Check for NVIDIA
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=name,memory.total",
            "--format=csv,noheader",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0 and stdout:
            return "pass", stdout.decode().strip().split("\n")[0]
    except Exception:
        pass
    return "warn", "No GPU detected (CPU-only mode)"


async def check_memory_available() -> CheckResult:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    gb = kb / (1024 * 1024)
                    if gb > 4.0:
                        return "pass", f"{gb:.1f}GB available"
                    return "fail", f"Only {gb:.1f}GB available (need > 4GB)"
    except Exception:
        pass
    # Fallback for non-Linux
    try:
        import resource
        # Just report pass with unknown amount
        return "warn", "Cannot determine available memory"
    except Exception:
        return "warn", "Cannot determine available memory"


# ── Services ───────────────────────────────────────────────────────

async def check_plugins_loadable() -> CheckResult:
    try:
        from cortex.plugins.loader import BUILTIN_PLUGINS
        count = len(BUILTIN_PLUGINS)
        return "pass", f"{count} built-in plugins available"
    except Exception as exc:
        return "fail", f"Cannot load plugin registry: {exc}"


async def check_admin_built() -> CheckResult:
    admin_dist = Path("admin/dist/index.html")
    if admin_dist.exists():
        return "pass", str(admin_dist.parent)
    return "warn", "Admin panel not built (run: cd admin && npm run build)"


async def check_satellites() -> CheckResult:
    try:
        from cortex.satellite.discovery import SatelliteDiscovery
        disc = SatelliteDiscovery()
        sats = disc.get_announced()
        if sats:
            return "pass", f"{len(sats)} satellite(s) connected"
        return "warn", "No satellites connected"
    except ImportError:
        return "warn", "Satellite discovery module not available"
    except Exception:
        return "warn", "No satellites connected"


# ═══════════════════════════════════════════════════════════════════
# Check registry — grouped by category
# ═══════════════════════════════════════════════════════════════════

CHECKS: list[tuple[str, str, CheckFn]] = [
    # (category, label, function)
    ("Core", "Python version >= 3.11", check_python_version),
    ("Core", "Database writable", check_db_writable),
    ("Core", "Database schema current", check_db_schema),

    ("LLM", "LLM provider reachable", check_ollama_reachable),
    ("LLM", "LLM model available", check_model_available),
    ("LLM", "Embedding model available", check_embedding_available),

    ("TTS/STT", "TTS provider available", check_tts_available),
    ("TTS/STT", "STT provider available", check_stt_available),

    ("Home Assistant", "Home Assistant connected", check_ha_connected),

    ("Network", "Port 5100 available", check_port_available),
    ("Network", "No port conflicts", check_port_conflicts),

    ("System", "Disk space > 1GB free", check_disk_space),
    ("System", "GPU detected", check_gpu_detected),
    ("System", "Memory > 4GB available", check_memory_available),

    ("Services", "Plugins loadable", check_plugins_loadable),
    ("Services", "Admin panel built", check_admin_built),
    ("Services", "Satellites discoverable", check_satellites),
]


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

async def _run_check(label: str, fn: CheckFn) -> tuple[str, str, str]:
    """Run a single check with timeout. Returns (label, status, detail)."""
    try:
        status, detail = await asyncio.wait_for(fn(), timeout=_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        status, detail = "fail", "Timed out"
    except Exception as exc:
        status, detail = "fail", str(exc)
    return label, status, detail


_ICONS = {"pass": "✅", "warn": "⚠️ ", "fail": "❌"}
_RICH_ICONS = {
    "pass": "[green]✅[/green]",
    "warn": "[yellow]⚠️ [/yellow]",
    "fail": "[red]❌[/red]",
}


async def run_diagnose() -> int:
    """Run all diagnostic checks and print a colour-coded report."""

    results: list[tuple[str, str, str, str]] = []  # (category, label, status, detail)
    for category, label, fn in CHECKS:
        _, status, detail = await _run_check(label, fn)
        results.append((category, label, status, detail))

    # Tally
    passed = sum(1 for _, _, s, _ in results if s == "pass")
    warnings = sum(1 for _, _, s, _ in results if s == "warn")
    failed = sum(1 for _, _, s, _ in results if s == "fail")

    if _HAS_RICH and _console is not None:
        _render_diag_rich(results, passed, warnings, failed)
    else:
        _render_diag_plain(results, passed, warnings, failed)

    return 1 if failed > 0 else 0


def _render_diag_rich(
    results: list[tuple[str, str, str, str]],
    passed: int,
    warnings: int,
    failed: int,
) -> None:
    assert _console is not None
    _console.print()
    _console.print("[bold cyan]Atlas Cortex — System Diagnostics[/bold cyan]")
    _console.print("[dim]═" * 50 + "[/dim]")
    _console.print()

    current_cat = ""
    for category, label, status, detail in results:
        if category != current_cat:
            if current_cat:
                _console.print()
            _console.print(f"[bold]{category}:[/bold]")
            current_cat = category
        icon = _RICH_ICONS.get(status, "?")
        _console.print(f"  {icon} {label} [dim]({detail})[/dim]")

    _console.print()
    _console.print("[dim]═" * 50 + "[/dim]")
    summary_parts = []
    if passed:
        summary_parts.append(f"[green]{passed} passed[/green]")
    if warnings:
        summary_parts.append(f"[yellow]{warnings} warnings[/yellow]")
    if failed:
        summary_parts.append(f"[red]{failed} failed[/red]")
    _console.print(f"Results: {', '.join(summary_parts)}")
    _console.print()


def _render_diag_plain(
    results: list[tuple[str, str, str, str]],
    passed: int,
    warnings: int,
    failed: int,
) -> None:
    print()
    print("Atlas Cortex — System Diagnostics")
    print("=" * 50)
    print()

    current_cat = ""
    for category, label, status, detail in results:
        if category != current_cat:
            if current_cat:
                print()
            print(f"{category}:")
            current_cat = category
        icon = _ICONS.get(status, "?")
        print(f"  {icon} {label} ({detail})")

    print()
    print("=" * 50)
    print(f"Results: {passed} passed, {warnings} warnings, {failed} failed")
    print()
