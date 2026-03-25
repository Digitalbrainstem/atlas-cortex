"""IR blaster — infrared command transmission (future implementation)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class IRBlaster:
    """IR command transmitter (future implementation).

    When implemented, will support:
    - Learning IR codes from remotes
    - Transmitting IR commands to devices
    - Preset device profiles (TV, AC, etc.)
    """

    async def setup(self, config: dict[str, Any]) -> bool:  # pragma: no cover
        logger.info("IR blaster is not yet implemented")
        return False

    async def health(self) -> bool:
        return False
