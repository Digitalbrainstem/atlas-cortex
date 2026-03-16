#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Atlas Distillation Pipeline — Master Orchestrator
# Runs all 22 distillation jobs on 1× H100 NVL (94GB)
# Estimated time: ~24 hours | Estimated cost: ~$36 on Vast.ai
#
# Usage:
#   bash tools/distillation/run_pipeline.sh [--resume] [--phase N]
#
# Phases:
#   1 = Teacher data generation
#   2 = Base model distillation (Ultra + Core)
#   3 = LoRA adapter training
#   4 = GGUF export
#   5 = Validation
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ─── Parse arguments ─────────────────────────────────────────────────────────
START_PHASE=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --resume) START_PHASE=1; shift ;;  # resume uses checkpoints to skip done steps
        --phase)  START_PHASE="$2"; shift 2 ;;
        *)        echo "Usage: $0 [--resume] [--phase N]"; exit 1 ;;
    esac
done

WORK="${WORK_DIR:-/workspace/atlas-distillation}"
DATA="${WORK}/data"
OUTPUT="${WORK}/output"
SCRIPTS="tools/distillation"
PROMPTS="data/distillation/prompts.jsonl"
CHECKPOINT_DIR="${WORK}/checkpoints"

# Teacher models — Qwen3.5 family (released Feb 2026)
# Using 122B-A10B MoE as primary teacher: 10B active params, AWQ ~60GB, fits on 94GB H100
GENERAL_TEACHER="QuantTrio/Qwen3.5-122B-A10B-AWQ"       # ~60GB AWQ, best quality
CODING_TEACHER="${GENERAL_TEACHER}"                       # same teacher, different prompts
REASONING_TEACHER="${GENERAL_TEACHER}"                    # same teacher, different prompts
MATH_TEACHER="${GENERAL_TEACHER}"                         # same teacher, different prompts
MEDICAL_TEACHER="${GENERAL_TEACHER}"                      # same teacher, different prompts
TEACHER_FALLBACK="QuantTrio/Qwen3.5-27B-AWQ"            # ~14GB AWQ fallback if 122B OOMs

# Student base models — Qwen3.5 (distillation target)
ULTRA_BASE="Qwen/Qwen3.5-9B"            # → Atlas Ultra (~5GB Q4)
CORE_BASE="Qwen/Qwen3.5-4B"             # → Atlas Core  (~2.5GB Q4)

VLLM_PORT=8000
VLLM_PID=""

mkdir -p "${DATA}" "${OUTPUT}"/{ultra,core,loras,gguf} "${CHECKPOINT_DIR}"

# ─── Helper functions ────────────────────────────────────────────────────────

log() { echo "$(date '+%H:%M:%S') ═══ $* ═══"; }

checkpoint_done() {
    [ -f "${CHECKPOINT_DIR}/$1.done" ]
}

mark_done() {
    touch "${CHECKPOINT_DIR}/$1.done"
    log "✓ Checkpoint: $1"
}

start_vllm() {
    local model="$1"
    local gpu_mem="${2:-0.92}"
    local extra_args="${3:-}"
    log "Starting vLLM server: ${model}"
    python3 -m vllm.entrypoints.openai.api_server \
        --model "${model}" \
        --port ${VLLM_PORT} \
        --gpu-memory-utilization "${gpu_mem}" \
        --max-model-len 4096 \
        --trust-remote-code \
        --dtype auto \
        --no-enable-log-requests \
        ${extra_args} \
        > "${WORK}/vllm.log" 2>&1 &
    VLLM_PID=$!
    log "vLLM PID: ${VLLM_PID}, waiting for server..."
    for i in $(seq 1 120); do
        if curl -s "http://localhost:${VLLM_PORT}/health" > /dev/null 2>&1; then
            log "vLLM server ready (${i}s)"
            return 0
        fi
        sleep 5
    done
    log "ERROR: vLLM server failed to start"
    cat "${WORK}/vllm.log" | tail -50
    return 1
}

