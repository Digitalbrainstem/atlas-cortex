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

Qwen3-TTS-1.7B (teacher for TTS distillation)
    │
    ├──→ Atlas-TTS-EN-1.7B  ← EN-only strip+prune │ 1.2GB Q4 │ Tier 1-2
    │
    └──→ Atlas-TTS-EN-0.6B  ← Full compression    │ 600MB Q4 │ Tier 2-3

Orpheus-3B (teacher for lightweight TTS)
    │
    ├──→ Orpheus-EN-1B      ← EN strip             │ 0.5GB Q4 │ Tier 3 (default)
    └──→ Orpheus-EN-400M    ← EN strip             │ 0.2GB Q4 │ Tier 4
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

## Voice Engine Strategy

Atlas supports multiple TTS engines, automatically selected based on hardware.
The engines form a quality cascade: **Qwen3-TTS → Orpheus → Kokoro → Piper**.

### Engine Comparison

| Feature | Qwen3-TTS | Orpheus | Kokoro | Piper |
|---------|:---:|:---:|:---:|:---:|
| Architecture | Custom Thinker-Talker | Llama decoder | VITS-based | VITS |
| Emotions | NL instructions + tags | Inline `<emotion>` tags | ❌ | ❌ |
| Voice cloning | ✅ 3-sec sample | ❌ | ❌ | ❌ |
| Multilingual | 10+ languages | English-focused | Multi | Multi |
| Streaming first-packet | ~97ms | ~200-300ms | ~50ms | ~30ms |
| Runtime requirement | PyTorch / vLLM | GGUF / llama.cpp | CPU native | CPU native |
| Shares LLM process? | ❌ Separate runtime | ✅ Same llama.cpp | N/A (CPU) | N/A (CPU) |
| Min GPU VRAM (effective) | ~3.0GB (0.6B Q4+runtime) | 0.7GB (EN-1B Q4) | 0 (CPU) | 0 (CPU) |
| LoRA support | ✅ Talker layers | ✅ Llama layers | ❌ | ❌ |
| Quality (MOS est.) | ~4.2/5 | ~4.0/5 | ~3.8/5 | ~3.5/5 |

### Qwen3-TTS — Premium Voice Engine

**When to use**: Multi-GPU setups (dedicated TTS GPU), or single GPU 12GB+ alongside Core.

Qwen3-TTS uses a Thinker-Talker architecture with Multi-Token Prediction and a
SNAC-style 12Hz/16-codebook neural codec. It generates audio tokens autoregressively,
then decodes to PCM via a learned neural decoder.

**Model family (stock)**:

| Variant | Params | Disk | VRAM (with PyTorch) | Quality |
|---------|--------|------|:---:|---------|
| Qwen3-TTS-1.7B | 1.7B | 4.5GB | ~6.5GB | Excellent + full features |
| Qwen3-TTS-0.6B | 600M | 2.5GB | ~4.0GB | Very good + full features |
| Qwen3-TTS-0.6B-Lite (pruned+Q4) | ~300M | 808MB | ~2.5GB | Good + core features |

**Key capabilities**:
- Natural language emotion: "speak angrily with a hint of sadness"
- Voice cloning from 3-second audio sample
- Free-form voice design via text description
- Streaming synthesis (97ms first-packet latency)
- RTF ~0.8 (faster than real-time)

**Why it needs a dedicated GPU or large shared GPU**: PyTorch runtime adds ~1.5-2.0GB
VRAM overhead on top of model weights. On a shared single GPU, this means the 0.6B model
effectively uses ~3-4GB — fine on 12GB+, tight on 8GB.

### Orpheus — Efficient Emotional Voice

**When to use**: Single GPU 8-12GB (recommended), or as lightweight alternative on any GPU.

Orpheus is a Llama-based 3B decoder that generates SNAC audio tokens (7 tokens/frame,
24kHz). Because it's pure Llama architecture, it runs natively in llama.cpp — the
**same process as the LLM** — with zero additional runtime overhead.

**Model family (Canopy Labs)**:

| Variant | Params | Q4 File | VRAM (in llama.cpp) | Quality |
|---------|--------|---------|:---:|---------|
| Orpheus-3B | 3B | 2.2GB | 2.5GB | Excellent + full emotions |
| Orpheus-1B | 1B | ~0.7GB | ~0.9GB | Very good + emotions |
| Orpheus-400M | 400M | ~0.3GB | ~0.4GB | Good + basic emotions |
| Orpheus-150M | 150M | ~0.1GB | ~0.2GB | Decent, limited expression |

