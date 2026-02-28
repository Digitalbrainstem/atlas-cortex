"""Atlas Cortex service discovery entry point.

Usage::

    python -m cortex.discover [--non-interactive] [--data-dir PATH]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="python -m cortex.discover",
        description="Atlas Cortex service discovery",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=None,
        help="Override data directory (default: ./data or CORTEX_DATA_DIR env var)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run with all defaults — no prompts",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None

    from cortex.db import get_db, init_db, set_db_path

    if data_dir is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
        set_db_path(data_dir / "cortex.db")

    init_db()
    conn = get_db()

    # Load seed patterns on first run (idempotent — INSERT OR IGNORE).
    from cortex.discovery.wizard import load_seed_patterns, run_discovery_wizard

    load_seed_patterns(conn)

    try:
        run_discovery_wizard(conn, non_interactive=args.non_interactive)
    except KeyboardInterrupt:
        print("\n\nDiscovery cancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
