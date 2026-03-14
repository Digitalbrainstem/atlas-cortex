# Atlas Model Distillation Architecture

## Overview

Atlas uses a **cascade distillation** approach to create a family of optimized models
from a single teacher run. All models share the same LoRA adapter ecosystem, enabling
consistent domain expertise across hardware tiers.

```
Qwen3.5-70B (cloud teacher — run once)
    │
    ├──→ Atlas Ultra  (9B)  ← Direct distill      │ 5.5GB Q4 │ 60-80 tok/s GPU
    │        │
    │        └──→ Atlas Core (2B)  ← Stage 2       │ 1.3GB Q4 │ 150-200 tok/s GPU
    │
    └── Shared LoRA adapters: coding, reasoning, math, medical, atlas personality
```

**Future tiers** (Phase 3+):
- Atlas Lite (0.8B) — Raspberry Pi, phones
- Atlas Nano (0.3B) — IoT edge, voice-only

---

## Model Tiers

### Atlas Ultra — 9B Dense (Primary)

| Metric | Value |
|--------|-------|
| Base model | Qwen3.5-9B |
| Teacher | Qwen3.5-70B (cloud) |
| Distillation | Direct (70B → 9B) |
| Q4_K_M size | ~5.5GB |
| Base MMLU | 82.5 |
| Post-distill MMLU (est.) | ~85-87 |
| With LoRAs (est.) | ~90-95 |
| With inference tricks (est.) | ~92-95 |
| Inference speed (GPU) | 60-80 tok/s |
| Min hardware | 8GB VRAM GPU |
| Target use | Power users, dual-GPU setups |

### Atlas Core — 2B Dense (Default)

| Metric | Value |
|--------|-------|
| Base model | Qwen3.5-2B |
| Teacher | Atlas Ultra 9B (two-stage: 70B → 9B → 2B) |
| Q4_K_M size | ~1.3GB |
| Base MMLU | 66 |
| Post-distill MMLU (est.) | ~80-83 |
| With LoRAs (est.) | ~87-91 |
| With inference tricks (est.) | ~89-93 |
| Inference speed (GPU) | 150-200 tok/s |
| Inference speed (CPU) | 20-40 tok/s |
| Min hardware | 4GB VRAM or modern CPU |
| Target use | Default model, single-GPU setups |

---

## LoRA Adapter Ecosystem

Each adapter is trained from the **best specialist open model** for that domain.
All adapters work with both Ultra and Core base models.

### Domain Adapters

| Adapter | Teacher Model | Size | Purpose |
|---------|--------------|------|---------|
| `general.lora` | Qwen3.5-70B | ~50MB | General knowledge boost |
| `coding.lora` | DeepSeek-Coder-V2-236B | ~50MB | Code generation, debugging |
| `reasoning.lora` | DeepSeek-R1-671B | ~50MB | Chain-of-thought, logic |
| `math.lora` | DeepSeek-Math / Qwen-Math | ~50MB | Mathematical problem solving |
| `medical.lora` | BioMistral / OpenBioLLM | ~50MB | Clinical reasoning, health |
| `atlas.lora` | Atlas conversation logs | ~50MB | Personality, home context |

### How LoRAs Are Trained

1. Run specialist teacher on curated domain prompts (1K-5K per domain)
2. Collect full responses including reasoning traces
3. QLoRA fine-tune (4-bit) the base model on teacher outputs
4. Each adapter: ~1-2 hours training on H100, ~3-4 hours on A100
5. Validate against domain benchmark + Atlas core principles test

### LoRA Routing

The pipeline's existing model router (`cortex/providers/`) is extended to:
1. Classify incoming query by domain (coding, math, medical, general)
2. Activate the appropriate LoRA adapter(s)
3. Multiple LoRAs can stack for cross-domain queries
4. Fallback: general.lora if domain unclear

---

## Inference-Time Enhancements

These techniques boost quality with no additional training:

### For Hard Reasoning
- **Best-of-N sampling** (N=8-16): Generate multiple attempts, pick most consistent
  - +5-10% on reasoning benchmarks, 8-16x compute cost
  - Only triggered for queries classified as "complex reasoning"
- **Self-verification loop**: Generate → critique → regenerate (2-3 iterations)
  - +3-5% on reasoning, 2-3x compute cost
