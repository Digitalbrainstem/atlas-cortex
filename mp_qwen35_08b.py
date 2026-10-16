#!/usr/bin/env python3
"""CAG on Qwen3.5-0.8B — Hybrid cache handler for linear+attention architecture.

Qwen3.5 uses a hybrid architecture:
  - 6 full_attention layers (standard KV cache)
  - 18 linear_attention layers (conv + recurrent state)

This requires a custom cache clone that handles both types.
"""
from __future__ import annotations
import os, json, time, gc, re
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/small_models")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_ID = "Qwen/Qwen3.5-0.8B"

# ── Qwen3.5 Hybrid Cache Handler ───────────────────────────────────

def clone_qwen35_cache(cache):
    """Deep clone a Qwen3_5DynamicCache (hybrid linear+attention)."""
    from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5DynamicCache

    # Need the config to create a new cache
    config = cache._config if hasattr(cache, '_config') else None
    if config is None:
        # Reconstruct minimal config info from cache structure
        class MinConfig:
            def __init__(self, layer_types, num_hidden_layers):
                self.layer_types = layer_types
                self.num_hidden_layers = num_hidden_layers
        layer_types = cache.layer_types
        num_layers = len(layer_types)
        config = MinConfig(layer_types, num_layers)

    new_cache = Qwen3_5DynamicCache(config)

    for i in range(len(cache.key_cache)):
        # Clone KV cache (full_attention layers have tensors, linear layers have empty/None)
        if cache.key_cache[i] is not None:
            new_cache.key_cache[i] = cache.key_cache[i].clone()
            new_cache.value_cache[i] = cache.value_cache[i].clone()

        # Clone conv/recurrent states (linear_attention layers)
        if cache.conv_states[i] is not None:
            new_cache.conv_states[i] = cache.conv_states[i].clone()
        if cache.recurrent_states[i] is not None:
            new_cache.recurrent_states[i] = cache.recurrent_states[i].clone()

    return new_cache


def build_cache_qwen35(text, tokenizer, model):
    """Build cache using user+assistant format (Qwen3.5 requires user message)."""
    msgs = [
        {"role": "user", "content": f"Please remember the following reference material. Do not respond yet, just acknowledge.\n\n{text}"},
        {"role": "assistant", "content": "I have noted all the reference material and will use it to answer your questions."},
    ]
    prefix = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
    ids = tokenizer(prefix, return_tensors="pt").input_ids.to("cuda")
    prefix_len = ids.shape[1]
    with torch.no_grad():
        out = model(ids, use_cache=True)
    return out.past_key_values, prefix_len


def query_with_cache_qwen35(q, cache, prefix_len, tok, model, max_new=200, thinking=False):
    """Query using cloned Qwen3.5 hybrid cache."""
    c = clone_qwen35_cache(cache)
    msgs = [{"role": "user", "content": q}]
    qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True,
                                  enable_thinking=thinking)
    qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
    ql = qids.shape[1]

    t0 = time.time()
    with torch.no_grad():
        out = model.generate(
            qids, past_key_values=c,
            cache_position=torch.arange(prefix_len, prefix_len + ql, device="cuda"),
            max_new_tokens=max_new, do_sample=False,
        )
    lat = time.time() - t0
    resp = tok.decode(out[0][ql:], skip_special_tokens=not thinking)
    if thinking and "</think>" in resp:
        resp = resp.split("</think>")[-1].strip()
    return resp, lat


def query_baseline_qwen35(q, tok, model, max_new=200, thinking=False):
    msgs = [{"role": "user", "content": q}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True,
                                    enable_thinking=thinking)
    ids = tok(text, return_tensors="pt").input_ids.to("cuda")
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    resp = tok.decode(out[0][ids.shape[1]:], skip_special_tokens=not thinking)
    if thinking and "</think>" in resp:
        resp = resp.split("</think>")[-1].strip()
    return resp, lat


def strict_score(response, keywords, min_required=None):
    r = response.lower()
    found = [k for k in keywords if k.lower() in r]
    needed = min_required or len(keywords)
    return len(found) >= needed, found


# ── Knowledge + Questions (same as small_models.py) ────────────────

