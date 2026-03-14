#!/usr/bin/env python3
"""Generate training data from a teacher model via OpenAI-compatible or Ollama API.

Usage (vLLM on cloud):
    python3 tools/distillation/generate_teacher_data.py \
        --api-url http://localhost:8000/v1 \
        --api-type openai \
        --model Qwen/Qwen2.5-72B-Instruct-AWQ \
        --output data/distillation/teacher_general.jsonl \
        --prompts data/distillation/prompts.jsonl \
        --workers 8

Usage (Ollama local):
    python3 tools/distillation/generate_teacher_data.py \
        --api-url http://192.168.3.8:11434 \
        --api-type ollama \
        --model qwen3.5:35b-a3b \
        --output data/distillation/teacher_outputs.jsonl \
        --prompts data/distillation/prompts.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Atlas, a knowledgeable and helpful personal AI assistant. "
    "You have broad general knowledge equivalent to a well-educated college graduate. "
    "You give clear, concise, and accurate answers. When you don't know something, "
    "you say so honestly. You are warm but not overly chatty."
)


async def generate_one_openai(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    prompt: dict,
    semaphore: asyncio.Semaphore,
    think: bool = False,
    max_retries: int = 3,
) -> dict | None:
    """Send one prompt via OpenAI-compatible API (vLLM, etc.)."""
    async with semaphore:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if prompt.get("context"):
            messages.append({"role": "user", "content": prompt["context"]})
            messages.append({"role": "assistant", "content": prompt.get("context_response", "I understand.")})
        messages.append({"role": "user", "content": prompt["prompt"]})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 2048,
        }

        for attempt in range(max_retries):
            try:
                async with session.post(
                    f"{url}/chat/completions",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning("HTTP %d for %s (attempt %d): %s", resp.status, prompt.get("id"), attempt + 1, body[:200])
                        await asyncio.sleep(2 ** attempt)
                        continue
                    data = await resp.json()
                    choice = data["choices"][0]
                    response_text = choice["message"]["content"]
                    if not response_text.strip():
                        log.warning("Empty response for %s", prompt.get("id"))
                        continue

                    usage = data.get("usage", {})
                    return {
                        "id": prompt.get("id", "unknown"),
                        "category": prompt.get("category", "general"),
                        "system": SYSTEM_PROMPT,
                        "prompt": prompt["prompt"],
                        "context": prompt.get("context"),
                        "context_response": prompt.get("context_response"),
                        "response": response_text,
                        "model": model,
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "timestamp": time.time(),
                    }
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Error for %s: %s (attempt %d)", prompt.get("id"), e, attempt + 1)
                await asyncio.sleep(2 ** attempt)

        log.error("Failed all retries for %s", prompt.get("id"))
        return None


async def generate_one_ollama(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    prompt: dict,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> dict | None:
    """Send one prompt via Ollama API."""
    async with semaphore:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if prompt.get("context"):
            messages.append({"role": "user", "content": prompt["context"]})
            messages.append({"role": "assistant", "content": prompt.get("context_response", "I understand.")})
        messages.append({"role": "user", "content": prompt["prompt"]})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.7, "top_p": 0.9, "num_predict": 2048},
        }

        for attempt in range(max_retries):
            try:
                async with session.post(
                    f"{url}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=300)
                ) as resp:
                    if resp.status != 200:
                        log.warning("HTTP %d for %s (attempt %d)", resp.status, prompt.get("id"), attempt + 1)
                        await asyncio.sleep(2 ** attempt)
                        continue
                    data = await resp.json()
                    response_text = data.get("message", {}).get("content", "")
                    if not response_text.strip():
                        continue

                    return {
                        "id": prompt.get("id", "unknown"),
                        "category": prompt.get("category", "general"),
                        "system": SYSTEM_PROMPT,
                        "prompt": prompt["prompt"],
                        "context": prompt.get("context"),
                        "context_response": prompt.get("context_response"),
                        "response": response_text,
                        "model": model,
                        "prompt_tokens": data.get("prompt_eval_count", 0),
                        "completion_tokens": data.get("eval_count", 0),
                        "timestamp": time.time(),
                    }
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Error for %s: %s (attempt %d)", prompt.get("id"), e, attempt + 1)
                await asyncio.sleep(2 ** attempt)

        log.error("Failed all retries for %s", prompt.get("id"))
        return None


async def main(args: argparse.Namespace) -> None:
    prompts_path = Path(args.prompts)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prompts = []
    with open(prompts_path) as f:
        for line in f:
            if line.strip():
                prompts.append(json.loads(line))

    # Optional category filter
    if args.category:
        categories = set(args.category.split(","))
        prompts = [p for p in prompts if p.get("category") in categories]

    log.info("Loaded %d prompts from %s", len(prompts), prompts_path)

    # Resume support
    completed_ids = set()
    if output_path.exists():
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    completed_ids.add(json.loads(line).get("id"))
        log.info("Resuming: %d already completed", len(completed_ids))

    remaining = [p for p in prompts if p.get("id") not in completed_ids]
    log.info("Remaining: %d prompts to process", len(remaining))

    if not remaining:
        log.info("All prompts already processed!")
        return

    generate_fn = generate_one_openai if args.api_type == "openai" else generate_one_ollama
    semaphore = asyncio.Semaphore(args.workers)
    completed = 0
    failed = 0
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        batch_size = args.batch_size
        for i in range(0, len(remaining), batch_size):
            batch = remaining[i : i + batch_size]
            tasks = [
                generate_fn(session, args.api_url, args.model, p, semaphore)
                for p in batch
            ]
            results = await asyncio.gather(*tasks)

            with open(output_path, "a") as f:
                for result in results:
                    if result:
                        f.write(json.dumps(result) + "\n")
                        completed += 1
                    else:
                        failed += 1

            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            total_remaining = len(remaining) - completed - failed
            eta = total_remaining / rate if rate > 0 else float("inf")
            log.info(
                "Progress: %d/%d done, %d failed | %.1f prompts/min | ETA: %.0fm",
                completed + len(completed_ids), len(prompts), failed,
                rate * 60, eta / 60,
            )

    elapsed = time.time() - start_time
    log.info(
        "Complete! %d generated, %d failed in %.1f min (%.1f prompts/min)",
        completed, failed, elapsed / 60,
        completed / elapsed * 60 if elapsed > 0 else 0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate teacher training data")
    parser.add_argument("--api-url", default="http://localhost:8000/v1",
                        help="API base URL (OpenAI: http://host:port/v1, Ollama: http://host:11434)")
    parser.add_argument("--api-type", choices=["openai", "ollama"], default="openai",
                        help="API backend type")
    parser.add_argument("--model", required=True, help="Teacher model name/path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--prompts", default="data/distillation/prompts.jsonl")
    parser.add_argument("--category", default=None,
                        help="Comma-separated category filter (e.g., 'coding,reasoning')")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(main(args))
