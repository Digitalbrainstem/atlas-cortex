"""Download Stack Exchange Q&A data filtered by domain sites.
The HuggingFaceH4/stack-exchange-preferences dataset has 10.7M rows
from the entire SE network, filterable by site."""
from __future__ import annotations
import json
from pathlib import Path
from datasets import load_dataset

OUT = Path("/workspace/atlas-distillation/data/lora_datasets_expanded")

def save(data, path):
    with open(path, "w") as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  Saved {len(data)} to {path.name}")

# Site-to-domain mapping
SITE_DOMAINS = {
    # Agriculture/Animals
    "gardening": "agriculture",
    "pets": "agriculture", 
    "sustainability": "agriculture",
    "cooking": "agriculture",  # food safety overlaps
    # Engineering  
    "electronics": "engineering",
    "engineering": "engineering",
    "arduino": "engineering",
    "robotics": "engineering",
    "3dprinting": "engineering",
    "dsp": "engineering",
    # Earth/Space
    "astronomy": "earth_space",
    "earthscience": "earth_space",
    "space": "earth_space",
    # Biology/Biomed
    "biology": "biology",
    "bioinformatics": "biology",
    # Physics/Chem
    "physics": "physics_chem",
    "chemistry": "physics_chem",
    # Social Science
    "history": "social_science",
    "psychology": "social_science",
    "cogsci": "social_science",
    "philosophy": "social_science",
    "politics": "social_science",
    "economics": "social_science",
    # Creative Arts
    "music": "creative_arts",
    "movies": "creative_arts",
    "writing": "creative_arts",
    "gamedev": "creative_arts",
    "scifi": "creative_arts",
    "literature": "creative_arts",
    # AI/ML
    "ai": "ai_ml",
    "datascience": "ai_ml",
    "stats": "ai_ml",
}

print("=== Loading Stack Exchange Preferences (streaming) ===")
print("This is 10.7M rows - we stream and filter...")

domain_rows = {d: [] for d in set(SITE_DOMAINS.values())}
count = 0
try:
    ds = load_dataset("HuggingFaceH4/stack-exchange-preferences", split="train", streaming=True)
    for ex in ds:
        count += 1
        if count % 100000 == 0:
            print(f"  Processed {count/1e6:.1f}M rows...")
            # Print current counts
            for d, rows in domain_rows.items():
                if rows:
                    print(f"    {d}: {len(rows)}")
        
        # Get the site name
        qid = ex.get("qid", "")
        # qid format is usually like "gardening_12345"
        site = qid.split("_")[0] if "_" in qid else ""
        
        if site not in SITE_DOMAINS:
            continue
        
        domain = SITE_DOMAINS[site]
        
        question = ex.get("question", "")
        # answers is a list, take the highest scored
        answers = ex.get("answers", [])
        if not answers or not question:
            continue
        
        # Sort by score, take best answer
        if isinstance(answers, list):
            best = max(answers, key=lambda a: a.get("pm_score", a.get("score", 0)) if isinstance(a, dict) else 0)
            answer_text = best.get("text", "") if isinstance(best, dict) else str(best)
        else:
            continue
            
        if len(answer_text) < 50:
            continue
            
        domain_rows[domain].append({
            "prompt": question[:2000],  # Cap length
            "response": answer_text[:3000],
            "source": f"stackexchange/{site}"
        })
        
        # Cap per domain at 20K to manage size
        all_full = all(len(rows) >= 20000 for rows in domain_rows.values() if rows)
        if all_full:
            break
            
except Exception as e:
    print(f"  Error: {e}")

# Save each domain
for domain, rows in domain_rows.items():
    if rows:
        save(rows, OUT / f"stackexchange_{domain}.jsonl")

# Also download StackMathQA
print("\n=== Loading StackMathQA (2M math Q&A) ===")
try:
    ds = load_dataset("math-ai/StackMathQA", split="train", streaming=True)
    math_rows = []
    for ex in ds:
        q = ex.get("question", "") or ex.get("Q", "")
        a = ex.get("answer", "") or ex.get("A", "")
        if q and a and len(a) > 30:
            math_rows.append({"prompt": q[:2000], "response": a[:3000]})
        if len(math_rows) >= 20000:
            break
    save(math_rows, OUT / "stackmath_qa.jsonl")
except Exception as e:
    print(f"  StackMathQA error: {e}")

# GPQA (PhD physics)
print("\n=== Loading GPQA ===")
try:
    ds = load_dataset("Idavidrein/gpqa", "gpqa_main", split="train", trust_remote_code=True)
    rows = []
    for ex in ds:
        q = ex.get("Question", "")
        a = ex.get("Correct Answer", "")
        if q and a:
            rows.append({"prompt": q, "response": a})
    save(rows, OUT / "gpqa_physics.jsonl")
except Exception as e:
    print(f"  GPQA error: {e}")

print("\n=== DONE ===")
import subprocess
r = subprocess.run(["wc", "-l"] + [str(p) for p in sorted(OUT.glob("stack*.jsonl")) ] + [str(p) for p in sorted(OUT.glob("gpqa*.jsonl"))], capture_output=True, text=True)
print(r.stdout)
