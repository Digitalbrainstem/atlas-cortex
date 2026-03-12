# Bio-Inspired Architecture — Implementation Plan

This is the focused implementation plan for the bio-inspired architecture patterns
discovered during the human body mapping exercise. This is SEPARATE from the LLM
optimization strategy (`docs/llm-optimization-strategy.md`) which covers model
distillation, pruning, and the Model Scout system.

## What This Covers

Six architectural additions inspired by human biology:

1. **Muscle Memory** — Semantic response cache (Layer 1.5)
2. **Thalamus** — Query triage / attention gating
3. **Parallel Pipeline** — asyncio.gather for concurrent pre-flight
4. **Immune Vaccine** — Prompt vaccination before LLM
5. **Hormonal Engine** — System-wide state (cortisol, dopamine, etc.)
6. **Sleep Architecture** — Structured circadian cycles
7. **Zero-I/O Pipeline** — In-process models, eliminate HTTP overhead
8. **Memory Decay** — Synaptic pruning (strengthen used, weaken unused)

## Prerequisites

- Existing pipeline in `cortex/pipeline/` is functional
- Safety middleware in `cortex/safety/` is functional
- Memory HOT/COLD paths in `cortex/memory/` exist

---

## Phase A: Quick Wins (No Dependencies, Immediate Impact)

### A1: Parallel Pipeline Pre-flight

**Files:** `cortex/pipeline/__init__.py`
**Effort:** Small — refactor existing sequential awaits to asyncio.gather
**Impact:** ~66ms saved per L3 query

```python
# Current (sequential):
context = await assemble_context(...)        # 1ms
safety = await check_input(...)              # 10ms
memory = await hot_query(...)                # 40ms

# New (parallel):
safety, context, memory, filler = await asyncio.gather(
    check_input(message),
    assemble_context(message, ...),
    hot_query(message, ...) if needs_memory else noop(),
    select_filler(sentiment) if is_l3 else noop(),
)
```

**Notes:**
- Safety and context have no dependencies on each other
- Memory prefetch starts immediately, ready by time L3 needs it
- Filler selection runs concurrently — audio is ready before LLM responds

**Success criteria:** Measure time from request entry to first LLM token. Should
drop by ~50-66ms.

### A2: Immune Vaccine Layer

**Files:** `cortex/safety/middleware.py`, `cortex/pipeline/layer3_llm.py`
**Effort:** Small — use existing ConversationDriftMonitor temperature
**Impact:** Better jailbreak defense at the actual attack vector

```python
# In layer3_llm.py, right before building the LLM prompt:
def vaccinate_prompt(system_prompt: str, drift_temperature: float) -> str:
    if drift_temperature >= 0.7:
        return system_prompt + STRONG_DEFENSE_INJECTION
    elif drift_temperature >= 0.4:
        return system_prompt + MODERATE_DEFENSE_REMINDER
    return system_prompt
```

**Notes:**
- ConversationDriftMonitor already tracks safety temperature per conversation
- Just need to pass that temperature to the prompt builder
- Strong defense text is ~100 tokens — minimal impact on context window

**Success criteria:** Jailbreak pass-through rate drops. Test with existing
jailbreak patterns that currently bypass input guardrails.

### A3: Deferred Logging

**Files:** `cortex/pipeline/__init__.py`
**Effort:** Tiny — wrap `_log_interaction()` in fire-and-forget
**Impact:** ~2-5ms saved per request (no sync DB write in hot path)

```python
# Current: synchronous write
_log_interaction(conn, context, message, response, ...)

# New: fire-and-forget queue
asyncio.create_task(_log_interaction_async(context, message, response, ...))
```

**Success criteria:** Interaction logging still works (verify in tests), but
no longer shows up in request latency measurements.

---

## Phase B: Muscle Memory (Moderate Effort, High Impact)

### B1: Semantic Response Cache

**New file:** `cortex/pipeline/layer1_5_cache.py`
**Modify:** `cortex/pipeline/__init__.py` (insert between L1 and L2)
**Effort:** Medium — needs embedding computation + similarity search
**Impact:** 30-40% of queries skip LLM entirely after learning period

**Dependencies:** Needs embedding model. Options:
- Use Ollama's `/api/embeddings` endpoint (adds I/O — acceptable for now)
- Use sentence-transformers in-process (better, but adds dependency)
- Use numpy cosine similarity on cached embeddings (simplest)

**Data structure:**
```python
@dataclass
class CachedResponse:
    query_embedding: np.ndarray   # 384-dim vector
    response: str
    confidence: float
    hit_count: int                # Neuroplasticity — strengthens with use
    created_at: datetime
    last_hit: datetime

class SemanticCache:
    similarity_threshold: float = 0.92
    max_entries: int = 10000
    decay_rate: float = 0.01      # Per day without hits
```

**Pipeline integration:**
```
L0 (context) → L1 (instant) → L1.5 (muscle memory) → L2 (plugins) → L3 (LLM)
                                  │
                                  ├─ Cache HIT → return cached response (10ms)
                                  └─ Cache MISS → continue to L2/L3
                                     → after L3: cache the response for next time
```

**Success criteria:** After 1 week of operation, measure cache hit rate.
Target: 20%+ of L3-eligible queries served from cache.

### B2: Thalamus (Query Triage)

**New file:** `cortex/pipeline/thalamus.py`
**Modify:** `cortex/pipeline/__init__.py` (insert before L1)
**Effort:** Small-Medium
**Impact:** ~15% of queries get instant micro-response, skip pipeline entirely

