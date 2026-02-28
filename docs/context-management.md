# Atlas Cortex — Context Window Management & Hardware Abstraction

## Overview

LLM context windows are finite and expensive — every token in the window consumes GPU memory and slows inference. Atlas Cortex actively manages context by compacting conversation history, checkpointing state, and dynamically sizing windows based on detected hardware. The system is **hardware-agnostic**: on first run it auto-detects GPU, VRAM, RAM, and CPU, then configures all limits accordingly.

---

## Hardware Auto-Detection

### First-Run Discovery

On installation or first startup, Cortex runs a hardware probe and stores the results:

```python
def discover_hardware():
    """Detect available compute resources. Runs once at install, re-runs on demand."""
    hw = {}
    
    # GPU Detection (vendor-agnostic)
    hw['gpus'] = detect_gpus()        # returns list of {vendor, name, vram_mb, driver, compute_api}
    hw['cpu'] = detect_cpu()          # {model, cores, threads, freq_mhz, arch}
    hw['ram_mb'] = detect_ram()       # total system RAM in MB
    hw['disk'] = detect_disk()        # {data_path_free_gb, is_nvme}
    hw['os'] = detect_os()            # {name, version, container_runtime}
    
    # Derived limits
    hw['limits'] = compute_limits(hw)
    
    return hw
```

### GPU Detection Matrix

| Vendor | Detection Method | Compute API | Container Flag |
|--------|-----------------|-------------|----------------|
| AMD (discrete) | `rocm-smi`, `/sys/class/drm/card*/device/mem_info_vram_total` | ROCm / HIP | `--device /dev/kfd --device /dev/dri` |
| NVIDIA | `nvidia-smi --query-gpu=...` | CUDA | `--gpus all` |
| Intel Arc | `xpu-smi`, sycl-ls | oneAPI / Level Zero | `--device /dev/dri` |
| Apple Silicon | `system_profiler SPDisplaysDataType` | Metal (via MLX/llama.cpp) | N/A (native) |
| CPU-only | No GPU detected | llama.cpp CPU / GGML | — |
| iGPU (any) | Detected but flagged as `is_igpu=True` | Varies | Used only if no discrete GPU |

### Automatic Limit Computation

