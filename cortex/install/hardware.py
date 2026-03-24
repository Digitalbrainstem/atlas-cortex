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
            lower = name.lower()
            is_igpu = (
                any(x in lower for x in ["tegra", "orin", "xavier", "integrated"])
                or vram_mb < 1024
            )
            gpus.append({
                "vendor": "nvidia",
                "name": name,
                "vram_mb": vram_mb,
                "is_igpu": is_igpu,
                "compute_api": "cuda",
            })
    return gpus


_AMD_IGPU_MARKERS = [
    "vega 3", "vega 6", "vega 7", "vega 8", "vega 10", "vega 11",
    "raphael", "780m", "680m", "610m", "integrated",
]

# Known AMD discrete VRAM sizes (substring → MB) for lspci fallback
_AMD_VRAM_HINTS: list[tuple[str, int]] = [
    ("7900 xtx", 24576), ("7900 xt", 20480), ("7900 gre", 16384),
    ("7800 xt", 16384), ("7700 xt", 12288), ("7600", 8192),
    ("6950 xt", 16384), ("6900 xt", 16384), ("6800 xt", 16384),
    ("6800", 16384), ("6750 xt", 12288), ("6700 xt", 12288),
    ("6600 xt", 8192), ("6600", 8192),
]


def _is_amd_igpu(name: str, vram_mb: int) -> bool:
    lower = name.lower()
    if any(x in lower for x in _AMD_IGPU_MARKERS):
        return True
    # Small VRAM with no discrete-class model name ⇒ likely iGPU
    return vram_mb > 0 and vram_mb < 2048


def detect_amd_gpus() -> list[dict[str, Any]]:
    """Detect AMD GPUs via rocm-smi, /sys, or lspci fallback."""
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
                    name = info.get("Card series", "AMD GPU")
                    gpus.append({
                        "vendor": "amd",
                        "name": name,
                        "vram_mb": vram_mb,
                        "is_igpu": _is_amd_igpu(name, vram_mb),
                        "compute_api": "rocm",
                    })
            if gpus:
                return gpus
        except Exception:
            pass

    # Fallback: /sys/class/drm
    gpus: list[dict[str, Any]] = []
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
                    "is_igpu": _is_amd_igpu("AMD GPU", vram_mb),
                    "compute_api": "rocm",
                })
            except Exception:
                pass
    if gpus:
        return gpus

    # Fallback: lspci
    out = _run(["lspci", "-nn"])
    for line in out.splitlines():
        if not ("VGA" in line or "Display" in line or "3D controller" in line):
            continue
        lower = line.lower()
        if "amd" not in lower and "radeon" not in lower and "advanced micro" not in lower:
            continue
        name = line.split(": ", 1)[1] if ": " in line else "AMD GPU"
        vram_mb = 0
        name_lower = name.lower()
        for hint, mb in _AMD_VRAM_HINTS:
            if hint in name_lower:
                vram_mb = mb
                break
        gpus.append({
            "vendor": "amd",
            "name": name.strip(),
            "vram_mb": vram_mb,
            "is_igpu": _is_amd_igpu(name, vram_mb),
            "compute_api": "rocm",
        })
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


def detect_intel_gpus() -> list[dict[str, Any]]:
    """Detect Intel GPUs (Arc discrete or integrated UHD/Iris)."""
    gpus: list[dict[str, Any]] = []
    out = _run(["lspci", "-nn"])
    for line in out.splitlines():
        if not ("VGA" in line or "Display" in line or "3D controller" in line):
            continue
        lower = line.lower()
        if "intel" not in lower:
            continue
        name = line.split(": ", 1)[1] if ": " in line else "Intel GPU"
        is_discrete = any(
            x in lower
            for x in ["arc", "a770", "a750", "a580", "a380", "a310", "b580"]
        )
        vram_mb = 0
        if is_discrete:
            name_lower = name.lower()
            if "a770" in name_lower:
                vram_mb = 16384
            elif "a750" in name_lower or "a580" in name_lower:
                vram_mb = 8192
            elif "b580" in name_lower:
                vram_mb = 12288
            elif "a380" in name_lower:
                vram_mb = 6144
            elif "a310" in name_lower:
                vram_mb = 4096
            else:
                vram_mb = 4096  # conservative guess for unknown Arc
        gpus.append({
            "vendor": "intel",
            "name": name.strip(),
            "vram_mb": vram_mb,
            "is_igpu": not is_discrete,
            "compute_api": "sycl" if is_discrete else "none",
        })
    return gpus