KNOWLEDGE = """Important Reference Material — Medical Drug Information:

1. NeoCardiol (NCD-4417) is a dual-acting myosin activator and mitochondrial membrane stabilizer for heart failure.
   - Overall efficacy: 73% on primary composite endpoint (HR 0.27, p<0.001)
   - Trial: 1,243 patients across 87 sites in 14 countries, 52 weeks
   - LVEF improvement: 8.4 percentage points vs 1.2 placebo
   - Six-minute walk: +47 meters vs +8 placebo
   - Adverse events: nausea 12%, dizziness 5%, hepatotoxicity 2% (ALT >3x ULN)
   - Hepatotoxicity resolved within 8 weeks of discontinuation
   - Contraindicated in Child-Pugh B/C hepatic impairment

2. FloraRestore (FR-8821) is an oral microbiome transplant capsule with 47 bacterial strains for recurrent C. difficile.
   - CDI recurrence at 8 weeks: 8% vs 31% placebo (ARR 23%, NNT 4.3)
   - Trial: 847 patients at 64 sites, double-blind placebo-controlled
   - Shannon diversity: 1.8 → 4.1 in treatment group (vs 1.8 → 2.3 placebo)
   - Donor strain engraftment: 89% in responders
   - Adverse events: bloating 18%, mild diarrhea 9%, flatulence 14%
   - Dosing: 4 capsules daily x 3 days after vancomycin taper

3. NeuroSyncX is a closed-loop adaptive DBS implant for treatment-resistant Parkinson's disease.
   - Tremor reduction: 68% at 36 months (UPDRS-III: 14.2 → 4.5)
   - Overall UPDRS-III improvement: 52% (47.8 → 22.9)
   - Battery life: 4.2 years projected (95% CI 3.8-4.6)
   - MRI-conditional: 1.5T ONLY, SAR ≤0.1 W/kg, head coil only. 3T CONTRAINDICATED.
   - Surgical complications: lead migration 3.2%, infection 2.6%, transient dysarthria 7.1%
   - Trial: 312 patients at 23 centers, bilateral STN implantation"""

QUESTIONS = [
    {"q": "What was NeoCardiol's overall efficacy rate?", "strict": ["73"], "difficulty": "easy"},
    {"q": "How many patients were in the FloraRestore trial?", "strict": ["847"], "difficulty": "easy"},
    {"q": "What is NeuroSyncX's projected battery life?", "strict": ["4.2"], "difficulty": "easy"},
    {"q": "What was the CDI recurrence rate with FloraRestore at 8 weeks?", "strict": ["8%", "8 percent"], "difficulty": "easy"},
    {"q": "What percentage of NeoCardiol patients experienced hepatotoxicity?", "strict": ["2%", "2 percent"], "difficulty": "easy"},
    {"q": "Summarize NeoCardiol's safety profile including the most common adverse events and their rates.", "strict": ["12%", "5%", "2%", "nausea"], "min": 3, "difficulty": "medium"},
    {"q": "What evidence supports FloraRestore's mechanism of restoring gut health?", "strict": ["Shannon", "4.1", "89%", "engraftment"], "min": 2, "difficulty": "medium"},
    {"q": "Describe the key improvements NeuroSyncX showed over 36 months.", "strict": ["68%", "52%", "UPDRS", "22.9"], "min": 2, "difficulty": "medium"},
    {"q": "What is NeoCardiol's mechanism of action?", "strict": ["myosin", "mitochondrial"], "min": 2, "difficulty": "medium"},
    {"q": "What are FloraRestore's dosing instructions?", "strict": ["4 capsules", "3 days", "vancomycin"], "min": 2, "difficulty": "medium"},
    {"q": "Would NeoCardiol be appropriate for a patient with severe liver disease (Child-Pugh C)?", "strict": ["contraindicated", "Child-Pugh", "hepatotoxicity"], "min": 2, "difficulty": "hard"},
    {"q": "Can a NeuroSyncX patient safely get a 3T MRI of their brain?", "strict": ["contraindicated", "1.5T", "3T"], "min": 2, "difficulty": "hard"},
    {"q": "Based on the NNT, how many patients need FloraRestore treatment to prevent one CDI recurrence?", "strict": ["4.3", "NNT"], "min": 2, "difficulty": "hard"},
    {"q": "If a patient on NeoCardiol develops ALT 4x upper limit of normal, what should be done?", "strict": ["discontinu", "8 weeks", "resolve"], "min": 2, "difficulty": "hard"},
    {"q": "Compare the trial sizes across all three studies.", "strict": ["1243", "847", "312"], "min": 3, "difficulty": "hard"},
    {"q": "Which of the three treatments had the lowest adverse event rate overall?", "strict": ["FloraRestore", "bloating", "18%"], "min": 2, "difficulty": "very_hard"},
    {"q": "A patient with heart failure AND recurrent C. difficile AND elevated liver enzymes — which treatment should be started first and why?", "strict": ["FloraRestore", "liver", "hepatotoxicity", "NeoCardiol"], "min": 3, "difficulty": "very_hard"},
    {"q": "Which trial had the strongest statistical evidence based on sample size and effect size?", "strict": ["1243", "NeoCardiol", "73%", "HR"], "min": 2, "difficulty": "very_hard"},
    {"q": "If a 60-year-old has Parkinson's, heart failure (LVEF 30%), and CDI, prioritize their treatments.", "strict": ["NeuroSyncX", "FloraRestore", "NeoCardiol"], "min": 3, "difficulty": "very_hard"},
    {"q": "Compare the number-needed-to-treat for FloraRestore with NeoCardiol's absolute risk reduction.", "strict": ["4.3", "73%", "NNT"], "min": 2, "difficulty": "very_hard"},
]

