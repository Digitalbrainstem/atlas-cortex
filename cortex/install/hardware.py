"""Hardware detection for Atlas Cortex installer.

Detects GPU (AMD/NVIDIA/Intel/Apple/CPU-only), VRAM, RAM, and disk space,
then computes safe default context limits and model recommendations.

See docs/context-management.md and docs/installation.md for full design.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# GPU detection
# ──────────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a subprocess and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def detect_nvidia_gpus() -> list[dict[str, Any]]:
    """Detect NVIDIA GPUs via nvidia-smi."""
    out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    gpus = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            name = parts[0].strip()
            try:
                vram_mb = int(parts[1].strip())
            except ValueError:
                vram_mb = 0
            gpus.append({
                "vendor": "nvidia",
                "name": name,
                "vram_mb": vram_mb,
                "is_igpu": "integrated" in name.lower() or vram_mb < 2048,
                "compute_api": "cuda",
            })
    return gpus


def detect_amd_gpus() -> list[dict[str, Any]]:
    """Detect AMD GPUs via rocm-smi or /sys."""
    # Try rocm-smi
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if out:
        try:
            import json
            data = json.loads(out)
            gpus = []
            for key, info in data.items():
                if key.startswith("card"):
                    vram_bytes = int(info.get("VRAM Total Memory (B)", 0))
                    vram_mb = vram_bytes // (1024 * 1024)
                    gpus.append({
                        "vendor": "amd",
                        "name": info.get("Card series", "AMD GPU"),
                        "vram_mb": vram_mb,
                        "is_igpu": vram_mb < 2048,
                        "compute_api": "rocm",
                    })
            if gpus:
                return gpus
        except Exception:
            pass

    # Fallback: /sys/class/drm
    gpus = []
    drm = Path("/sys/class/drm")
    if drm.exists():
        for card in drm.glob("card*/device/mem_info_vram_total"):
            try:
                vram_bytes = int(card.read_text().strip())
                vram_mb = vram_bytes // (1024 * 1024)
                gpus.append({
                    "vendor": "amd",
                    "name": "AMD GPU",
                    "vram_mb": vram_mb,
                    "is_igpu": vram_mb < 2048,
                    "compute_api": "rocm",
                })
            except Exception:
                pass
    return gpus


def detect_apple_silicon() -> list[dict[str, Any]]:
    """Detect Apple Silicon (unified memory) on macOS."""
    if platform.system() != "Darwin":
        return []
    out = _run(["system_profiler", "SPHardwareDataType"])
    if "Apple M" not in out:
        return []
    # Apple Silicon shares RAM — report system RAM as VRAM
    ram_mb = detect_ram()
    # Estimate usable GPU fraction (≈ 60% for M-series)
    usable = int(ram_mb * 0.6)
    model_match = re.search(r"Apple (M\d[\w\s]*?)(?:\n|$)", out)
    name = model_match.group(1).strip() if model_match else "Apple Silicon"
    return [{
        "vendor": "apple",
        "name": name,
        "vram_mb": usable,
        "is_igpu": False,  # M-series is not an iGPU in traditional sense
        "compute_api": "metal",
    }]


def detect_gpus() -> list[dict[str, Any]]:
    """Return a list of detected GPUs (best-effort, never raises)."""
    gpus: list[dict[str, Any]] = []
    gpus.extend(detect_nvidia_gpus())
    if not gpus:
        gpus.extend(detect_amd_gpus())
    if not gpus:
        gpus.extend(detect_apple_silicon())
    return gpus


# ──────────────────────────────────────────────────────────────────
# CPU / RAM / Disk
# ──────────────────────────────────────────────────────────────────

def detect_cpu() -> dict[str, Any]:
    cpu: dict[str, Any] = {
        "model": platform.processor() or "Unknown CPU",
        "arch": platform.machine(),
        "cores": os.cpu_count() or 1,
    }
    # Try to get a friendlier name on Linux
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                cpu["model"] = line.split(":", 1)[1].strip()
                break
    return cpu


def detect_ram() -> int:
    """Return total system RAM in MB."""
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb // 1024
    # macOS fallback
    out = _run(["sysctl", "-n", "hw.memsize"])
    if out:
        try:
            return int(out) // (1024 * 1024)
        except ValueError:
            pass
    return 0


def detect_disk(path: str | None = None) -> dict[str, Any]:
    """Return free disk space in GB for the data directory."""
    check_path = path or os.environ.get("CORTEX_DATA_DIR", "./data")
    try:
        usage = shutil.disk_usage(check_path)
        return {
            "free_gb": usage.free / (1024 ** 3),
            "total_gb": usage.total / (1024 ** 3),
            "path": check_path,
        }
    except Exception:
        return {"free_gb": 0.0, "total_gb": 0.0, "path": check_path}


# ──────────────────────────────────────────────────────────────────
# Model recommendations
# ──────────────────────────────────────────────────────────────────

_VRAM_TIERS = [
    (48000, {
        "fast": "qwen2.5:32b",
        "thinking": "qwen3:30b-a3b",
        "embedding": "nomic-embed-text",
        "class": "30B-70B",
        "default_context": 65536,
        "thinking_context": 131072,
    }),
    (24000, {
        "fast": "qwen2.5:14b",
        "thinking": "qwen3:30b-a3b",
        "embedding": "nomic-embed-text",
        "class": "30B-70B",
        "default_context": 32768,
        "thinking_context": 65536,
    }),
    (16000, {
        "fast": "qwen2.5:14b",
        "thinking": "qwen3:30b-a3b",
        "embedding": "nomic-embed-text",
        "class": "14B-30B",
        "default_context": 16384,
        "thinking_context": 32768,
    }),
    (8000, {
        "fast": "qwen2.5:7b",
        "thinking": "qwen2.5:14b",
        "embedding": "nomic-embed-text",
        "class": "7B-14B",
        "default_context": 8192,
        "thinking_context": 16384,
    }),
    (4000, {
        "fast": "qwen2.5:3b",
        "thinking": "qwen2.5:7b",
        "embedding": "nomic-embed-text",
        "class": "1B-7B",
        "default_context": 4096,
        "thinking_context": 8192,
    }),
    (0, {
        "fast": "qwen2.5:1.5b",
        "thinking": "qwen2.5:3b",
        "embedding": "nomic-embed-text",
        "class": "1B-3B (Q4)",
        "default_context": 2048,
        "thinking_context": 4096,
    }),
]


def recommend_models(hardware: dict[str, Any]) -> dict[str, Any]:
    """Return recommended model names based on available VRAM."""
    gpus = hardware.get("gpus", [])
    best_vram = max((g["vram_mb"] for g in gpus if not g.get("is_igpu", False)), default=0)
    if best_vram == 0 and gpus:
        best_vram = max(g["vram_mb"] for g in gpus)

    for min_vram, rec in _VRAM_TIERS:
        if best_vram >= min_vram:
            return rec
    return _VRAM_TIERS[-1][1]


# ──────────────────────────────────────────────────────────────────
# Master detect function
# ──────────────────────────────────────────────────────────────────

def detect_hardware() -> dict[str, Any]:
    """Detect all hardware. Returns a dict suitable for the hardware_profile table."""
    hw: dict[str, Any] = {}
    hw["gpus"] = detect_gpus()
    hw["cpu"] = detect_cpu()
    hw["ram_mb"] = detect_ram()
    hw["disk"] = detect_disk()
    hw["os"] = {
        "name": platform.system(),
        "version": platform.version(),
        "release": platform.release(),
    }
    hw["recommended_models"] = recommend_models(hw)
    return hw