```python
def compute_limits(hw):
    """Set safe defaults based on detected hardware."""
    
    limits = {}
    
    # Separate discrete GPUs from iGPUs
    discrete = [g for g in hw['gpus'] if not g['is_igpu']]
    igpus = [g for g in hw['gpus'] if g['is_igpu']]
    # iGPUs are used as fallback if no discrete GPUs are present (see below)
    
    # Sort discrete GPUs by VRAM (largest first)
    discrete.sort(key=lambda g: g['vram_mb'], reverse=True)
    
    # --- Multi-GPU Assignment ---
    # If multiple discrete GPUs, assign each a role based on VRAM and capabilities.
    # Goal: eliminate model-switching latency by dedicating GPUs to workloads.
    
    if len(discrete) >= 2:
        limits['gpu_mode'] = 'multi'
        limits['gpu_assignments'] = assign_gpus(discrete)
    elif len(discrete) == 1:
        limits['gpu_mode'] = 'single'
        limits['gpu_assignments'] = {
            'llm': {'gpu_index': 0, 'gpu': discrete[0]},
            'tts': {'gpu_index': 0, 'gpu': discrete[0], 'shared': True},
            'stt': {'device': 'cpu'},
            'embedding': {'device': 'cpu'},
        }
    else:
        # Try iGPU fallback before going full CPU-only
        if igpus:
            best_igpu = max(igpus, key=lambda g: g['vram_mb'])
            limits['gpu_mode'] = 'igpu_fallback'
            limits['gpu_assignments'] = {
                'llm': {'gpu_index': best_igpu.get('gpu_index', 0), 'gpu': best_igpu, 'shared': True},
                'tts': {'device': 'cpu'},  # Piper fallback (iGPU VRAM too limited for TTS)
                'stt': {'device': 'cpu'},
                'embedding': {'device': 'cpu'},
            }
        else:
            limits['gpu_mode'] = 'cpu_only'
            limits['gpu_assignments'] = {
                'llm': {'device': 'cpu'},
                'tts': {'device': 'cpu'},  # Piper fallback
                'stt': {'device': 'cpu'},
                'embedding': {'device': 'cpu'},
            }
    
    # Use the LLM GPU (or best available) for context/model sizing
    llm_gpu = limits['gpu_assignments'].get('llm', {}).get('gpu')
    
    if llm_gpu:
        vram = llm_gpu['vram_mb']
        
        # Reserve 10% VRAM for OS/display, 5% for embeddings
        usable_vram = int(vram * 0.85)
        
        # Model size budget (leave room for KV cache)
        limits['max_model_size_mb'] = int(usable_vram * 0.70)   # 70% for model weights
        limits['kv_cache_budget_mb'] = int(usable_vram * 0.30)   # 30% for KV cache
        
        # Context window from KV cache budget
        # KV cache ≈ 2 * n_layers * n_heads * head_dim * n_tokens * 2 bytes (fp16)
        # Rough heuristic: ~0.5MB per 1K tokens for 30B-class models
        limits['max_context_tokens'] = int(limits['kv_cache_budget_mb'] / 0.5 * 1024)
        limits['max_context_tokens'] = min(limits['max_context_tokens'], 131072)  # hard cap
        
        # Safe defaults per VRAM tier
        if vram >= 24000:       # 24GB+ (RTX 4090, 7900 XTX)
            limits['default_context'] = 32768
            limits['thinking_context'] = 65536
            limits['recommended_model_class'] = '30B-70B'
        elif vram >= 16000:     # 16-24GB (7900 XT, RTX 4080, A4000)
            limits['default_context'] = 16384
            limits['thinking_context'] = 32768
            limits['recommended_model_class'] = '14B-30B'
        elif vram >= 8000:      # 8-16GB (RTX 3070, RX 7600)
            limits['default_context'] = 8192
            limits['thinking_context'] = 16384
            limits['recommended_model_class'] = '7B-14B'
        elif vram >= 4000:      # 4-8GB (older GPUs, iGPUs)
            limits['default_context'] = 4096
            limits['thinking_context'] = 8192
            limits['recommended_model_class'] = '1B-7B'
        else:                   # <4GB or CPU-only
            limits['default_context'] = 2048
            limits['thinking_context'] = 4096
            limits['recommended_model_class'] = '1B-3B'
    else:
        # CPU-only fallback
        ram = hw['ram_mb']
        limits['max_model_size_mb'] = int(ram * 0.40)  # 40% of RAM
        limits['kv_cache_budget_mb'] = int(ram * 0.15)
        limits['default_context'] = 4096
        limits['thinking_context'] = 8192
        limits['recommended_model_class'] = '3B-7B (Q4)'
    
    # Embedding model selection (always CPU to keep GPUs free)
    if llm_gpu and llm_gpu['vram_mb'] >= 8000:
        limits['embedding_model'] = 'nomic-embed-text'    # 768-dim, 274MB
        limits['embedding_device'] = 'cpu'                 # keep GPU free for LLM
    else:
        limits['embedding_model'] = 'all-minilm'           # 384-dim, 46MB, faster
        limits['embedding_device'] = 'cpu'
    
    # Concurrent model limit (per-GPU, not global, in multi-GPU mode)
    if limits['gpu_mode'] == 'multi':
        limits['max_loaded_models_per_gpu'] = 1  # each GPU runs its own workload
    else:
        limits['max_loaded_models'] = 1 if (llm_gpu and llm_gpu['vram_mb'] < 16000) else 2
    
    return limits


def assign_gpus(gpus):
    """Assign workloads to GPUs based on VRAM and capabilities.
    
    Strategy:
      - Largest VRAM GPU → LLM (needs the most memory)
      - Second GPU → TTS + STT + speaker-id (voice workloads)
      - If 3+ GPUs → third handles STT/embedding, second is TTS-only
      - Mixed vendors are fine — each GPU runs its own container/runtime
    
    Scoring heuristic for each role:
      - LLM: maximize VRAM (bigger model = better reasoning)
      - TTS: needs 6-12GB for Orpheus; prefer GPU with good compute
      - STT: lighter workload, can share with TTS GPU or run on CPU
    """
    assignments = {}
    
    # GPU 0 (largest VRAM) → always LLM
    assignments['llm'] = {
        'gpu_index': 0,
        'gpu': gpus[0],
        'env': _gpu_env(gpus[0], 0),
    }
    
    # GPU 1 → voice workloads (TTS primary, STT secondary)
    assignments['tts'] = {
        'gpu_index': 1,
        'gpu': gpus[1],
        'shared': False,  # dedicated — no model switching needed
        'env': _gpu_env(gpus[1], 1),
    }
    assignments['stt'] = {
        'gpu_index': 1,
        'gpu': gpus[1],
        'shared_with': 'tts',
        'env': _gpu_env(gpus[1], 1),
    }
    
    # GPU 2+ → spread remaining workloads
    if len(gpus) >= 3:
        assignments['stt'] = {
            'gpu_index': 2,
            'gpu': gpus[2],
            'env': _gpu_env(gpus[2], 2),
        }
        assignments['embedding'] = {
            'gpu_index': 2,
            'gpu': gpus[2],
            'shared_with': 'stt',
            'env': _gpu_env(gpus[2], 2),
        }
    else:
        assignments['embedding'] = {'device': 'cpu'}
    
    return assignments


def _gpu_env(gpu, index):
    """Generate environment variables to isolate a workload to a specific GPU."""
    # Use the vendor-native/system GPU index if available; fall back to the
    # position in the sorted list for backward compatibility.
    gpu_index = gpu.get('gpu_index', index)

    vendor = gpu.get('vendor', '').lower()
    if vendor == 'amd':
        return {
            'HIP_VISIBLE_DEVICES': str(gpu_index),
            'HSA_OVERRIDE_GFX_VERSION': gpu.get('gfx_version', ''),
        }
    elif vendor == 'nvidia':
        return {
            'CUDA_VISIBLE_DEVICES': str(gpu_index),
        }
    elif vendor == 'intel':
        return {
            'ONEAPI_DEVICE_SELECTOR': f'level_zero:{gpu_index}',
            'ZE_AFFINITY_MASK': str(gpu_index),
        }
    else:
        return {}
```

### Multi-GPU Architecture

When multiple discrete GPUs are detected, Atlas assigns each a dedicated role:

```
┌──────────────────────────────────────────────────────────────┐
│                    Multi-GPU Assignment                        │
│                                                               │
│  GPU 0 (largest VRAM)          GPU 1 (second GPU)            │
│  ┌─────────────────────┐      ┌──────────────────────────┐   │
│  │  LLM Instance        │      │  Voice Instance           │   │
│  │  • Ollama :11434     │      │  • Ollama/IPEX :11435     │   │
│  │  • HIP_VISIBLE=0     │      │  • HIP/CUDA/ONEAPI=1     │   │
│  │  • Full VRAM for LLM │      │  • Orpheus TTS            │   │
│  │  • No model switching │      │  • faster-whisper STT     │   │
│  │                      │      │  • speaker-id             │   │
│  └─────────────────────┘      └──────────────────────────┘   │
│                                                               │
│  Benefits:                                                    │
│  • Zero model-switching latency (LLM stays loaded)           │
│  • TTS runs simultaneously with LLM                          │
│  • Mixed GPU vendors supported (separate containers)         │
│  • LLM gets 100% of its VRAM (no TTS competition)           │
└──────────────────────────────────────────────────────────────┘
```