# 100-turn conversation spot-checks
CONVERSATION_TURNS = [
    "Tell me about NeoCardiol's mechanism of action.",
    "What was the efficacy rate?",
    "How many patients were in the trial?",
    "What about the safety profile?",
    "Is it safe for patients with liver problems?",
    "What's the hepatotoxicity rate specifically?",
    "How long until hepatotoxicity resolves after stopping?",
    "What was the LVEF improvement?",
    "How did the six-minute walk test change?",
    "What was the hazard ratio?",
    "Now tell me about FloraRestore.",
    "What's the recurrence rate at 8 weeks?",
    "How many bacterial strains does it contain?",
    "What's the NNT?",
    "What were the adverse events?",
    "How is it dosed?",
    "What happened to Shannon diversity?",
    "What was the engraftment rate in responders?",
    "How does it compare to placebo at 6 months?",
    "Was it a double-blind trial?",
    "Tell me about the NeuroSyncX implant.",
    "What was the tremor reduction?",
    "Is it MRI compatible?",
    "What's the battery life?",
    "What surgical complications occurred?",
] + [f"Remind me: what was NeoCardiol's efficacy?" for _ in range(25)] \
  + [f"What was FloraRestore's recurrence rate?" for _ in range(25)] \
  + [f"NeuroSyncX battery life?" for _ in range(25)]