def detect_gpus() -> list[dict[str, Any]]:
    """Detect ALL GPUs from ALL vendors.

    A system may have mixed vendors (e.g. AMD + NVIDIA), so every vendor
    is always checked regardless of earlier results.  Apple Silicon is the
    exception — it is only probed when no discrete GPUs are found (there is
    no mixed-vendor scenario on Apple hardware).
    """
    gpus: list[dict[str, Any]] = []
    gpus.extend(detect_nvidia_gpus())
    gpus.extend(detect_amd_gpus())
    gpus.extend(detect_intel_gpus())
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

# Atlas distilled models — preferred when available
ATLAS_MODELS = {
    "ultra": {
        "name": "atlas-ultra:9b",
        "params": "9B",
        "min_vram": 8000,
        "description": "Atlas Ultra 9B — best quality",
    },
    "core": {
        "name": "atlas-core:2b",
        "params": "2B",
        "min_vram": 4000,
        "description": "Atlas Core 2B — faster, lighter",
    },
}

ATLAS_LORAS = {
    "coding.lora": {"description": "Code generation & debugging", "size_mb": 50, "recommended": True},
    "reasoning.lora": {"description": "Chain-of-thought & logic", "size_mb": 50, "recommended": True},
    "math.lora": {"description": "Mathematical problem solving", "size_mb": 50, "recommended": True},
    "atlas.lora": {"description": "Atlas personality & home context", "size_mb": 50, "recommended": True},
    "medical.lora": {"description": "Clinical reasoning & health", "size_mb": 50, "recommended": False},
    "sysadmin.lora": {"description": "Server management & networking", "size_mb": 50, "recommended": False},
    "security.lora": {"description": "Vulnerability analysis & hardening", "size_mb": 50, "recommended": False},
    "creative.lora": {"description": "Creative writing & storytelling", "size_mb": 50, "recommended": False},
}

# VRAM tiers: (min_vram_mb, {atlas models, fallback models, ...})
_VRAM_TIERS = [
    (48000, {
        "fast": "qwen3.5:27b",
        "fast_fallback": "qwen3.5:27b",
        "thinking": "qwen3.5:27b",
        "embedding": "nomic-embed-text",
        "loras": ["coding.lora", "reasoning.lora", "math.lora", "atlas.lora"],
        "class": "27B+",
        "default_context": 65536,
        "thinking_context": 262144,
    }),
    (24000, {
        "fast": "qwen3.5:27b",
        "fast_fallback": "qwen3.5:27b",
        "thinking": "qwen3.5:27b",
        "embedding": "nomic-embed-text",
        "loras": ["coding.lora", "reasoning.lora", "math.lora", "atlas.lora"],
        "class": "27B",
        "default_context": 32768,
        "thinking_context": 131072,
    }),
    (16000, {
        "fast": "qwen3.5:9b",
        "fast_fallback": "qwen3.5:9b",
        "thinking": "qwen3.5:27b",
        "embedding": "nomic-embed-text",
        "loras": ["coding.lora", "reasoning.lora", "math.lora", "atlas.lora"],
        "class": "9B-27B",
        "default_context": 32768,
        "thinking_context": 65536,
        "note": "27B fits at Q4 (~16.5GB), 9B for fast responses",
    }),
    (8000, {
        "fast": "qwen3.5:9b",
        "fast_fallback": "qwen3.5:9b",
        "thinking": "qwen3.5:9b",
        "embedding": "nomic-embed-text",
        "loras": ["coding.lora", "reasoning.lora"],
        "class": "9B",
        "default_context": 16384,
        "thinking_context": 32768,
    }),
    (4000, {
        "fast": "qwen3.5:9b",
        "fast_fallback": "qwen2.5:3b",
        "thinking": "qwen3.5:9b",
        "embedding": "nomic-embed-text",
        "loras": ["atlas.lora"],
        "class": "3B-9B",
        "default_context": 8192,
        "thinking_context": 16384,
    }),
    (0, {
        "fast": "qwen2.5:1.5b",
        "fast_fallback": "qwen2.5:1.5b",
        "thinking": "qwen2.5:3b",
        "embedding": "nomic-embed-text",
        "loras": [],
        "class": "1B-3B (CPU)",
        "default_context": 2048,
        "thinking_context": 4096,
    }),
]


def check_atlas_model(model: str) -> bool:
    """Check whether an Atlas model is available via Ollama."""
    out = _run(["ollama", "show", model])
    return bool(out)