- **MCTS** (future): Monte Carlo Tree Search over reasoning steps
  - +10-15% on hard reasoning, 10-50x compute cost

### For Math
- **Code-augmented math**: Model writes Python, executes, returns verified answer
  - +3-5% on math benchmarks, requires Python runtime
- **Tool use**: Calculator for arithmetic, SymPy for algebra
  - +2-3%, negligible cost
- **Majority voting** (×5): Solve 5 times, take most common answer
  - +2-4%, 5x compute cost

### Adaptive Compute Budget
```
Simple query ("What time is it?")     → Direct answer, no tricks     (~50ms)
Medium query ("Explain photosynthesis") → Single pass + general.lora   (~200ms)
Hard reasoning ("Compare X and Y...")   → Best-of-4 + reasoning.lora   (~800ms)
Complex math ("Prove that...")          → Code-augmented + verify       (~2-5s)
```

The pipeline classifies query complexity and allocates compute accordingly.
Simple queries stay fast; hard queries get extra quality.

---

## Distillation Pipeline

### Phase 1: Teacher Data Generation (Cloud GPU)

**Input**: 15K curated prompts across all domains
**Output**: JSONL files with (prompt, response, reasoning_trace) triplets

```bash
# Run on rented H100 / A100
python tools/distillation/generate_teacher_data.py \
  --model qwen3.5-70b \
  --prompts data/distillation/prompts.jsonl \
  --output data/distillation/teacher_responses.jsonl \
  --workers 4 \
  --think true  # Capture reasoning traces
```

**Domain-specific data** (for LoRA training):
```bash
# Generate coding teacher data from DeepSeek-Coder
python tools/distillation/generate_teacher_data.py \
  --model deepseek-coder-v2 \
  --prompts data/distillation/prompts_coding.jsonl \
  --output data/distillation/teacher_coding.jsonl

# Repeat for: reasoning (DeepSeek-R1), math, medical
```

### Phase 2: Base Model Distillation

**Stage 1**: 70B → 9B (Atlas Ultra)
```bash
python tools/distillation/train_student.py \
  --student qwen3.5-9b \
  --teacher-data data/distillation/teacher_responses.jsonl \
  --output models/atlas-ultra-9b \
  --method qlora \
  --epochs 3 \
  --lr 2e-4
```

**Stage 2**: 9B → 2B (Atlas Core)
```bash
# Use the distilled 9B as teacher for the 2B
python tools/distillation/train_student.py \
  --student qwen3.5-2b \
  --teacher-model models/atlas-ultra-9b \
  --prompts data/distillation/prompts.jsonl \
  --output models/atlas-core-2b \
  --method qlora \
  --epochs 5 \
  --lr 3e-4
```

### Phase 3: LoRA Adapter Training

```bash
# Train each domain adapter
for domain in coding reasoning math medical atlas; do
  python tools/distillation/train_lora.py \
    --base-model models/atlas-core-2b \
    --train-data data/distillation/teacher_${domain}.jsonl \
    --output models/loras/${domain}.lora \
    --rank 64 \
    --alpha 128 \
    --epochs 3
done
```

### Phase 4: Quantization & Packaging

```bash
# Convert to GGUF Q4_K_M for deployment
python tools/distillation/export_gguf.py \
  --model models/atlas-ultra-9b \
  --quant Q4_K_M \
  --output models/atlas-ultra-9b-q4.gguf

python tools/distillation/export_gguf.py \
  --model models/atlas-core-2b \
  --quant Q4_K_M \
  --output models/atlas-core-2b-q4.gguf
```

### Phase 5: Benchmarking

```bash
python tools/distillation/benchmark.py \
  --models atlas-ultra-9b,atlas-core-2b \
  --benchmarks mmlu,swe-bench,arc-agi,math \
  --loras coding,reasoning,math,medical \
  --output data/distillation/benchmark_results.json
```

---

## Orpheus TTS Distillation (Phase 2.5)

Orpheus 3B is an LLM-based TTS model (~5-7GB on GPU). Distillation targets:

### Orpheus Model Family (Canopy Labs ships multiple sizes)