stop_vllm() {
    if [ -n "${VLLM_PID}" ] && kill -0 "${VLLM_PID}" 2>/dev/null; then
        log "Stopping vLLM (PID ${VLLM_PID})"
        kill "${VLLM_PID}" 2>/dev/null || true
        wait "${VLLM_PID}" 2>/dev/null || true
        VLLM_PID=""
        sleep 5
    fi
}

generate_teacher() {
    local name="$1"
    local model="$2"
    local output_file="$3"
    local prompts_file="$4"
    local extra_args="${5:-}"

    if checkpoint_done "teacher_${name}"; then
        log "Skipping teacher_${name} (already done)"
        return
    fi

    log "Phase 1: Generating ${name} teacher data from ${prompts_file}"

    python3 "${SCRIPTS}/generate_teacher_data.py" \
        --api-url "http://localhost:${VLLM_PORT}/v1" \
        --api-type openai \
        --model "${model}" \
        --prompts "${prompts_file}" \
        --output "${output_file}" \
        --workers 24 \
        --batch-size 200 \
        ${extra_args}

    mark_done "teacher_${name}"
}

# ─── Trap for cleanup ────────────────────────────────────────────────────────
cleanup() {
    stop_vllm
    log "Pipeline interrupted. Resume with: bash $0 --resume"
}
trap cleanup EXIT

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Teacher Data Generation (~14h on 1× H100 NVL)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "${START_PHASE}" -le 1 ]; then
    log "PHASE 1: Teacher Data Generation (Qwen3.5-122B-A10B)"

    # Start the single teacher model — used for all domains
    # --enforce-eager needed for qwen3_next (Mamba hybrid) CUDA graph compatibility
    # --reasoning-parser qwen3: properly separates thinking from answer in API
    # --language-model-only: skip vision encoder, free VRAM for more KV cache
    start_vllm "${GENERAL_TEACHER}" "0.92" "--enforce-eager --reasoning-parser qwen3 --language-model-only"

    # General knowledge — 15K prompts (no thinking, fast)
    generate_teacher "general" "${GENERAL_TEACHER}" "${DATA}/teacher_general.jsonl" "${PROMPTS}"

    # Domain specialists — research-level, enable thinking for quality
    generate_teacher "coding" "${GENERAL_TEACHER}" "${DATA}/teacher_coding.jsonl" \
        "${DATA}/prompts_coding.jsonl" "--think"

    generate_teacher "reasoning" "${GENERAL_TEACHER}" "${DATA}/teacher_reasoning.jsonl" \
        "${DATA}/prompts_reasoning.jsonl" "--think"

    generate_teacher "math" "${GENERAL_TEACHER}" "${DATA}/teacher_math.jsonl" \
        "${DATA}/prompts_math.jsonl" "--think"

    generate_teacher "medical" "${GENERAL_TEACHER}" "${DATA}/teacher_medical.jsonl" \
        "${DATA}/prompts_medical.jsonl" "--think"

    stop_vllm
    log "PHASE 1 COMPLETE — All teacher data generated"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Base Model Distillation (~5h)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "${START_PHASE}" -le 2 ]; then
    log "PHASE 2: Base Model Distillation"

    # Stage 1: 122B teacher data → Ultra (Qwen3.5-9B base, distilled)
    if ! checkpoint_done "distill_ultra"; then
        log "Distilling Atlas Ultra from ${ULTRA_BASE}"
        python3 "${SCRIPTS}/train_student.py" \
            --student "${ULTRA_BASE}" \
            --teacher-data "${DATA}/teacher_general.jsonl" \
            --output "${OUTPUT}/ultra" \
            --epochs 3 \
            --lr 2e-4 \
            --batch-size 4 \
            --grad-accum 4 \
            --max-seq-length 2048 \
            --merge
        mark_done "distill_ultra"
    fi

    # Stage 2: Train Core (Qwen3.5-4B) on same teacher data
    if ! checkpoint_done "distill_core"; then
        log "Distilling Atlas Core from ${CORE_BASE}"
        python3 "${SCRIPTS}/train_student.py" \
            --student "${CORE_BASE}" \
            --teacher-data "${DATA}/teacher_general.jsonl" \
            --output "${OUTPUT}/core" \
            --epochs 5 \
            --lr 3e-4 \
            --batch-size 4 \
            --grad-accum 4 \
            --max-seq-length 2048 \
            --merge
        mark_done "distill_core"
    fi

    log "PHASE 2 COMPLETE — Ultra and Core distilled"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: LoRA Adapter Training (~8h)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "${START_PHASE}" -le 3 ]; then
    log "PHASE 3: LoRA Adapter Training"

    # Train LoRAs on the Ultra base (they also work on Core)
    LORA_BASE="${OUTPUT}/ultra/merged"

    for domain in general coding reasoning math medical; do
        if ! checkpoint_done "lora_${domain}"; then
            data_file="${DATA}/teacher_${domain}.jsonl"
            if [ ! -f "${data_file}" ]; then
                log "WARNING: ${data_file} not found, skipping ${domain} LoRA"
                continue
            fi
            log "Training ${domain}.lora"
            python3 "${SCRIPTS}/train_lora.py" \
                --base-model "${LORA_BASE}" \
                --train-data "${data_file}" \
                --output "${OUTPUT}/loras/${domain}" \
                --rank 64 \
                --alpha 128 \
                --epochs 3 \
                --lr 2e-4 \
                --batch-size 4 \
                --grad-accum 4
            mark_done "lora_${domain}"
        fi
    done

    log "PHASE 3 COMPLETE — All LoRA adapters trained"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: GGUF Export (~30 min)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "${START_PHASE}" -le 4 ]; then
    log "PHASE 4: GGUF Export"

    if ! checkpoint_done "gguf_ultra"; then
        python3 "${SCRIPTS}/export_gguf.py" \
            --model "${OUTPUT}/ultra/merged" \
            --output "${OUTPUT}/gguf/atlas-ultra-q4.gguf" \
            --quant Q4_K_M
        mark_done "gguf_ultra"
    fi

    if ! checkpoint_done "gguf_core"; then
        python3 "${SCRIPTS}/export_gguf.py" \
            --model "${OUTPUT}/core/merged" \
            --output "${OUTPUT}/gguf/atlas-core-q4.gguf" \
            --quant Q4_K_M
        mark_done "gguf_core"
    fi

    log "PHASE 4 COMPLETE — GGUF models exported"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Quick Validation