**Key advantage**: Shares the llama.cpp process with the LLM. Both models' weights
live in VRAM with a single runtime footprint (~0.5-1.0GB shared overhead). This makes
Orpheus 2-3x more VRAM-efficient than Qwen3-TTS on a shared GPU.

**Deterministic inline emotion tags**:
```
<emotion=happy>Good morning!</emotion> <emotion=sad>I miss you.</emotion>
```

### Kokoro / Piper — CPU Fallback

**When to use**: GPU under 8GB (Kokoro), or Raspberry Pi / IoT (Piper).

| Engine | Size | Emotions | Quality | Speed | Min Hardware |
|--------|------|:---:|---------|-------|------------|
| Kokoro | ~500MB RAM | ❌ | Very good | RTF 0.3 | Any modern CPU |
| Piper | ~50MB RAM | ❌ | Good | RTF 0.1 | Raspberry Pi Zero |

These are lightweight VITS-based engines that run entirely on CPU.
No GPU required. Kokoro for quality, Piper for absolute minimum hardware.

---

## Voice Engine Distillation

### Qwen3-TTS Distillation Pipeline (Phase 2.5a)

Target: Create English-optimized variants that fit alongside LLM on shared GPU.

**Compression stack** (proven by AtomGradient on Apple Silicon, we adapt for CUDA):

```
Qwen3-TTS-1.7B (4.5GB)
  │
  ├─ Step 1: EN-only vocabulary pruning      → ~3.2GB  (remove non-EN tokens)
  ├─ Step 2: MLP neuron pruning (30%)        → ~2.5GB  (remove low-importance neurons)
  ├─ Step 3: Transformer layer pruning (20%) → ~2.1GB  (remove least-active layers)
  └─ Step 4: Q4 quantization                 → ~1.2GB  (4-bit weights)
     = Atlas-TTS-EN-1.7B-Q4 (~1.2GB disk, ~3.0GB VRAM with runtime)

Qwen3-TTS-0.6B (2.5GB)
  │
  ├─ Step 1: EN-only vocabulary pruning      → ~1.8GB
  ├─ Step 2: MLP neuron pruning (30%)        → ~1.4GB
  ├─ Step 3: Transformer layer pruning (20%) → ~1.1GB
  └─ Step 4: Q4 quantization                 → ~600MB
     = Atlas-TTS-EN-0.6B-Q4 (~600MB disk, ~2.3GB VRAM with runtime)
```

**LoRA adapters for Qwen3-TTS** (target: Talker attention layers):

```
Atlas-TTS-EN-1.7B (base)
  + voice_atlas.lora     (10-20MB)  ← Default Atlas voice personality
  + voice_clone.lora     (10-20MB)  ← User voice clone (from 3-sec sample)
  + medical_terms.lora   (10-20MB)  ← Medical/technical pronunciation
  + emotions_enhanced.lora(10-20MB) ← Distilled emotion range from 1.7B to 0.6B
```

**Distillation training** (Qwen3-TTS specific):
```bash
# Step 1: Generate teacher audio pairs (1.7B generates, we capture tokens)
python tools/distillation/generate_tts_teacher.py \
  --model qwen3-tts-1.7b \
  --texts data/distillation/tts_corpus.txt \
  --output data/distillation/tts_teacher_tokens.jsonl

# Step 2: Prune and fine-tune 0.6B on teacher tokens
python tools/distillation/distill_tts.py \
  --student qwen3-tts-0.6b \
  --teacher-tokens data/distillation/tts_teacher_tokens.jsonl \
  --prune-langs "en-only" \
  --prune-neurons 0.3 \
  --output models/atlas-tts-en-0.6b

# Step 3: LoRA training for voice/pronunciation
python tools/distillation/train_tts_lora.py \
  --base models/atlas-tts-en-0.6b \
  --voice-samples data/voices/atlas_bella/ \
  --output models/loras/tts_voice_atlas.lora
```

### Orpheus Distillation Pipeline (Phase 2.5b)

Target: English-only compressed variants for efficient shared-GPU operation.