def recommend_models(hardware: dict[str, Any]) -> dict[str, Any]:
    """Return recommended model names based on available VRAM.

    Prefers Atlas distilled models (atlas-ultra:9b, atlas-core:2b) when
    the VRAM tier supports them.  The ``fast_fallback`` key contains the
    generic Qwen model to use when the Atlas model is not yet available.
    """
    gpus = hardware.get("gpus", [])
    best_vram = max((g["vram_mb"] for g in gpus if not g.get("is_igpu", False)), default=0)
    if best_vram == 0 and gpus:
        best_vram = max(g["vram_mb"] for g in gpus)

    for min_vram, rec in _VRAM_TIERS:
        if best_vram >= min_vram:
            return dict(rec)
    return dict(_VRAM_TIERS[-1][1])


def resolve_fast_model(rec: dict[str, Any]) -> str:
    """Return the best available fast model.

    Checks whether the Atlas model is already pulled; falls back to the
    generic Qwen model if not.
    """
    atlas = rec.get("fast", "")
    if atlas.startswith("atlas-") and check_atlas_model(atlas):
        return atlas
    return rec.get("fast_fallback", atlas)


# ──────────────────────────────────────────────────────────────────
# Deployment recommendation
# ──────────────────────────────────────────────────────────────────

def _docker_variant(primary: dict[str, Any], secondary: dict[str, Any] | None = None) -> str:
    """Determine which docker-compose GPU override to use."""
    vendors = {primary["vendor"]}
    if secondary:
        vendors.add(secondary["vendor"])

    if vendors == {"nvidia"}:
        return "gpu-nvidia"
    elif vendors == {"amd"}:
        return "gpu-amd"
    elif "nvidia" in vendors and "amd" in vendors:
        return "gpu-both"
    elif "intel" in vendors:
        return "gpu-intel"
    return "cpu"


def recommend_deployment(hardware: dict[str, Any]) -> dict[str, Any]:
    """Recommend how to deploy across available hardware.

    Returns a deployment plan with tier, device assignments, model
    recommendations, human-readable notes, and docker-compose variant.
    """
    gpus = hardware.get("gpus", [])
    discrete_gpus = [g for g in gpus if not g.get("is_igpu", False)]

    if len(discrete_gpus) >= 2:
        sorted_gpus = sorted(discrete_gpus, key=lambda g: g["vram_mb"], reverse=True)
        primary = sorted_gpus[0]
        secondary = sorted_gpus[1]

        notes = [
            f"LLM on {primary['name']} ({primary['vram_mb'] // 1024}GB) via {primary.get('compute_api', '?').upper()}",
            f"TTS + specialist models on {secondary['name']} ({secondary['vram_mb'] // 1024}GB) via {secondary.get('compute_api', '?').upper()}",
            "LoRA training can run overnight on the LLM GPU",
        ]
        if primary.get("vendor") != secondary.get("vendor"):
            notes.append(
                f"Mixed vendor: LLM uses {primary.get('compute_api', '?').upper()}, "
                f"TTS uses {secondary.get('compute_api', '?').upper()}"
            )

        return {
            "tier": "dual-gpu",
            "llm_device": primary,
            "tts_device": secondary,
            "specialist_device": secondary,
            "models": recommend_models(hardware),
            "notes": notes,
            "docker_compose_variant": _docker_variant(primary, secondary),
        }

    elif len(discrete_gpus) == 1:
        gpu = discrete_gpus[0]
        return {
            "tier": "single-gpu",
            "llm_device": gpu,
            "tts_device": gpu,
            "specialist_device": None,
            "models": recommend_models(hardware),
            "notes": [
                f"All models share {gpu['name']} ({gpu['vram_mb'] // 1024}GB)",
                "TTS and LLM take turns on the same GPU",
            ],
            "docker_compose_variant": _docker_variant(gpu),
        }

    else:
        return {
            "tier": "cpu-only",
            "llm_device": None,
            "tts_device": None,
            "specialist_device": None,
            "models": _VRAM_TIERS[-1][1],
            "notes": [
                "No discrete GPU detected — running CPU-only",
                "Expect slower responses (5-15 seconds)",
                "Use smaller models: qwen2.5:1.5b or qwen2.5:3b",
                "TTS: Piper (CPU, fast) recommended over GPU-based TTS",
            ],
            "docker_compose_variant": "cpu",
        }


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
    hw["deployment"] = recommend_deployment(hw)
    return hw
