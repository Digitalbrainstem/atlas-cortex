"""Data quality filter for LoRA training datasets.
Filters out low-quality rows that hurt training:
- Short/empty responses
- MCQ-format answers without explanations
- Off-topic content from keyword filters
- Duplicate or near-duplicate content
"""
from __future__ import annotations
import json, re, hashlib
from pathlib import Path
from collections import Counter


def quality_score(row: dict) -> tuple[float, list[str]]:
    """Score a row 0-1 with reasons for low scores."""
    prompt = row.get("prompt", "") or row.get("question", "") or row.get("instruction", "")
    response = row.get("response", "") or row.get("answer", "") or row.get("output", "")
    issues = []
    score = 1.0

    # Length checks
    if len(response) < 30:
        issues.append("response_too_short")
        score -= 0.5
    elif len(response) < 100:
        issues.append("response_short")
        score -= 0.2

    if len(prompt) < 10:
        issues.append("prompt_too_short")
        score -= 0.3

    # MCQ format without explanation (the medicine problem)
    mcq_pattern = re.compile(
        r"(The correct answer is [A-D]|Answer:\s*[A-D]\b|Option [A-D] is correct)",
        re.IGNORECASE,
    )
    if mcq_pattern.search(response[:200]):
        # Check if there's a real explanation after the answer
        explanation = re.split(r"(?:Explanation|Because|This is because|Rationale)[:\s]", response, maxsplit=1)
        if len(explanation) < 2 or len(explanation[1]) < 100:
            issues.append("mcq_no_explanation")
            score -= 0.4

    # Trivial factoid answers (SQuAD problem)
    if len(response) < 50 and not any(c in response for c in ".!?"):
        issues.append("trivial_factoid")
        score -= 0.3

    # Off-topic indicators from keyword filtering
    off_topic_signals = [
        r"As an AI language model",
        r"I cannot provide medical advice",
        r"I'm sorry, but I",
        r"I don't have personal",
    ]
    for signal in off_topic_signals:
        if re.search(signal, response, re.IGNORECASE):
            issues.append("ai_refusal")
            score -= 0.3
            break

    # HTML artifacts
    if "<p>" in response or "<div>" in response or "&amp;" in response:
        issues.append("html_artifacts")
        score -= 0.1

    return max(0, min(1, score)), issues


def filter_dataset(
    input_path: Path,
    output_path: Path,
    min_score: float = 0.5,
    report: bool = True,
) -> dict:
    """Filter a JSONL dataset by quality score.

    Returns stats dict with counts and issue breakdown.
    """
    rows_in = 0
    rows_out = 0
    issue_counts: Counter = Counter()
    seen_hashes: set = set()
    kept = []

    with open(input_path) as f:
        for line in f:
            rows_in += 1
            row = json.loads(line)

            # Dedup by prompt hash
            prompt = row.get("prompt", "") or row.get("question", "")
            h = hashlib.md5(prompt[:300].lower().strip().encode()).hexdigest()
            if h in seen_hashes:
                issue_counts["duplicate"] += 1
                continue
            seen_hashes.add(h)

            score, issues = quality_score(row)
            for issue in issues:
                issue_counts[issue] += 1

            if score >= min_score:
                kept.append(row)
                rows_out += 1

    # Write filtered output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats = {
        "input_rows": rows_in,
        "output_rows": rows_out,
        "removed": rows_in - rows_out,
        "removal_rate": f"{(rows_in - rows_out) / rows_in * 100:.1f}%",
        "issues": dict(issue_counts.most_common()),
    }

    if report:
        print(f"  {input_path.name}: {rows_in} → {rows_out} ({stats['removal_rate']} removed)")
        for issue, count in issue_counts.most_common(5):
            print(f"    {issue}: {count}")

    return stats


def reformat_mcq_to_qa(row: dict) -> dict | None:
    """Convert MCQ format to natural Q&A.

    Input:  "Question + Options: A) X B) Y C) Z D) W"
            → "The correct answer is B. Explanation: ..."
    Output: "Question"
            → "Explanation text without the MCQ scaffolding"
    """
    prompt = row.get("prompt", "")
    response = row.get("response", "")

    # Strip options from prompt
    clean_prompt = re.sub(
        r"\n?Options?:\s*[A-D]\).*$", "", prompt, flags=re.DOTALL
    ).strip()

    # Extract explanation from response
    explanation = ""
    for splitter in [
        r"Explanation:\s*",
        r"Because\s+",
        r"This is because\s+",
        r"Rationale:\s*",
        r"\.\s+",  # After "The correct answer is X."
    ]:
        parts = re.split(splitter, response, maxsplit=1)
        if len(parts) >= 2 and len(parts[1]) > 50:
            explanation = parts[1].strip()
            break

    if not explanation or len(explanation) < 50:
        return None

    return {"prompt": clean_prompt, "response": explanation}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python data_quality_filter.py <input.jsonl> [output.jsonl] [min_score]")
        print("       python data_quality_filter.py --scan-dir <directory>")
        sys.exit(1)

    if sys.argv[1] == "--scan-dir":
        directory = Path(sys.argv[2])
        print(f"Scanning {directory}...")
        for f in sorted(directory.glob("*.jsonl")):
            filter_dataset(f, f.with_suffix(".filtered.jsonl"), min_score=0.5)
    else:
        input_path = Path(sys.argv[1])
        output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path.with_suffix(".filtered.jsonl")
        min_score = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
        filter_dataset(input_path, output_path, min_score=min_score)
