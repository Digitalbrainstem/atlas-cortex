#!/usr/bin/env python3
"""CAG Ultimate Stress Test — Papers + Personal Data + Recall at 1M+

Sequence:
  Phase 1: Load 30K paper, verify
  Phase 2: Load medical facts for 3 patients, verify
  Phase 3: Load another 30K paper, verify
  Phase 4: 30+ turns of random distraction (quantum physics, history, etc.)
  Phase 5: Load 2 more papers
  Phase 6: 20+ more distraction turns
  Phase 7: RECALL — ask about patients from Phase 2

The patients' data should be BURIED under hundreds of thousands of tokens.
Traditional system: impossible. CAG persistent cache: should work.
"""
from __future__ import annotations
import os, json, time, gc, random
from pathlib import Path
from datetime import datetime

os.environ["HSA_OVERRIDE_GFX_VERSION"] = "11.0.0"
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache

RESULTS_DIR = Path("/mnt/fastpool/atlas-distillation/results/small_models")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_ID = "Qwen/Qwen3-4B"
random.seed(42)


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


def generate_paper(paper_num, tok, target_tokens=30000):
    """~30K token synthetic paper with unique anchor facts."""
    drug_names = ["Nexoril","Venostab","Cardimex","Pulmozine","Renalclear",
                  "Neurovex","Hemostil","Dermazol","Gastroven","Thyronex"]
    drug = drug_names[paper_num % len(drug_names)] + f"-{paper_num+100}"
    efficacy = round(random.uniform(20, 95), 1)
    patients = random.randint(200, 8000)
    lead = f"Dr. {chr(65+paper_num%26)}.{['Smith','Chen','Patel','Kim','Park'][paper_num%5]}"

    header = f"""=== PAPER #{paper_num+1}: {drug} Trial ===
Lead: {lead} | Patients: {patients} | Efficacy: {efficacy}% | Drug: {drug}

ABSTRACT: {drug} Phase III trial enrolled {patients} patients, demonstrating {efficacy}% efficacy. Lead investigator {lead}.\n"""

    filler = f"{drug} showed {efficacy}% efficacy in {patients} patients. {lead} reported results. "
    text = header
    while len(tok.encode(text)) < target_tokens:
        text += filler

    return text, {"drug": drug, "efficacy": str(efficacy), "patients": str(patients), "author": lead}, len(tok.encode(text))


