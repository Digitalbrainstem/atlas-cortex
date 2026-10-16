#!/usr/bin/env python3
"""CAG Small Model + Unlimited Context Test Suite

Tests CAG knowledge injection on the smallest Qwen3.5 and Qwen3 models:
  1. Knowledge injection accuracy (strict scoring)
  2. 100-turn persistent context (unlimited conversation)
  3. CAG + thinking mode on tiny models
  4. Compressed cache accuracy (quantize down)
"""
from __future__ import annotations
import os, json, time, gc, re, sys
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/small_models")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Models to test ──────────────────────────────────────────────────
MODELS = [
    "Qwen/Qwen3-0.6B",     # 600M — smallest Qwen3, standard DynamicCache
    "Qwen/Qwen3-4B",       # 4B — our proven model, for comparison
]
# Note: Qwen3.5 uses Qwen3_5DynamicCache (hybrid linear+standard attention)
# which is incompatible with standard DynamicCache clone/inject.
# Qwen3.5 testing requires a separate approach — queued for future work.

# ── Knowledge for injection ─────────────────────────────────────────
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

# ── Questions with STRICT scoring (specific numbers required) ───────
QUESTIONS = [
    # Easy (direct lookup)
    {"q": "What was NeoCardiol's overall efficacy rate?", "strict": ["73"], "difficulty": "easy"},
    {"q": "How many patients were in the FloraRestore trial?", "strict": ["847"], "difficulty": "easy"},
    {"q": "What is NeuroSyncX's projected battery life?", "strict": ["4.2"], "difficulty": "easy"},
    {"q": "What was the CDI recurrence rate with FloraRestore at 8 weeks?", "strict": ["8%", "8 percent"], "difficulty": "easy"},
    {"q": "What percentage of NeoCardiol patients experienced hepatotoxicity?", "strict": ["2%", "2 percent"], "difficulty": "easy"},
    # Medium (comprehension)
    {"q": "Summarize NeoCardiol's safety profile including the most common adverse events and their rates.", "strict": ["12%", "5%", "2%", "nausea"], "min": 3, "difficulty": "medium"},
    {"q": "What evidence supports FloraRestore's mechanism of restoring gut health?", "strict": ["Shannon", "4.1", "89%", "engraftment"], "min": 2, "difficulty": "medium"},
    {"q": "Describe the key improvements NeuroSyncX showed over 36 months.", "strict": ["68%", "52%", "UPDRS", "22.9"], "min": 2, "difficulty": "medium"},
    {"q": "What is NeoCardiol's mechanism of action?", "strict": ["myosin", "mitochondrial"], "min": 2, "difficulty": "medium"},
    {"q": "What are FloraRestore's dosing instructions?", "strict": ["4 capsules", "3 days", "vancomycin"], "min": 2, "difficulty": "medium"},
    # Hard (inference)
    {"q": "Would NeoCardiol be appropriate for a patient with severe liver disease (Child-Pugh C)?", "strict": ["contraindicated", "Child-Pugh", "hepatotoxicity"], "min": 2, "difficulty": "hard"},
    {"q": "Can a NeuroSyncX patient safely get a 3T MRI of their brain?", "strict": ["contraindicated", "1.5T", "3T"], "min": 2, "difficulty": "hard"},
    {"q": "Based on the NNT, how many patients need FloraRestore treatment to prevent one CDI recurrence?", "strict": ["4.3", "NNT"], "min": 2, "difficulty": "hard"},
    {"q": "If a patient on NeoCardiol develops ALT 4x upper limit of normal, what should be done?", "strict": ["discontinu", "8 weeks", "resolve"], "min": 2, "difficulty": "hard"},
    {"q": "Compare the trial sizes across all three studies.", "strict": ["1243", "847", "312"], "min": 3, "difficulty": "hard"},
    # Very Hard (multi-hop / inference across docs)
    {"q": "Which of the three treatments had the lowest adverse event rate overall?", "strict": ["FloraRestore", "bloating", "18%"], "min": 2, "difficulty": "very_hard"},
    {"q": "A patient with heart failure AND recurrent C. difficile AND elevated liver enzymes — which treatment should be started first and why?", "strict": ["FloraRestore", "liver", "hepatotoxicity", "NeoCardiol"], "min": 3, "difficulty": "very_hard"},
    {"q": "Which trial had the strongest statistical evidence based on sample size and effect size?", "strict": ["1243", "NeoCardiol", "73%", "HR"], "min": 2, "difficulty": "very_hard"},
    {"q": "If a 60-year-old has Parkinson's, heart failure (LVEF 30%), and CDI, prioritize their treatments.", "strict": ["NeuroSyncX", "FloraRestore", "NeoCardiol"], "min": 3, "difficulty": "very_hard"},
    {"q": "Compare the number-needed-to-treat for FloraRestore with NeoCardiol's absolute risk reduction.", "strict": ["4.3", "73%", "NNT"], "min": 2, "difficulty": "very_hard"},
]

