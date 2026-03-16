#!/usr/bin/env python3
"""QLoRA distillation: train a student model on teacher-generated data.

This performs Supervised Fine-Tuning (SFT) using QLoRA — the student learns
to reproduce teacher responses. The LoRA is then merged back into the base
model to produce a fully distilled model.

Usage:
    python3 tools/distillation/train_student.py \
        --student Qwen/Qwen2.5-14B-Instruct \
        --teacher-data data/distillation/teacher_general.jsonl \
        --output output/ultra \
        --epochs 3 --lr 2e-4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def load_teacher_data(path: str, tokenizer) -> Dataset:
    """Load teacher JSONL and format as chat messages."""
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

    log.info("Loading tokenizer: %s", args.student)
    tokenizer = AutoTokenizer.from_pretrained(args.student, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    log.info("Loading student model in 4-bit: %s", args.student)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.student,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    log.info("Trainable params: %s / %s (%.2f%%)", f"{trainable:,}", f"{total:,}", 100 * trainable / total)

    dataset = load_teacher_data(args.teacher_data, tokenizer)

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
        save_total_limit=2,
        max_length=args.max_seq_length,
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

    log.info("Starting training: %d examples, %d epochs", len(dataset), args.epochs)
    trainer.train()

    log.info("Saving LoRA adapter to %s", output_dir / "lora")
    model.save_pretrained(str(output_dir / "lora"))
    tokenizer.save_pretrained(str(output_dir / "lora"))

    if args.merge:
        log.info("Merging LoRA into base model...")
        merged = model.merge_and_unload()
        merge_dir = output_dir / "merged"
        log.info("Saving merged model to %s", merge_dir)
        merged.save_pretrained(str(merge_dir), safe_serialization=True)
        tokenizer.save_pretrained(str(merge_dir))

    log.info("Done! Output at %s", output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QLoRA student distillation")
    parser.add_argument("--student", required=True, help="HuggingFace model ID for student")
    parser.add_argument("--teacher-data", required=True, help="Teacher JSONL file")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--lora-rank", type=int, default=64)
    parser.add_argument("--lora-alpha", type=int, default=128)
    parser.add_argument("--merge", action="store_true", default=True,
                        help="Merge LoRA into base model after training")
    parser.add_argument("--no-merge", action="store_false", dest="merge")
    args = parser.parse_args()
    main(args)