**Single-GPU Fallback**: If only one GPU is detected, the system reverts to time-multiplexed sharing (LLM unloads → TTS loads → TTS unloads → LLM reloads). This adds ~2-3 seconds of switching latency but works correctly.

**Mixed Vendor Support**: Each GPU runs in its own container or process with vendor-specific isolation (`HIP_VISIBLE_DEVICES` for AMD, `CUDA_VISIBLE_DEVICES` for NVIDIA, `ONEAPI_DEVICE_SELECTOR` for Intel). They never conflict because they use separate driver stacks.

### Hardware Profile Storage

```sql
CREATE TABLE hardware_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gpu_mode TEXT,               -- 'multi' | 'single' | 'cpu_only'
    gpu_count INTEGER DEFAULT 0,
    cpu_model TEXT,
    cpu_cores INTEGER,
    ram_mb INTEGER,
    disk_free_gb REAL,
    os_name TEXT,
    limits_json TEXT,            -- computed limits (JSON blob — diagnostic, not queried)
    assignments_json TEXT,       -- GPU role assignments (JSON blob)
    is_current BOOLEAN DEFAULT TRUE
);

-- One row per GPU detected
CREATE TABLE hardware_gpu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES hardware_profile(id),
    gpu_index INTEGER,           -- 0, 1, 2...
    vendor TEXT,                  -- 'amd' | 'nvidia' | 'intel' | 'apple'
    name TEXT,
    vram_mb INTEGER,
    is_igpu BOOLEAN DEFAULT FALSE,
    compute_api TEXT,             -- 'rocm' | 'cuda' | 'oneapi' | 'metal'
    driver_version TEXT,
    assigned_role TEXT,           -- 'llm' | 'voice' | 'stt' | null
    env_json TEXT                 -- isolation env vars (JSON blob)
);

-- Only one profile is current at a time
CREATE UNIQUE INDEX idx_hw_current ON hardware_profile(is_current) WHERE is_current = TRUE;
CREATE INDEX idx_hw_gpu_profile ON hardware_gpu(profile_id);
```

Re-detection can be triggered:
- User says "Atlas, re-detect hardware" (after a GPU swap, RAM upgrade, etc.)
- On container restart if `--redetect` flag is passed
- Automatically if model loading fails with OOM

---

## Context Window Strategy

### The Problem

Context windows are the primary bottleneck:

| Model | Max Context | KV Cache at Max | Impact |
|-------|-------------|-----------------|--------|
| qwen3:30b-a3b (current) | 32K tokens | ~8GB | Fills 40% of 20GB VRAM |
| Same model at 8K tokens | 8K tokens | ~2GB | Leaves room for everything |

Every token in the window:
- Consumes KV cache memory (proportional to layers × heads × tokens)
- Increases attention computation time (quadratic in full attention, but most models use GQA)
- Must be reprocessed on every generation step

### Dynamic Context Sizing

Cortex adjusts the context window per-request based on task complexity:

```python
def select_context_size(query_analysis, hw_limits):
    """Pick the right context window for this specific request."""
    
    base = hw_limits['default_context']
    max_ctx = hw_limits['thinking_context']
    
    # Layer 1 (instant) — no LLM, no context needed
    if query_analysis['layer'] == 1:
        return 0
    
    # Layer 2 (device commands) — minimal context for confirmation
    if query_analysis['layer'] == 2:
        return 512
    
    # Layer 3 (LLM) — varies by task
    if query_analysis['needs_thinking']:
        # Deep reasoning: expand context but compact first
        return min(max_ctx, base * 2)
    
    if query_analysis['is_followup']:
        # Continuing a conversation: need recent history
        return base
    
    if query_analysis['is_simple_question']:
        # One-shot question: minimal context
        return min(base, 4096)
    
    # Default
    return base
```

---

## Context Compaction

### Why Compact?

A 30-message conversation can easily hit 15K-20K tokens. Most of that is stale — old greetings, resolved questions, superseded information. Compaction shrinks the history while preserving essential context.

### Compaction Strategy: Tiered Summarization

```
┌─────────────────────────────────────────────────────────┐
│                  Full Context Window                      │
│                                                           │
│  ┌──────────────────────┐                                │
│  │ CHECKPOINT SUMMARIES  │  ← oldest, most compressed    │
│  │ (1 paragraph each)    │     Turns 1-20 → 200 tokens   │
│  └──────────────────────┘                                │
│                                                           │
│  ┌──────────────────────┐                                │
│  │ RECENT SUMMARY        │  ← medium compression          │
│  │ (key points)          │     Turns 21-28 → 500 tokens  │
│  └──────────────────────┘                                │
│                                                           │
│  ┌──────────────────────┐                                │
│  │ ACTIVE MESSAGES       │  ← no compression (verbatim)  │
│  │ (last 3-5 turns)     │     Turns 29-32 → 2000 tokens │
│  └──────────────────────┘                                │
│                                                           │
│  ┌──────────────────────┐                                │
│  │ SYSTEM CONTEXT        │  ← always present              │
│  │ (personality, memory, │     ~500-1000 tokens           │
│  │  user profile, room)  │                                │
│  └──────────────────────┘                                │
│                                                           │
│  Total: ~3200 tokens (vs 20K uncompacted)                │
└─────────────────────────────────────────────────────────┘
```