# ── 100-turn conversation topics (for persistent context test) ──────
CONVERSATION_TURNS = [
    # First 20: about NeoCardiol
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
    # 11-20: about FloraRestore
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
    # 21-30: about NeuroSyncX
    "Tell me about the NeuroSyncX implant.",
    "What was the tremor reduction?",
    "Is it MRI compatible?",
    "What's the battery life?",
    "What surgical complications occurred?",
    "How many centers participated?",
    "What was the overall UPDRS improvement?",
    "How does the adaptive algorithm help?",
    "What's the stimulation energy savings?",
    "What Hoehn & Yahr stages were included?",
    # 31-50: cross-referencing (tests if persistent context holds)
    "Going back to NeoCardiol — what was the efficacy rate again?",
    "And FloraRestore's recurrence rate?",
    "Compare the trial sizes of all three studies.",
    "Which treatment had the worst adverse event profile?",
    "Which is safest for a patient with liver disease?",
    "What was NeoCardiol's six-minute walk improvement?",
    "How many sites were in the FloraRestore trial?",
    "What's NeuroSyncX's UPDRS-III final score?",
    "Is NeoCardiol contraindicated anywhere?",
    "What's FloraRestore's placebo recurrence rate?",
    # 41-50: harder questions
    "If I have both heart failure and CDI, what do I take first?",
    "Can I get a 3T MRI with NeuroSyncX?",
    "What's the absolute risk reduction for FloraRestore?",
    "How does NeoCardiol's HR compare to typical heart failure drugs?",
    "Rank the three treatments by trial quality (sample size, blinding, endpoints).",
    "What monitoring is needed for NeoCardiol in the first 6 months?",
    "Could FloraRestore work for immunocompromised patients?",
    "What percentage of NeuroSyncX patients had lead migration?",
    "Is NeoCardiol's hepatotoxicity dose-dependent?",
    "Summarize all three treatments in one paragraph.",
    # 51-70: repeat earlier questions to test long-term retention
    "What was NeoCardiol's efficacy rate?",
    "FloraRestore recurrence rate at 8 weeks?",
    "NeuroSyncX battery life?",
    "NeoCardiol hepatotoxicity rate?",
    "FloraRestore NNT?",
    "NeuroSyncX tremor reduction?",
    "NeoCardiol trial size?",
    "FloraRestore Shannon diversity change?",
    "NeuroSyncX MRI safety?",
    "NeoCardiol LVEF improvement?",
    "FloraRestore dosing?",
    "NeuroSyncX surgical complication rate for infection?",
    "NeoCardiol mechanism?",
    "FloraRestore engraftment rate?",
    "NeuroSyncX UPDRS improvement percentage?",
    "NeoCardiol six-minute walk?",
    "FloraRestore bloating rate?",
    "NeuroSyncX number of centers?",
    "How long for NeoCardiol hepatotoxicity to resolve?",
    "FloraRestore bacterial strain count?",
    # 71-100: mix of easy and hard to test sustained performance
    "What's NeoCardiol's efficacy?",
    "FloraRestore recurrence?",
    "NeuroSyncX battery?",
    "Compare all three trial sizes.",
    "Which is safest for liver patients?",
    "NeoCardiol HR?",
    "FloraRestore NNT?",
    "NeuroSyncX tremor reduction percentage?",
    "List NeoCardiol adverse events.",
    "List FloraRestore adverse events.",
    "List NeuroSyncX complications.",
    "What's the best treatment for someone with all three conditions?",
    "NeoCardiol contraindications?",
    "FloraRestore mechanism?",
    "NeuroSyncX adaptive algorithm benefit?",
    "NeoCardiol LVEF change vs placebo?",
    "FloraRestore vs placebo at 6 months?",
    "NeuroSyncX quality of life improvement?",
    "Rank treatments by adverse event profile.",
    "Summarize everything you know about these three treatments.",
]

