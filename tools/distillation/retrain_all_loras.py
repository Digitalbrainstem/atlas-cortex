"""Retrain ALL 11 LoRAs on both Core 4B and Ultra 9B with expanded datasets.
Loads all available data per domain (teacher + free + expanded + SE).
Trains sequentially, saves after each, backs up incrementally."""
from __future__ import annotations
import json, torch, os, time, glob
from pathlib import Path
from datasets import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer, SFTConfig

DATA_DIR = Path("/workspace/atlas-distillation/data")
BACKUP_DIR = Path("/workspace/atlas-distillation/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

ATLAS_SYSTEM = (
    "You are Atlas, a knowledgeable and friendly personal AI assistant. "
    "You are warm, concise, and direct. You anticipate needs and offer helpful follow-ups. "
    "You are honest about uncertainty. You speak naturally, never with corporate filler phrases."
)

LORA_CONFIGS = {
    "expert":    {"r": 64, "alpha": 128, "epochs": 1},
    "advanced":  {"r": 32, "alpha": 64,  "epochs": 1},
    "competent": {"r": 16, "alpha": 32,  "epochs": 1},
}

# Map domain -> (tier, list of data file glob patterns)
DOMAINS = {
    "medicine": ("expert", [
        "specialist_teacher/teacher_medicine.jsonl",
        "lora_datasets/medical_o1.jsonl",
        "lora_datasets_expanded/medical_expanded.jsonl",
        "lora_datasets_expanded/medical_medquad.jsonl",
        "lora_datasets_expanded/squad_medical.jsonl",
    ]),
    "coding": ("expert", [
        "specialist_teacher/teacher_coding.jsonl",
        "lora_datasets/magicoder_oss.jsonl",
        "lora_datasets_expanded/coding_extra.jsonl",
    ]),
    "math_reasoning": ("expert", [
        "specialist_teacher/teacher_math_reasoning.jsonl",
        "lora_datasets/numina_math.jsonl",
        "lora_datasets_expanded/math_extra.jsonl",
        "lora_datasets_expanded/stackmath_qa.jsonl",
    ]),
    "ai_ml": ("expert", [
        "specialist_teacher/teacher_ai_ml.jsonl",
        "lora_datasets_expanded/ai_ml_expanded.jsonl",
        "lora_datasets_expanded/stackexchange_ai_ml.jsonl",
    ]),
    "physics_chemistry": ("advanced", [
        "specialist_teacher/teacher_physics_chemistry.jsonl",
        "lora_datasets/camel_physics.jsonl",
        "lora_datasets/camel_chemistry.jsonl",
        "lora_datasets_expanded/physics_chem_sciq_boost.jsonl",
        "lora_datasets_expanded/stackexchange_physics_chem.jsonl",
    ]),
    "biology_biomed": ("advanced", [
        "specialist_teacher/teacher_biology_biomed.jsonl",
        "lora_datasets/camel_biology.jsonl",
        "lora_datasets_expanded/biology_sciq_boost.jsonl",
        "lora_datasets_expanded/stackexchange_biology.jsonl",
    ]),
    "engineering": ("advanced", [
        "specialist_teacher/teacher_engineering.jsonl",
        "lora_datasets_expanded/engineering_expanded.jsonl",
        "lora_datasets_expanded/squad_engineering.jsonl",
        "lora_datasets_expanded/stackexchange_engineering.jsonl",
    ]),
    "earth_space": ("competent", [
        "specialist_teacher/teacher_earth_space.jsonl",
        "lora_datasets_expanded/earth_space_expanded.jsonl",
        "lora_datasets_expanded/squad_earth_space.jsonl",
        "lora_datasets_expanded/stackexchange_earth_space.jsonl",
    ]),
    "social_science": ("competent", [
        "specialist_teacher/teacher_social_science.jsonl",
        "lora_datasets_expanded/social_science_expanded.jsonl",
        "lora_datasets_expanded/squad_social_science.jsonl",
        "lora_datasets_expanded/stackexchange_social_science.jsonl",
    ]),
    "creative_arts": ("competent", [
        "specialist_teacher/teacher_creative_arts.jsonl",
        "lora_datasets_expanded/creative_arts_expanded.jsonl",
        "lora_datasets_expanded/squad_creative_arts.jsonl",
        "lora_datasets_expanded/stackexchange_creative_arts.jsonl",
    ]),
    "agriculture_animals": ("competent", [
        "specialist_teacher/teacher_agriculture_animals.jsonl",
        "specialist_teacher/teacher_agriculture_animals_v2.jsonl",
        "lora_datasets_expanded/agriculture_agronomy.jsonl",
        "lora_datasets_expanded/agriculture_farming.jsonl",
        "lora_datasets_expanded/squad_agriculture.jsonl",
        "lora_datasets_expanded/pet_health_symptoms.jsonl",
        "lora_datasets_expanded/stackexchange_agriculture.jsonl",
    ]),
    "cooking": ("competent", [
        "lora_datasets_expanded/stackexchange_cooking.jsonl",
    ]),
}

import random

def load_domain_data(file_patterns):
    rows = []
    for pattern in file_patterns:
        path = DATA_DIR / pattern
        if not path.exists():
            continue
        try:
            with open(path) as f:
                for line in f:
                    d = json.loads(line)
                    prompt = d.get("prompt") or d.get("question") or d.get("instruction") or d.get("input", "")
                    response = d.get("response") or d.get("answer") or d.get("output", "")
                    if prompt and response and len(response) > 20:
                        rows.append({
                            "messages": [
                                {"role": "system", "content": ATLAS_SYSTEM},
                                {"role": "user", "content": prompt[:2000]},
                                {"role": "assistant", "content": response[:4000]}
                            ]
                        })
        except Exception as e:
            print(f"    Warning loading {pattern}: {e}")
    return rows

def train_lora_on_model(model_dir, lora_out_base, model_name):
    print('=' * 60)
    print(f"RETRAINING ALL LoRAs ON {model_name}")
    print('=' * 60)
    
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir), quantization_config=bnb_config,
        device_map="auto", trust_remote_code=True,
        dtype=torch.bfloat16, attn_implementation="sdpa",
    )
    model = prepare_model_for_kbit_training(model)

    for domain, (tier, file_patterns) in DOMAINS.items():
        out_dir = lora_out_base / domain
        print(f"\n--- {domain} (tier: {tier}) ---")

        # Skip if already retrained (check marker)
        marker = BACKUP_DIR / f"retrain_{model_name}_{domain}.marker"
        if marker.exists():
            print(f"  SKIP - already retrained")
            continue

        rows = load_domain_data(file_patterns)
        if not rows:
            print(f"  SKIP - no data files found")
            continue

        random.seed(42)
        random.shuffle(rows)
        # Cap at 50K for training speed (diminishing returns past this)
        if len(rows) > 30000:
            rows = rows[:30000]

        ds = Dataset.from_list(rows)
        split = ds.train_test_split(test_size=min(500, int(len(rows)*0.05)), seed=42)
        print(f"  Data: {len(split["train"])} train, {len(split["test"])} eval")

        cfg = LORA_CONFIGS[tier]
        lora_config = LoraConfig(
            r=cfg["r"], lora_alpha=cfg["alpha"],
            target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
            lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        )
        sft_config = SFTConfig(
            output_dir=str(out_dir / "checkpoints"),
            num_train_epochs=cfg["epochs"],
            per_device_train_batch_size=4,
            per_device_eval_batch_size=4,
            gradient_accumulation_steps=2,
            learning_rate=1e-4, lr_scheduler_type="cosine",
            warmup_ratio=0.05, weight_decay=0.01, bf16=True,
            logging_steps=50, eval_strategy="epoch",
            save_strategy="epoch", save_total_limit=1,
            load_best_model_at_end=True, metric_for_best_model="eval_loss",
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            report_to="none", max_length=4096, packing=False,
        )

        trainer = SFTTrainer(
            model=model, args=sft_config,
            train_dataset=split["train"], eval_dataset=split["test"],
            processing_class=tokenizer, peft_config=lora_config,
        )

        start = time.time()
        result = trainer.train()
        elapsed = time.time() - start
        print(f"  Loss: {result.training_loss:.4f}, Time: {elapsed/60:.1f} min")

        out_dir.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(out_dir))
        tokenizer.save_pretrained(str(out_dir))

        # Mark as done
        marker.write_text(f"{domain} retrained at {time.ctime()}, loss={result.training_loss:.4f}, data={len(split["train"])}")
        print(f"  Saved + marked done")

        del trainer
        torch.cuda.empty_cache()

    print(f"\n=== ALL {model_name} LoRAs RETRAINED ===")

def main():
    import sys
    model_arg = sys.argv[1] if len(sys.argv) > 1 else "both"

    if model_arg in ("4b", "both"):
        train_lora_on_model(
            Path("/workspace/atlas-distillation/models/atlas-core-4b-merged"),
            Path("/workspace/atlas-distillation/models/loras-core-4b-v2"),
            "core4b"
        )

    if model_arg in ("9b", "both"):
        train_lora_on_model(
            Path("/workspace/atlas-distillation/models/atlas-ultra-9b-merged"),
            Path("/workspace/atlas-distillation/models/loras-ultra-9b-v2"),
            "ultra9b"
        )

    print("\n=== ALL RETRAINING COMPLETE ===")

if __name__ == "__main__":
    main()