### Compaction Triggers

| Trigger | Action |
|---------|--------|
| Context reaches 60% of limit | Summarize oldest third of messages |
| Context reaches 80% of limit | Create checkpoint, keep only last 5 turns |
| User starts new topic | Checkpoint current topic, fresh context |
| Model switch (thinking mode) | Compact to make room for thinking tokens |
| 10+ messages in conversation | Automatic rolling summarization |

### Checkpoint System

A checkpoint captures the essential state of a conversation segment so the full messages can be dropped from context.

```sql
CREATE TABLE context_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,      -- Open WebUI conversation ID
    user_id TEXT NOT NULL,
    checkpoint_number INTEGER NOT NULL,
    
    -- Compressed summary of this segment
    summary TEXT NOT NULL,              -- LLM-generated summary of the checkpoint window
    summary_tokens INTEGER,             -- token count of the summary
    
    -- What was covered
    turn_range_start INTEGER,           -- first message turn number
    turn_range_end INTEGER,             -- last message turn number  
    original_token_count INTEGER,       -- how many tokens before compaction
    topics TEXT,                        -- comma-separated topics covered
    
    -- Key facts extracted (for quick retrieval without re-reading)
    decisions_made TEXT,                -- any decisions/choices made in this segment
    entities_mentioned TEXT,            -- devices, people, files referenced
    unresolved_questions TEXT,          -- anything left open at checkpoint time
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(conversation_id, checkpoint_number)
);

CREATE INDEX idx_ctx_ckpt_conv ON context_checkpoints(conversation_id);
```

### Checkpoint Creation Flow

```python
async def create_checkpoint(conversation_id, messages, user_id):
    """Compress a block of messages into a checkpoint summary."""
    
    # 1. Count what we're compressing
    old_messages = messages[:-5]   # keep last 5 verbatim
    keep_messages = messages[-5:]
    
    original_tokens = count_tokens(old_messages)
    
    # 2. Ask the LLM to summarize (use fast model, small context)
    summary_prompt = f"""Summarize this conversation segment concisely. 
    Preserve: decisions made, user preferences stated, questions asked, 
    devices/files/people mentioned, any unresolved topics.
    
    Conversation:
    {format_messages(old_messages)}
    
    Provide:
    1. A 2-3 sentence summary
    2. Key decisions (bullet list)
    3. Unresolved items (bullet list)"""
    
    result = await ollama_generate(
        model=fast_model,        # use the quick model for summarization
        prompt=summary_prompt,
        context_length=8192,     # minimal context for this task
    )
    
    # 3. Store checkpoint
    checkpoint_num = get_next_checkpoint_number(conversation_id)
    insert_checkpoint(
        conversation_id=conversation_id,
        user_id=user_id,
        checkpoint_number=checkpoint_num,
        summary=result['summary'],
        summary_tokens=count_tokens(result['summary']),
        turn_range_start=old_messages[0]['turn'],
        turn_range_end=old_messages[-1]['turn'],
        original_token_count=original_tokens,
        topics=result.get('topics', ''),
        decisions_made=result.get('decisions', ''),
        entities_mentioned=result.get('entities', ''),
        unresolved_questions=result.get('unresolved', ''),
    )
    
    # 4. Return compacted context
    return {
        'checkpoints': load_checkpoints(conversation_id),  # all checkpoint summaries
        'active_messages': keep_messages,                   # last 5 verbatim
    }
```

### Checkpoint Referencing

When the LLM needs detail from a checkpointed segment, Cortex can expand it:

```python
async def expand_checkpoint(conversation_id, checkpoint_id, query):
    """Retrieve details from a specific checkpoint if the LLM needs them."""
    
    checkpoint = get_checkpoint(checkpoint_id)
    
    # First, check if the summary + extracted facts answer the question
    if is_answerable_from_summary(checkpoint, query):
        return checkpoint['summary']
    
    # If not, retrieve the original messages from interaction_log
    original_messages = get_messages_by_turn_range(
        conversation_id,
        checkpoint['turn_range_start'],
        checkpoint['turn_range_end']
    )
    
    # Re-inject relevant portions only
    relevant = filter_relevant_messages(original_messages, query)
    return format_messages(relevant)
```

### Token Budget Allocation

For any given request, the total context budget is divided:

```
Total context budget (e.g., 16384 tokens)
├── System prompt (personality, rules)       ~300 tokens    (fixed)
├── User profile + room context              ~200 tokens    (fixed)
├── Memory context (HOT path results)        ~500 tokens    (variable, top-K)
├── Checkpoint summaries                     ~200-800 tokens (grows with history)
├── Recent summary                           ~300-500 tokens (if present)
├── Active messages (last 3-5 turns)         ~1000-3000 tokens (verbatim)
├── Grounding context (if needed)            ~500 tokens    (optional)
├── Current user message                     ~100-500 tokens (verbatim)
└── Generation headroom                      remainder      (for LLM output)
```

The budget manager ensures generation headroom is always ≥ 2048 tokens (for thinking models, ≥ 4096):