# ═══════════════════════════════════════════════════════════════════════════════

if [ "${START_PHASE}" -le 5 ]; then
    log "PHASE 5: Quick Validation"

    python3 -c "
import json
from pathlib import Path

output = Path('${OUTPUT}')
print('═══ Atlas Distillation Results ═══')
print()

# Check GGUF files
for gguf in sorted(output.glob('gguf/*.gguf')):
    size_gb = gguf.stat().st_size / 1e9
    print(f'  GGUF: {gguf.name} ({size_gb:.2f} GB)')

print()

# Check LoRA adapters
for lora_dir in sorted(output.glob('loras/*')):
    meta_file = lora_dir / 'adapter_meta.json'
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        print(f'  LoRA: {lora_dir.name} (rank={meta[\"rank\"]}, {meta[\"examples\"]} examples)')

print()
print('═══ Pipeline complete! ═══')
print('Download these artifacts to your local machine:')
print(f'  rsync -avz {output}/gguf/ local:models/')
print(f'  rsync -avz {output}/loras/ local:models/loras/')
"

    log "PHASE 5 COMPLETE — All done!"
fi

log "═══════════════════════════════════════════════════════════════"
log "  Atlas Distillation Pipeline COMPLETE"
log "  Output: ${OUTPUT}/"
log "  GGUFs: ${OUTPUT}/gguf/"
log "  LoRAs: ${OUTPUT}/loras/"
log "═══════════════════════════════════════════════════════════════"