**SPADE compression** (Structured Pruning and Adaptive Distillation):

```
Orpheus-3B (2.2GB Q4)
  │
  ├─ EN-only strip: remove multilingual vocab/embeddings  → ~1.6GB Q4
  ├─ Structured pruning: halve transformer depth           → ~1.0GB Q4
  └─ = Orpheus-EN-3B-Pruned Q4 (~1.0GB, ~1.3GB VRAM)

Orpheus-1B (0.7GB Q4)
  │
  ├─ EN-only strip                                         → ~0.5GB Q4
  └─ = Orpheus-EN-1B Q4 (~0.5GB, ~0.7GB VRAM) ← Sweet spot for 8GB shared GPU

Orpheus-400M (0.3GB Q4)
  │
  ├─ EN-only strip                                         → ~0.2GB Q4
  └─ = Orpheus-EN-400M Q4 (~0.2GB, ~0.3GB VRAM) ← Minimal GPU, still emotional
```

**LoRA adapters for Orpheus** (Llama-native, same tooling as LLM):

```
Orpheus-EN-1B (base, ~0.5GB)
  + voice_bella.lora     (10-20MB)  ← Default Atlas voice personality
  + medical_vocab.lora   (10-20MB)  ← Medical pronunciation (78%→95% accuracy)
  + emotions_3b.lora     (10-20MB)  ← Emotion range distilled from 3B model
```

LoRA targets: `q_proj`, `v_proj` attention layers. Each ~10-20MB, swaps in ms.
Uses identical QLoRA tooling as LLM adapters (same `train_lora.py` script).

### Parallel Pipeline Execution

Regardless of TTS engine, the pipeline parallelism works the same way:

```
LLM:  [====sentence 1====][====sentence 2====][===sentence 3===]
                           ↓                   ↓
TTS:                 [~~synth S1~~]      [~~synth S2~~]     [~S3~]
                           ↓                   ↓
Audio:                     [▶ play S1 ▶▶▶]    [▶ play S2 ▶▶▶]
```

- **Dual GPU**: True parallel — LLM on GPU1, TTS on GPU2, zero contention
- **Single GPU (Orpheus)**: CUDA time-slicing, shared VRAM, single process
- **Single GPU (Qwen3-TTS)**: Separate processes, CUDA MPS or time-slicing
- **CPU TTS (Kokoro/Piper)**: TTS runs on CPU while LLM uses GPU, naturally parallel

---

## Hardware Tiers

Atlas auto-detects hardware and recommends the optimal configuration.
Users can always override to choose a different tier.

### Tier 1 — Dual GPU (8GB+ each)

**The best experience.** LLM and TTS on separate GPUs, true parallelism.

| Component | GPU | Model | VRAM Used |
|-----------|-----|-------|:---:|
| LLM | Larger GPU | Atlas Ultra 9B Q4 | 5.5GB + ~2GB runtime |
| TTS | Smaller GPU | Qwen3-TTS 1.7B (full) | 4.5GB + ~2GB runtime |
| STT | TTS GPU (non-overlapping) | Whisper | Shared with TTS |

- **Total**: ~7.5GB on LLM GPU, ~6.5GB on TTS GPU
- **Quality**: Maximum — full-size Ultra reasoning + full Qwen3-TTS emotions/cloning
- **Latency**: Near real-time — zero GPU contention between LLM and TTS
- **Voice cloning**: ✅ Available (Qwen3-TTS feature)

### Tier 2 — Single GPU 12GB+

**Great experience.** Core LLM + Qwen3-TTS on one GPU, or Ultra + Orpheus.

**Option A (recommended): Core + Qwen3-TTS**
| Component | Model | VRAM |
|-----------|-------|:---:|
| LLM | Atlas Core 2B Q4 | 1.3GB |
| TTS | Atlas-TTS-EN-0.6B Q4 | ~600MB model |
| Runtimes | llama.cpp + PyTorch | ~2.5GB |
| KV cache | | ~1.0GB |
| **Total** | | **~5.4GB** |
| **Headroom on 12GB** | | **6.6GB** ✅ |

