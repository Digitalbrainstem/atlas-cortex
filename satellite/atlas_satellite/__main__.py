"""Atlas Satellite Agent entry point.

Usage:
    python -m atlas_satellite [--config CONFIG_PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from .agent import SatelliteAgent
from .config import SatelliteConfig
from .diagnostics import main as diagnostics_main
from .setup_server import SetupServer


async def _run(agent: SatelliteAgent, setup_port: int) -> None:
    """Run the setup server and agent until a signal or agent exit."""
    setup = SetupServer(port=setup_port) if setup_port > 0 else None
    if setup:
        await setup.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    agent_task = asyncio.create_task(agent.start(), name="satellite-agent")
    stop_task = asyncio.create_task(stop_event.wait(), name="satellite-stop-signal")
    try:
        done, _ = await asyncio.wait(
            (agent_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and not agent_task.done():
            agent_task.cancel()
        await asyncio.gather(agent_task, return_exceptions=False)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        if setup:
            await setup.stop()


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
        "--setup-port",
        type=int,
        default=0,
        help="Port for the optional WiFi setup server (disabled by default)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run installation diagnostics instead of starting the agent",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()
    if args.diagnose:
        diagnostic_args = ["atlas-satellite-diagnose"]
        if args.config:
            diagnostic_args += ["--config", args.config]
        import sys

        sys.argv = diagnostic_args
        diagnostics_main()
        return

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
            Path("/etc/atlas-satellite/config.json"),
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
    agent = SatelliteAgent(config, config_path=config_path)
    try:
        asyncio.run(_run(agent, args.setup_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
