#!/usr/bin/env python3
"""Export a HuggingFace model to GGUF format using llama.cpp's converter.

Usage:
    python3 tools/distillation/export_gguf.py \
        --model output/ultra/merged \
        --output output/gguf/atlas-ultra-9b-q4.gguf \
        --quant Q4_K_M
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LLAMA_CPP_DIR = Path(os.environ.get("LLAMA_CPP_DIR", "/workspace/atlas-distillation/llama.cpp"))


def find_convert_script() -> Path:
    """Find the llama.cpp convert script."""
    candidates = [
        LLAMA_CPP_DIR / "convert_hf_to_gguf.py",
        LLAMA_CPP_DIR / "convert-hf-to-gguf.py",
        Path("llama.cpp") / "convert_hf_to_gguf.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"llama.cpp convert script not found. Tried: {[str(c) for c in candidates]}. "
        f"Set LLAMA_CPP_DIR or run setup_cloud.sh first."
    )


def find_quantize_binary() -> Path:
    """Find the llama-quantize binary."""
    candidates = [
        LLAMA_CPP_DIR / "build" / "bin" / "llama-quantize",
        LLAMA_CPP_DIR / "llama-quantize",
        shutil.which("llama-quantize"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def convert_to_gguf(model_dir: Path, output_fp16: Path) -> None:
    """Convert HF model to GGUF FP16."""
    script = find_convert_script()
    cmd = [sys.executable, str(script), str(model_dir), "--outfile", str(output_fp16), "--outtype", "f16"]
    log.info("Converting to GGUF FP16: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Convert failed:\n%s\n%s", result.stdout[-2000:], result.stderr[-2000:])
        raise RuntimeError("GGUF conversion failed")
    log.info("FP16 GGUF created: %s (%.1f GB)", output_fp16, output_fp16.stat().st_size / 1e9)


def quantize_gguf(input_fp16: Path, output_quant: Path, quant_type: str) -> None:
    """Quantize GGUF from FP16 to target quantization."""
    quantize_bin = find_quantize_binary()
    if quantize_bin is None:
        log.warning("llama-quantize not found, building llama.cpp...")
        subprocess.run(
            ["cmake", "-B", "build", "-DGGML_CUDA=OFF"],
            cwd=str(LLAMA_CPP_DIR), check=True,
        )
        subprocess.run(
            ["cmake", "--build", "build", "--target", "llama-quantize", "-j"],
            cwd=str(LLAMA_CPP_DIR), check=True,
        )
        quantize_bin = LLAMA_CPP_DIR / "build" / "bin" / "llama-quantize"

    cmd = [str(quantize_bin), str(input_fp16), str(output_quant), quant_type]
    log.info("Quantizing to %s: %s", quant_type, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Quantize failed:\n%s\n%s", result.stdout[-2000:], result.stderr[-2000:])
        raise RuntimeError("GGUF quantization failed")
    log.info(
        "Quantized GGUF: %s (%.2f GB)",
        output_quant, output_quant.stat().st_size / 1e9,
    )


def main(args: argparse.Namespace) -> None:
    model_dir = Path(args.model)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fp16_path = output_path.with_suffix(".fp16.gguf")

    log.info("Step 1: Convert HF → GGUF FP16")
    convert_to_gguf(model_dir, fp16_path)

    if args.quant.upper() == "F16":
        fp16_path.rename(output_path)
        log.info("Keeping FP16: %s", output_path)
    else:
        log.info("Step 2: Quantize FP16 → %s", args.quant)
        quantize_gguf(fp16_path, output_path, args.quant)
        fp16_path.unlink()
        log.info("Removed intermediate FP16 file")

    log.info("Done! GGUF at %s (%.2f GB)", output_path, output_path.stat().st_size / 1e9)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export model to GGUF")
    parser.add_argument("--model", required=True, help="HF model directory")
    parser.add_argument("--output", required=True, help="Output GGUF path")
    parser.add_argument("--quant", default="Q4_K_M", help="Quantization type (Q4_K_M, Q5_K_M, Q8_0, F16)")
    args = parser.parse_args()
    main(args)
