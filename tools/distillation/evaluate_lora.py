"""Evaluate LoRA quality with benchmark questions.
Tests each domain LoRA against curated questions to verify it actually learned.
Compares: base model (no LoRA) vs base + LoRA to measure improvement.
"""
from __future__ import annotations
import json, time
from pathlib import Path

# Domain-specific evaluation questions
# Each has a question + key facts the answer MUST contain
EVAL_QUESTIONS = {
    "medicine": [
        {
            "q": "What are the classic symptoms of appendicitis and how is it diagnosed?",
            "must_contain": ["right lower quadrant", "McBurney", "rebound tenderness", "CT scan", "WBC"],
        },
        {
            "q": "Explain the mechanism of action of metformin in type 2 diabetes.",
            "must_contain": ["hepatic glucose", "insulin sensitivity", "AMPK", "gluconeogenesis"],
        },
        {
            "q": "What is the difference between systolic and diastolic heart failure?",
            "must_contain": ["ejection fraction", "preserved", "reduced", "HFpEF", "HFrEF"],
        },
    ],
    "coding": [
        {
            "q": "Explain the difference between a stack and a queue with examples.",
            "must_contain": ["LIFO", "FIFO", "push", "pop", "enqueue", "dequeue"],
        },
        {
            "q": "What is a race condition and how do you prevent it?",
            "must_contain": ["mutex", "lock", "concurrent", "thread", "synchroniz"],
        },
        {
            "q": "Explain Big O notation with examples of O(1), O(n), and O(n²).",
            "must_contain": ["constant", "linear", "quadratic", "time complexity"],
        },
    ],
    "math_reasoning": [
        {
            "q": "Prove that the square root of 2 is irrational.",
            "must_contain": ["contradiction", "p/q", "even", "coprime"],
        },
        {
            "q": "Explain the fundamental theorem of calculus.",
            "must_contain": ["antiderivative", "integral", "derivative", "continuous"],
        },
    ],
    "ai_ml": [
        {
            "q": "Explain how backpropagation works in neural networks.",
            "must_contain": ["chain rule", "gradient", "loss", "weights", "forward pass"],
        },
        {
            "q": "What is the difference between a LoRA and full fine-tuning?",
            "must_contain": ["low-rank", "adapter", "frozen", "parameter", "efficient"],
        },
    ],
    "engineering": [
        {
            "q": "Explain Ohm's law and give a practical example.",
            "must_contain": ["voltage", "current", "resistance", "V=IR"],
        },
        {
            "q": "What is a PID controller and where is it used?",
            "must_contain": ["proportional", "integral", "derivative", "feedback", "error"],
        },
    ],
    "agriculture_animals": [
        {
            "q": "Can my dog eat grapes?",
            "must_contain": ["toxic", "kidney", "raisin", "dangerous", "vet"],
        },
        {
            "q": "How do I start composting at home?",
            "must_contain": ["carbon", "nitrogen", "green", "brown", "moisture"],
        },
        {
            "q": "What are signs of a urinary tract infection in cats?",
            "must_contain": ["litter box", "straining", "blood", "frequent", "vet"],
        },
    ],
    "cooking": [
        {
            "q": "What is the Maillard reaction and why does it matter in cooking?",
            "must_contain": ["amino acid", "sugar", "browning", "flavor", "temperature"],
        },
        {
            "q": "How do I properly season a cast iron skillet?",
            "must_contain": ["oil", "heat", "oven", "layer", "polymeriz"],
        },
    ],
    "earth_space": [
        {
            "q": "What causes tectonic plate movement?",
            "must_contain": ["convection", "mantle", "crust", "subduction", "ridge"],
        },
        {
            "q": "How does a star form from a nebula?",
            "must_contain": ["gravity", "collapse", "fusion", "hydrogen", "protostar"],
        },
    ],
    "physics_chemistry": [
        {
            "q": "Explain the photoelectric effect and its significance.",
            "must_contain": ["photon", "electron", "threshold", "Einstein", "quantum"],
        },
    ],
    "biology_biomed": [
        {
            "q": "Explain how CRISPR-Cas9 gene editing works.",
            "must_contain": ["guide RNA", "Cas9", "double-strand", "repair", "target"],
        },
    ],
    "social_science": [
        {
            "q": "What was the significance of the Renaissance?",
            "must_contain": ["humanism", "art", "science", "classical", "reform"],
        },
    ],
    "creative_arts": [
        {
            "q": "What is the three-act structure in screenwriting?",
            "must_contain": ["setup", "confrontation", "resolution", "climax", "turning point"],
        },
    ],
}


def evaluate_response(response: str, must_contain: list[str]) -> dict:
    """Check how many required concepts appear in the response."""
    response_lower = response.lower()
    hits = []
    misses = []
    for keyword in must_contain:
        if keyword.lower() in response_lower:
            hits.append(keyword)
        else:
            misses.append(keyword)
    return {
        "score": len(hits) / len(must_contain) if must_contain else 0,
        "hits": hits,
        "misses": misses,
        "response_length": len(response),
    }


def run_evaluation(model_path: str, lora_path: str | None, domain: str, device: str = "auto"):
    """Run evaluation questions through a model with optional LoRA.
    
    Usage:
        # Base model only
        run_evaluation("models/atlas-core-4b-merged", None, "medicine")
        
        # Base + LoRA
        run_evaluation("models/atlas-core-4b-merged", "models/loras-core-4b-v2/medicine", "medicine")
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if domain not in EVAL_QUESTIONS:
        print(f"No eval questions for domain: {domain}")
        return

    print(f"\n{'='*60}")
    print(f"Evaluating: {domain}")
    print(f"Model: {model_path}")
    print(f"LoRA: {lora_path or 'NONE (base only)'}")
    print(f"{'='*60}")

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        quantization_config=bnb_config,
        device_map=device,
        trust_remote_code=True,
    )

    if lora_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, lora_path)
        model = model.merge_and_unload()

    results = []
    for qa in EVAL_QUESTIONS[domain]:
        messages = [
            {"role": "system", "content": "You are a knowledgeable expert. Answer thoroughly."},
            {"role": "user", "content": qa["q"]},
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)

        start = time.time()
        with torch.no_grad():
            outputs = model.generate(
                **inputs, max_new_tokens=512, temperature=0.3, do_sample=True
            )
        elapsed = time.time() - start

        response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        eval_result = evaluate_response(response, qa["must_contain"])

        print(f"\n  Q: {qa['q'][:80]}")
        print(f"  Score: {eval_result['score']:.0%} ({len(eval_result['hits'])}/{len(qa['must_contain'])} concepts)")
        print(f"  Hits: {eval_result['hits']}")
        if eval_result["misses"]:
            print(f"  Misses: {eval_result['misses']}")
        print(f"  Response length: {eval_result['response_length']} chars, Time: {elapsed:.1f}s")

        results.append(eval_result)

    avg_score = sum(r["score"] for r in results) / len(results)
    print(f"\n  DOMAIN AVERAGE: {avg_score:.0%}")
    return {"domain": domain, "avg_score": avg_score, "details": results}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python evaluate_lora.py <model_path> <domain> [lora_path]")
        print("Domains:", list(EVAL_QUESTIONS.keys()))
        sys.exit(1)

    model_path = sys.argv[1]
    domain = sys.argv[2]
    lora_path = sys.argv[3] if len(sys.argv) > 3 else None
    run_evaluation(model_path, lora_path, domain)
