"""Download final dataset discoveries: pet health, MedQuAD, BioASQ."""
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

# === PET HEALTH SYMPTOMS (2K) ===
print("=== Pet Health Symptoms ===")
try:
    ds = load_dataset("karenwky/pet-health-symptoms-dataset", split="train")
    rows = []
    for ex in ds:
        text = ex.get("text", "")
        condition = ex.get("condition", "")
        record_type = ex.get("record_type", "")
        if text and condition:
            if record_type == "Owner Observation":
                prompt = text
                response = f"Based on the symptoms described, this could be related to {condition}. I recommend consulting a veterinarian for proper diagnosis and treatment."
            else:
                prompt = f"What does this clinical finding suggest: {text}"
                response = f"This clinical finding is associated with {condition}. Further diagnostic workup may be needed to confirm."
            rows.append({"prompt": prompt, "response": response})
    save(rows, OUT / "pet_health_symptoms.jsonl")
except Exception as e:
    print(f"  Error: {e}")

# === MedQuAD (47K NIH Q&A) ===
print("=== MedQuAD ===")
try:
    ds = load_dataset("keivalya/MedQuad-MedicalQnADataset", split="train")
    rows = []
    for ex in ds:
        q = ex.get("Question", "") or ex.get("question", "")
        a = ex.get("Answer", "") or ex.get("answer", "")
        if q and a and len(a) > 30:
            rows.append({"prompt": q, "response": a})
    save(rows, OUT / "medical_medquad.jsonl")
except Exception as e:
    print(f"  Error: {e}")

# === BioASQ ===
print("=== BioASQ ===")
try:
    ds = load_dataset("kroshan/BioASQ", split="train")
    rows = []
    for ex in ds:
        q = ex.get("question", "") or ex.get("body", "")
        a = ex.get("ideal_answer", "") or ex.get("answer", "")
        if isinstance(a, list):
            a = a[0] if a else ""
        if q and a and len(str(a)) > 20:
            rows.append({"prompt": q, "response": str(a)})
    save(rows, OUT / "medical_bioasq.jsonl")
except Exception as e:
    print(f"  BioASQ error: {e}")
    # Try alternative
    try:
        ds = load_dataset("bigbio/bioasq_task_b", "bioasq_task_b_source", split="train", trust_remote_code=True)
        rows = []
        for ex in ds:
            q = ex.get("body", "") or ex.get("question", "")
            ideal = ex.get("ideal_answer", [])
            if isinstance(ideal, list) and ideal:
                a = ideal[0]
            else:
                a = str(ideal)
            if q and a and len(a) > 20:
                rows.append({"prompt": q, "response": a})
        save(rows, OUT / "medical_bioasq.jsonl")
    except Exception as e2:
        print(f"  BioASQ alt error: {e2}")

print("\n=== FINAL COUNTS ===")
import subprocess
r = subprocess.run(["wc", "-l"] + [str(p) for p in sorted(OUT.glob("*.jsonl"))], capture_output=True, text=True)
print(r.stdout)
