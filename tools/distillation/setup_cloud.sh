#!/usr/bin/env bash
# Atlas Distillation — Cloud GPU Setup
# Run once on a fresh Vast.ai / Azure H100 NVL instance
set -euo pipefail

WORK_DIR="${WORK_DIR:-/workspace/atlas-distillation}"
MODELS_DIR="${WORK_DIR}/models"
DATA_DIR="${WORK_DIR}/data"
OUTPUT_DIR="${WORK_DIR}/output"

echo "═══ Atlas Distillation — Cloud Setup ═══"
echo "Work dir: ${WORK_DIR}"

mkdir -p "${MODELS_DIR}" "${DATA_DIR}" "${OUTPUT_DIR}"/{ultra,core,loras,tts,gguf}

# --- Install Python dependencies ---
echo "→ Installing Python dependencies..."
pip install -q -r tools/distillation/requirements-cloud.txt

# --- Install llama.cpp for GGUF export ---
echo "→ Installing llama.cpp (for GGUF conversion)..."
if [ ! -d "${WORK_DIR}/llama.cpp" ]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "${WORK_DIR}/llama.cpp"
    cd "${WORK_DIR}/llama.cpp"
    pip install -q -r requirements.txt 2>/dev/null || true
    cd -
fi

# --- Download teacher models (cached by HuggingFace) ---
echo "→ Pre-downloading teacher models (this caches them for fast loading)..."
python3 -c "
from huggingface_hub import snapshot_download
import os

models = [
    ('Qwen/Qwen2.5-72B-Instruct-AWQ', 'General teacher (72B AWQ)'),
    ('deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct', 'Coding teacher (16B)'),
    ('deepseek-ai/DeepSeek-R1-Distill-Qwen-32B', 'Reasoning teacher (32B)'),
    ('Qwen/Qwen2.5-Math-72B-Instruct', 'Math teacher (72B)'),
    ('BioMistral/BioMistral-7B', 'Medical teacher (7B)'),
    ('Qwen/Qwen2.5-7B-Instruct', 'Student base — Core 2B alt'),
    ('Qwen/Qwen2.5-14B-Instruct', 'Student base — Ultra 9B alt'),
]

for model_id, desc in models:
    print(f'  Downloading: {desc} ({model_id})...')
    try:
        snapshot_download(model_id, resume_download=True)
        print(f'  ✓ {desc} ready')
    except Exception as e:
        print(f'  ⚠ {desc} failed: {e} (will retry at runtime)')
"

echo ""
echo "═══ Setup complete! ═══"
echo "Run the pipeline with:"
echo "  bash tools/distillation/run_pipeline.sh"
