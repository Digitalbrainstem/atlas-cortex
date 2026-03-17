"""Download Stack Exchange Q&A filtered by site from metadata URLs.
Includes cooking as a standalone domain."""
from __future__ import annotations
import json, re, html
from pathlib import Path
from datasets import load_dataset

OUT = Path("/workspace/atlas-distillation/data/lora_datasets_expanded")

def clean_html(text):
    """Strip HTML tags from SE content."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def save(data, path):
    with open(path, "w") as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  Saved {len(data)} to {path.name}")

SITE_DOMAINS = {
    "gardening": "agriculture",
    "pets": "agriculture",
    "sustainability": "agriculture",
    "cooking": "cooking",
    "electronics": "engineering",
    "engineering": "engineering",
    "arduino": "engineering",
    "robotics": "engineering",
    "3dprinting": "engineering",
    "dsp": "engineering",
    "astronomy": "earth_space",
    "earthscience": "earth_space",
    "space": "earth_space",
    "biology": "biology",
    "bioinformatics": "biology",
    "physics": "physics_chem",
    "chemistry": "physics_chem",
    "history": "social_science",
    "psychology": "social_science",
    "cogsci": "social_science",
    "philosophy": "social_science",
    "politics": "social_science",
    "economics": "social_science",
    "music": "creative_arts",
    "movies": "creative_arts",
    "writing": "creative_arts",
    "gamedev": "creative_arts",
    "scifi": "creative_arts",
    "literature": "creative_arts",
    "ai": "ai_ml",
    "datascience": "ai_ml",
    "stats": "ai_ml",
}

print("=== Loading Stack Exchange Preferences (streaming 10.7M) ===")
domain_rows = {d: [] for d in set(SITE_DOMAINS.values())}
domain_caps = {d: 100000 for d in domain_rows}
count = 0
skipped_meta = 0

try:
    ds = load_dataset("HuggingFaceH4/stack-exchange-preferences", split="train", streaming=True)
    for ex in ds:
        count += 1
        if count % 500000 == 0:
            print(f"  Scanned {count/1e6:.1f}M rows...")
            for d, rows in sorted(domain_rows.items()):
                if rows:
                    print(f"    {d}: {len(rows)}")
            # Check if all domains are full
            if all(len(domain_rows[d]) >= domain_caps[d] for d in domain_rows):
                print("  All domains full!")
                break

        meta = ex.get("metadata", [])
        if not meta or len(meta) < 2:
            skipped_meta += 1
            continue

        # Extract site from metadata URL: "https://gardening.stackexchange.com"
        site_url = meta[1] if isinstance(meta, list) else ""
        # Parse site name
        match = re.search(r"https?://([^.]+)\.(stackexchange|stackoverflow|askubuntu|superuser|serverfault)", site_url)
        if not match:
            continue
        site = match.group(1)
        
        # Skip meta sites
        if ".meta." in site_url or site.endswith("meta"):
            continue

        if site not in SITE_DOMAINS:
            continue

        domain = SITE_DOMAINS[site]
        if len(domain_rows[domain]) >= domain_caps[domain]:
            continue

        question = clean_html(ex.get("question", ""))
        answers = ex.get("answers", [])
        if not answers or not question or len(question) < 20:
            continue

        # Get best answer by score
        best = max(answers, key=lambda a: a.get("pm_score", 0) if isinstance(a, dict) else 0)
        answer_text = clean_html(best.get("text", "")) if isinstance(best, dict) else ""
        if len(answer_text) < 50:
            continue

        domain_rows[domain].append({
            "prompt": question[:2000],
            "response": answer_text[:4000],
            "source": f"stackexchange/{site}"
        })

except KeyboardInterrupt:
    print("  Interrupted, saving what we have...")
except Exception as e:
    print(f"  Error at row {count}: {e}")

print(f"\nScanned {count} rows total, skipped_meta={skipped_meta}")
for domain, rows in sorted(domain_rows.items()):
    if rows:
        save(rows, OUT / f"stackexchange_{domain}.jsonl")
    else:
        print(f"  {domain}: 0 rows")

print("\n=== FINAL ===")
import subprocess
r = subprocess.run(["wc", "-l"] + [str(p) for p in sorted(OUT.glob("stackexchange_*.jsonl"))], capture_output=True, text=True)
print(r.stdout)
