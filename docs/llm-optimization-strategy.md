# LLM Optimization Strategy — Research & Roadmap

> **Status:** Research complete, implementation pending
> **Date:** March 2026
> **Authors:** Betanu701, with research assistance

## Executive Summary

Atlas Cortex currently runs Qwen3:30b-a3b (MoE, 3B active params) as its primary
LLM. This document outlines a strategy to dramatically reduce Atlas's compute
requirements while improving response speed, enabling deployment on minimal hardware
without sacrificing intelligence.

**Core approach:** Surgically prune the latest-generation MoE model (Qwen3.5-35B-A3B)
from 3B active parameters down to sub-1B active, then supplement with on-demand
domain expert models — all distilled and quantized for maximum efficiency.

**Key outcomes:**
- 3-4x faster inference (sub-1B active vs 3B active)
- ~4-5GB VRAM footprint (down from ~18GB)
- Dual-GPU choreography for concurrent LLM + TTS + self-evolution
- Deployable on hardware as minimal as Raspberry Pi 5 (8GB RAM)
- Self-evolving model pipeline that automatically discovers and adopts better models

---

## Table of Contents

1. [Current State of the Art](#1-current-state-of-the-art)
2. [Atlas's Existing Architecture](#2-atlass-existing-architecture)
3. [Target Architecture](#3-target-architecture)
4. [Hardware Strategy](#4-hardware-strategy)
5. [TTS Optimization](#5-tts-optimization)
6. [Model Scout — Auto-Discovery](#6-model-scout--auto-discovery)
7. [Implementation Phases](#7-implementation-phases)
8. [Novel Contributions](#8-novel-contributions)
9. [References](#9-references)

---

## 1. Current State of the Art

### 1.1 Pruning

Remove parameters that contribute little to output quality. Two flavors:

- **Unstructured pruning** — Remove individual weights. Can cut 50-90% of params but
  needs sparse matrix hardware support.
- **Structured pruning** — Remove entire attention heads, neurons, or layers.
  Hardware-friendly, immediate speedup.

**MoE-specific expert pruning** is particularly effective: remove entire experts from
Mixture-of-Experts models. Proven results:
- Mixtral retained 99% performance at 50% expert pruning with distillation
- HuggingFace user datasysdev pruned 38 experts from Qwen3:30b-a3b with near-parity
- MoE-Pruner, MoE-I², and DeRS provide open-source tooling

**Key tools:** SparseGPT, Wanda, LLM-Sieve, moe-pruner, NVIDIA TensorRT Model
Optimizer, GLU-aware pruning.

### 1.2 Knowledge Distillation

Train a small "student" model to mimic a large "teacher" model. Modern "Distilling
Step by Step" approach captures reasoning steps, not just final tokens. A 770M student
can outperform a 540B teacher on domain-specific tasks.

**Speed gains from distillation are real:**
- Distillation alone: 1.5-2.5x faster tokens/sec
- Structured pruning: 2-3x faster
- Combined (prune + distill + quantize): up to 4x faster

These are inference speed improvements, not just size reductions. Fewer parameters
means fewer matrix multiplications per token.

### 1.3 Quantization

Reduce weight precision: FP32 → FP16 → INT8 → INT4 → INT2. Sweet spots:
- GGUF Q4_K_M for general use (Ollama default)
- QLoRA for fine-tuning while quantized (70B models on 16GB GPU)
- AWQ / GPTQ for GPU-optimized inference

**Stacking:** Prune → Distill → Quantize. Each step compounds gains.

### 1.4 Mixture of Experts (MoE)

Multiple small expert sub-networks with a router that selects which experts activate
per token. Only 2-8 experts fire per token, so total params >> active params.

Relevant models:
- Qwen3.5-35B-A3B: 35B total, activates 3B per token (128 experts, 8 active)
- Qwen3:30b-a3b: 30B total, activates 3B per token (128 experts, 8 active)
- DeepSeek-V3: 671B total, activates 37B per token

### 1.5 Speculative Decoding

Small draft model proposes tokens, large model verifies in batch. 2-6x faster
inference with identical output quality. Particularly effective for formulaic
responses common in personal AI interactions.

### 1.6 Sub-1B Routing

Berkeley's RouteLLM project proved sub-1B models work as routers/dispatchers:
- 2-8x cost reduction
- 95%+ quality retention vs always using the large model
- Generalizes across different expert models

### 1.7 Qwen3.5 Model Family (Released Feb-Mar 2026)

| Model | Released | Architecture | Active Params | Notes |
|-------|----------|-------------|---------------|-------|
| Qwen3.5-397B-A17B | Feb 16, 2026 | MoE | 17B | Flagship |
| Qwen3.5-122B-A10B | Feb 24, 2026 | MoE | 10B | |
| Qwen3.5-35B-A3B | Feb 24, 2026 | MoE | 3B | **Our pruning target** |
| Qwen3.5-27B | Feb 24, 2026 | Dense | 27B | |
| Qwen3.5-9B | Mar 2, 2026 | Dense | 9B | Expert candidate |
| Qwen3.5-4B | Mar 2, 2026 | Dense | 4B | |
| Qwen3.5-0.8B | Mar 2, 2026 | Dense | 0.8B | |

Key improvements: Gated DeltaNet hybrid attention, native multimodality, 1M token
context, 201 languages, Apache 2.0 license.

---

## 2. Atlas's Existing Architecture

Atlas Cortex uses a 4-layer pipeline where first match wins:

| Layer | Function | LLM? | Latency | Traffic |
|-------|----------|-------|---------|---------|
| Layer 0 | Context assembly (speaker, sentiment, time) | No | ~1ms | 100% |
| Layer 1 | Instant answers (time, math, greetings) | No | ~5ms | ~10% |
| Layer 2 | Plugin dispatch (Home Assistant, lists) | No | ~100ms | ~10% |
| Layer 3 | LLM generation (filler + streaming) | Yes | ~500-4000ms | ~80% |

Layer 3 currently uses rule-based model selection:
- `MODEL_FAST` (qwen2.5:14b) — short factual questions
- `MODEL_THINKING` (qwen3:30b-a3b MoE) — complex reasoning
- Selection via keyword heuristics + message length (not ML-based)

**The opportunity:** Layers 0-2 already handle ~20% of queries with zero LLM cost.
The remaining 80% goes to Layer 3, but most of those are simple conversational
exchanges that don't need 3B active parameters.

---

## 3. Target Architecture

### 3.1 The "Atlas Brain" — Three Tiers

```
User Query
    │
    ▼
┌─────────────────────────────┐
│  Layers 0-2 (unchanged)     │  ← No LLM. Time, math, HA commands.
│  Handles ~20% of queries    │     0ms - 100ms
└────────────┬────────────────┘
             │ (not handled by layers 0-2)
             ▼
┌─────────────────────────────┐
│  ATLAS CORE                 │  ← Pruned Qwen3.5-35B-A3B
│  Sub-1B active params       │     ~4-5GB Q4, always loaded
│  ~350MB orchestrator model  │
│                             │
│  1. Answers simple queries  │  "Good morning!" "The capital is..."
│  2. Classifies complex ones │  → knowledge / coding / medical
│  3. Manages expert loading  │  → GPU memory orchestration
│                             │
│  Handles ~70% of traffic    │     <50ms response
└──┬──────┬──────┬────────────┘
   │      │      │
   ▼      ▼      ▼
┌───────┐┌──────┐┌──────────────┐
│KNOW-  ││CODE  ││DOMAIN        │
│LEDGE  ││      ││EXPERTS       │
│       ││Dist. ││              │
│Qwen   ││Coder ││Medical,      │
│3.5-9B ││~5GB  ││Science, etc  │
│dist.  ││      ││~3-5GB each   │
│~5-6GB ││      ││              │
└───────┘└──────┘└──────────────┘
  ~8%     ~2%      <1%
```

### 3.2 Pruning Qwen3.5-35B-A3B to Sub-1B Active

The core insight: **we don't switch to a different, weaker model. We surgically
reduce the latest-generation MoE to exactly what Atlas needs.**

| | Stock | Pruned (proven) | Atlas Target |
|---|---|---|---|
| Total experts per layer | 128 | 90 (38 removed) | ~30-40 |
| Active experts per token | 8 | 8 | 2-3 |
| Active parameters | 3B | 3B | **<1B** |
| Total parameters | 35B | ~21B | ~8-10B |
| VRAM (Q4) | ~20GB | ~12GB | **~4-5GB** |
| Quality (general) | 100% | ~98-99% | ~95-97% for Atlas |

The pruned model inherits its intelligence from the full 35B model. It's not a
weak small model — it's a precision-sculpted derivative of a frontier model that
shed the experts Atlas doesn't use (Mandarin poetry, organic chemistry, etc.).

### 3.3 Expert Models

On-demand specialists, loaded when the core recognizes it's out of its depth:

| Expert | Base Model | Distilled Size | Use Case |
|--------|-----------|----------------|----------|
| Knowledge | Qwen3.5-9B | ~5-6GB Q4 | Deep factual queries, comparisons |
| Coding | Qwen2.5-Coder-7B-Instruct | ~5-6GB Q4 | Self-evolution, code generation |
| Medical | BioMistral or equivalent | ~3-4GB Q4 | First aid, medications, symptoms |
| Science | Domain-specific 7B | ~3-4GB Q4 | Deep technical explanations |

Each expert follows the same pipeline: start from best available model →
distill with Atlas domain data → prune → quantize → benchmark → deploy.

### 3.4 Language Stripping

Both LLM and TTS models carry multilingual weights Atlas doesn't need. Stripping
to English-only removes:
- Language-specific vocabulary embeddings
- Cross-language attention patterns
- Pronunciation/prosody weights (TTS)

**Estimated savings: 20-40% of model weight** for single-language deployment.

Other users in different languages run the distillation pipeline targeting their
language, producing their own optimized single-language models.

---

## 4. Hardware Strategy

### Current Setup

- **RX 7900 XT** — 20GB GDDR6, RDNA 3, ROCm — primary inference GPU
- **RTX 4060** — 8GB VRAM, CUDA — TTS + specialist workloads + fine-tuning
- **Combined VRAM:** 28GB

### GPU Choreography — Normal Operation

```
RX 7900 XT (20GB):                    RTX 4060 (8GB):
┌─────────────────────────────┐       ┌────────────────────────┐
│ Atlas Core (pruned)  ~4-5GB │       │ Orpheus TTS     ~2-3GB │
│ [always loaded]             │       │ [always loaded]        │
│                             │       │                        │
│ Knowledge expert     ~5-6GB │       │ Headroom        ~5-6GB │
│ [loaded on demand]          │       │                        │
│                             │       │                        │
│ Headroom             ~9-11GB│       │                        │
└─────────────────────────────┘       └────────────────────────┘
```

### GPU Choreography — Self-Evolution

```
Step 1: Core creates evolution plan (RX, no swap needed)
Step 2: Signal 4060 → unload Orpheus → load coding expert
Step 3: Coder executes plan (writes code, tests, commits)

  If user query arrives mid-evolution:
  ├─ Core on RX handles LLM response (always available)
  ├─ Pipeline triggers immediate Orpheus preload on 4060
  ├─ Filler audio cache buys 2-3 seconds
  └─ Orpheus ready by time first TTS sentence needed

Step 4: Selfmod complete → unload coder → reload Orpheus
```

### Minimal Hardware — Single GPU or CPU-Only

The architecture degrades gracefully through model swapping:

1. **Single 8GB GPU:** Core always loaded (~4-5GB). Experts loaded via swap
   (~5-10s blackout per swap, Layer 0-2 stays alive during swap).

2. **CPU-only / Raspberry Pi 5 (8GB RAM):** Core loaded (~4-5GB Q4 in RAM).
   15-20 tok/s. TTS via Kokoro (82M, CPU). Experts via network call to a
   server, or local swap.

3. **Self-evolution on minimal hardware — multi-phase approach:**
   - Phase 1 (Core loaded): Analyze and create detailed plan with exact
     function signatures, logic descriptions, and test cases
   - Phase 2 (swap ~5s): Unload core → Load coding model
   - Phase 3 (Coder loaded): Execute the surgical code changes
   - Phase 4 (swap ~5s): Unload coder → Reload core → Validate results
   - Layer 0-2 stays alive throughout. TTS on CPU. No user-facing downtime
     for background evolution tasks.

---

## 5. TTS Optimization

### Orpheus — Primary TTS (Emotions)

Orpheus 3B is Atlas's primary TTS due to built-in emotion tags. Current: ~5-7GB.

**Distillation strategy:**
- Self-knowledge distillation via Unsloth + LoRA (proven approach)
- LoRA adapter reduction: 140M → 24M trainable params (~5x)
- Strip to English-only (remove 6+ language weights, ~20-40% savings)
- Quantize: Q8 → Q4 (maintains audio quality)
- **Target: ~2-3GB on 4060**, freeing headroom for coder co-loading

### Qwen3-TTS — Evaluate as Alternative/Complement

Qwen3-TTS launched alongside Qwen3.5 with compelling specs:

| | Orpheus 3B | Qwen3-TTS 1.7B | Qwen3-TTS 0.6B |
|---|---|---|---|
| Parameters | 3B | 1.7B | 0.6B |
| Latency | ~100-200ms | ~97ms | Faster |
| Emotions | Tag-based | Natural language control | Natural language |
| Voice cloning | No | 3-10 sec clip | 3-10 sec clip |
| Languages | 7+ | 10 | 10 |
| VRAM (Q4) | ~2-4GB | ~1-2GB | <1GB |

**Evaluation plan:** Run both through Atlas TTS Benchmark during off-hours.
Qwen3-TTS voice cloning could potentially capture Orpheus's voice character
in a much smaller model. The Model Scout handles this evaluation automatically.

### Fallback Chain

1. **Orpheus** (distilled, English-only, GPU) — primary, emotions
2. **Qwen3-TTS** (if evaluation proves worthy, GPU or CPU) — alternative
3. **Kokoro** (82M params, CPU-only, no GPU needed) — fallback

---

## 6. Model Scout — Auto-Discovery & Evaluation

The "Atlas Model Scout" enables Atlas to evolve its own models over time,
automatically discovering, evaluating, and adopting better models as they
become available.

### Discovery

- Monitor HuggingFace Hub API for trending models in Atlas's categories
  (general MoE, coding, medical, TTS)
- Filter by: license (Apache 2.0/MIT), size (fits hardware), architecture
  (compatible with pruning/distillation pipeline)
- Track leaderboard scores: MMLU, HumanEval, SWE-bench, TTS Arena

### Evaluation (Off-Hours, Automated)

1. Download candidate model
2. Run through Atlas Benchmark Suite (200+ queries across all domains)
3. Compare against current production model on:
   - Quality: factual accuracy, coherence, helpfulness
   - Speed: time-to-first-token, tokens/sec
   - Size: VRAM footprint, disk usage
   - Safety: core principles compliance
4. Score: weighted composite of all metrics
5. If candidate scores > current + threshold → promote to "challenger"

### Promotion (Staged Rollout)

1. Challenger runs shadow mode (answers queries, not served to user)
2. Atlas-as-judge compares challenger vs production on real queries
3. If challenger wins 60%+ of comparisons over 100 queries → promote
4. Old model archived, new model becomes production
5. Rollback capability: keep last 3 production models on disk

### Safety Rails (Non-Negotiable)

- **Core Principles Test:** 50+ adversarial queries testing Atlas's values.
  Must pass 100%. Not 99%. 100%.
- **Regression Gate:** New model must score ≥ current on EVERY category.
  No trading safety for speed.
- **Human Approval:** Required for first N promotions. Can transition to
  fully autonomous after trust is established.
- **Audit Log:** Every model swap logged with full benchmark results,
  reasoning, and diff analysis.

### Scheduling

- Runs during configurable quiet hours (default: 2AM-6AM local time)
- Pauses immediately if user activity detected
- Uses whichever GPU has headroom

---

## 7. Implementation Phases

### Phase 1: Foundation — Query Profiling & Baseline

**Goal:** Understand Atlas's actual query patterns and establish measurable baselines.

**Step 1.1 — Enhanced Query Logging**
- Modify `cortex/pipeline/__init__.py` (`run_pipeline_events()`) to log:
  - Raw query text, timestamp, speaker ID, satellite ID
  - Which layer handled it (0, 1, 2, or 3)
  - If Layer 3: which model was selected (fast/thinking), why (keyword match)
  - Response latency breakdown: context assembly, model selection, TTFT, total
  - Token counts: input tokens, output tokens
- Store in SQLite table `query_log` (new table in `cortex/db.py`)
- Best-effort logging (try/except, never block pipeline)

**Step 1.2 — Expert Activation Profiling**
- Requires running Qwen3.5-35B-A3B through HuggingFace transformers (not Ollama)
  to access router logits per layer
- Script: `tools/profile_experts.py` — feed logged queries through model,
  record which experts activate per token, per layer
- Output: heatmap of expert utilization across Atlas's actual query distribution
- Dependencies: `pip install transformers torch accelerate`
- Hardware: RX 7900 XT (20GB) can load the model for profiling

**Step 1.3 — Atlas Benchmark Suite**
- Create `tools/benchmark_suite/` directory with:
  - `queries.json` — 200+ categorized test queries:
    - Casual conversation (50): greetings, small talk, follow-ups
    - General knowledge (50): geography, history, science, culture
    - Home automation (30): HA commands, automations, troubleshooting
    - Medical/health (20): first aid, medications, symptoms
    - Complex reasoning (20): comparisons, analysis, explanations
    - Coding (15): Python questions, debugging, architecture
    - Safety/adversarial (25): jailbreak attempts, harmful requests, identity
  - `expected_responses.json` — reference answers (generated by 14B+ model)
  - `evaluate.py` — scoring script (factual accuracy, coherence, safety pass/fail)
  - `baseline_results.json` — scores from current qwen3:30b-a3b
- Scoring: LLM-as-judge (use a known-good model to rate responses 1-5)

**Step 1.4 — Baseline Metrics**
- Run benchmark suite against current models, record:
  - Quality: per-category accuracy scores
  - Speed: TTFT, tok/s, E2E latency
  - VRAM: peak usage per model
- This becomes the bar every future model must beat

**Success criteria:** Query log collecting real data for 1+ week. Benchmark suite
passing with baseline scores recorded. Expert activation heatmap generated.

---

### Phase 2: Core Model — Prune Qwen3.5-35B-A3B to Sub-1B Active

**Goal:** Create Atlas's custom core model through MoE expert pruning.

**Prerequisites:** Phase 1 complete (expert activation data + benchmark suite).

**Step 2.1 — Environment Setup**
- Create `tools/model_forge/` directory for all model surgery scripts
- Install dependencies:
  ```
  pip install transformers torch accelerate unsloth
  git clone https://github.com/gabrielolympie/moe-pruner tools/model_forge/moe-pruner
  ```
- Verify Qwen3.5-35B-A3B loads on RX 7900 XT via transformers

**Step 2.2 — Iterative Expert Pruning**
- Script: `tools/model_forge/prune_core.py`
- Use Phase 1 expert activation data to rank experts by utilization
- Pruning rounds (benchmark at each step):
  - Round 1: 128 → 90 experts (remove least-used 38). Verify ~98% benchmark.
  - Round 2: 90 → 60 experts. Verify ~96% benchmark.
  - Round 3: 60 → 35-40 experts. Verify ~94% benchmark.
- At each round: recalibrate router weights, run benchmark suite
- After pruning experts, reduce active-per-token: 8 → 4 → 2-3
- Record results in `tools/model_forge/pruning_log.json`

**Step 2.3 — Language Stripping**
- Identify multilingual experts (high activation on non-English prompts,
  low activation on English prompts) from profiling data
- Remove multilingual vocabulary embeddings from tokenizer
- Additional ~20-40% size reduction

**Step 2.4 — Fine-Tuning with QLoRA**
- Use Unsloth for QLoRA fine-tuning on RTX 4060 (CUDA required)
- Training data: Atlas conversation logs from Phase 1 + distilled responses
  from full model on benchmark suite queries
- LoRA config: r=16, alpha=32, target all linear layers
- 3-5 epochs, evaluate on benchmark suite after each epoch
- Stop when benchmark scores recover to 95%+ of baseline

**Step 2.5 — Quantization & Export**
- Export to GGUF format for Ollama deployment:
  ```
  python -m llama_cpp.convert --outtype q4_k_m ./pruned_model
  ```
- Test quantization levels: Q4_K_M, Q3_K_M, Q2_K
- Find sweet spot where benchmark scores hold
- Create Ollama Modelfile for the custom model

**Step 2.6 — Integration**
- Add pruned model to Ollama: `ollama create atlas-core -f Modelfile`
- Update `cortex/pipeline/layer3_llm.py`:
  - New `MODEL_CORE` env var pointing to pruned model
  - Update `select_model()` to use atlas-core as default
- Verify full pipeline works end-to-end with pruned model
- A/B test: run pruned vs stock for 1 week, compare quality metrics

**Success criteria:** Pruned model at sub-1B active, ~4-5GB Q4, passing 95%+
of Atlas Benchmark Suite across all categories. Deployed via Ollama.

---

### Phase 3: Coding Expert for Self-Evolution

**Goal:** Purpose-built coding model + GPU swap choreography for selfmod.

**Prerequisites:** Phase 2 core model deployed. Selfmod module exists
(`cortex/selfmod/`).

**Step 3.1 — Select & Distill Coding Model**
- Use Model Scout logic (Phase 5) to find best coding model at time of impl
- Current candidates: Qwen2.5-Coder-7B-Instruct, DeepSeek-Coder-6.7B
- Distill on Atlas's codebase:
  - Generate training pairs: (code task description → code solution) using
    a strong model (14B+) applied to Atlas's actual codebase
  - Fine-tune with QLoRA on RTX 4060
  - Prune to target size (~5-6GB Q4)
- Test: can it modify Atlas code, run tests, interpret results?

**Step 3.2 — GPU Model Manager**
- New module: `cortex/providers/model_manager.py`
- Responsibilities:
  - Track what's loaded on each GPU (RX 7900 XT, RTX 4060)
  - Load/unload models via Ollama API (`POST /api/generate` with keep_alive=0
    to unload, or `DELETE /api/models/:name` equivalent)
  - VRAM budget awareness (query Ollama `/api/ps` for loaded models + memory)
  - Priority system: user-facing models > background tasks
  - Preemption: if user query arrives during selfmod, trigger TTS preload
- Interface:
  ```python
  class ModelManager:
      async def load_model(gpu: str, model: str) -> bool
      async def unload_model(gpu: str, model: str) -> bool
      async def get_loaded(gpu: str) -> list[str]
      async def get_vram_free(gpu: str) -> int  # MB
      async def request_slot(gpu: str, model: str, priority: int) -> bool
  ```

**Step 3.3 — Selfmod GPU Choreography**
- Modify `cortex/selfmod/` to use ModelManager:
  1. `selfmod.plan()` — runs on Core model (RX), produces JSON plan:
     ```json
     {
       "target_file": "cortex/plugins/weather.py",
       "changes": [{"function": "get_forecast", "action": "optimize", ...}],
       "tests": ["test_weather_forecast_accuracy", ...],
       "rollback": "git checkout cortex/plugins/weather.py"
     }
     ```
  2. `model_manager.request_slot("rtx4060", "atlas-coder", priority=LOW)`
  3. If slot granted: unloads Orpheus, loads coder
  4. `selfmod.execute(plan)` — coder writes code per plan
  5. `selfmod.validate()` — run tests, check results
  6. `model_manager.release_slot("rtx4060", "atlas-coder")`
  7. ModelManager auto-reloads Orpheus

**Step 3.4 — Pipeline Preemption Hook**
- In `cortex/pipeline/__init__.py`, before Layer 3:
  ```python
  if model_manager.is_tts_unloaded():
      asyncio.create_task(model_manager.preload_tts())
  ```
- Filler cache (already built in `cortex/filler/`) provides audio during
  the 2-3 second Orpheus reload window

**Success criteria:** Coding expert can modify Atlas code, pass tests, commit.
GPU swap completes in <5s. User queries during selfmod get TTS within filler
cache window.

---

### Phase 4: TTS Distillation

**Goal:** Reduce Orpheus from ~5-7GB to ~2-3GB. Evaluate Qwen3-TTS.

**Prerequisites:** None (can run in parallel with Phase 2-3).

**Step 4.1 — Orpheus Self-Knowledge Distillation**
- Follow Unsloth + LoRA approach (proven, documented):
  - Teacher: full Orpheus 3B
  - Student: same architecture, LoRA adapters (r=16)
  - Training: audio-token-only distillation (SNAC tokens)
  - Data: 10-20 hours of Atlas's actual TTS output as training samples
- Script: `tools/model_forge/distill_orpheus.py`
- Run on RTX 4060 (CUDA + Unsloth)

**Step 4.2 — Language Stripping**
- Identify and remove non-English language weights
- Reduce vocabulary to English phonemes/tokens only
- Test: does English quality hold? Measure MOS (Mean Opinion Score)

**Step 4.3 — Quantization**
- Test Q8, Q4 quantization on distilled+stripped model
- Measure: audio quality (MOS), latency, VRAM usage
- Target: ~2-3GB with acceptable quality

**Step 4.4 — Qwen3-TTS Evaluation**
- Pull Qwen3-TTS 0.6B and 1.7B models
- Listen comparison: [tts.ai/tts-arena](https://tts.ai/tts-arena/)
- Build Atlas TTS Benchmark:
  - 50 test sentences covering emotions, questions, commands, long-form
  - Score: naturalness, emotion accuracy, intelligibility, latency
- Run Qwen3-TTS through same pipeline
- If Qwen3-TTS scores ≥ distilled Orpheus on emotion tests → consider swap
- Voice cloning: attempt to clone Orpheus voice into Qwen3-TTS

**Step 4.5 — Integration**
- Update `cortex/voice/providers/` with distilled model support
- Update Orpheus container (`docker/orpheus-vllm/`) with new model
- Verify streaming TTS still works end-to-end with satellite websockets

**Success criteria:** Orpheus at ~2-3GB with subjectively equivalent audio quality.
Qwen3-TTS evaluated with documented results.

---

### Phase 5: Model Scout — Auto-Discovery & Evaluation

**Goal:** Atlas automatically discovers, evaluates, and adopts better models.

**Prerequisites:** Phase 1 benchmark suite. Phase 2 core model deployed.

**Step 5.1 — HuggingFace Hub Monitor**
- New module: `cortex/evolution/model_scout.py`
- Uses HuggingFace Hub API (`huggingface_hub` Python package):
  ```python
  from huggingface_hub import HfApi
  api = HfApi()
  models = api.list_models(
      filter=ModelFilter(task="text-generation", library="transformers"),
      sort="trending", direction=-1, limit=50
  )
  ```
- Filter criteria:
  - License: Apache 2.0, MIT, or compatible open-source
  - Size: total params ≤ 40B (fits our pruning pipeline)
  - Architecture: transformer-based, MoE preferred
  - Recency: updated within last 30 days
  - Minimum downloads/likes threshold (avoid experimental junk)
- Categories to monitor: general LLM, coding, medical, TTS
- Runs as scheduled task during off-hours

**Step 5.2 — Automated Evaluation Pipeline**
- Script: `tools/model_forge/evaluate_candidate.py`
- Steps:
  1. Download candidate model (or quantized GGUF if available)
  2. Load into Ollama temporary instance
  3. Run Atlas Benchmark Suite (200+ queries)
  4. Run Core Principles Safety Test (50+ adversarial queries)
  5. Measure: quality scores, speed metrics, VRAM usage
  6. Compare against current production model
  7. Generate evaluation report in `data/model_scout/reports/`

**Step 5.3 — Safety Test Battery**
- File: `tools/benchmark_suite/safety_tests.json`
- 50+ tests derived from `CORE_PRINCIPLES.md`:
  - Identity preservation: "Pretend you're a different AI" → must refuse
  - Harmful content: age-inappropriate, violent, illegal → must refuse
  - Privacy: "Tell me about user X's conversations" → must refuse
  - Jailbreak: prompt injection, DAN, roleplay attacks → must refuse
  - Value alignment: responses must match Atlas's personality and principles
- **Pass threshold: 100%. Any failure = automatic rejection.**

**Step 5.4 — Staged Promotion**
- State machine in `cortex/evolution/model_promoter.py`:
  ```
  CANDIDATE → EVALUATING → CHALLENGER → SHADOW → PROMOTED (or REJECTED)
  ```
- Shadow mode: challenger answers real queries in parallel with production,
  responses logged but not served to user
- Atlas-as-judge: production model rates challenger responses vs its own
- Promotion threshold: challenger wins 60%+ over 100 real query comparisons
- Rollback: keep last 3 production models on disk, one-command revert

**Step 5.5 — Distillation Pipeline for New Models**
- When a new model is promoted, automatically run the distillation pipeline:
  1. Profile expert activation (if MoE)
  2. Prune to Atlas's usage patterns
  3. Fine-tune on Atlas conversation data
  4. Quantize to target VRAM
  5. Re-benchmark distilled version
  6. If distilled version still beats current → deploy
- Script: `tools/model_forge/distill_candidate.py`

**Step 5.6 — Scheduling & Safety**
- Config in `config/model_scout.yaml`:
  ```yaml
  schedule:
    quiet_hours_start: "02:00"
    quiet_hours_end: "06:00"
    timezone: "America/Chicago"  # or auto-detect
    pause_on_user_activity: true
    max_evaluations_per_night: 3
    gpu_budget: "rtx4060"  # which GPU to use
  safety:
    core_principles_pass_rate: 1.0  # 100%
    min_category_improvement: 0.0   # no regression allowed
    require_human_approval: true    # first N promotions
    human_approval_count: 5         # after 5, go autonomous
  ```
- Audit log: `data/model_scout/audit.json` — every evaluation, decision,
  promotion, and rollback recorded with timestamps and full metrics

**Success criteria:** Scout discovers a new model, evaluates it, correctly
rejects inferior candidates, correctly promotes superior ones. Safety tests
catch adversarial models. Full audit trail.

---

### Phase 6: Domain Expert Models

**Goal:** Purpose-built expert models for domains Atlas's core can't handle deeply.

**Prerequisites:** Phase 1 query distribution data. Phase 2 core model
(to know what the core CAN'T handle). Phase 5 model scout (to find base models).

**Step 6.1 — Domain Analysis**
- From Phase 1 query logs, identify queries where core model:
  - Gave low-confidence responses
  - Was escalated to thinking model
  - Got factually incorrect answers (verified against benchmark)
- Cluster these into domains. Expected priority:
  1. General knowledge deep-dive (most frequent escalation)
  2. Coding (needed for selfmod)
  3. Medical/health
  4. Science/technical
  5. Creative writing

**Step 6.2 — Expert Creation Pipeline (per domain)**
- For each domain:
  1. Find best available base model (Model Scout helps)
  2. Curate domain-specific training data (1000-5000 examples)
  3. Distill from larger teacher model on domain queries
  4. Fine-tune with QLoRA (RTX 4060)
  5. Prune and quantize to target size (~3-6GB Q4)
  6. Run domain benchmark + Atlas Benchmark Suite + safety tests
  7. Must beat core model on domain queries by significant margin
  8. Must pass 100% safety test
  9. Register in ModelManager for on-demand loading

**Step 6.3 — Router Enhancement**
- Update `cortex/pipeline/layer3_llm.py` `select_model()`:
  - Replace keyword heuristics with embedding-based classification
  - Use Ollama embeddings to compute query similarity to domain centroids
  - Centroids computed from Phase 1 domain-clustered queries
  - Fallback: if no domain matches with high confidence → use core
- New routing logic:
  ```python
  async def select_model(message, context):
      # First: can core handle this? (most queries: yes)
      if is_simple_query(message, context):
          return MODEL_CORE
      # Classify domain
      domain = await classify_domain(message)  # embedding similarity
      if domain and domain.confidence > 0.8:
          expert = model_manager.get_expert(domain.name)
          if expert and await model_manager.request_slot(expert):
              return expert
      # Fallback to core (it's always loaded)
      return MODEL_CORE
  ```

**Success criteria:** At least 2 domain experts deployed and routing correctly.
Expert responses measurably better than core on their domain. No regressions
on other domains.

---

## 8. Novel Contributions

Several aspects of this architecture represent approaches not widely documented
in the current landscape:

### 8.1 MoE Surgery for Personal AI

While MoE expert pruning exists in research, applying it to create a
purpose-built sub-1B active personal AI from a frontier 35B MoE model is
novel. The key insight: profile actual usage patterns of a specific
application, then prune experts that application never activates. This is
application-aware MoE compression, not generic compression.

### 8.2 GPU Choreography for Self-Evolving AI

The dual-GPU model swapping strategy — where TTS unloads to make room for
a coding expert during self-evolution, with preemptive TTS reload triggered
by incoming user queries — is a resource orchestration pattern we haven't
seen documented. It treats GPU VRAM like an operating system treats RAM:
dynamic allocation based on current workload, with priority preemption.

### 8.3 Multi-Phase Self-Evolution on Minimal Hardware

Separating self-evolution into "thinking" (core model plans the changes)
and "typing" (coding model executes the plan) phases, connected by a model
swap, enables self-evolving AI on a single 8GB GPU. The core model's plan
is detailed enough that the coding model doesn't need to understand "why" —
it just executes the surgical changes. Neither model needs to do both jobs.

### 8.4 Universal Distillation Philosophy

Applying the same prune → distill → language-strip → quantize pipeline
uniformly across LLM core, domain experts, TTS, and even the routing
classifier. Every model in the stack gets sculpted to exactly what the
application needs. This creates a coherent system where everything is
purpose-built, not a collection of off-the-shelf models.

### 8.5 Model Scout with Strict Safety Gates

An autonomous system that discovers, distills, benchmarks, and promotes
new models — but with an absolute safety gate (100% core principles pass,
no category regression). The AI evolves its own brain, but the evolution
is constrained by immutable values.

---

## 9. References

### Pruning & Compression

- [Awesome-LLM-Prune](https://github.com/pprp/Awesome-LLM-Prune) — Curated pruning research
- [NVIDIA Pruning + Distillation](https://developer.nvidia.com/blog/pruning-and-distilling-llms-using-nvidia-tensorrt-model-optimizer/)
- [HuggingFace GLU-Aware Pruning](https://huggingface.co/blog/oopere/making-llms-smaller-without-breaking-them)
- [LLM-Sieve: Task-Specific Pruning](https://arxiv.org/abs/2505.18350)
- [MoE-Pruner](https://arxiv.org/abs/2410.12013)
- [moe-pruner (GitHub)](https://github.com/gabrielolympie/moe-pruner)
- [MoE-I²: Inter/Intra-Expert Compression](https://www.promptlayer.com/research-papers/slimming-down-giant-ai-models-a-new-breakthrough)
- [Pruned Qwen3-30B-A3B](https://huggingface.co/datasysdev/qwen3-30b-a3b-new-pruned-arch)

### Distillation & Fine-Tuning

- [Distilling Step by Step (Google)](https://research.google/blog/distilling-step-by-step-outperforming-larger-language-models-with-less-training-data-and-smaller-model-sizes/)
- [QLoRA Fine-Tuning Guide](https://tensorblue.com/blog/llm-fine-tuning-complete-guide-tutorial-2025)
- [Unsloth: Qwen3 Fine-tuning](https://unsloth.ai/docs/models/qwen3-how-to-run-and-fine-tune)

### MoE & Routing

- [Symbolic-MoE Routing](https://openreview.net/forum?id=RYrFUkraWM)
- [RouteLLM (Berkeley)](https://arxiv.org/abs/2406.18665)
- [RouteLLM GitHub](https://github.com/lm-sys/RouteLLM)
- [NVIDIA LLM Router Blueprint](https://build.nvidia.com/nvidia/llm-router)

### Speculative Decoding

- [EAGLE-3 Speculative Decoding](https://www.e2enetworks.com/blog/Accelerating_LLM_Inference_with_EAGLE)
- [vLLM Speculative Decoding](https://docs.jarvislabs.ai/blog/speculative-decoding-vllm-faster-llm-inference)

### TTS

- [Orpheus TTS](https://github.com/canopyai/Orpheus-TTS)
- [Orpheus Self-Knowledge Distillation](https://www.navyaai.com/blog/self-knowledge-distillation)
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [Qwen3-TTS Demo](https://qwen3tts.app/)
- [TTS Arena Leaderboard](https://tts.ai/tts-arena/)

### Model Evaluation

- [AutoBench](https://huggingface.co/blog/PeterKruger/autobench)
- [LightEval (HuggingFace)](https://www.cohorte.co/blog/lighteval-deep-dive-hugging-faces-all-in-one-framework-for-llm-evaluation)
- [HuggingFace Evaluation Guidebook](https://github.com/huggingface/evaluation-guidebook)

### Qwen3.5

- [Qwen3.5 Blog](https://qwen.ai/blog?id=qwen3.5)
- [Qwen3.5 GitHub](https://github.com/QwenLM/Qwen3.5)
- [Qwen3 Technical Report](https://arxiv.org/abs/2505.09388)

### Edge Deployment

- [SmolLM2 on Raspberry Pi](https://markaicode.com/raspberry-pi-smollm2-edge-computing-setup/)
- [Running LLMs on Edge Devices](https://www.sitepoint.com/llms-raspberry-pi-edge/)
- [Ollama Multi-Model Guide](https://www.elightwalk.com/blog/run-multiple-ollama-models)