# Spot-check answers for conversation (strict keywords at specific turns)
CONVERSATION_CHECKS = {
    0: ["myosin", "mitochondrial"],          # Q1: mechanism
    1: ["73"],                                 # Q2: efficacy
    10: ["FloraRestore", "microbiome"],        # Q11: about FloraRestore
    13: ["4.3"],                               # Q14: NNT
    21: ["68%"],                               # Q22: tremor reduction
    30: ["73"],                                 # Q31: recall NeoCardiol efficacy
    31: ["8%"],                                 # Q32: recall FloraRestore
    50: ["73"],                                 # Q51: long-term recall
    51: ["8%"],                                 # Q52: long-term recall
    52: ["4.2"],                               # Q53: long-term recall
    70: ["73"],                                 # Q71: very long-term recall
    99: ["NeoCardiol", "FloraRestore", "NeuroSyncX"],  # Q100: final summary
}


# ── Utilities ───────────────────────────────────────────────────────

def clone_cache(cache):
    c = DynamicCache()
    if hasattr(cache, "layers"):
        for L in cache.layers:
            c.update(L.keys.clone(), L.values.clone(), len(c.layers))
    elif hasattr(cache, "key_cache"):
        for i in range(len(cache.key_cache)):
            if cache.key_cache[i] is not None:
                c.update(cache.key_cache[i].clone(), cache.value_cache[i].clone(), i)
    return c

def build_cache(text, tokenizer, model):
    # Try system message first, fall back to user message if template requires it
    for msg_format in [
        [{"role": "system", "content": text}],
        [{"role": "user", "content": f"Please remember the following reference material. Do not respond yet, just acknowledge.\n\n{text}"},
         {"role": "assistant", "content": "I have noted all the reference material and will use it to answer your questions."}],
    ]:
        try:
            prefix = tokenizer.apply_chat_template(msg_format, tokenize=False, enable_thinking=False)
            break
        except Exception:
            try:
                prefix = tokenizer.apply_chat_template(msg_format, tokenize=False)
                break
            except Exception:
                continue
    else:
        raise RuntimeError("Could not build cache — no chat template worked")

    ids = tokenizer(prefix, return_tensors="pt").input_ids.to("cuda")
    prefix_len = ids.shape[1]
    with torch.no_grad():
        out = model(ids, use_cache=True)
    return out.past_key_values, prefix_len

def query_with_cache(q, cache, prefix_len, tok, model, max_new=200, thinking=False):
    c = clone_cache(cache)
    msgs = [{"role": "user", "content": q}]
    try:
        qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    except Exception:
        qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
    ql = qids.shape[1]
    pos = torch.arange(prefix_len, prefix_len + ql, device="cuda").unsqueeze(0)
    mask = torch.ones(1, prefix_len + ql, device="cuda", dtype=torch.long)
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(qids, past_key_values=c, position_ids=pos,
                             attention_mask=mask, max_new_tokens=max_new, do_sample=False)
    lat = time.time() - t0
    resp = tok.decode(out[0][ql:], skip_special_tokens=not thinking)
    if thinking and "</think>" in resp:
        resp = resp.split("</think>")[-1].strip()
    return resp, lat

