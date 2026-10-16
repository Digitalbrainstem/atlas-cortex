"""Wake word detection — optional, supports openwakeword backend.

VAD-only activation must be explicitly configured. A missing wake model
must not silently activate on arbitrary speech.

Supports both ONNX and TFLite inference frameworks, preferring ONNX
which is available on 64-bit ARM (aarch64) and x86_64.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """Detects wake words in audio frames."""

    def __init__(
        self,
        wake_word: str = "atlas",
        threshold: float = 0.5,
        model_path: str = "",
        *,
        initialize: bool = True,
    ):
        self.wake_word = wake_word
        self.threshold = threshold
        self._backend = "none"
        self._detector = None
        self._model_name = ""
        self.status = "loading"
        self.error = ""
        self._model_path = model_path
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        self._init_backend(self._model_path)

    def _init_backend(self, model_path: str) -> None:
        """Try to initialize openwakeword, fall back to none."""
        if model_path and not os.path.isfile(model_path):
            self.status = "model_missing"
            self.error = "Configured wake model is missing"
            logger.warning("%s: %s", self.error, model_path)
            return
        try:
            from openwakeword.model import Model

            model_paths = []
            if model_path and os.path.isfile(model_path):
                model_paths = [model_path]
                self._model_name = os.path.splitext(os.path.basename(model_path))[0]
                logger.info("Using custom wake word model: %s", model_path)

            # v0.4.0 passes **kwargs to AudioFeatures, so only use named params
            try:
                import onnxruntime  # noqa: F401
                self._detector = Model(
                    wakeword_model_paths=model_paths,
                    inference_framework="onnx",
                )
                fw = "onnx"
            except (ImportError, TypeError):
                try:
                    self._detector = Model(wakeword_model_paths=model_paths)
                    fw = "auto"
                except TypeError:
                    self._detector = Model()
                    fw = "auto"

            models = list(self._detector.models.keys())
            target = self._model_name or self.wake_word.strip().replace(" ", "_")
            if target not in models:
                self.status = "model_missing"
                self.error = f"No model matching configured wake word: {target}"
                logger.warning(self.error)
                return
            self._model_name = target
            self._backend = "openwakeword"
            self.status = "warming"
            logger.info("Wake word backend: openwakeword (%s), models: %s", fw, models)
        except ImportError:
            self.status = "unavailable"
            self.error = "openwakeword or its inference backend is not installed"
            logger.warning(self.error)
        except Exception:
            self.status = "error"
            self.error = "Wake model initialization failed"
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
                if not predictions:
                    return 0.0

                # Match against custom model name or configured wake word
                target = self._model_name or self.wake_word
                if target not in predictions:
                    return 0.0
                score = float(predictions[target])
                self.status = "ready"

                if score > 0.2:
                    logger.info("Wake word candidate: %.4f", score)
                return score
            except Exception:
                self.status = "error"
                self.error = "Wake model inference failed"
                self._backend = "none"
                logger.exception(self.error)
                return 0.0
        return 0.0

    def reset(self) -> None:
        if self._backend == "openwakeword" and self._detector:
            self._detector.reset()

    @property
    def available(self) -> bool:
        return self._backend != "none"
