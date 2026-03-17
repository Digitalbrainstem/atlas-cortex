"""Download extra coding and math datasets from user-found sources."""
from __future__ import annotations
import json
from pathlib import Path
from datasets import load_dataset

OUT_DIR = Path("/workspace/atlas-distillation/data/lora_datasets_expanded")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def save_jsonl(data, path):
    with open(path, "w") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  Saved {len(data)} rows to {path.name}")

def download_coding():
    print("=== EXTRA CODING DATASETS ===")
    rows = []
    
    # Evol-Instruct-Code-80k
    print("Loading Evol-Instruct-Code-80k...")
    try:
        ds = load_dataset("nickrosh/Evol-Instruct-Code-80k-v1", split="train")
        for ex in ds:
            prompt = ex.get("instruction", "")
            response = ex.get("output", "")
            if prompt and response and len(response) > 50:
                rows.append({"prompt": prompt, "response": response})
        print(f"  Evol-Instruct-Code: {len(rows)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # CodeFeedback-Filtered-Instruction (157k)
    print("Loading CodeFeedback-Filtered-Instruction...")
    try:
        ds = load_dataset("m-a-p/CodeFeedback-Filtered-Instruction", split="train")
        count = 0
        for ex in ds:
            prompt = ex.get("query", "") or ex.get("instruction", "")
            response = ex.get("answer", "") or ex.get("response", "")
            if prompt and response and len(response) > 50:
                rows.append({"prompt": prompt, "response": response})
                count += 1
        print(f"  CodeFeedback: {count}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Dolphin-Coder
    print("Loading dolphin-coder...")
    try:
        ds = load_dataset("QuixiAI/dolphin-coder", split="train")
        count = 0
        for ex in ds:
            prompt = ex.get("instruction", "") or ex.get("question", "")
            inp = ex.get("input", "")
            response = ex.get("output", "") or ex.get("response", "")
            if prompt and response:
                if inp:
                    prompt = f"{prompt}\n\nInput: {inp}"
                rows.append({"prompt": prompt, "response": response})
                count += 1
        print(f"  dolphin-coder: {count}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Deduplicate
    seen = set()
    unique = []
    for r in rows:
        key = r["prompt"][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    save_jsonl(unique, OUT_DIR / "coding_extra.jsonl")

def download_math():
    print("\n=== EXTRA MATH DATASETS ===")
    rows = []
    
    # MATH dataset (Hendrycks) - 12.5K competition math problems
    print("Loading MATH (Hendrycks)...")
    try:
        ds = load_dataset("qwedsacf/competition_math", split="train")
        for ex in ds:
            problem = ex.get("problem", "")
            solution = ex.get("solution", "")
            if problem and solution:
                rows.append({"prompt": problem, "response": solution})
        print(f"  MATH: {len(rows)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Orca-Math (200K grade school word problems)
    print("Loading Orca-Math...")
    try:
        ds = load_dataset("microsoft/orca-math-word-problems-200k", split="train")
        count = 0
        for ex in ds:
            prompt = ex.get("question", "")
            response = ex.get("answer", "")
            if prompt and response:
                rows.append({"prompt": prompt, "response": response})
                count += 1
                if count >= 20000:  # Cap at 20K
                    break
        print(f"  Orca-Math: {count}")
    except Exception as e:
        print(f"  Error: {e}")
    
    seen = set()
    unique = []
    for r in rows:
        key = r["prompt"][:200].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    save_jsonl(unique, OUT_DIR / "math_extra.jsonl")

if __name__ == "__main__":
    download_coding()
    download_math()
    print("\n=== ALL EXTRA DONE ===")
    import subprocess
    result = subprocess.run(["wc", "-l"] + [str(p) for p in sorted(OUT_DIR.glob("*extra*.jsonl"))], capture_output=True, text=True)
    print(result.stdout)
