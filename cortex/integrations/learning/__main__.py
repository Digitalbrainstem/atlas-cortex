"""CLI entry point: python -m cortex.integrations.learning

Runs the nightly evolution job against the configured Cortex database.
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger(__name__)


async def _main() -> int:
    from cortex.db import get_db, init_db
    from cortex.integrations.learning import NightlyEvolution

    init_db()
    conn = get_db()
    evolution = NightlyEvolution(conn=conn)
    stats = await evolution.run()
    logger.info("Nightly evolution complete: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
