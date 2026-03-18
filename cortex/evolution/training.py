"""LoRA training pipeline — automated QLoRA fine-tuning for AMD ROCm GPUs.

Training runs as a subprocess (Python + PyTorch/PEFT/bitsandbytes-rocm)
to avoid importing torch into the main server process.  Progress is
tracked in the ``evolution_runs`` table.
"""

# Module ownership: Self-evolution LoRA training pipeline

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cortex.db import get_db

log = logging.getLogger(__name__)

_DEFAULT_CONFIG: dict[str, Any] = {
    "quantization": "4bit-nf4",
    "double_quant": True,
    "rank": 16,
    "lora_alpha": 32,
    "batch_size": 2,
    "gradient_checkpointing": True,
    "epochs": 3,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "max_seq_length": 2048,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"],
}

# Benchmarked on RX 7900 XT (20 GB VRAM) with QLoRA rank-16
_SAMPLES_PER_SECOND = 2.5


class LoRATrainer:
    """Automated QLoRA fine-tuning pipeline for AMD ROCm GPUs."""

    def __init__(self, output_dir: str = "") -> None:
        self.output_dir = output_dir or os.getenv(
            "LORA_OUTPUT_DIR",
            os.path.join(os.path.expanduser("~"), ".cortex", "lora"),
        )

    # ── Data preparation ─────────────────────────────────────────

    async def prepare_training_data(self, domain: str, limit: int = 1000) -> str:
        """Generate training data from conversation logs.

        Returns path to JSONL training file.
        """
        conn = get_db()
        rows = conn.execute(
            "SELECT message, response, resolved_area FROM interactions "
            "WHERE sentiment IN ('positive', 'neutral') "
            "AND resolved_area = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (domain, limit),
        ).fetchall()

        output_path = os.path.join(self.output_dir, "data", f"{domain}.jsonl")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w") as f:
            for row in rows:
                entry = {
                    "instruction": dict(row)["message"],
                    "output": dict(row)["response"],
                    "domain": domain,
                }
                f.write(json.dumps(entry) + "\n")

        log.info("Prepared %d training samples for '%s' → %s", len(rows), domain, output_path)
        return output_path

    # ── Training launch ──────────────────────────────────────────

    async def start_training(self, domain: str, config: dict[str, Any] | None = None) -> int:
        """Launch QLoRA training run.  Returns evolution_run_id.

        Training runs as a subprocess to avoid blocking the server.
        Writes progress to the ``evolution_runs`` table.
        """
        merged = {**_DEFAULT_CONFIG, **(config or {})}
        merged["domain"] = domain

        # Prepare data
        data_path = await self.prepare_training_data(domain, limit=merged.get("data_limit", 1000))

        # Record run
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO evolution_runs (run_type, status, config, started_at) "
            "VALUES ('training', 'running', ?, ?)",
            (json.dumps(merged), now),
        )
        conn.commit()
        run_id: int = cur.lastrowid  # type: ignore[assignment]

        adapter_dir = os.path.join(self.output_dir, "adapters", f"{domain}-run{run_id}")
        os.makedirs(adapter_dir, exist_ok=True)

        # Generate training script
        script = self._generate_training_script(data_path, adapter_dir, merged)
        script_path = os.path.join(adapter_dir, "train.py")
        with open(script_path, "w") as f:
            f.write(script)

        # Launch as subprocess (ROCm — NOT CUDA)
        env = {**os.environ, "HSA_OVERRIDE_GFX_VERSION": "11.0.0"}
        try:
            proc = subprocess.Popen(
                [sys.executable, script_path],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=adapter_dir,
            )
            # Store PID for status checks
            conn.execute(
                "UPDATE evolution_runs SET config = ? WHERE id = ?",
                (json.dumps({**merged, "_pid": proc.pid, "_adapter_dir": adapter_dir}), run_id),
            )
            conn.commit()
            log.info("Started training run %d (PID %d) for domain '%s'", run_id, proc.pid, domain)
        except Exception as exc:
            log.error("Failed to launch training: %s", exc)
            conn.execute(
                "UPDATE evolution_runs SET status = 'failed', results = ? WHERE id = ?",
                (json.dumps({"error": str(exc)}), run_id),
            )
            conn.commit()

        return run_id

    # ── Status & validation ──────────────────────────────────────

    async def check_training_status(self, run_id: int) -> dict[str, Any]:
        """Check if training is still running, get metrics."""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM evolution_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if row is None:
            return {"error": "Run not found"}

        run = dict(row)
        config = json.loads(run.get("config", "{}"))
        pid = config.get("_pid")
        status = run["status"]

        # Check if process is still alive
        if status == "running" and pid:
            try:
                os.kill(pid, 0)  # Signal 0 — just check existence
            except OSError:
                # Process finished — check for results
                adapter_dir = config.get("_adapter_dir", "")
                results_path = os.path.join(adapter_dir, "results.json") if adapter_dir else ""
                if results_path and os.path.exists(results_path):
                    with open(results_path) as f:
                        results = json.load(f)
                    conn.execute(
                        "UPDATE evolution_runs SET status = 'completed', "
                        "results = ?, completed_at = ? WHERE id = ?",
                        (json.dumps(results), datetime.now(timezone.utc).isoformat(), run_id),
                    )
                    status = "completed"
                else:
                    conn.execute(
                        "UPDATE evolution_runs SET status = 'failed', completed_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), run_id),
                    )
                    status = "failed"
                conn.commit()

        return {
            "run_id": run_id,
            "status": status,
            "domain": config.get("domain", ""),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "results": json.loads(run.get("results", "{}")),
        }

    async def validate_adapter(self, adapter_path: str) -> dict[str, Any]:
        """Run eval suite against new adapter.

        Returns ``{eval_score, safety_score, personality_score}``.
        """
        if not os.path.isdir(adapter_path):
            return {"eval_score": 0.0, "safety_score": 0.0, "personality_score": 0.0, "error": "Adapter not found"}

        # Check that required files exist
        required = ["adapter_config.json", "adapter_model.safetensors"]
        missing = [f for f in required if not os.path.exists(os.path.join(adapter_path, f))]
        if missing:
            return {
                "eval_score": 0.0,
                "safety_score": 0.0,
                "personality_score": 0.0,
                "error": f"Missing files: {', '.join(missing)}",
            }

        # In production this would load the adapter and run benchmarks.
        # For now return placeholder scores that indicate "needs evaluation".
        return {
            "eval_score": 0.0,
            "safety_score": 0.0,
            "personality_score": 0.0,
            "status": "pending_evaluation",
        }

    # ── Config & estimation ──────────────────────────────────────

    def get_training_config(self, domain: str) -> dict[str, Any]:
        """Get training hyperparameters for a domain."""
        config = dict(_DEFAULT_CONFIG)
        config["domain"] = domain
        config["output_dir"] = os.path.join(self.output_dir, "adapters", domain)
        return config

    def estimate_training_time(self, dataset_size: int) -> str:
        """Estimate training duration based on dataset size and hardware.

        Based on RX 7900 XT benchmarks with QLoRA rank-16.
        """
        epochs = _DEFAULT_CONFIG["epochs"]
        total_steps = dataset_size * epochs
        seconds = total_steps / _SAMPLES_PER_SECOND
        if seconds < 60:
            return f"{int(seconds)} seconds"
        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)} minutes"
        hours = minutes / 60
        return f"{hours:.1f} hours"

    # ── Private ──────────────────────────────────────────────────

    def _generate_training_script(
        self, data_path: str, output_dir: str, config: dict[str, Any],
    ) -> str:
        """Generate a standalone Python training script for QLoRA on ROCm."""
        target_modules = json.dumps(config.get("target_modules", _DEFAULT_CONFIG["target_modules"]))
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Auto-generated QLoRA training script for ROCm.\"\"\"
            import json, os, sys

            # ROCm environment
            os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", "11.0.0")

            try:
                import torch
                from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
                from transformers import (
                    AutoModelForCausalLM, AutoTokenizer,
                    BitsAndBytesConfig, TrainingArguments,
                )
                from trl import SFTTrainer
            except ImportError as exc:
                print(f"Missing dependency: {{exc}}", file=sys.stderr)
                sys.exit(1)

            # Config
            DATA_PATH = {data_path!r}
            OUTPUT_DIR = {output_dir!r}
            BASE_MODEL = os.environ.get("LORA_BASE_MODEL", "Qwen/Qwen2.5-7B")

            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant={config.get('double_quant', True)},
                bnb_4bit_compute_dtype=torch.bfloat16,
            )

            lora_config = LoraConfig(
                r={config.get('rank', 16)},
                lora_alpha={config.get('lora_alpha', 32)},
                target_modules={target_modules},
                lora_dropout=0.05,
                task_type="CAUSAL_LM",
            )

            tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            model = AutoModelForCausalLM.from_pretrained(
                BASE_MODEL, quantization_config=bnb_config, device_map="auto",
                trust_remote_code=True,
            )
            model = prepare_model_for_kbit_training(model)
            model = get_peft_model(model, lora_config)

            training_args = TrainingArguments(
                output_dir=OUTPUT_DIR,
                num_train_epochs={config.get('epochs', 3)},
                per_device_train_batch_size={config.get('batch_size', 2)},
                gradient_checkpointing={config.get('gradient_checkpointing', True)},
                learning_rate={config.get('learning_rate', 2e-4)},
                warmup_ratio={config.get('warmup_ratio', 0.03)},
                logging_steps=10,
                save_strategy="epoch",
                bf16=True,
                report_to="none",
            )

            # Load dataset
            from datasets import load_dataset
            dataset = load_dataset("json", data_files=DATA_PATH, split="train")

            trainer = SFTTrainer(
                model=model,
                tokenizer=tokenizer,
                train_dataset=dataset,
                args=training_args,
                max_seq_length={config.get('max_seq_length', 2048)},
            )

            trainer.train()
            trainer.save_model(OUTPUT_DIR)

            # Write results
            results = {{
                "status": "completed",
                "samples": len(dataset),
                "epochs": {config.get('epochs', 3)},
                "adapter_dir": OUTPUT_DIR,
            }}
            with open(os.path.join(OUTPUT_DIR, "results.json"), "w") as f:
                json.dump(results, f)

            print("Training complete:", OUTPUT_DIR)
        """)