**Option B: Ultra + Orpheus** (more LLM power, less TTS features)
| Component | Model | VRAM |
|-----------|-------|:---:|
| LLM | Atlas Ultra 9B Q4 | 5.5GB |
| TTS | Orpheus-EN-1B Q4 | 0.7GB |
| Runtime | llama.cpp (shared) | ~1.0GB |
| KV cache | | ~1.5GB |
| **Total** | | **~8.7GB** |
| **Headroom on 12GB** | | **3.3GB** ✅ |

### Tier 3 — Single GPU 8-12GB

**Good experience.** Recommend Orpheus for VRAM efficiency; Qwen3-TTS as option.

**Recommended: Core + Orpheus**
| Component | Model | VRAM |
|-----------|-------|:---:|
| LLM | Atlas Core 2B Q4 | 1.3GB |
| TTS | Orpheus-EN-1B Q4 | 0.7GB |
| Runtime | llama.cpp (shared) | ~0.8GB |
| KV cache | | ~0.5GB |
| **Total** | | **~3.3GB** |
| **Headroom on 8GB** | | **4.7GB** ✅ |

**Option: Core + Qwen3-TTS** (if user wants voice cloning)
| Component | Model | VRAM |
|-----------|-------|:---:|
| LLM | Atlas Core 2B Q4 | 1.3GB |
| TTS | Atlas-TTS-EN-0.6B Q4 | ~600MB |
| Runtimes | llama.cpp + PyTorch | ~2.5GB |
| KV cache | | ~0.5GB |
| **Total** | | **~4.9GB** |
| **Headroom on 8GB** | | **3.1GB** ⚠️ (tight but works) |

### Tier 4 — Single GPU <8GB

**Decent experience.** GPU handles LLM, TTS runs on CPU.

| Component | Where | Model | Resource |
|-----------|-------|-------|:---:|
| LLM | GPU | Atlas Core 2B Q4 | 1.3GB + ~0.8GB runtime |
| TTS | CPU | Kokoro | ~500MB RAM |

- Works on GPUs as small as 4GB
- Kokoro runs entirely on CPU with no GPU VRAM needed
- No emotional expression, but reliable and fast (RTF ~0.3)

### Tier 5 — CPU Only (8GB+ RAM)

**Basic experience.** Everything on CPU. Modern desktop/laptop, no GPU required.

| Component | Model | RAM Used |
|-----------|-------|:---:|
| LLM | Atlas Core 2B Q4 | ~1.3GB |
| TTS | Kokoro | ~500MB |
| Runtime | llama.cpp CPU | ~300MB |
| **Total RAM** | | **~2.1GB** |

- LLM speed: ~20-40 tok/s on modern x86 CPU, ~10-20 tok/s on ARM
- Perfectly usable for conversation, just slower on complex queries
- Works on any machine with 4GB+ free RAM

### Tier 6 — Minimal / Raspberry Pi / IoT

**Lightweight experience.** Smallest possible footprint.

| Hardware | LLM | TTS | RAM |
|----------|-----|-----|:---:|
| RPi 5 (8GB) | Atlas Lite 0.8B Q4 | Piper | ~700MB |
| RPi 5 (4GB) | Atlas Nano 0.3B Q4 | Piper | ~400MB |
| RPi 4 (2GB) | Atlas Nano 0.3B Q2 | Piper | ~250MB |

- Piper is the lightest TTS (~50MB RAM, RTF ~0.1)
- Atlas Lite/Nano are future Phase 3+ models (distilled from Core)
- Limited capabilities but still handles: time, weather, basic Q&A, home control

---

## GPU Vendor Compatibility

Atlas must work across NVIDIA, AMD, and Intel GPUs. Each has different
ecosystem maturity that affects which TTS engine and inference backend to use.

### Compatibility Matrix

| Component | NVIDIA (CUDA) | AMD (ROCm) | Intel (SYCL/oneAPI) |
|-----------|:---:|:---:|:---:|
| llama.cpp (LLM + Orpheus) | ✅ Excellent | ✅ Good | ✅ Good (SYCL backend) |
| Ollama | ✅ Native | ✅ Native | ⚠️ Community builds |
| PyTorch (Qwen3-TTS) | ✅ Native CUDA | ✅ ROCm builds | ⚠️ IPEX extension |
| vLLM | ✅ Native | ✅ ROCm | ❌ Limited |
| LoRA loading (PEFT) | ✅ Full | ✅ Full | ⚠️ Partial |
| Whisper STT | ✅ Native | ✅ ROCm | ⚠️ IPEX |

