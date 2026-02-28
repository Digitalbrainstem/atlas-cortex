"""Wake word detection — optional, supports openwakeword backend.

When disabled (default), the agent uses VAD-only mode: any detected
speech triggers audio streaming to the server.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """Detects wake words in audio frames."""

    def __init__(
        self,
        wake_word: str = "hey atlas",
        threshold: float = 0.5,
        model_path: str = "",
    ):
        self.wake_word = wake_word
        self.threshold = threshold
        self._backend = "none"
        self._detector = None
        self._init_backend(model_path)

    def _init_backend(self, model_path: str) -> None:
        """Try to initialize openwakeword, fall back to none."""
        try:
            from openwakeword.model import Model

            kwargs = {"inference_framework": "tflite"}
            if model_path:
                kwargs["wakeword_models"] = [model_path]
            self._detector = Model(**kwargs)
            self._backend = "openwakeword"
            logger.info("Wake word backend: openwakeword")
        except ImportError:
            logger.info(
                "openwakeword not installed — wake word detection disabled. "
                "Using VAD-only mode."
            )
        except Exception:
            logger.exception("Failed to initialize openwakeword")

    def process(self, audio_data: bytes) -> float:
        """Process an audio frame, return confidence (0.0-1.0).

        Returns > threshold when wake word is detected.
        """
        if self._backend == "openwakeword" and self._detector:
            try:
                import numpy as np

                samples = np.frombuffer(audio_data, dtype=np.int16)
                predictions = self._detector.predict(samples)
                return max(predictions.values()) if predictions else 0.0
            except Exception:
                return 0.0
        return 0.0

    def reset(self) -> None:
        if self._backend == "openwakeword" and self._detector:
            self._detector.reset()

    @property
    def available(self) -> bool:
        return self._backend != "none"