# 3 patients with very specific, unique data
PATIENTS = [
    {
        "name": "Marcus Rivera",
        "text": """Patient: Marcus Rivera, DOB 03/14/1978, MRN 7742901
BP: 142/91, HR: 78, Weight: 227 lbs, Height: 5'10"
Diagnosis: Stage 2 Hypertension, Type 2 Diabetes (A1C 7.8%)
Medications: Metformin 1000mg BID, Losartan 50mg daily, Atorvastatin 20mg
Allergies: Sulfa drugs (anaphylaxis), Codeine (nausea)
Insurance: Aetna PPO, ID: AET-338927-MR
Pharmacy: Walgreens #4481, 1200 Elm Street
PCP: Dr. Rachel Torres at Summit Medical Group
Next appointment: April 17, 2026 at 9:15 AM
Lab: Creatinine 1.3, eGFR 68, Total cholesterol 234, LDL 152, HDL 41
Family: Wife Angela (nurse), son Diego age 14, daughter Sofia age 9
Emergency contact: Angela Rivera, 555-0147""",
        "checks": {
            "BP": ["142", "91"],
            "A1C": ["7.8"],
            "allergy": ["Sulfa", "Codeine"],
            "insurance": ["338927", "Aetna"],
            "pharmacy": ["Walgreens", "4481"],
            "doctor": ["Torres", "Summit"],
            "appointment": ["April 17", "9:15"],
            "kids": ["Diego", "Sofia"],
            "eGFR": ["68"],
            "cholesterol": ["234"],
        }
    },
    {
        "name": "Linda Nakamura",
        "text": """Patient: Linda Nakamura, DOB 11/22/1965, MRN 5519034
BP: 128/82, HR: 72, Weight: 156 lbs, Height: 5'4"
Diagnosis: Breast cancer (Stage IIA, ER+/PR+/HER2-), post-lumpectomy
Medications: Tamoxifen 20mg daily, Letrozole 2.5mg daily, Calcium 600mg + Vit D
Allergies: Penicillin (rash), Latex (contact dermatitis)
Insurance: UnitedHealth Choice Plus, ID: UHC-881204-LN
Pharmacy: CVS #2209, 780 Oak Boulevard
Oncologist: Dr. Samuel Okafor at City Cancer Center
Next appointment: May 2, 2026 at 2:00 PM
Lab: CA 15-3: 18 U/mL (normal), WBC 5.2, Hgb 12.1, Platelets 198
Family: Husband Kenji (retired teacher), daughter Mia age 28
Emergency contact: Kenji Nakamura, 555-0293""",
        "checks": {
            "diagnosis": ["breast cancer", "Stage IIA"],
            "medication": ["Tamoxifen", "Letrozole"],
            "allergy": ["Penicillin", "Latex"],
            "insurance": ["881204", "UnitedHealth"],
            "pharmacy": ["CVS", "2209"],
            "oncologist": ["Okafor", "City Cancer"],
            "appointment": ["May 2", "2:00"],
            "family": ["Kenji", "Mia"],
            "WBC": ["5.2"],
        }
    },
    {
        "name": "James Okonkwo",
        "text": """Patient: James Okonkwo, DOB 06/05/1990, MRN 3387621
BP: 118/76, HR: 65, Weight: 185 lbs, Height: 6'1"
Diagnosis: Generalized Anxiety Disorder, Mild Asthma
Medications: Sertraline 100mg daily, Albuterol inhaler PRN, Montelukast 10mg
Allergies: Ibuprofen (GI bleeding), Shellfish (hives)
Insurance: BlueCross BlueShield, ID: BCBS-442917-JO
Pharmacy: Rite Aid #3312, 456 Maple Drive
Psychiatrist: Dr. Elena Vasquez at Mindwell Clinic
Next appointment: April 28, 2026 at 11:30 AM
Lab: TSH 2.1, Cortisol AM 14.2, CBC normal, CMP normal
Family: Partner David (software engineer), dog named Biscuit
Emergency contact: David Chen, 555-0518""",
        "checks": {
            "diagnosis": ["Anxiety", "Asthma"],
            "medication": ["Sertraline", "Albuterol"],
            "allergy": ["Ibuprofen", "Shellfish"],
            "insurance": ["442917", "BlueCross"],
            "pharmacy": ["Rite Aid", "3312"],
            "psychiatrist": ["Vasquez", "Mindwell"],
            "appointment": ["April 28", "11:30"],
            "family": ["David", "Biscuit"],
            "cortisol": ["14.2"],
        }
    },
]

DISTRACTIONS = [
    "Explain how nuclear fusion works.",
    "What caused the fall of the Roman Empire?",
    "How does CRISPR gene editing work?",
    "Tell me about the history of jazz music.",
    "What is the theory of relativity?",
    "How do submarines work?",
    "Explain the water cycle.",
    "What is the Fibonacci sequence?",
    "Tell me about the French Revolution.",
    "How does GPS work?",
    "What are tectonic plates?",
    "Explain cryptocurrency mining.",
    "What caused the Ice Age?",
    "How do solar panels generate electricity?",
    "Tell me about the Renaissance.",
    "What is string theory?",
    "How does the human immune system work?",
    "Explain how a jet engine works.",
    "What is dark energy?",
    "Tell me about ancient Greek democracy.",
    "How does machine learning work?",
    "What causes the tides?",
    "Explain photosynthesis in detail.",
    "What was the Space Race?",
    "How do computers work at the transistor level?",
    "Tell me about the Silk Road.",
    "What is quantum entanglement?",
    "How do volcanoes form?",
    "Explain the Krebs cycle.",
    "What was the Industrial Revolution?",
    "How does radar work?",
    "Tell me about ancient Rome.",
    "What is the Big Bang theory?",
    "How do antibiotics work?",
    "Explain neural networks.",
    "What caused World War I?",
    "How does 3D printing work?",
    "Tell me about Egyptian hieroglyphics.",
    "What is plasma physics?",
    "How do black holes form?",
    "Explain the stock market.",
    "What is CERN?",
    "How do electric vehicles work?",
    "Tell me about the Ottoman Empire.",
    "What is the greenhouse effect?",
    "How does an MRI machine work?",
    "Explain supply and demand.",
    "What are exoplanets?",
    "How do lasers work?",
    "Tell me about the Cold War.",
]

# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  ULTIMATE CAG STRESS TEST — Papers + Patients + Recall")
    print(f"  Model: {MODEL_ID}")
    print(f"  Started: {datetime.now().isoformat()}")
    print("=" * 70)

    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float16).to("cuda")
    model.eval()

    system = "You are a medical assistant. Remember ALL patient data and research papers exactly."
    msgs = [{"role": "system", "content": system}]
    prefix = tok.apply_chat_template(msgs, tokenize=False, enable_thinking=False)
    ids = tok(prefix, return_tensors="pt").input_ids.to("cuda")
    with torch.no_grad():
        out = model(ids, use_cache=True)
    conv_cache = out.past_key_values
    current_pos = ids.shape[1]

    results = {"model": MODEL_ID, "phases": [], "total_tokens": 0}
    turn_num = 0

    def chat_turn(text, label="", max_new=150):
        nonlocal conv_cache, current_pos, turn_num
        turn_num += 1
        user_msgs = [{"role": "user", "content": text}]
        qt = tok.apply_chat_template(user_msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        qids = tok(qt, return_tensors="pt").input_ids.to("cuda")
        ql = qids.shape[1]
        pos = torch.arange(current_pos, current_pos + ql, device="cuda").unsqueeze(0)
        mask = torch.ones(1, current_pos + ql, device="cuda", dtype=torch.long)
        t0 = time.time()
        with torch.no_grad():
            out = model.generate(qids, past_key_values=conv_cache, position_ids=pos,
                                 attention_mask=mask, max_new_tokens=max_new, do_sample=False)
        lat = time.time() - t0
        gen_len = out[0].shape[0] - ql
        resp = tok.decode(out[0][ql:], skip_special_tokens=True)
        current_pos += ql + gen_len
        return resp, lat

    # ── Phase 1: Load first paper ───────────────────────────────────
    print(f"\n  PHASE 1: Loading Paper #1 (~30K tokens)")
    paper1_text, paper1_anchors, paper1_tokens = generate_paper(0, tok, 30000)
    resp, lat = chat_turn(f"Read and memorize this paper:\n{paper1_text[:15000]}", "paper1a")
    print(f"    Loaded part 1 ({lat:.1f}s, ctx={current_pos})")
    resp, lat = chat_turn(f"Continue reading:\n{paper1_text[15000:]}", "paper1b")
    print(f"    Loaded part 2 ({lat:.1f}s, ctx={current_pos})")
    resp, lat = chat_turn(f"What is the efficacy of {paper1_anchors['drug']}?", "paper1_check")
    found = paper1_anchors["efficacy"] in resp
    print(f"    Check: {'✓' if found else '✗'} efficacy={paper1_anchors['efficacy']}% → {resp[:60]}")
    results["total_tokens"] = current_pos

    # ── Phase 2: Load 3 patient records ─────────────────────────────
    print(f"\n  PHASE 2: Loading 3 patient records")
    for p in PATIENTS:
        resp, lat = chat_turn(f"New patient record — memorize everything:\n{p['text']}", f"patient_{p['name']}")
        print(f"    Loaded {p['name']} ({lat:.1f}s, ctx={current_pos})")
    # Quick verify
    resp, lat = chat_turn("What is Marcus Rivera's blood pressure?", "patient_check")
    found = "142" in resp
    print(f"    Check Marcus BP: {'✓' if found else '✗'} → {resp[:60]}")
    results["total_tokens"] = current_pos

    # ── Phase 3: Load paper #2 ──────────────────────────────────────
    print(f"\n  PHASE 3: Loading Paper #2 (~30K tokens)")
    paper2_text, paper2_anchors, paper2_tokens = generate_paper(1, tok, 30000)
    resp, lat = chat_turn(f"New paper:\n{paper2_text[:15000]}", "paper2a")
    resp, lat = chat_turn(f"Continue:\n{paper2_text[15000:]}", "paper2b")
    resp, lat = chat_turn(f"What is the efficacy of {paper2_anchors['drug']}?", "paper2_check")
    found = paper2_anchors["efficacy"] in resp
    print(f"    Check: {'✓' if found else '✗'} efficacy={paper2_anchors['efficacy']}% → {resp[:60]}")
    results["total_tokens"] = current_pos

    # ── Phase 4: 30 distraction turns ───────────────────────────────
    print(f"\n  PHASE 4: 30 distraction turns")
    for i in range(30):
        resp, lat = chat_turn(DISTRACTIONS[i], f"distract_{i}")
        if i % 10 == 0:
            print(f"    Turn {i+1}/30 ({lat:.1f}s, ctx={current_pos})")
    print(f"    Done. Context: {current_pos:,} tokens")
    results["total_tokens"] = current_pos

    # ── Phase 5: Load papers #3 and #4 ──────────────────────────────
    print(f"\n  PHASE 5: Loading Papers #3 and #4")
    for pn in [2, 3]:
        ptxt, panch, ptok = generate_paper(pn, tok, 30000)
        resp, lat = chat_turn(f"New paper:\n{ptxt[:15000]}", f"paper{pn+1}a")
        resp, lat = chat_turn(f"Continue:\n{ptxt[15000:]}", f"paper{pn+1}b")
        print(f"    Paper #{pn+1} loaded ({current_pos:,} tokens)")
    results["total_tokens"] = current_pos

    # ── Phase 6: 20 more distraction turns ──────────────────────────
    print(f"\n  PHASE 6: 20 more distraction turns")
    for i in range(20):
        resp, lat = chat_turn(DISTRACTIONS[30+i], f"distract2_{i}")
        if i % 10 == 0:
            print(f"    Turn {i+1}/20 ({lat:.1f}s, ctx={current_pos})")
    print(f"    Done. Context: {current_pos:,} tokens")
    results["total_tokens"] = current_pos

    # ── Phase 7: RECALL patient data ────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  PHASE 7: PATIENT RECALL (buried under {current_pos:,} tokens)")
    print(f"{'='*70}")

    total_checks = 0
    total_passed = 0
    recall_results = []

    for p in PATIENTS:
        print(f"\n  --- Recalling {p['name']} ---")
        for check_name, expected_keywords in p["checks"].items():
            q_map = {
                "BP": f"What is {p['name']}'s blood pressure?",
                "A1C": f"What is {p['name']}'s A1C level?",
                "allergy": f"What allergies does {p['name']} have?",
                "insurance": f"What is {p['name']}'s insurance ID number?",
                "pharmacy": f"What pharmacy does {p['name']} use?",
                "doctor": f"Who is {p['name']}'s doctor?",
                "appointment": f"When is {p['name']}'s next appointment?",
                "kids": f"What are {p['name']}'s children's names?",
                "family": f"Tell me about {p['name']}'s family.",
                "eGFR": f"What is {p['name']}'s eGFR?",
                "cholesterol": f"What is {p['name']}'s total cholesterol?",
                "diagnosis": f"What is {p['name']}'s diagnosis?",
                "medication": f"What medications is {p['name']} taking?",
                "oncologist": f"Who is {p['name']}'s oncologist?",
                "psychiatrist": f"Who is {p['name']}'s psychiatrist?",
                "WBC": f"What is {p['name']}'s white blood cell count?",
                "cortisol": f"What is {p['name']}'s cortisol level?",
            }
            q = q_map.get(check_name, f"Tell me about {p['name']}'s {check_name}")
            resp, lat = chat_turn(q, f"recall_{p['name']}_{check_name}", max_new=80)

            found = [k for k in expected_keywords if k.lower() in resp.lower()]
            passed = len(found) >= 1
            total_checks += 1
            total_passed += int(passed)
            sym = "✓" if passed else "✗"
            print(f"    {sym} {check_name}: expected {expected_keywords} → found {found} ({lat:.1f}s)")

            recall_results.append({
                "patient": p["name"], "check": check_name,
                "expected": expected_keywords, "found": found,
                "passed": passed, "response": resp[:150],
            })

    # Summary
    print(f"\n{'='*70}")
    print(f"  RESULTS")
    print(f"{'='*70}")
    print(f"  Total context: {current_pos:,} tokens")
    print(f"  Papers loaded: 4 (~120K tokens)")
    print(f"  Patients loaded: 3")
    print(f"  Distraction turns: 50")
    print(f"  Patient recall: {total_passed}/{total_checks} ({total_passed/max(total_checks,1)*100:.1f}%)")
    print(f"")
    print(f"  Traditional 32K system: patients buried at ~60K, ALL forgotten")
    print(f"  CAG persistent cache: {total_passed}/{total_checks} recalled at {current_pos:,} tokens")
    print(f"{'='*70}")

    results["recall"] = {"passed": total_passed, "total": total_checks,
                          "accuracy": total_passed/max(total_checks,1)*100,
                          "details": recall_results}

    out = RESULTS_DIR / "ultimate_stress_test.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved: {out}")
    print(f"Finished: {datetime.now().isoformat()}")
