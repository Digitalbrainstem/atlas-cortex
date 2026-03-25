"""SIP/VoIP phone bridge — placeholder for future voice call support."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SIPBridge:
    """SIP/VoIP phone bridge (future implementation).

    This is a stub module for future SIP integration. When implemented,
    it will support:
    - Inbound/outbound voice calls via SIP trunk
    - DTMF navigation
    - Call recording
    - Voicemail transcription
    """

    async def setup(self, config: dict[str, Any]) -> bool:  # pragma: no cover
        logger.info("SIP bridge is not yet implemented")
        return False

    async def health(self) -> bool:
        return False
