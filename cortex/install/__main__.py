"""Atlas Cortex installer entry point.

Usage::

    python -m cortex.install [--data-dir PATH] [--non-interactive]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m cortex.install",
        description="Atlas Cortex interactive installer",
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

    from cortex.install.wizard import run_installer
    try:
        run_installer(data_dir=data_dir, non_interactive=args.non_interactive)
    except KeyboardInterrupt:
        print("\n\nInstallation cancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