### Vendor-Specific Recommendations

**NVIDIA (CUDA)** — Best ecosystem support, all engines work natively.
- Qwen3-TTS: PyTorch CUDA (production-grade)
- Orpheus: llama.cpp CUDA (excellent performance)
- Recommended for: Tier 1-3 with any TTS engine

**AMD (ROCm)** — Good for LLM inference, PyTorch ROCm works for Qwen3-TTS.
- Qwen3-TTS: PyTorch ROCm (functional, less community testing)
- Orpheus: llama.cpp HIP backend (good performance)
- Recommended for: Large-VRAM cards (7900 XT, 7900 XTX) as LLM GPU

**Intel (Arc / SYCL)** — Improving rapidly, best with llama.cpp SYCL.
- Qwen3-TTS: IPEX-based (experimental, may need workarounds)
- Orpheus: llama.cpp SYCL (good for inference, well-tested)
- Recommended for: Orpheus preferred over Qwen3-TTS until IPEX matures

### Mixed GPU Setups (Dual Vendor)

When a system has GPUs from different vendors (common in enthusiast builds):

| LLM GPU | TTS GPU | Config | Notes |
|---------|---------|--------|-------|
| AMD (large) | NVIDIA (smaller) | Ultra on ROCm + Qwen3-TTS on CUDA | ★ Ideal mixed setup |
| NVIDIA (large) | AMD (smaller) | Ultra on CUDA + Orpheus on ROCm | Good fallback |
| NVIDIA | NVIDIA | Either on either | Full flexibility |
| AMD | AMD | Both on ROCm | Works well |
| Intel | Any | LLM on Intel + TTS on other | Use SYCL for LLM |

---

## Reference Configuration — Betanu701's Setup

**Hardware**: RTX 4060 (8GB, CUDA) + RX 7900 XT (20GB, ROCm) + 128GB RAM

This is a Tier 1 dual-GPU setup with mixed vendors. Optimal allocation:

```
┌─────────────────────────────────────────────────────────────────┐
│  GPU 1: RX 7900 XT (20GB ROCm)         — LLM + LoRA engine    │
│  ├─ Atlas Ultra 9B Q4                    5.5GB                  │
│  ├─ Active LoRA adapter                  ~50MB                  │
│  ├─ KV cache (4K context)                ~1.5GB                 │
│  ├─ llama.cpp ROCm runtime               ~0.5GB                │
│  ├─ Total used                           ~7.6GB                 │
│  └─ Headroom                             12.4GB ← room to grow │
│                                                                  │
│  GPU 2: RTX 4060 (8GB CUDA)            — TTS + STT engine      │
│  ├─ Qwen3-TTS 1.7B (full quality)       4.5GB                  │
│  ├─ PyTorch CUDA runtime                 ~1.5GB                 │
│  ├─ TTS LoRA adapter                     ~20MB                  │
│  ├─ Total used                           ~6.0GB                 │
│  └─ Headroom                             2.0GB                  │
│                                                                  │
│  CPU: Whisper STT (before pipeline)     — non-overlapping       │
│  RAM: 128GB (ample for everything)                               │
└─────────────────────────────────────────────────────────────────┘
```

**Why this allocation**:
- RX 7900 XT has 20GB — fits Ultra 9B comfortably with massive headroom for
  future growth (larger KV cache, bigger LoRAs, potential Ultra 14B upgrade)
- RTX 4060 is CUDA-native — PyTorch's best-supported path for Qwen3-TTS
- ROCm llama.cpp is well-tested for LLM inference
- True parallel: LLM thinks on AMD while TTS synthesizes on NVIDIA
- STT (Whisper) can share the 4060 since it runs before TTS (non-overlapping)

**Expected latency** (estimated):
| Phase | Time | Where |
|-------|:---:|-------|
| STT (Whisper) | ~190ms | CPU or 4060 |
| Pipeline layers 0-2 | ~100ms | CPU |
| LLM first token | ~200ms | RX 7900 XT |
| LLM full sentence (~30 tok) | ~400ms | RX 7900 XT |
| TTS first audio | ~97ms after sentence | RTX 4060 |
| **Time to first audio** | **~890ms** | **Both GPUs parallel** |

