#!/usr/bin/env python3
"""Train a domain-specific LoRA adapter on a base Atlas model.

Unlike train_student.py which merges LoRA back into the model, this
produces a standalone LoRA adapter file (~50MB) that can be hot-swapped
at inference time.

Usage:
    python3 tools/distillation/train_lora.py \
        --base-model output/ultra/merged \
        --train-data data/distillation/teacher_coding.jsonl \
        --output output/loras/coding \
        --rank 64 --epochs 3
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_training_data(path: str, tokenizer) -> Dataset:
    """Load teacher JSONL and format as chat for SFT."""
    records = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            messages = [
                {"role": "system", "content": item.get("system", "You are a helpful assistant.")},
            ]
            if item.get("context"):
                messages.append({"role": "user", "content": item["context"]})
                messages.append({"role": "assistant", "content": item.get("context_response", "I understand.")})
            messages.append({"role": "user", "content": item["prompt"]})
            messages.append({"role": "assistant", "content": item["response"]})

            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            records.append({"text": text})

    log.info("Loaded %d training examples from %s", len(records), path)
    return Dataset.from_list(records)


def main(args: argparse.Namespace) -> None:
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Loading tokenizer: %s", args.base_model)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    log.info("Loading base model in 4-bit: %s", args.base_model)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.rank,
        lora_alpha=args.alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log.info("LoRA trainable params: %s", f"{trainable:,}")

    dataset = load_training_data(args.train_data, tokenizer)

    training_args = SFTConfig(
        output_dir=str(output_dir / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        weight_decay=0.01,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        fp16=False,
        bf16=True,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=1,
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        processing_class=tokenizer,
    )

    log.info("Training LoRA: %d examples, %d epochs, rank=%d", len(dataset), args.epochs, args.rank)
    trainer.train()

    # Save only the LoRA adapter (not the full model)
    log.info("Saving LoRA adapter to %s", output_dir)
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # Write metadata
    meta = {
        "base_model": args.base_model,
        "train_data": args.train_data,
        "rank": args.rank,
        "alpha": args.alpha,
        "epochs": args.epochs,
        "lr": args.lr,
        "examples": len(dataset),
    }
    (output_dir / "adapter_meta.json").write_text(json.dumps(meta, indent=2))

    log.info("Done! LoRA adapter saved to %s", output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train domain LoRA adapter")
    parser.add_argument("--base-model", required=True, help="Base model path or HF ID")
    parser.add_argument("--train-data", required=True, help="Domain teacher data JSONL")
    parser.add_argument("--output", required=True, help="Output directory for LoRA")
    parser.add_argument("--rank", type=int, default=64)
    parser.add_argument("--alpha", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    args = parser.parse_args()
    main(args)