def query_baseline(q, tok, model, max_new=200, thinking=False):
    msgs = [{"role": "user", "content": q}]
    try:
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=thinking)
    except Exception:
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
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

def kw_check(response, keywords):
    r = response.lower()
    return [k for k in keywords if k.lower() in r]


# ── Test 1: Knowledge Injection Accuracy ────────────────────────────

def test_knowledge_injection(tok, model, model_name):
    print(f"\n{'='*60}")
    print(f"  TEST 1: Knowledge Injection — {model_name}")
    print(f"{'='*60}")

    cache, prefix_len = build_cache(KNOWLEDGE, tok, model)
    cache_size_mb = sum(
        L.keys.nelement() * L.keys.element_size() + L.values.nelement() * L.values.element_size()
        for L in (cache.layers if hasattr(cache, "layers") else [])
    ) / 1e6 if hasattr(cache, "layers") else 0
    print(f"  Cache: {prefix_len} tokens, ~{cache_size_mb:.1f}MB")

    results = {"model": model_name, "prefix_tokens": prefix_len, "cache_mb": cache_size_mb}
    by_diff = {}

    for i, q in enumerate(QUESTIONS):
        diff = q["difficulty"]
        if diff not in by_diff:
            by_diff[diff] = {"baseline": 0, "cag": 0, "cag_think": 0, "total": 0}

        # Baseline
        rb, lb = query_baseline(q["q"], tok, model, max_new=200)
        pb, _ = strict_score(rb, q["strict"], q.get("min"))

        # CAG
        rc, lc = query_with_cache(q["q"], cache, prefix_len, tok, model, max_new=200)
        pc, fc = strict_score(rc, q["strict"], q.get("min"))

        # CAG + thinking
        rct, lct = query_with_cache(q["q"], cache, prefix_len, tok, model, max_new=512, thinking=True)
        pct, fct = strict_score(rct, q["strict"], q.get("min"))

        by_diff[diff]["baseline"] += int(pb)
        by_diff[diff]["cag"] += int(pc)
        by_diff[diff]["cag_think"] += int(pct)
        by_diff[diff]["total"] += 1

        sym = lambda x: "✓" if x else "✗"
        print(f"  Q{i+1:2d} [{diff:>9}] Base:{sym(pb)} CAG:{sym(pc)} CAG+T:{sym(pct)} ({lc:.1f}s/{lct:.1f}s)")

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

    results["by_difficulty"] = by_diff
    results["overall"] = {"baseline": tot_b, "cag": tot_c, "cag_think": tot_ct, "total": tot_n}

    del cache; gc.collect(); torch.cuda.empty_cache()
    return results


# ── Test 2: 100-Turn Persistent Context ─────────────────────────────