```python
def build_context(request, hw_limits):
    """Assemble the final context, compacting as needed to stay in budget."""
    
    total_budget = select_context_size(request.analysis, hw_limits)
    
    # Fixed components
    system_tokens = count_tokens(request.system_prompt)
    profile_tokens = count_tokens(request.user_profile)
    current_msg_tokens = count_tokens(request.message)
    
    # Reserve generation headroom
    if request.analysis['needs_thinking']:
        generation_reserve = 4096
    else:
        generation_reserve = 2048
    
    remaining = total_budget - system_tokens - profile_tokens - current_msg_tokens - generation_reserve
    
    # Fill remaining budget in priority order
    context_parts = []
    
    # 1. Memory (most valuable, capped)
    memory_budget = min(remaining * 0.20, 800)
    memory_context = truncate_to_tokens(request.memory_hits, memory_budget)
    context_parts.append(memory_context)
    remaining -= count_tokens(memory_context)
    
    # 2. Active messages (verbatim recent turns)
    active_budget = min(remaining * 0.60, 3000)
    active_msgs = fit_messages_to_budget(request.recent_messages, active_budget)
    context_parts.append(active_msgs)
    remaining -= count_tokens(active_msgs)
    
    # 3. Checkpoint summaries (compressed history)
    if remaining > 200:
        checkpoints = fit_checkpoints_to_budget(request.checkpoints, remaining)
        context_parts.append(checkpoints)
    
    return assemble_prompt(request.system_prompt, request.user_profile, 
                          context_parts, request.message)
```

---

## Thinking Mode Context Management

When the model uses extended thinking (qwen3 `/think` mode), the thinking tokens consume context too. Cortex manages this:

### Pre-Think Compaction

Before routing to the thinking model:
1. **Aggressive compaction** — checkpoint everything except last 2-3 turns
2. **Strip non-essential context** — remove filler metadata, reduce memory to top-3
3. **Expand context window** to `thinking_context` limit (if hardware allows)

```python
async def prepare_for_thinking(request, hw_limits):
    """Compact context before sending to thinking model."""
    
    # 1. Force checkpoint if not already done
    if len(request.messages) > 3:
        request = await create_checkpoint(
            request.conversation_id,
            request.messages,
            request.user_id
        )
    
    # 2. Use expanded context limit
    request.context_limit = hw_limits['thinking_context']
    
    # 3. Reduce memory injection to essentials only
    request.memory_hits = request.memory_hits[:3]
    
    # 4. Build lean context
    return build_context(request, hw_limits)
```

### Thinking Token Budget

On the current hardware (20GB VRAM, qwen3:30b-a3b):

| Context Mode | Input Tokens | Thinking Reserve | Output Tokens | KV Cache |
|--------------|-------------|------------------|---------------|----------|
| Normal (fast) | 8K | 0 | 2K | ~2.5GB |
| Thinking | 8K | 16K | 4K | ~7GB |
| Max thinking | 8K | 32K | 4K | ~11GB |

The system monitors actual thinking token usage and adjusts over time:
- If the model consistently uses <4K thinking tokens, reduce the reserve
- If responses are cut off, increase the reserve for the next request

---

## Conversation Lifecycle

### Guiding Principle: The User Never Sees the Seams

Context management is entirely invisible to the user. They should never see an error, a truncation warning, or a "context limit reached" message. If the window fills up, Cortex handles it silently — compacting, checkpointing, chunking, and reassembling — while the user sees only a natural, continuous conversation. Filler phrases bridge any latency gaps.

```
New conversation starts
    │
    ▼
Messages 1-5: Full verbatim context
    │
    ▼
Message 6+: Rolling summary of oldest messages begins
    │
    ▼
Context at 60%: Summarize messages 1-N into recent summary
    │
    ▼
Context at 80%: Checkpoint → summary stored in DB, only last 5 messages remain
    │
    ▼
Multiple checkpoints accumulate as conversation grows
    │
    ▼
Conversation ends → Final checkpoint created, full history queryable via interaction_log
```

### Transparent Overflow Recovery

When context hits the limit mid-generation (the LLM is producing output and runs out of room), or when a complex task requires more output than the generation reserve allows, Cortex handles it transparently:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Overflow Recovery Flow                         │
│                                                                   │
│  User sends complex request                                      │
│       │                                                           │
│       ▼                                                           │
│  Build context → send to LLM → LLM starts generating             │
│       │                                                           │
│       ▼                                                           │
│  Monitor: is output approaching generation reserve limit?         │
│       │                                                           │
│       ├── NO → stream output normally, done                       │
│       │                                                           │
│       └── YES → Overflow Protocol:                                │
│            │                                                      │
│            ▼                                                      │
│       1. Capture partial output so far                            │
│       2. Stream filler to user: "Let me continue..."             │
│       3. Aggressively compact: checkpoint everything,             │
│          keep only the partial output + original question         │
│       4. Re-send to LLM with instruction:                        │
│          "Continue from where you left off. Do NOT repeat         │
│           what was already said. Here's what you've covered:      │
│           [partial output summary]"                               │
│       5. Stream continuation to user                              │
│       6. If STILL overflows → repeat (up to 3 chunks)            │
│       7. Final assembly: deduplicate, ensure coherence            │
│                                                                   │
│  User sees: one seamless response                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Chunked Generation for Long Outputs

Some tasks naturally produce long output (code generation, detailed explanations, multi-step plans). Rather than hoping it fits, Cortex proactively chunks:

```python
async def generate_with_overflow(request, hw_limits):
    """Generate a response, handling overflow transparently."""
    
    chunks = []
    filler_sent = False
    total_output_tokens = 0
    max_output_tokens = hw_limits.get('max_total_output', 100000)  # safety net, not a hard limit
    
    while True:
        # Check for user interruption before each chunk
        if await check_user_interrupt(request.conversation_id):
            interrupt = await get_interrupt(request.conversation_id)
            if interrupt['type'] == 'stop':
                # User said "stop" / "that's enough" / "got it"
                break
            elif interrupt['type'] == 'clarify':
                # User asked a follow-up mid-generation — pivot
                return await handle_mid_generation_question(
                    request, chunks, interrupt['message']
                )
        
        # Diminishing returns check — if we've generated a LOT, ask if user wants more
        if total_output_tokens > 8000 and len(chunks) >= 3:
            yield "\n\nThat covers the main points — want me to keep going with more detail?"
            user_response = await wait_for_user_response(request.conversation_id, timeout=30)
            if user_response and is_negative(user_response):
                break
            # If no response or positive, continue
        
        # Build context (tighter each attempt)
        context = build_context(request, hw_limits)
        
        if len(chunks) > 0:
            covered_summary = summarize_chunks(chunks)
            context = inject_continuation_prompt(context, covered_summary)
        
        # Generate
        result = await ollama_generate_streaming(
            model=request.model,
            prompt=context,
            stream=True,
        )
        
        partial_output = ""
        async for token in result:
            # Check for real-time interruption during streaming
            if await check_user_interrupt(request.conversation_id):
                interrupt = await get_interrupt(request.conversation_id)
                if interrupt['type'] == 'stop':
                    chunks.append(partial_output)
                    return  # stop immediately
                elif interrupt['type'] == 'clarify':
                    chunks.append(partial_output)
                    return await handle_mid_generation_question(
                        request, chunks, interrupt['message']
                    )
            
            partial_output += token
            yield token
            
            if is_near_generation_limit(partial_output, hw_limits):
                break
        
        total_output_tokens += count_tokens(partial_output)
        chunks.append(partial_output)
        
        # Did the LLM finish naturally?
        if result.done:
            break
        
        # Truncated — continue with filler
        if not filler_sent:
            yield select_continuation_filler(request.sentiment)
            filler_sent = True
        
        request = await compact_for_continuation(request, chunks)
        
        # Safety net — prevent infinite loops (not a hard limit, just sanity)
        if total_output_tokens > max_output_tokens:
            yield "\n\nI've covered a lot — let me know if you need me to go deeper on any part."
            break
    
    # Post-processing: verify no repetition across chunks
    final_output = "".join(chunks)
    if has_significant_repetition(final_output):
        # Use fast model to deduplicate
        final_output = await deduplicate_output(final_output)


async def compact_for_continuation(request, chunks_so_far):
    """Aggressively compact context to make room for the next chunk."""
    
    # Checkpoint ALL conversation history
    await create_checkpoint(
        request.conversation_id,
        request.messages,
        request.user_id,
    )
    
    # New context: only the original question + what we've covered
    request.messages = [
        request.messages[0],   # original user message
    ]
    
    # Summarize what's been generated (not the full text — just key points)
    request.continuation_context = summarize_chunks(chunks_so_far)
    
    return request
```

### Continuation Filler Phrases

When overflow recovery adds latency, the user hears/sees natural bridging:

| Scenario | Example Fillers |
|----------|----------------|
| First overflow (short pause) | "Bear with me, there's quite a bit to cover..." |
| Complex task continuing | "...and continuing with the rest..." |
| Multi-part answer | "Let me also address..." |
| Code generation overflow | "...and here's the rest of that..." |
| Research/lookup overflow | "Found some more details on that..." |

These are NOT the confidence/sentiment fillers from the filler engine — they're specifically for seamless continuation. They're short, natural, and don't draw attention to the underlying mechanics.

### Output Deduplication

When responses span multiple LLM calls, overlap can occur. The assembler handles this:

```python
async def deduplicate_output(full_text):
    """Remove repeated content from multi-chunk output."""
    
    # 1. Sentence-level overlap detection
    sentences = split_sentences(full_text)
    seen = set()
    deduped = []
    
    for sentence in sentences:
        # Normalize for comparison (lowercase, strip whitespace)
        normalized = normalize(sentence)
        
        # Fuzzy match — catch near-duplicates (not just exact)
        if not any(similarity(normalized, s) > 0.85 for s in seen):
            deduped.append(sentence)
            seen.add(normalized)
    
    result = " ".join(deduped)
    
    # 2. If significant content was removed, do a coherence pass
    if len(result) < len(full_text) * 0.80:
        # Use fast model to smooth transitions
        result = await smooth_transitions(result)
    
    return result
```

### What the User Experiences

From the user's perspective, context overflow is invisible:

| Internal Event | User Sees |
|---------------|-----------|
| Context at 60%, compaction triggered | Nothing — response continues normally |
| Context at 80%, checkpoint created | Nothing — conversation continues |
| Output truncated, continuation needed | "...and continuing with that..." then response flows |
| Multiple chunks assembled | One coherent response, no visible seams |
| Emergency compaction (near OOM) | Slight pause, maybe a filler, then response |
| Conversation at 100+ messages | Same quality — checkpoints keep it manageable |

**The user never sees:**
- "Context limit reached"
- Truncated responses
- "I can't process that much text"
- Repeated paragraphs
- Loss of conversation context
- Any acknowledgment that limits exist

---

## User Interruption Handling

The user can interrupt Atlas at any point during generation — to stop, redirect, or ask a clarifying question. This mirrors natural conversation: you don't wait for someone to finish a monologue before speaking.

### Interruption Types