CONVERSATION_CHECKS = {
    0: ["myosin", "mitochondrial"],
    1: ["73"],
    5: ["2%"],
    10: ["FloraRestore"],
    13: ["4.3"],
    21: ["68"],
    23: ["4.2"],
    30: ["73"],       # Long-term recall
    55: ["8%"],       # Long-term recall
    80: ["4.2"],      # Very long-term recall
    99: ["4.2"],      # Final turn recall
}


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"CAG on Qwen3.5-0.8B — Hybrid Cache Handler")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    print(f"\nLoading {MODEL_ID}...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float16).to("cuda")
    model.eval()
    vram = torch.cuda.memory_allocated() / 1e9
    print(f"Loaded. VRAM: {vram:.1f}GB")

    # Warmup
    _ = query_baseline_qwen35("Hello", tok, model, max_new=5)
    print("Warmup done.\n")

    # ── Test 1: Knowledge Injection ─────────────────────────────────
    print("=" * 60)
    print("  TEST 1: Knowledge Injection")
    print("=" * 60)

    cache, prefix_len = build_cache_qwen35(KNOWLEDGE, tok, model)
    cache_info = f"{prefix_len} tokens"
    print(f"  Cache built: {cache_info}")

    # Store config reference for cloning
    cache._config = model.config.text_config

    by_diff = {}
    details = []

    for i, q in enumerate(QUESTIONS):
        diff = q["difficulty"]
        if diff not in by_diff:
            by_diff[diff] = {"baseline": 0, "cag": 0, "cag_think": 0, "total": 0}

        rb, lb = query_baseline_qwen35(q["q"], tok, model, max_new=200)
        pb, _ = strict_score(rb, q["strict"], q.get("min"))

        rc, lc = query_with_cache_qwen35(q["q"], cache, prefix_len, tok, model, max_new=200)
        pc, fc = strict_score(rc, q["strict"], q.get("min"))

        rct, lct = query_with_cache_qwen35(q["q"], cache, prefix_len, tok, model, max_new=512, thinking=True)
        pct, fct = strict_score(rct, q["strict"], q.get("min"))

        by_diff[diff]["baseline"] += int(pb)
        by_diff[diff]["cag"] += int(pc)
        by_diff[diff]["cag_think"] += int(pct)
        by_diff[diff]["total"] += 1

        sym = lambda x: "✓" if x else "✗"
        print(f"  Q{i+1:2d} [{diff:>9}] Base:{sym(pb)} CAG:{sym(pc)} CAG+T:{sym(pct)} ({lc:.1f}s/{lct:.1f}s)")

        details.append({"q": q["q"], "diff": diff, "base": pb, "cag": pc, "cag_think": pct,
                         "base_resp": rb[:200], "cag_resp": rc[:200], "cag_think_resp": rct[:200]})

    print(f"\n  {'Difficulty':>10} | {'Base':>6} | {'CAG':>6} | {'CAG+Think':>10}")
    print(f"  {'-'*45}")
    for diff in ["easy", "medium", "hard", "very_hard"]:
        if diff in by_diff:
            d = by_diff[diff]
            n = d["total"]
            print(f"  {diff:>10} | {d['baseline']/n*100:>5.0f}% | {d['cag']/n*100:>5.0f}% | {d['cag_think']/n*100:>9.0f}%")
    tot_b = sum(d["baseline"] for d in by_diff.values())
    tot_c = sum(d["cag"] for d in by_diff.values())
    tot_ct = sum(d["cag_think"] for d in by_diff.values())
    tot_n = sum(d["total"] for d in by_diff.values())
    print(f"  {'OVERALL':>10} | {tot_b/tot_n*100:>5.0f}% | {tot_c/tot_n*100:>5.0f}% | {tot_ct/tot_n*100:>9.0f}%")

    # ── Test 2: 100-Turn Persistent Context ─────────────────────────
    print(f"\n{'='*60}")
    print(f"  TEST 2: 100-Turn Persistent Context")
    print(f"{'='*60}")

    conv_cache = clone_qwen35_cache(cache)
    conv_cache._config = model.config.text_config
    current_pos = prefix_len
    checks_passed = 0
    checks_total = 0
    turn_data = []

    for turn_idx in range(min(100, len(CONVERSATION_TURNS))):
        q = CONVERSATION_TURNS[turn_idx]
        msgs = [{"role": "user", "content": q}]
        qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
        ql = qids.shape[1]

        t0 = time.time()
        with torch.no_grad():
            out = model.generate(
                qids, past_key_values=conv_cache,
                cache_position=torch.arange(current_pos, current_pos + ql, device="cuda"),
                max_new_tokens=100, do_sample=False,
            )
        lat = time.time() - t0
        resp = tok.decode(out[0][ql:], skip_special_tokens=True)
        gen_len = out[0].shape[0] - ql
        current_pos += ql + gen_len

        check_result = None
        if turn_idx in CONVERSATION_CHECKS:
            expected = CONVERSATION_CHECKS[turn_idx]
            found = [k for k in expected if k.lower() in resp.lower()]
            passed = len(found) > 0
            checks_total += 1
            checks_passed += int(passed)
            sym = "✓" if passed else "✗"
            print(f"  Turn {turn_idx+1:3d}: {sym} ({lat:.1f}s, pos={current_pos}) {q[:45]}... → {found}")
            check_result = {"expected": expected, "found": found, "passed": passed}
        elif turn_idx < 5 or turn_idx % 20 == 0:
            print(f"  Turn {turn_idx+1:3d}: ({lat:.1f}s, pos={current_pos}) {q[:45]}...")

        turn_data.append({"turn": turn_idx+1, "latency": lat, "pos": current_pos, "check": check_result})

        # Reset if getting too long (avoid OOM on 0.8B)
        if current_pos > 8000:
            conv_cache = clone_qwen35_cache(cache)
            conv_cache._config = model.config.text_config
            current_pos = prefix_len
            print(f"    [Cache reset at turn {turn_idx+1}]")

    print(f"\n  Spot-checks: {checks_passed}/{checks_total} ({checks_passed/max(checks_total,1)*100:.0f}%)")

    # Save results
    results = {
        "model": MODEL_ID, "vram_gb": vram, "timestamp": datetime.now().isoformat(),
        "knowledge": {"by_difficulty": by_diff, "overall": {"base": tot_b, "cag": tot_c, "cag_think": tot_ct, "total": tot_n}, "details": details},
        "persistent": {"checks_passed": checks_passed, "checks_total": checks_total, "turns": turn_data},
    }
    out = RESULTS_DIR / "cag_Qwen_Qwen3.5-0.8B.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out}")
    print(f"Finished: {datetime.now().isoformat()}")