**Fallback path**: If Qwen3-TTS has issues on the 4060, switch to Orpheus-3B Q4
(2.2GB on the 4060 via llama.cpp CUDA, freeing 4GB headroom).

---

## Hardware Detection & Model Selection

Atlas setup auto-detects hardware and recommends the optimal configuration.
Users can always override any recommendation.

```python
@dataclass
class GPUInfo:
    name: str
    vram_gb: float
    vendor: str        # "nvidia", "amd", "intel"
    backend: str       # "cuda", "rocm", "sycl"

@dataclass
class HardwareInfo:
    gpus: list[GPUInfo]
    ram_gb: float
    cpu_cores: int
    is_arm: bool       # True for Raspberry Pi, Apple Silicon

@dataclass
class ModelConfig:
    llm: str
    llm_gpu: int       # GPU index (-1 for CPU)
    tts: str
    tts_gpu: int       # GPU index (-1 for CPU)
    note: str
    alternatives: list[str]

def recommend_model_config(hardware: HardwareInfo) -> ModelConfig:
    """Recommend Atlas model tier based on detected hardware."""

    gpus = sorted(hardware.gpus, key=lambda g: g.vram_gb, reverse=True)
    num_gpus = len(gpus)
    total_vram = sum(g.vram_gb for g in gpus)

    # --- Tier 1: Dual GPU (8GB+ each) ---
    if num_gpus >= 2 and gpus[1].vram_gb >= 8:
        # Prefer NVIDIA for TTS (PyTorch CUDA), larger GPU for LLM
        tts_gpu = next((i for i, g in enumerate(gpus) if g.vendor == "nvidia"), 1)
        llm_gpu = 0 if tts_gpu != 0 else 1
        return ModelConfig(
            llm="atlas-ultra-9b-q4",
            llm_gpu=llm_gpu,
            tts="qwen3-tts-1.7b",           # Full quality, dedicated GPU
            tts_gpu=tts_gpu,
            note="Best quality: Ultra reasoning + Qwen3-TTS voice cloning",
            alternatives=["orpheus-3b-q4 (if Qwen3-TTS unavailable)"]
        )

    # --- Tier 2: Single GPU 12GB+ ---
    elif num_gpus >= 1 and gpus[0].vram_gb >= 12:
        return ModelConfig(
            llm="atlas-core-2b-q4",
            llm_gpu=0,
            tts="atlas-tts-en-0.6b-q4",     # Compressed Qwen3-TTS
            tts_gpu=0,
            note="Core + Qwen3-TTS: emotions + voice cloning on one GPU",
            alternatives=[
                "atlas-ultra-9b-q4 + orpheus-en-1b-q4 (more LLM power)",
                "atlas-core-2b-q4 + orpheus-en-1b-q4 (more headroom)"
            ]
        )

    # --- Tier 3: Single GPU 8-12GB ---
    elif num_gpus >= 1 and gpus[0].vram_gb >= 8:
        return ModelConfig(
            llm="atlas-core-2b-q4",
            llm_gpu=0,
            tts="orpheus-en-1b-q4",          # Shared llama.cpp process
            tts_gpu=0,
            note="Core + Orpheus: emotional TTS, efficient shared runtime",
            alternatives=[
                "atlas-tts-en-0.6b-q4 (Qwen3-TTS, if voice cloning needed)"
            ]
        )

    # --- Tier 4: Single GPU <8GB ---
    elif num_gpus >= 1 and gpus[0].vram_gb >= 4:
        return ModelConfig(
            llm="atlas-core-2b-q4",
            llm_gpu=0,
            tts="kokoro",                    # CPU TTS, no GPU VRAM needed
            tts_gpu=-1,
            note="Core on GPU + Kokoro on CPU: fast LLM, reliable TTS",
            alternatives=["orpheus-en-400m-q4 (emotional, uses ~0.3GB VRAM)"]
        )

    # --- Tier 5: CPU Only (8GB+ RAM) ---
    elif hardware.ram_gb >= 8:
        return ModelConfig(
            llm="atlas-core-2b-q4",
            llm_gpu=-1,
            tts="kokoro",
            tts_gpu=-1,
            note="CPU-only: 20-40 tok/s, reliable for conversation",
            alternatives=["atlas-lite-0.8b-q4 + piper (faster on weak CPU)"]
        )

    # --- Tier 6: Minimal / IoT ---
    elif hardware.ram_gb >= 4:
        model = "atlas-lite-0.8b-q4" if hardware.ram_gb >= 6 else "atlas-nano-0.3b-q4"
        return ModelConfig(
            llm=model,
            llm_gpu=-1,
            tts="piper",
            tts_gpu=-1,
            note="Minimal: basic assistant on constrained hardware",
            alternatives=[]
        )

    else:
        return ModelConfig(
            llm="atlas-nano-0.3b-q2",
            llm_gpu=-1,
            tts="piper",
            tts_gpu=-1,
            note="Ultra-minimal: voice commands only",
            alternatives=[]
        )
```

