"""Frozen A04/A05 software resource gate: python3 -m scripts.qualify_mvp."""
from __future__ import annotations

import argparse
import gc
import json
import platform
import sqlite3
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import psutil

from cortex.db import _create_schema
from cortex.orchestrator.turn_store import TurnCapacityError, TurnStore, TurnStorePolicy
from cortex.pipeline.turns import TurnIdentity


def measure() -> dict:
    policy = TurnStorePolicy(max_active=1, prefetch=8)
    with tempfile.TemporaryDirectory(prefix="atlas-mvp-resource-") as directory:
        path = Path(directory) / "turns.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            _create_schema(conn)
            conn.commit()
            store = TurnStore(conn, policy)
            process = psutil.Process()
            baseline = 0
            started = time.monotonic()
            for index in range(10_000):
                store.accept(TurnIdentity(str(index), "resource-corpus", "anonymous"),
                             "A representative household question. " * 20, input_validated=True)
                if index == 99:
                    gc.collect()
                    baseline = process.memory_info().rss
            gc.collect()
            growth = max(0, process.memory_info().rss - baseline)
            assert growth <= 32 * 1024 * 1024, f"RSS growth exceeded 32 MiB: {growth}"
            assert store.status()["counts"] == {"accepted": 10_000}
            assert len(store.pending_ids()) == 8
            for index in ("0", "1"):
                store.claim(index, "anonymous", "router")
                store.mark_llm_pending(index, "anonymous", "router", 1)
            store.claim("0", "anonymous", "worker", stage="llm")
            try:
                store.claim("1", "anonymous", "worker", stage="llm")
            except TurnCapacityError:
                pass
            else:
                raise AssertionError("A second active inference was admitted")
            usage = store.status()["payload_bytes"]
        finally:
            conn.close()
        with sqlite3.connect(path) as reopened:
            assert reopened.execute("SELECT COUNT(*) FROM pending_turns").fetchone()[0] == 10_000
            assert reopened.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        return {
            "gate": "A04/A05", "result": "pass", "pending_turns": 10_000,
            "rss_growth_bytes_100_to_10000": growth, "rss_limit_bytes": 32 * 1024 * 1024,
            "payload_bytes": usage, "bounded_prefetch": 8, "active_limit_verified": 1,
            "policy": asdict(policy), "elapsed_seconds": time.monotonic() - started,
            "python": platform.python_version(), "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version, "temporary_storage_parent": str(path.parent.parent),
            "scope": "SQLite/software resource gate; not GPU memory or physical power-loss qualification",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(measure(), indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
