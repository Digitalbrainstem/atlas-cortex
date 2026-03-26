"""Atlas Satellite Agent entry point.

Usage:
    python -m atlas_satellite [--config CONFIG_PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .agent import SatelliteAgent
from .config import SatelliteConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas Satellite Agent")
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to config.json (default: auto-detect based on mode)",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Atlas server URL (overrides config)",
    )
    parser.add_argument(
        "--room",
        default=None,
        help="Room name (overrides config)",
    )
    parser.add_argument(
        "--led",
        default=None,
        choices=["none", "respeaker", "gpio"],
        help="LED type (overrides config)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    config_path = args.config
    if config_path is None:
        # Auto-detect: check dedicated path first, then shared
        for candidate in [
            Path("/opt/atlas-satellite/config.json"),
            Path.home() / ".atlas-satellite" / "config.json",
        ]:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path:
        config = SatelliteConfig.load(config_path)
        logging.getLogger(__name__).info("Loaded config from %s", config_path)
    else:
        config = SatelliteConfig()
        logging.getLogger(__name__).warning("No config found — using defaults")

    # Generate ID if empty
    if not config.satellite_id:
        config.satellite_id = config.generate_id()

    # CLI overrides
    if args.server:
        config.server_url = args.server
    if args.room:
        config.room = args.room
    if args.led:
        config.led_type = args.led

    # Create and run agent
    agent = SatelliteAgent(config)

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.new_event_loop()

    def _shutdown(sig: int) -> None:
        logging.getLogger(__name__).info("Received signal %d — shutting down", sig)
        loop.call_soon_threadsafe(loop.stop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    try:
        loop.run_until_complete(agent.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(agent.stop())
        loop.close()


if __name__ == "__main__":
    main()