**Triage tiers:**
```python
class QueryTier(IntEnum):
    AUTONOMIC = 0    # "ok", "thanks", "bye" → micro-response, no processing
    REFLEX = 1       # Pattern-matchable → Layer 1
    MEMORY = 2       # Familiar query → Layer 1.5 cache check
    REASONING = 3    # Novel query → full pipeline (L2 → L3)
    DEEP = 4         # Complex query → L3 with expert LoRA

def triage(message: str, embedding: np.ndarray = None) -> QueryTier:
    tokens = message.lower().split()
    if len(tokens) <= 2 and tokens[0] in ACKNOWLEDGMENTS:
        return QueryTier.AUTONOMIC
    # ... more heuristics
```

**Success criteria:** Measure how many queries are classified into each tier.
Tier 0 (AUTONOMIC) should be 10-15% of daily queries.

---

## Phase C: Hormonal Engine (Medium Effort, Systemic Impact)

### C1: Core Hormonal State

**New file:** `cortex/state/hormones.py`
**Effort:** Medium — the state object is simple; integration touches many files
**Impact:** System-wide behavioral adaptation

```python
@dataclass
class HormonalState:
    cortisol: float = 0.0
    dopamine: float = 0.5
    serotonin: float = 0.7
    adrenaline: float = 0.0
    oxytocin: float = 0.0        # Per-user
    melatonin: float = 0.0
    
    def update(self, trigger: str, magnitude: float = 1.0):
        """Apply a hormonal trigger with natural decay."""
        ...
    
    def decay(self, elapsed_seconds: float):
        """All hormones naturally decay toward baseline."""
        ...
```

### C2: Subsystem Integration

Each subsystem reads hormonal state and adjusts:
- `layer3_llm.py`: High cortisol → add `max_tokens` limit (shorter responses)
- `cortex/filler/`: High adrenaline → skip filler (respond immediately)
- `cortex/safety/`: High cortisol → lower detection thresholds
- TTS: High serotonin → warmer voice parameters
- Self-evolution: Any adrenaline > 0 → abort immediately

**Success criteria:** Observable behavioral changes during stress tests.
Simulate high load → verify shorter responses. Simulate jailbreak attempt →
verify elevated safety thresholds persist for 5 minutes.

---

## Phase D: Sleep Architecture (Integrates Everything)

### D1: Sleep Cycle Controller

**New file:** `cortex/maintenance/sleep.py`
**Effort:** Medium — orchestrates other components
**Dependencies:** Phases A-C should be in place for full value

```python
class SleepController:
    async def run_cycle(self):
        """One 90-minute sleep cycle."""
        await self.stage_1_transition()      # 10 min
        await self.stage_2_consolidation()   # 20 min (memory decay, merge)
        await self.stage_3_deep()            # 30 min (diagnostics, cleaning)
        await self.stage_4_rem()             # 30 min (self-evolution)
    
    async def micro_nap(self):
        """Quick maintenance during idle (>60s no query)."""
        await flush_deferred_logs()          # 10ms
        await update_memory_decay()          # 5ms
        await validate_cache_freshness()     # 5ms
```

### D2: Memory Decay and Consolidation

**Modify:** `cortex/memory/cold.py`, new `cortex/memory/consolidation.py`
**Schema change:** Add `relevance_score`, `last_accessed`, `emotional_weight`
to memories table

**Success criteria:** After 30 days, memory retrieval latency should be faster
(fewer, more relevant entries). Stale memory count should decrease.

---

## Phase E: Zero-I/O Pipeline (Advanced, High Impact)

### E1: In-Process LLM

**New file:** `cortex/providers/llamacpp.py`
**Effort:** Medium — new provider implementation
**Impact:** ~100ms saved per query (no HTTP overhead)

Replace OllamaProvider with a LlamaCppProvider that loads the model in-process
via llama-cpp-python. The C library releases the GIL during inference.

**Prerequisites:** Distilled base model exists as GGUF file.

### E2: In-Process Embedding

**Modify:** `cortex/memory/hot.py`
**Effort:** Small — use sentence-transformers or numpy
**Impact:** Memory retrieval becomes pure compute (~2ms vs ~20-40ms)

### E3: In-Memory Vector Index

**New file:** `cortex/memory/vector_index.py`
**Effort:** Medium — replace ChromaDB/SQLite FTS5 with in-memory numpy/FAISS
**Impact:** Zero disk I/O for memory retrieval

---

## Execution Order

```
Phase A (Quick Wins) ── can start immediately, no dependencies
  A1: Parallel pipeline      ← START HERE
  A2: Immune vaccine
  A3: Deferred logging

Phase B (Muscle Memory) ── start after A1 validates parallel pattern
  B1: Semantic cache
  B2: Thalamus triage

Phase C (Hormonal) ── independent of A/B, can run in parallel
  C1: Core state object
  C2: Subsystem integration

Phase D (Sleep) ── needs B1 (cache to validate) + C1 (hormones to control)
  D1: Sleep controller
  D2: Memory decay

Phase E (Zero-I/O) ── needs distilled model from LLM optimization plan
  E1: In-process LLM
  E2: In-process embedding
  E3: In-memory vectors
```

---

## Relationship to Other Plans

- **`docs/llm-optimization-strategy.md`** — Model distillation, pruning, Model Scout.
  Phase E here depends on having a distilled model from that plan.
- **`docs/research-lora-tts-stt.md`** — Domain-specific TTS/STT adapters.
  Independent research thread, but skill packages integrate with Phase C hormones.
- **Blog articles** — Architectural concepts documented in `Betanu701/atlas-blog-drafts`.