| Variant | Params | Q4 File | VRAM | Quality |
|---------|--------|---------|------|---------|
| Orpheus-3B (stock) | 3B | 2.2GB | 2.5GB | Excellent + full emotions |
| Orpheus-1B | 1B | ~0.7GB | ~0.9GB | Very good + emotions |
| Orpheus-400M | 400M | ~0.3GB | ~0.4GB | Good + basic emotions |
| Orpheus-150M | 150M | ~0.1GB | ~0.2GB | Decent, less expression |

### Compression Stack (English-Only + Pruning + Distillation)

Using SPADE methodology (Structured Pruning and Adaptive Distillation):
1. **English-only stripping**: Remove multilingual vocab/embeddings (~25-30% reduction)
2. **Structured pruning**: Halve transformer depth using importance metrics (~20% VRAM, 1.7x faster)
3. **Distillation**: 3B teacher → 1B student retains emotion capability
4. **Quantization**: Q4_K_M with minimal perceptible quality loss

| Our Variant | Base | VRAM (est.) | Quality |
|------------|------|-------------|---------|
| Orpheus-EN-3B Q4 | 3B stripped | ~1.8-2.0GB | Full quality, English only |
| Orpheus-EN-1B Q4 | 1B stripped+pruned | ~0.6-0.7GB | Sweet spot for shared GPU |
| Orpheus-EN-400M Q4 | 400M stripped | ~0.2-0.3GB | Minimal footprint, still emotional |
| Kokoro (fallback) | N/A | ~0.5GB CPU | Good quality, no emotions, CPU only |

### LoRA Adapters for Orpheus

Since Orpheus is Llama-based, LoRA works identically to LLM adapters:

```
Orpheus-EN-1B (base, ~0.7GB)
  + voice_bella.lora     (10-20MB)  ← Default Atlas voice personality
  + voice_custom.lora    (10-20MB)  ← User's custom voice clone
  + medical_vocab.lora   (10-20MB)  ← Medical pronunciation (78%→95% accuracy)
  + emotions.lora        (10-20MB)  ← Enhanced emotion range (distilled from 3B)
```

LoRA targets: `q_proj`, `v_proj` attention layers. Each ~10-20MB, swaps in ms.

### Parallel Pipeline Execution (Single GPU)

Orpheus runs alongside the LLM on the **same GPU** without offloading:

```
LLM:  [====sentence 1====][====sentence 2====][===sentence 3===]
                           ↓                   ↓
TTS:                 [~~synth S1~~]      [~~synth S2~~]     [~S3~]
                           ↓                   ↓
Audio:                     [▶ play S1 ▶▶▶]    [▶ play S2 ▶▶▶]
```

Both models' weights stay in VRAM permanently. CUDA time-slices compute
between them. LLM token generation is memory-bound with natural pauses
that Orpheus fills. SNAC decoding is CPU-only (~2.6% overhead).

### 12GB Single-GPU Configurations

| LLM | LLM VRAM | TTS | TTS VRAM | KV+Runtime | Total | Headroom |
|-----|:---:|-----|:---:|:---:|:---:|:---:|
| Ultra 9B Q4 | 5.5GB | Orpheus-3B Q4 | 2.5GB | 2.0GB | 10.0GB | 2.0GB |
| Ultra 9B Q4 | 5.5GB | Orpheus-EN-1B Q4 | 0.7GB | 2.0GB | 8.2GB | 3.8GB |
| Core 2B Q4 | 1.3GB | Orpheus-3B Q4 | 2.5GB | 2.0GB | 5.8GB | 6.2GB |
| Core 2B Q4 | 1.3GB | Orpheus-EN-1B Q4 | 0.7GB | 2.0GB | 4.0GB | 8.0GB |

**Atlas Ultra + stock Orpheus-3B fits on 12GB with headroom.** With our
compressed Orpheus-EN-1B, even 8GB GPUs run both models comfortably.

---

## Hardware Detection & Model Selection

Atlas setup should auto-detect hardware and recommend the optimal configuration:

```python
def recommend_model_config(hardware: HardwareInfo) -> ModelConfig:
    """Recommend Atlas model tier based on detected hardware."""
    
    total_vram = sum(gpu.vram_gb for gpu in hardware.gpus)
    num_gpus = len(hardware.gpus)
    
    if total_vram >= 16 and num_gpus >= 2:
        # Dual GPU: Ultra + full Orpheus on separate GPUs
        return ModelConfig(
            llm="atlas-ultra-9b",        # 5.5GB on primary GPU
            tts="orpheus-3b-q4",         # 2.5GB on secondary GPU
            note="Best quality: full emotional TTS + powerful reasoning"
        )
    
    elif total_vram >= 12:
        # Single 12GB+ GPU: Ultra + Orpheus side-by-side (parallel pipeline)
        return ModelConfig(
            llm="atlas-ultra-9b",        # 5.5GB
            tts="orpheus-en-1b-q4",      # 0.7GB (both fit with 3.8GB headroom)
            note="Parallel pipeline: emotional TTS while LLM thinks"
        )
    
    elif total_vram >= 8:
        # Single 8GB GPU: Core + Orpheus side-by-side
        return ModelConfig(
            llm="atlas-core-2b",         # 1.3GB
            tts="orpheus-en-1b-q4",      # 0.7GB (both fit with 4GB headroom)
            note="Great balance: emotional TTS + fast responses"
        )
    
    elif total_vram >= 4:
        # Small GPU: Core + Kokoro (CPU TTS)
        return ModelConfig(
            llm="atlas-core-2b",          # 1.3GB on GPU
            tts="kokoro",                 # CPU, no GPU needed
            note="Fast responses, reliable TTS (no emotion)"
        )
    
    elif hardware.ram_gb >= 8:
        # CPU only (Raspberry Pi, no GPU)
        return ModelConfig(
            llm="atlas-core-2b",          # 1.3GB in RAM, CPU inference
            tts="kokoro",                 # CPU
            note="Runs well on CPU, 15-25 tok/s"
        )
    
    elif hardware.ram_gb >= 4:
        # Minimal hardware
        return ModelConfig(
            llm="atlas-lite-0.8b",        # 0.5GB in RAM
            tts="piper",                  # Lightest CPU TTS
            note="Basic assistant, fast on minimal hardware"
        )
```

### User-Facing Output

```
╔══════════════════════════════════════════════════════╗
║  Atlas Hardware Detection                            ║
╠══════════════════════════════════════════════════════╣
║  GPU 1: RTX 4060 (8GB)                              ║
║  GPU 2: RX 7900 XT (20GB)                           ║
║  RAM: 128GB                                          ║
║                                                      ║
║  Recommended: Atlas Ultra + Orpheus EN               ║
║                                                      ║
║  Available configurations:                           ║
║  ┌────────────┬────────┬────────┬───────────────┐   ║
║  │ Model      │ MMLU   │ Speed  │ Notes         │   ║
║  ├────────────┼────────┼────────┼───────────────┤   ║
║  │ Ultra (9B) │ ~92-95 │ 70t/s  │ ★ Recommended │   ║
║  │ Core  (2B) │ ~89-93 │ 180t/s │ Faster, good  │   ║
║  │ Lite (0.8B)│ ~70-77 │ 40t/s  │ Minimal       │   ║
║  └────────────┴────────┴────────┴───────────────┘   ║
║                                                      ║
║  TTS options:                                        ║
║  ┌────────────┬──────────┬────────┬─────────────┐   ║
║  │ Engine     │ Emotions │ VRAM   │ Quality     │   ║
║  ├────────────┼──────────┼────────┼─────────────┤   ║
║  │ Orpheus EN │ ✅ Yes   │ 2-3GB  │ ★ Best      │   ║
║  │ Kokoro     │ ❌ No    │ 0 (CPU)│ Very good   │   ║
║  │ Piper      │ ❌ No    │ 0 (CPU)│ Good        │   ║
║  └────────────┴──────────┴────────┴─────────────┘   ║
╚══════════════════════════════════════════════════════╝
```

---

## Cloud Training Requirements

### Minimum (Ultra + Core only)
- **1× H100 80GB** or **1× A100 80GB**
- Teacher: Qwen3.5-70B Q4 (~40GB)
- Data generation: ~7-12 hours
- Training Ultra (9B): ~3-4 hours
- Training Core (2B from Ultra): ~1-2 hours
- LoRA training (5 domains): ~5-10 hours
- **Total: ~16-28 hours**
- **Cost: ~$25-45 on Vast.ai ($1.50/hr H100)**

