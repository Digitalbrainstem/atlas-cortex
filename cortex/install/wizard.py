"""Interactive CLI installer wizard for Atlas Cortex.

Implements the two-stage installer described in docs/installation.md:
  Stage 1: Deterministic setup (no LLM required)
  Stage 2: LLM-assisted refinement (optional, runs after Part 2)

Run with: python -m cortex.install
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


BANNER = r"""
╔══════════════════════════════════════════════╗
║         Atlas Cortex — Installation          ║
╚══════════════════════════════════════════════╝
"""

DONE_BANNER = """
╔══════════════════════════════════════════════╗
║  ✓ Atlas Cortex is running!                  ║
║                                              ║
║  Web UI: Open WebUI → select "Atlas Cortex"  ║
║  API:    http://localhost:{port}/v1           ║
║                                              ║
║  Next: say "Hey Atlas" or run service        ║
║  discovery to find Home Assistant, etc.      ║
║                                              ║
║  $ python -m cortex.discover                 ║
╚══════════════════════════════════════════════╝
"""


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _input(prompt: str) -> str:
    return input(prompt).strip()


def _yes_no(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _input(prompt + suffix)
    if not raw:
        return default
    return raw.lower().startswith("y")


def run_installer(data_dir: Path | None = None, non_interactive: bool = False) -> dict[str, Any]:
    """Run the interactive installer.

    Args:
        data_dir:        Override the data directory.
        non_interactive: Skip all prompts, use defaults.

    Returns the final configuration dict.
    """
    _print(BANNER)

    # ── [1/6] Python environment ───────────────────────────────
    _print("[1/6] Checking Python environment...")
    import platform
    _print(f"  ✓ Python {platform.python_version()}")
    _print(f"  ✓ Platform: {platform.system()} {platform.release()}")

    # ── [2/6] Hardware detection ───────────────────────────────
    _print("\n[2/6] Detecting hardware...")
    from cortex.install.hardware import detect_hardware, recommend_models
    hw = detect_hardware()
    cpu = hw.get("cpu", {})
    ram = hw.get("ram_mb", 0)
    disk = hw.get("disk", {})
    gpus = hw.get("gpus", [])

    _print(f"  CPU: {cpu.get('model', 'Unknown')} ({cpu.get('cores', '?')} cores)")
    _print(f"  RAM: {ram // 1024} GB")
    _print(f"  Disk: {disk.get('free_gb', 0):.0f} GB free on {disk.get('path', '.')}")
    if gpus:
        for gpu in gpus:
            igpu_note = " (iGPU)" if gpu.get("is_igpu") else ""
            _print(f"  GPU: {gpu['name']} ({gpu['vram_mb'] // 1024} GB {gpu.get('compute_api', '').upper()}){igpu_note}")
    else:
        _print("  GPU: None detected (CPU-only mode)")

    rec = recommend_models(hw)
    _print(f"\n  Recommended model tier: {rec['class']}")

    # ── [3/6] LLM backend discovery ───────────────────────────
    _print("\n[3/6] Scanning for existing LLM backends...")
    from cortex.install.providers import discover_backends_sync
    backends = discover_backends_sync()
    if backends:
        for i, b in enumerate(backends, 1):
            _print(f"  ✓ {b['name']} found at {b['url']}")
    else:
        _print("  No LLM backends found on localhost.")

    selected_backend: dict[str, Any] = {}
    if backends and not non_interactive:
        _print("\n  Which LLM backend should Atlas use?")
        for i, b in enumerate(backends, 1):
            marker = ">" if i == 1 else " "
            _print(f"  {marker} {i}. {b['name']} at {b['url']}{' (recommended — already running)' if i == 1 else ''}")
        _print(f"    {len(backends)+1}. Install Ollama fresh")
        _print(f"    {len(backends)+2}. Other (provide URL)")
        try:
            choice = int(_input("\n  Selection [1]: ") or "1")
            if 1 <= choice <= len(backends):
                selected_backend = backends[choice - 1]
            else:
                selected_backend = {"name": "Ollama", "url": "http://localhost:11434", "provider": "ollama"}
        except ValueError:
            selected_backend = backends[0] if backends else {"name": "Ollama", "url": "http://localhost:11434", "provider": "ollama"}
    elif backends:
        selected_backend = backends[0]
    else:
        selected_backend = {"name": "Ollama", "url": "http://localhost:11434", "provider": "ollama"}

    _print(f"\n  Selected: {selected_backend['name']} at {selected_backend['url']}")

    # ── [4/6] Model selection ──────────────────────────────────
    _print("\n[4/6] Selecting models for your hardware...")
    _print(f"  Fast:      {rec['fast']}")
    _print(f"  Thinking:  {rec['thinking']}")
    _print(f"  Embedding: {rec['embedding']}")

    model_fast = rec["fast"]
    model_thinking = rec["thinking"]
    model_embedding = rec["embedding"]

    if not non_interactive:
        accept = _yes_no("\n  Accept recommendations?")
        if not accept:
            model_fast = _input(f"  Fast model [{rec['fast']}]: ") or rec["fast"]
            model_thinking = _input(f"  Thinking model [{rec['thinking']}]: ") or rec["thinking"]
            model_embedding = _input(f"  Embedding model [{rec['embedding']}]: ") or rec["embedding"]

    # ── [5/6] Data directory ───────────────────────────────────
    _print("\n[5/6] Setting up data directory...")
    if data_dir is None:
        default_data = os.environ.get("CORTEX_DATA_DIR", "./data")
        if non_interactive:
            data_dir = Path(default_data)
        else:
            raw = _input(f"  Data directory [{default_data}]: ")
            data_dir = Path(raw) if raw else Path(default_data)

    data_dir.mkdir(parents=True, exist_ok=True)
    _print(f"  ✓ Data directory: {data_dir.resolve()}")

    # Initialise database
    from cortex.db import init_db
    db_path = data_dir / "cortex.db"
    init_db(db_path)
    _print("  ✓ Database initialised")

    # Save hardware profile
    _save_hardware_profile(data_dir / "cortex.db", hw, rec)
    _print("  ✓ Hardware profile saved")

    # ── [6/6] Write config ─────────────────────────────────────
    _print("\n[6/6] Writing configuration...")
    port = int(os.environ.get("CORTEX_PORT", "5100"))
    config: dict[str, Any] = {
        "LLM_PROVIDER": selected_backend.get("provider", "ollama"),
        "LLM_URL": selected_backend.get("url", "http://localhost:11434"),
        "LLM_API_KEY": "",
        "MODEL_FAST": model_fast,
        "MODEL_THINKING": model_thinking,
        "MODEL_EMBEDDING": model_embedding,
        "EMBED_PROVIDER": selected_backend.get("provider", "ollama"),
        "EMBED_URL": selected_backend.get("url", "http://localhost:11434"),
        "EMBED_MODEL": model_embedding,
        "CORTEX_HOST": "0.0.0.0",
        "CORTEX_PORT": str(port),
        "CORTEX_DATA_DIR": str(data_dir.resolve()),
    }

    env_path = data_dir / "cortex.env"
    _write_env(env_path, config)
    _print(f"  ✓ Config written to {env_path}")

    _print(DONE_BANNER.format(port=port))
    return config


def _write_env(path: Path, config: dict[str, str]) -> None:
    """Write a cortex.env file."""
    lines = [
        "# Atlas Cortex Configuration — generated by installer\n",
        "# Edit this file to override any setting.\n\n",
    ]
    sections = {
        "LLM Provider": ["LLM_PROVIDER", "LLM_URL", "LLM_API_KEY"],
        "Models": ["MODEL_FAST", "MODEL_THINKING", "MODEL_EMBEDDING"],
        "Embeddings": ["EMBED_PROVIDER", "EMBED_URL", "EMBED_MODEL"],
        "Server": ["CORTEX_HOST", "CORTEX_PORT", "CORTEX_DATA_DIR"],
    }
    for section, keys in sections.items():
        lines.append(f"# {section}\n")
        for key in keys:
            lines.append(f"{key}={config.get(key, '')}\n")
        lines.append("\n")
    path.write_text("".join(lines))


def _save_hardware_profile(db_path: Path, hw: dict[str, Any], rec: dict[str, Any]) -> None:
    """Persist hardware detection results to the hardware_profile table."""
    import json
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        # Mark any existing current profile as not current
        conn.execute("UPDATE hardware_profile SET is_current = FALSE WHERE is_current = TRUE")
        gpus = hw.get("gpus", [])
        best = next((g for g in gpus if not g.get("is_igpu", False)), gpus[0] if gpus else None)
        conn.execute(
            """
            INSERT INTO hardware_profile
              (gpu_vendor, gpu_name, vram_mb, is_igpu, cpu_model, cpu_cores,
               ram_mb, disk_free_gb, os_name, limits_json, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE)
            """,
            (
                best.get("vendor") if best else None,
                best.get("name") if best else None,
                best.get("vram_mb") if best else None,
                best.get("is_igpu", False) if best else False,
                hw.get("cpu", {}).get("model"),
                hw.get("cpu", {}).get("cores"),
                hw.get("ram_mb"),
                hw.get("disk", {}).get("free_gb"),
                hw.get("os", {}).get("name"),
                json.dumps(rec),
            ),
        )
        conn.commit()
    finally:
        conn.close()