| Signal | Detection | Action |
|--------|-----------|--------|
| **Stop** | "stop", "that's enough", "got it", "thanks", "ok ok" | Immediately stop generation, keep what was streamed |
| **Redirect** | "wait, actually...", "forget that, I need...", new question | Stop current generation, pivot to new request with prior context |
| **Clarify** | "what do you mean by...", "can you explain the X part?" | Pause generation, answer the clarification, optionally resume |
| **Refine** | "make it shorter", "more detail on X", "skip the intro" | Stop, re-generate with the refinement instruction |

### How Interruption Works

```
User sends message while Atlas is still generating
    │
    ▼
Cortex receives new message → classify as interrupt
    │
    ├── STOP → halt generation immediately
    │          save partial output to interaction_log
    │          acknowledge naturally: "Sure, stopping there."
    │
    ├── REDIRECT → halt generation
    │              checkpoint partial output
    │              begin processing new request
    │              prior partial output available as context
    │
    ├── CLARIFY → pause generation (hold state)
    │             answer clarification inline
    │             ask: "Want me to continue where I left off?"
    │             if yes → resume generation from pause point
    │             if no → done
    │
    └── REFINE → halt generation
                 re-generate with original request + refinement
                 compact prior attempt into "don't do this" context
```

### Implementation

```python
class InterruptHandler:
    """Monitors for user messages during active generation."""
    
    def __init__(self):
        self.active_generations = {}  # conversation_id → generation state
    
    async def check_user_interrupt(self, conversation_id):
        """Non-blocking check for incoming user message during generation."""
        return conversation_id in self._pending_interrupts
    
    async def classify_interrupt(self, message):
        """Classify what kind of interrupt this is — fast, no LLM needed."""
        
        text = message.lower().strip()
        
        # Stop signals (pattern match, no LLM)
        stop_patterns = [
            r'^(stop|enough|got it|that\'?s enough|ok+|thanks?|thank you|nvm|never\s*mind)[\.\!]*$',
            r'^(i\'?m good|all good|that works|perfect)[\.\!]*$',
        ]
        if any(re.match(p, text) for p in stop_patterns):
            return 'stop'
        
        # Refine signals
        refine_patterns = [
            r'^(make it|be more|less|shorter|longer|simpler|more detail)',
            r'^(skip|focus on|just the|only the)',
        ]
        if any(re.match(p, text) for p in refine_patterns):
            return 'refine'
        
        # Clarify signals (questions about what was just said)
        if text.startswith(('what do you mean', 'what\'s', 'can you explain', 'huh')):
            return 'clarify'
        
        # Default: treat as redirect (new request)
        return 'redirect'


async def handle_mid_generation_question(request, chunks_so_far, new_message):
    """User asked a clarifying question while we were generating."""
    
    # Summarize what we've generated so far
    partial_context = summarize_chunks(chunks_so_far)
    
    # Build new request with the clarification
    clarification_request = build_clarification_context(
        original_question=request.messages[0],
        partial_answer=partial_context,
        clarification=new_message,
    )
    
    # Answer the clarification (may be instant if simple)
    answer = await process_through_pipeline(clarification_request)
    yield answer
    
    # Offer to continue
    yield "\n\nWant me to continue where I left off?"
```

### Voice Interruption

For voice interactions, interruption is even more natural — the user just starts talking:

- **Wake word during generation** → pause output, listen
- **Short utterance** ("stop", "ok") → classify as stop
- **New sentence** → classify as redirect/clarify
- **Silence after pause** (>3 seconds) → resume generation

The satellite mic should be in **listen-during-playback** mode so it can detect when the user speaks over Atlas. This requires echo cancellation (the mic hears Atlas's own TTS output and must filter it out to detect the user's voice).

### Cross-Conversation Context

When a user starts a new conversation, Cortex doesn't start from zero:
- **Memory** (HOT path) provides all learned preferences and facts
- **Recent interactions** from `interaction_log` provide last few exchanges
- **No old checkpoints** are loaded (clean slate for the conversation flow)
- But if the user says "remember when we talked about X?", Cortex searches checkpoints + memory

---

## Hardware-Agnostic Model Selection

Cortex uses detected hardware to select appropriate models:

```python
def recommend_models(hw_limits):
    """Suggest models based on detected hardware."""
    
    max_size = hw_limits['max_model_size_mb']
    
    models = {
        'fast': None,      # quick responses, Layer 3 simple queries
        'standard': None,  # general use, most Layer 3 queries
        'thinking': None,  # deep reasoning, complex problems
        'embedding': hw_limits['embedding_model'],
    }
    
    # Model candidates ordered by capability
    candidates = [
        # name,               size_mb, supports_thinking, quality_tier
        ('qwen3:72b-a22b',    42000,   True,             'excellent'),
        ('qwen3:30b-a3b',     18600,   True,             'great'),
        ('qwen2.5:32b',       18000,   False,            'great'),
        ('qwen3:14b',         9000,    True,             'good'),
        ('qwen2.5:14b',       9000,    False,            'good'),
        ('qwen3:8b',          5000,    True,             'decent'),
        ('qwen2.5:7b',        4500,    False,            'decent'),
        ('qwen3:4b',          2500,    True,             'basic'),
        ('qwen2.5:3b',        2000,    False,            'basic'),
        ('qwen3:1.7b',        1100,    True,             'minimal'),
    ]
    
    # Pick best model that fits
    for name, size, thinks, tier in candidates:
        if size <= max_size:
            if thinks and not models['thinking']:
                models['thinking'] = name
            if not models['standard']:
                models['standard'] = name
    
    # Fast model: pick something ≤50% of max size
    for name, size, thinks, tier in candidates:
        if size <= max_size * 0.50:
            models['fast'] = name
            break
    
    # Fallback: if only one model fits, use it for everything
    if not models['fast']:
        models['fast'] = models['standard']
    if not models['thinking']:
        models['thinking'] = models['standard']
    
    return models
```