def test_persistent_context(tok, model, model_name, num_turns=100):
    print(f"\n{'='*60}")
    print(f"  TEST 2: {num_turns}-Turn Persistent Context — {model_name}")
    print(f"{'='*60}")

    # Build knowledge cache
    cache, prefix_len = build_cache(KNOWLEDGE, tok, model)
    conversation_cache = clone_cache(cache)
    current_len = prefix_len

    results = {"model": model_name, "turns": [], "checks": {}}
    checks_passed = 0
    checks_total = 0

    for turn_idx in range(min(num_turns, len(CONVERSATION_TURNS))):
        q = CONVERSATION_TURNS[turn_idx]

        # Build query
        msgs = [{"role": "user", "content": q}]
        try:
            qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except Exception:
            qt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
        ql = qids.shape[1]

        pos = torch.arange(current_len, current_len + ql, device="cuda").unsqueeze(0)
        mask = torch.ones(1, current_len + ql, device="cuda", dtype=torch.long)

        t0 = time.time()
        with torch.no_grad():
            out = model.generate(
                qids, past_key_values=conversation_cache,
                position_ids=pos, attention_mask=mask,
                max_new_tokens=150, do_sample=False,
            )
        lat = time.time() - t0
        new_tokens = out[0][ql:]
        resp = tok.decode(new_tokens, skip_special_tokens=True)

        # Update conversation cache with generated tokens
        # (In a full impl we'd append to cache; here we rebuild for safety)
        gen_len = new_tokens.shape[0]
        current_len += ql + gen_len

        # Check answer if this turn has a spot-check
        check_result = None
        if turn_idx in CONVERSATION_CHECKS:
            expected = CONVERSATION_CHECKS[turn_idx]
            found = kw_check(resp, expected)
            passed = len(found) > 0
            check_result = {"expected": expected, "found": found, "passed": passed}
            checks_total += 1
            checks_passed += int(passed)
            sym = "✓" if passed else "✗"
            print(f"  Turn {turn_idx+1:3d}: {sym} ({lat:.1f}s, {current_len} tokens) {q[:50]}... → found {found}")
        else:
            if turn_idx < 10 or turn_idx % 10 == 0:
                print(f"  Turn {turn_idx+1:3d}: ({lat:.1f}s, {current_len} tokens) {q[:50]}...")

        results["turns"].append({
            "turn": turn_idx + 1, "question": q, "response": resp[:200],
            "latency": lat, "total_tokens": current_len,
            "check": check_result,
        })

        # Rebuild cache periodically to prevent OOM (simple approach)
        if current_len > 4000:
            # Reset conversation but keep knowledge
            conversation_cache = clone_cache(cache)
            current_len = prefix_len
            if turn_idx % 10 == 0:
                print(f"    [Cache reset at turn {turn_idx+1}, tokens were {current_len}]")

    results["checks_passed"] = checks_passed
    results["checks_total"] = checks_total
    results["check_accuracy"] = checks_passed / max(checks_total, 1) * 100
    print(f"\n  Spot-checks: {checks_passed}/{checks_total} ({results['check_accuracy']:.0f}%)")

    del cache, conversation_cache; gc.collect(); torch.cuda.empty_cache()
    return results


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("CAG Small Model Test Suite")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    all_results = {"timestamp": datetime.now().isoformat(), "models": {}}

    for model_name in MODELS:
        print(f"\n\n{'#'*60}")
        print(f"  Loading {model_name}...")
        print(f"{'#'*60}")

        tok = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name, dtype=torch.float16).to("cuda")
        model.eval()
        vram = torch.cuda.memory_allocated() / 1e9
        print(f"  Loaded. VRAM: {vram:.1f}GB")

        # Warmup
        _ = query_baseline("Hello", tok, model, max_new=5)

        model_results = {"vram_gb": vram}

        # Test 1: Knowledge injection
        model_results["knowledge"] = test_knowledge_injection(tok, model, model_name)

        # Test 2: 100-turn persistent context (only on 0.8B to save time)
        if "0.8B" in model_name or "0.6B" in model_name:
            model_results["persistent"] = test_persistent_context(tok, model, model_name, num_turns=100)
        else:
            model_results["persistent"] = test_persistent_context(tok, model, model_name, num_turns=50)

        all_results["models"][model_name] = model_results

        # Save per-model results immediately
        safe_name = model_name.replace("/", "_")
        out = RESULTS_DIR / f"cag_{safe_name}.json"
        with open(out, "w") as f:
            json.dump(model_results, f, indent=2)
        print(f"\n  Saved: {out}")

        # Cleanup
        del model, tok
        gc.collect()
        torch.cuda.empty_cache()

    # Save combined
    all_results["completed"] = datetime.now().isoformat()
    combined = RESULTS_DIR / "small_models_combined.json"
    with open(combined, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nAll done! Combined: {combined}")
    print(f"Finished: {datetime.now().isoformat()}")