### Optimal (with larger teacher)
- **2× H100 80GB** (tensor parallel)
- Teacher: Qwen3.5-235B-A22B (~130GB)
- Higher quality distillation, ~30% better reasoning transfer
- **Total: ~20-30 hours**
- **Cost: ~$60-90 on Vast.ai**

### Budget (API-only data gen + local training)
- Cloud API (Together AI): ~$2-5 for teacher data
- Local training on RTX 4060: ~24-48 hours (QLoRA, slow but free)
- **Total cost: ~$2-5**

---

## Estimated Scores Summary

### Atlas Ultra (9B) + Full LoRA Suite

| Benchmark | Claude Opus 4.6 | GPT-5.4 | Atlas Ultra (est.) |
|-----------|:---:|:---:|:---:|
| MMLU | 91.1% | 89.6% | ~90-93% |
| SWE-bench | 80.8% | 80.0% | ~78-82% |
| ARC-AGI-2 | 68.8% | 52.9% | ~60-68% |
| Math | ~90% | ~88% | ~88-93% |

### Atlas Core (2B) + Full LoRA Suite

| Benchmark | Claude Opus 4.6 | GPT-5.4 | Atlas Core (est.) |
|-----------|:---:|:---:|:---:|
| MMLU | 91.1% | 89.6% | ~87-91% |
| SWE-bench | 80.8% | 80.0% | ~72-77% |
| ARC-AGI-2 | 68.8% | 52.9% | ~50-58% |
| Math | ~90% | ~88% | ~83-88% |

---

## Implementation Phases

### Phase 2.1 — Teacher Data Generation ✱ NEXT
- [ ] Set up cloud GPU (Vast.ai H100 or Azure A100)
- [ ] Pull Qwen3.5-70B as general teacher
- [ ] Pull specialist teachers (DeepSeek-Coder, DeepSeek-R1, DeepSeek-Math)
- [ ] Generate 15K general teacher responses
- [ ] Generate 2K-5K per domain (coding, reasoning, math, medical)
- [ ] Validate and clean teacher data

### Phase 2.2 — Base Model Distillation
- [ ] Distill 70B → Atlas Ultra (9B) via QLoRA
- [ ] Distill Ultra 9B → Atlas Core (2B) via QLoRA
- [ ] Quantize both to GGUF Q4_K_M
- [ ] Benchmark against base Qwen3.5-9B and 2B

### Phase 2.3 — LoRA Training
- [ ] Train general.lora (from 70B teacher data)
- [ ] Train coding.lora (from DeepSeek-Coder data)
- [ ] Train reasoning.lora (from DeepSeek-R1 data)
- [ ] Train math.lora (from DeepSeek-Math data)
- [ ] Train medical.lora (from BioMistral data)
- [ ] Train atlas.lora (from Atlas conversation logs)
- [ ] Benchmark each LoRA independently and stacked

### Phase 2.4 — Integration & Deployment
- [ ] Integrate LoRA loading into `cortex/providers/`
- [ ] Build LoRA router (domain classification → adapter selection)
- [ ] Implement inference-time enhancements (best-of-N, self-verify)
- [ ] Build GGUF export pipeline
- [ ] Test on target hardware (RTX 4060, RX 7900 XT, Raspberry Pi)

### Phase 2.5 — Orpheus TTS Distillation
- [ ] Strip Orpheus to English-only (remove multilingual vocab/embeddings)
- [ ] Quantize Orpheus EN to Q4
- [ ] Train pronunciation LoRA for domain vocabulary
- [ ] Benchmark: quality, latency, VRAM vs stock Orpheus
- [ ] Create Kokoro fallback path for single-GPU setups

### Phase 2.6 — Hardware Detection & Setup
- [ ] Build hardware detection module (`cortex/setup/hardware.py`)
- [ ] Implement model recommendation engine
- [ ] Create interactive setup wizard with score/speed comparison table
- [ ] Auto-download recommended models on first run
- [ ] Test across hardware configurations

### Phase 2.7 — Benchmarking & Validation
- [ ] Build Atlas Benchmark Suite (200+ queries across all domains)
- [ ] Core principles compliance test (50+ adversarial queries, must pass 100%)
- [ ] Side-by-side comparison: Ultra vs Core vs proprietary
- [ ] Latency benchmarks on each hardware tier
- [ ] Publish results to blog repo
