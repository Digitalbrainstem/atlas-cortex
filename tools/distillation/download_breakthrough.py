"""Download breakthrough agriculture, engineering, and STEM datasets."""
from __future__ import annotations
import json
from pathlib import Path
from datasets import load_dataset

OUT = Path("/workspace/atlas-distillation/data/lora_datasets_expanded")
OUT.mkdir(parents=True, exist_ok=True)

def save(data, path):
    with open(path, "w") as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  Saved {len(data)} to {path.name}")

# === AGRICULTURE: 45acp/agronomy (8.5K) ===
print("=== 45acp/agronomy ===")
try:
    ds = load_dataset("45acp/agronomy", split="train")
    rows = []
    for ex in ds:
        q = ex.get("question", "") or ex.get("instruction", "")
        a = ex.get("answer", "") or ex.get("output", "") or ex.get("response", "")
        if q and a and len(a) > 20:
            rows.append({"prompt": q, "response": a})
    save(rows, OUT / "agriculture_agronomy.jsonl")
except Exception as e:
    print(f"  Error: {e}")

# === AGRICULTURE: argilla/farming (1.7K) ===
print("=== argilla/farming ===")
try:
    ds = load_dataset("argilla/farming", split="train")
    rows = []
    for ex in ds:
        q = ex.get("instruction", "") or ex.get("input", "") or ex.get("question", "")
        a = ex.get("output", "") or ex.get("response", "") or ex.get("answer", "")
        if not q and not a:
            # Try different format
            text = ex.get("text", "")
            if text:
                rows.append({"prompt": "Explain farming practices", "response": text[:500]})
                continue
        if q and a:
            rows.append({"prompt": q, "response": a})
    save(rows, OUT / "agriculture_farming.jsonl")
except Exception as e:
    print(f"  Error: {e}")

# === CROP3 / AI4Agr (English only) ===
print("=== AI4Agr CROP3 ===")
try:
    # Try loading from HuggingFace
    ds = load_dataset("AI4Agr/CROP3", split="train")
    rows = []
    for ex in ds:
        q = ex.get("question", "") or ex.get("instruction", "") or ex.get("input", "")
        a = ex.get("answer", "") or ex.get("output", "") or ex.get("response", "")
        if q and a and len(a) > 10:
            # Filter for English
            if any(ord(c) > 0x4e00 for c in q[:50]):
                continue  # Skip Chinese
            rows.append({"prompt": q, "response": a})
    save(rows, OUT / "agriculture_crop3.jsonl")
except Exception as e:
    print(f"  CROP3 error: {e}")
    # Try alternative names
    for name in ["AI4Agr/CROP", "AI4Agr/crop3", "RenqiChen/The_Crop"]:
        try:
            ds = load_dataset(name, split="train")
            rows = []
            for ex in ds:
                q = ex.get("question", "") or ex.get("instruction", "")
                a = ex.get("answer", "") or ex.get("output", "")
                if q and a and not any(ord(c) > 0x4e00 for c in q[:50]):
                    rows.append({"prompt": q, "response": a})
            if rows:
                save(rows, OUT / "agriculture_crop3.jsonl")
                break
        except Exception as e2:
            print(f"  {name} error: {e2}")

# === stemdataset/STEM (filter Engineering + Science) ===
print("=== stemdataset/STEM ===")
try:
    ds = load_dataset("stemdataset/STEM", split="train")
    eng_rows, sci_rows = [], []
    for ex in ds:
        subject = ex.get("subject", "")
        problem = ex.get("problem", "")
        choices = ex.get("choices", [])
        answer_idx = ex.get("answer_idx", -1)
        # Skip image-dependent questions
        if ex.get("pic_prob", False) or ex.get("pic_choice", False):
            continue
        if not problem or not choices or answer_idx < 0 or answer_idx >= len(choices):
            continue
        answer = choices[answer_idx]
        opts = " | ".join(f"{chr(65+i)}) {c}" for i, c in enumerate(choices))
        prompt = f"{problem}\nOptions: {opts}"
        response = f"The answer is {chr(65+answer_idx)}) {answer}"
        row = {"prompt": prompt, "response": response}
        if subject == "Engineering":
            eng_rows.append(row)
        elif subject == "Science":
            sci_rows.append(row)
    if eng_rows:
        save(eng_rows, OUT / "engineering_stem.jsonl")
    if sci_rows:
        save(sci_rows, OUT / "science_stem_boost.jsonl")
except Exception as e:
    print(f"  STEM error: {e}")

print("\n=== DONE ===")
import subprocess
r = subprocess.run(["wc", "-l"] + [str(p) for p in sorted(OUT.glob("*.jsonl"))], capture_output=True, text=True)
print(r.stdout)
