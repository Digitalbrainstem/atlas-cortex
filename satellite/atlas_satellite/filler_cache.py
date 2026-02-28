"""Filler phrase audio cache.

Stores pre-rendered TTS filler audio clips locally for instant playback
while the server processes a response (< 1ms latency vs network round-trip).
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FillerCache:
    """Manages locally cached filler phrase audio files."""

    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fillers: list[Path] = []
        self._refresh()

    def _refresh(self) -> None:
        self._fillers = sorted(self.cache_dir.glob("*.wav"))
        logger.debug("Filler cache: %d files in %s", len(self._fillers), self.cache_dir)

    def get_random(self) -> Optional[str]:
        """Get a random filler audio file path, or None if cache is empty."""
        if not self._fillers:
            return None
        return str(random.choice(self._fillers))

    def add(self, filler_id: str, audio_data: bytes) -> None:
        """Add a filler audio file to the cache."""
        path = self.cache_dir / f"{filler_id}.wav"
        path.write_bytes(audio_data)
        self._refresh()
        logger.info("Cached filler: %s (%d bytes)", filler_id, len(audio_data))

    def sync(self, fillers: list[dict]) -> None:
        """Sync cache with server-provided filler list.

        Adds new fillers, removes old ones not in the incoming list.
        Each dict must have 'id' (str) and 'audio' (bytes).
        """
        existing = {f.stem for f in self._fillers}
        incoming = {f["id"] for f in fillers}

        # Remove old
        for filler_id in existing - incoming:
            (self.cache_dir / f"{filler_id}.wav").unlink(missing_ok=True)
            logger.debug("Removed filler: %s", filler_id)

        # Add new
        for filler in fillers:
            if filler["id"] not in existing:
                self.add(filler["id"], filler["audio"])

        self._refresh()

    @property
    def count(self) -> int:
        return len(self._fillers)
