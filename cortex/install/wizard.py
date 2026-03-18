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
    from cortex.install.hardware import (
        detect_hardware, recommend_models, resolve_fast_model,
        ATLAS_MODELS, ATLAS_LORAS,
    )
    hw = detect_hardware()
    cpu = hw.get("cpu", {})
    ram = hw.get("ram_mb", 0)
    disk = hw.get("disk", {})
    gpus = hw.get("gpus", [])
    deployment = hw.get("deployment", {})

    _print("\n── Hardware Detection ──\n")
    _print(f"  CPU: {cpu.get('model', 'Unknown')} ({cpu.get('cores', '?')} cores)")
    _print(f"  RAM: {ram // 1024} GB")
    _print(f"  Disk: {disk.get('free_gb', 0):.0f} GB free on {disk.get('path', '.')}")

    if gpus:
        _print("\n  GPUs Found:")
        for i, gpu in enumerate(gpus, 1):
            api = gpu.get("compute_api", "").upper()
            kind = "integrated" if gpu.get("is_igpu") else "discrete"
            vram_str = f"{gpu['vram_mb'] // 1024}GB VRAM" if gpu.get("vram_mb") else "VRAM unknown"
            api_str = f" ({api})" if api and api != "NONE" else ""
            _print(f"    {i}. {gpu.get('vendor', '').upper()} {gpu['name']} — {vram_str}{api_str}  [{kind}]")
    else:
        _print("\n  GPU: None detected (CPU-only mode)")

    # Show deployment recommendation
    tier = deployment.get("tier", "cpu-only")
    rec = recommend_models(hw)
    fast_model = resolve_fast_model(rec)
    loras = rec.get("loras", [])

    _print(f"\n── Deployment Recommendation ──\n")

    if tier == "dual-gpu":
        llm_dev = deployment.get("llm_device", {})
        tts_dev = deployment.get("tts_device", {})
        _print(f"  Deployment: Dual-GPU\n")
        _print("  ★ Recommended:")
        _print(f"    • LLM:  {llm_dev.get('name', '?')} ({llm_dev.get('vram_mb', 0) // 1024}GB, {llm_dev.get('compute_api', '?').upper()}) → {fast_model}")
        _print(f"    • TTS:  {tts_dev.get('name', '?')} ({tts_dev.get('vram_mb', 0) // 1024}GB, {tts_dev.get('compute_api', '?').upper()}) → Orpheus / Kokoro")
        _print(f"    • LoRA training: {llm_dev.get('name', '?')} (overnight)")
        if loras:
            _print(f"    • LoRA adapters: {', '.join(loras)}")
        if llm_dev.get("vendor") != tts_dev.get("vendor"):
            _print(f"\n  ℹ  Mixed vendor: LLM uses {llm_dev.get('compute_api', '?').upper()}, TTS uses {tts_dev.get('compute_api', '?').upper()}")
        _print("\n  Other options:")
        _print("    • Single-GPU mode (everything on one GPU)")
        _print("    • CPU-only mode (slower, no GPU required)")
    elif tier == "single-gpu":
        gpu_dev = deployment.get("llm_device", {})
        _print(f"  Deployment: Single-GPU\n")
        _print("  ★ Recommended:")
        _print(f"    • LLM + TTS: {gpu_dev.get('name', '?')} ({gpu_dev.get('vram_mb', 0) // 1024}GB) → {fast_model}")
        _print("    • LLM and TTS take turns on the same GPU")
        if loras:
            _print(f"    • LoRA adapters: {', '.join(loras)}")
        _print("\n  Other options:")
        _print("    • CPU-only mode (slower, no GPU required)")
    elif tier == "cpu-only":
        _print("  Deployment: CPU-only\n")
        _print("  ★ Recommended:")
        _print(f"    • Model: {fast_model} (smallest available)")
        _print("    • TTS: Piper (CPU, fast)")
        _print("    • Expect slower responses (5-15 seconds)")

    if not non_interactive and gpus:
        accept = _yes_no("\n  Accept recommended?")
        if not accept:
            _print("  (Adjust model choices in step 4)")

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

    best_vram = 0
    for g in gpus:
        if not g.get("is_igpu", False):
            best_vram = max(best_vram, g.get("vram_mb", 0))
    if best_vram == 0:
        for g in gpus:
            best_vram = max(best_vram, g.get("vram_mb", 0))

    # Check if Atlas models are available
    atlas_ultra_ok = fast_model == "atlas-ultra:9b"
    atlas_core_ok = fast_model == "atlas-core:2b"
    fallback = rec.get("fast_fallback", "qwen2.5:7b")

    if not non_interactive:
        _print(f"\n  ★ Recommended for your hardware ({best_vram // 1024}GB VRAM):")
        if best_vram >= 8000:
            rec_label = "atlas-ultra:9b" if atlas_ultra_ok else f"atlas-ultra:9b (will try) or {fallback}"
            if loras:
                rec_label += f" + {', '.join(loras[:3])}"
            _print(f"    {rec_label}")
        else:
            rec_label = "atlas-core:2b" if atlas_core_ok else f"atlas-core:2b (will try) or {fallback}"
            _print(f"    {rec_label}")

        _print("\n  Other options:")
        _print("    1. atlas-ultra:9b  (9B params, 8GB+ VRAM)  — best quality")
        _print("    2. atlas-core:2b   (2B params, 4GB+ VRAM)  — faster, lighter")
        _print(f"    3. {fallback:<17s}({rec['class']})           — generic Qwen (no Atlas training)")
        _print("    4. Custom          (enter model name)")

        raw = _input("\n  Selection [recommended]: ")
        if raw == "1":
            model_fast = "atlas-ultra:9b"
        elif raw == "2":
            model_fast = "atlas-core:2b"
        elif raw == "3":
            model_fast = fallback
        elif raw == "4":
            model_fast = _input("  Model name: ") or fast_model
        else:
            model_fast = fast_model
    else:
        model_fast = fast_model

    model_thinking = rec["thinking"]
    model_embedding = rec["embedding"]

    # ── LoRA adapter selection (if Atlas model chosen) ─────────
    from cortex.install.hardware import ATLAS_LORAS
    selected_loras: list[str] = []

    is_atlas_model = "atlas" in model_fast.lower()
    if is_atlas_model and not non_interactive:
        _print("\n  LoRA Expert Adapters:")
        _print("  These make Atlas smarter in specific domains (~50MB each).\n")

        recommended = [k for k, v in ATLAS_LORAS.items() if v["recommended"]]
        optional = [k for k, v in ATLAS_LORAS.items() if not v["recommended"]]

        _print("  ★ Recommended (included by default):")
        for name in recommended:
            info = ATLAS_LORAS[name]
            _print(f"    ✓ {name:<20s} {info['description']}")

        _print("\n  Optional:")
        for i, name in enumerate(optional, 1):
            info = ATLAS_LORAS[name]
            _print(f"    {i}. {name:<20s} {info['description']}")

        _print(f"\n  Total recommended: {len(recommended)} adapters (~{len(recommended) * 50}MB)")

        if _yes_no("  Install all LoRAs (recommended + optional)?"):
            selected_loras = list(ATLAS_LORAS.keys())
            _print(f"  ✓ All {len(selected_loras)} LoRA adapters selected")
        elif _yes_no("  Install recommended LoRAs?"):
            selected_loras = recommended
            _print(f"  ✓ {len(selected_loras)} recommended LoRAs selected")
            # Ask about each optional one
            for name in optional:
                info = ATLAS_LORAS[name]
                if _yes_no(f"  Also install {name} ({info['description']})?", default=False):
                    selected_loras.append(name)
        else:
            _print("  No LoRAs selected (you can add them later)")
    elif is_atlas_model:
        # Non-interactive: install recommended only
        selected_loras = [k for k, v in ATLAS_LORAS.items() if v["recommended"]]

    _print(f"\n  Fast:      {model_fast}")
    _print(f"  Thinking:  {model_thinking}")
    _print(f"  Embedding: {model_embedding}")
    if selected_loras:
        _print(f"  LoRAs:     {', '.join(selected_loras)}")

    if not non_interactive:
        if not _yes_no("\n  Accept model selection?"):
            model_fast = _input(f"  Fast model [{model_fast}]: ") or model_fast
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