### Model Configuration Storage

```sql
CREATE TABLE model_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL UNIQUE,          -- 'fast' | 'standard' | 'thinking' | 'embedding'
    model_name TEXT NOT NULL,           -- Ollama model tag
    context_default INTEGER,            -- default context window
    context_max INTEGER,                -- max context window
    temperature REAL DEFAULT 0.7,
    auto_selected BOOLEAN DEFAULT TRUE, -- TRUE if hardware-detected, FALSE if user overrode
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Users can override auto-selection:
```
"Atlas, use qwen3:30b-a3b for everything"
→ UPDATE model_config SET model_name = 'qwen3:30b-a3b', auto_selected = FALSE WHERE role IN ('fast', 'standard', 'thinking')

"Atlas, reset to automatic model selection"
→ Re-run recommend_models(), SET auto_selected = TRUE
```

---

## GPU Memory Monitoring

Cortex monitors GPU memory in real-time to prevent OOM crashes:

```python
async def check_gpu_health():
    """Check GPU memory before loading a model or expanding context."""
    
    gpu = get_current_gpu()
    used_mb = gpu['vram_used_mb']
    total_mb = gpu['vram_total_mb']
    free_mb = total_mb - used_mb
    utilization = used_mb / total_mb
    
    if utilization > 0.95:
        # Critical — refuse to load more, suggest compaction
        return 'critical', "GPU memory critical. Compacting context."
    
    if utilization > 0.85:
        # Warning — reduce context size, skip thinking mode
        return 'warning', "GPU memory high. Using reduced context."
    
    return 'ok', None
```

When GPU memory is constrained:
1. **Reduce context window** to minimum viable (4096)
2. **Skip thinking mode** — use standard model even for complex queries
3. **Force aggressive compaction** — checkpoint immediately
4. **Log the event** — so the nightly job can recommend hardware upgrades or smaller models

---

## Installation Flow

```
$ python -m cortex.install

Atlas Cortex — First-Time Setup
═══════════════════════════════

[1/4] Detecting hardware...
  CPU: AMD Ryzen 7 5700G (8c/16t)
  RAM: 128 GB DDR4
  GPU: AMD Radeon RX 7900 XT (20 GB GDDR6, ROCm)
  Disk: 347 GB free on /data

[2/4] Computing limits...
  VRAM tier: 16-24 GB
  Max model size: ~12 GB (leaves room for KV cache)
  Default context: 16,384 tokens
  Thinking context: 32,768 tokens
  Embedding model: nomic-embed-text (CPU)
  Max loaded models: 2

[3/4] Recommending models...
  Fast:     qwen2.5:14b (9.0 GB, ~55 tok/s)
  Standard: qwen3:30b-a3b (18.6 GB, ~75 tok/s)  ← MoE, only 3B active
  Thinking: qwen3:30b-a3b (extended context)
  Embed:    nomic-embed-text (274 MB, CPU)

  Accept these? [Y/n/customize]

[4/4] Pulling models...
  ✓ qwen3:30b-a3b (already present)
  ✓ qwen2.5:14b (already present)
  ✓ nomic-embed-text (already present)

  ✓ Setup complete. Hardware profile saved.
  
  Run 'python -m cortex.start' or add Atlas Cortex as an Open WebUI Pipe.
```

---

## Observability

### Context Metrics Table

```sql
CREATE TABLE context_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id INTEGER REFERENCES interaction_log(id),
    context_budget INTEGER,             -- total tokens allocated
    system_tokens INTEGER,              -- system prompt + profile
    memory_tokens INTEGER,              -- HOT path memory injection
    checkpoint_tokens INTEGER,          -- compressed history
    active_message_tokens INTEGER,      -- verbatim recent messages
    generation_reserve INTEGER,         -- reserved for output
    thinking_tokens_used INTEGER,       -- actual thinking tokens consumed (if thinking mode)
    compaction_triggered BOOLEAN DEFAULT FALSE,
    checkpoint_created BOOLEAN DEFAULT FALSE,
    gpu_vram_used_mb INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Health Dashboard Queries

```sql
-- Average context utilization
SELECT 
    DATE(created_at) as day,
    ROUND(AVG(
        (system_tokens + memory_tokens + checkpoint_tokens + active_message_tokens) * 100.0 
        / context_budget
    ), 1) as avg_utilization_pct,
    SUM(compaction_triggered) as compactions,
    SUM(checkpoint_created) as checkpoints
FROM context_metrics
GROUP BY DATE(created_at)
ORDER BY day DESC LIMIT 7;

-- Thinking mode token usage patterns
SELECT 
    ROUND(AVG(thinking_tokens_used)) as avg_think_tokens,
    MAX(thinking_tokens_used) as max_think_tokens,
    COUNT(*) as thinking_requests
FROM context_metrics
WHERE thinking_tokens_used > 0
AND created_at > datetime('now', '-7 days');
```

---

## Integration Points

| System | Integration |
|--------|-------------|
| **Architecture (Layer 3)** | Context builder assembles prompt with compaction before LLM call |
| **Memory (HOT path)** | Memory hits injected within token budget; more results = more tokens |
| **Grounding** | Grounding loop gets its own token budget inside generation reserve |
| **Backup** | `context_checkpoints` table backed up with cortex.db |
| **Nightly Evolution** | Reviews context_metrics to tune default windows and compaction thresholds |
| **User Profiles** | Profile size affects fixed token overhead; verbose profiles get trimmed |