### User-Facing Setup Output

```
╔════════════════════════════════════════════════════════════════╗
║  Atlas Hardware Detection                                      ║
╠════════════════════════════════════════════════════════════════╣
║  GPU 1: RX 7900 XT (20GB, ROCm)                               ║
║  GPU 2: RTX 4060 (8GB, CUDA)                                  ║
║  RAM: 128GB │ CPU: Ryzen 7 5700G (16 threads)                 ║
║                                                                ║
║  ★ Tier 1: Dual GPU — Best Experience                         ║
║                                                                ║
║  LLM Engine (GPU 1 — RX 7900 XT):                            ║
║  ┌─────────────┬────────┬────────┬────────────────────────┐   ║
║  │ Model       │ MMLU   │ Speed  │ Notes                  │   ║
║  ├─────────────┼────────┼────────┼────────────────────────┤   ║
║  │ Ultra (9B)  │ ~92-95 │ 70t/s  │ ★ Recommended          │   ║
║  │ Core  (2B)  │ ~89-93 │ 180t/s │ Faster, more headroom  │   ║
║  └─────────────┴────────┴────────┴────────────────────────┘   ║
║                                                                ║
║  TTS Engine (GPU 2 — RTX 4060):                               ║
║  ┌──────────────┬──────────┬────────┬──────────┬──────────┐   ║
║  │ Engine       │ Emotions │ Clone  │ VRAM     │ Quality  │   ║
║  ├──────────────┼──────────┼────────┼──────────┼──────────┤   ║
║  │ Qwen3-TTS   │ ✅ NL    │ ✅ 3s  │ 6.5GB    │ ★ Best   │   ║
║  │ Orpheus 3B   │ ✅ Tags  │ ❌     │ 2.5GB    │ Great    │   ║
║  │ Kokoro       │ ❌       │ ❌     │ 0 (CPU)  │ Good     │   ║
║  └──────────────┴──────────┴────────┴──────────┴──────────┘   ║
╚════════════════════════════════════════════════════════════════╝
```

---

## Cloud Training Requirements

### LLM Distillation (Ultra + Core)
- **1× H100 80GB** or **1× A100 80GB**
- Teacher: Qwen3.5-70B Q4 (~40GB)
- Data generation: ~7-12 hours
- Training Ultra (9B): ~3-4 hours
- Training Core (2B from Ultra): ~1-2 hours
- LoRA training (5 domains): ~5-10 hours
- **Total: ~16-28 hours**
- **Cost: ~$25-45 on Vast.ai ($1.50/hr H100)**

### TTS Distillation (Qwen3-TTS + Orpheus)
- **1× A100 40GB** or RTX 4090 (cheaper, TTS models are smaller)
- Qwen3-TTS EN-only strip + pruning: ~2-4 hours
- Qwen3-TTS LoRA training (voice, pronunciation): ~2-3 hours
- Orpheus EN-only strip: ~1-2 hours
- Orpheus LoRA training (voice, emotions): ~1-2 hours
- **Total: ~6-11 hours**
- **Cost: ~$10-20 on Vast.ai**

### Full Pipeline (LLM + TTS + LoRAs)
- **Total: ~22-39 hours** across all distillation runs
- **Total cost: ~$35-65 on Vast.ai**

### Budget Alternative (API + local)
- Cloud API (Together AI): ~$2-5 for teacher data generation
- Local training on RTX 4060: ~24-48 hours (QLoRA, slow but free)
- **Total cost: ~$2-5** (but much slower)

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

### Phase 2.2 — Base LLM Distillation
- [ ] Distill 70B → Atlas Ultra (9B) via QLoRA
- [ ] Distill Ultra 9B → Atlas Core (2B) via QLoRA
- [ ] Quantize both to GGUF Q4_K_M
- [ ] Benchmark against base Qwen3.5-9B and 2B

### Phase 2.3 — LLM LoRA Training
- [ ] Train general.lora (from 70B teacher data)
- [ ] Train coding.lora (from DeepSeek-Coder data)
- [ ] Train reasoning.lora (from DeepSeek-R1 data)
- [ ] Train math.lora (from DeepSeek-Math data)
- [ ] Train medical.lora (from BioMistral data)
- [ ] Train atlas.lora (from Atlas conversation logs)
- [ ] Benchmark each LoRA independently and stacked

### Phase 2.4 — LLM Integration & Deployment
- [ ] Integrate LoRA loading into `cortex/providers/`
- [ ] Build LoRA router (domain classification → adapter selection)
- [ ] Implement inference-time enhancements (best-of-N, self-verify)
- [ ] Build GGUF export pipeline
- [ ] Test on target hardware tiers (Tier 1-6)

### Phase 2.5a — Qwen3-TTS Distillation
- [ ] Download Qwen3-TTS 1.7B and 0.6B base models
- [ ] Strip non-English vocabulary and embeddings
- [ ] Apply MLP neuron pruning (30%) and transformer layer pruning (20%)
- [ ] Quantize to Q4: create Atlas-TTS-EN-1.7B-Q4 and Atlas-TTS-EN-0.6B-Q4
- [ ] Generate teacher audio tokens (1.7B → 0.6B distillation data)
- [ ] Train voice personality LoRA (Atlas default voice)
- [ ] Train medical pronunciation LoRA
- [ ] Build PyTorch CUDA inference server for Atlas integration
- [ ] Benchmark: quality (MOS), latency (TTFB), VRAM on 4060 and 7900 XT

### Phase 2.5b — Orpheus Distillation
- [ ] Strip Orpheus models to English-only (remove multilingual vocab/embeddings)
- [ ] Quantize Orpheus-EN-1B and EN-400M to Q4_K_M GGUF
- [ ] Train voice personality LoRA (matching Atlas voice)
- [ ] Train pronunciation LoRA for medical/technical terms
- [ ] Train emotions LoRA (distilled from 3B emotion range)
- [ ] Benchmark: quality, latency, VRAM vs stock Orpheus on shared GPU

### Phase 2.6 — Hardware Detection & Multi-Tier Setup
- [ ] Build hardware detection module (`cortex/setup/hardware.py`)
  - [ ] Detect GPU vendor (NVIDIA/AMD/Intel), VRAM, driver version
  - [ ] Detect multi-GPU configurations and mixed-vendor setups
  - [ ] Detect CPU capabilities (x86 vs ARM, core count)
- [ ] Implement model recommendation engine (6 tiers)
- [ ] Build TTS engine selector (Qwen3-TTS / Orpheus / Kokoro / Piper)
- [ ] Create interactive setup wizard with tier/quality comparison
- [ ] Auto-download recommended models on first run
- [ ] Test across all 6 hardware tiers
- [ ] Test mixed-vendor GPU setups (NVIDIA+AMD, NVIDIA+Intel)

### Phase 2.7 — Benchmarking & Validation
- [ ] Build Atlas Benchmark Suite (200+ queries across all domains)
- [ ] Core principles compliance test (50+ adversarial queries, must pass 100%)
- [ ] End-to-end latency benchmarks on each hardware tier
  - [ ] Time-to-first-audio for each tier (target: <1s Tier 1, <2s Tier 5)
  - [ ] TTS quality comparison (Qwen3-TTS vs Orpheus vs Kokoro)
- [ ] Side-by-side comparison: Ultra vs Core vs proprietary (GPT-5.4, Claude Opus)
- [ ] GPU vendor performance comparison (CUDA vs ROCm vs SYCL)
- [ ] Publish results to blog repo

### Phase 3 (Future) — Edge Models
- [ ] Distill Core 2B → Atlas Lite 0.8B (Raspberry Pi tier)
- [ ] Distill Lite 0.8B → Atlas Nano 0.3B (IoT/voice-only)
- [ ] Optimize for ARM (Raspberry Pi 4/5, phones)
- [ ] Pair with Piper TTS for minimal footprint
