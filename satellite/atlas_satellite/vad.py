"""Voice Activity Detection with adaptive energy baseline.

The ReSpeaker 2-mic HAT has a high noise floor that saturates
webrtcvad at every aggressiveness level.  This module uses an
adaptive energy baseline: speech is detected when RMS rises
significantly above the rolling ambient average, and silence is
detected when it returns to baseline.  webrtcvad is used only as
a secondary signal when the noise floor is low enough.
"""

from __future__ import annotations

import collections
import logging
import struct

logger = logging.getLogger(__name__)

try:
    import webrtcvad
except ImportError:
    webrtcvad = None  # type: ignore[assignment]
    logger.warning("webrtcvad not installed — VAD disabled")


def _rms(audio_data: bytes) -> float:
    """Compute RMS energy of 16-bit PCM audio."""
    n = len(audio_data) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", audio_data[:n * 2])
    return (sum(s * s for s in samples) / n) ** 0.5


class VoiceActivityDetector:
    """Detects speech boundaries using adaptive energy thresholding.

    On noisy microphones (e.g. ReSpeaker HAT) where webrtcvad classifies
    everything as speech, we fall back to energy-based detection:

    1. Maintain a rolling baseline of ambient RMS (``_ambient_rms``).
    2. A frame is "speech" if its RMS exceeds ``_ambient_rms * speech_energy_ratio``.
    3. Silence detection uses a sliding window: if the fraction of
       non-speech frames in the last ``window_size`` frames exceeds
       ``silence_ratio``, trigger speech_end.
    4. The ambient baseline only updates during non-speech periods to
       avoid tracking the speaker's voice as "ambient".
    """

    def __init__(
        self,
        aggressiveness: int = 1,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        speech_threshold: int = 3,
        silence_threshold: int = 20,
        max_speech_frames: int = 333,
        energy_threshold: float = 80.0,
        window_size: int = 40,
        silence_ratio: float = 0.70,
        speech_energy_ratio: float = 2.2,
        ambient_alpha: float = 0.03,
        utterance_silence_threshold: int = 0,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self.speech_threshold = speech_threshold
        self.silence_threshold = silence_threshold
        self.max_speech_frames = max_speech_frames
        self.energy_threshold = energy_threshold
        self.window_size = window_size
        self.silence_ratio = silence_ratio
        self.speech_energy_ratio = speech_energy_ratio
        self.ambient_alpha = ambient_alpha

        # Extended listening: utterance_silence_threshold > silence_threshold
        # enables phrase_end detection at the short threshold and speech_end
        # at the long threshold.  When 0 (default), phrase detection is off
        # and the original silence_threshold drives speech_end directly.
        self.utterance_silence_threshold = utterance_silence_threshold

        self._vad = None
        if webrtcvad is not None:
            self._vad = webrtcvad.Vad(aggressiveness)

        self._speech_count = 0
        self._silence_count = 0
        self._in_speech = False
        self._speech_frame_total = 0
        self._phrase_fired = False  # True once phrase_end emitted for current pause
        self._window: collections.deque[bool] = collections.deque(maxlen=window_size)

        # Adaptive energy baseline
        self._ambient_rms: float = 0.0
        self._calibrated = False
        self._calibration_frames: list[float] = []
        self._calibration_target = 20  # ~600ms at 30ms/frame
        self._use_energy_mode = False
        self._frame_count = 0

    def _calibrate(self, rms: float) -> None:
        """Collect initial frames to establish ambient baseline."""
        self._calibration_frames.append(rms)
        if len(self._calibration_frames) >= self._calibration_target:
            self._ambient_rms = sum(self._calibration_frames) / len(self._calibration_frames)
            self._calibrated = True

            # If ambient RMS is high, webrtcvad is useless — use energy mode
            if self._ambient_rms > 500:
                self._use_energy_mode = True
                logger.info(
                    "High noise floor (ambient RMS=%.0f) — using energy-based VAD",
                    self._ambient_rms,
                )
            else:
                logger.info(
                    "Low noise floor (ambient RMS=%.0f) — using webrtcvad+energy",
                    self._ambient_rms,
                )

    def is_speech(self, audio_data: bytes) -> bool:
        """Check if a single frame contains speech."""
        rms = _rms(audio_data)
        self._frame_count += 1

        if not self._calibrated:
            self._calibrate(rms)
            return False

        if self._use_energy_mode:
            # Energy-only mode: speech if RMS significantly above ambient
            threshold = self._ambient_rms * self.speech_energy_ratio
            return rms > threshold

        # Hybrid mode: energy gate + webrtcvad
        if rms < self.energy_threshold:
            return False
        if self._vad is None:
            return rms > self._ambient_rms * self.speech_energy_ratio
        try:
            return self._vad.is_speech(audio_data, self.sample_rate)
        except Exception:
            return False

    def process(self, audio_data: bytes) -> str:
        """Process a frame and return state.

        Returns: 'speech_start', 'speech', 'phrase_end', 'speech_end',
        or 'silence'.

        When ``utterance_silence_threshold`` is set (> 0), a short pause
        (``silence_threshold`` frames) emits ``phrase_end`` and a long pause
        (``utterance_silence_threshold`` frames) emits ``speech_end``.
        When not set, behaviour is identical to the original: only
        ``speech_end`` fires at ``silence_threshold``.
        """
        rms = _rms(audio_data)
        frame_is_speech = self.is_speech(audio_data)
        self._window.append(frame_is_speech)

        # Update ambient baseline only when NOT in speech
        if self._calibrated and not self._in_speech and not frame_is_speech:
            self._ambient_rms = (
                self._ambient_rms * (1 - self.ambient_alpha)
                + rms * self.ambient_alpha
            )

        if frame_is_speech:
            self._speech_count += 1
            self._silence_count = 0
            self._phrase_fired = False  # speech resumed — reset phrase flag

            if not self._in_speech and self._speech_count >= self.speech_threshold:
                self._in_speech = True
                self._speech_frame_total = self._speech_count
                logger.debug(
                    "Speech start (RMS=%.0f, ambient=%.0f, ratio=%.1f)",
                    rms, self._ambient_rms, rms / max(self._ambient_rms, 1),
                )
                return "speech_start"
            elif self._in_speech:
                self._speech_frame_total += 1
                if self._speech_frame_total >= self.max_speech_frames:
                    logger.warning(
                        "Max speech duration reached (%d frames), forcing end",
                        self._speech_frame_total,
                    )
                    self._reset_state()
                    return "speech_end"
                return "speech"
        else:
            self._silence_count += 1
            self._speech_count = 0

            if self._in_speech:
                use_phrase = self.utterance_silence_threshold > 0

                # ── Long pause → speech_end ──
                long_thresh = (
                    self.utterance_silence_threshold if use_phrase
                    else self.silence_threshold
                )
                if self._silence_count >= long_thresh:
                    logger.debug(
                        "Speech end (consecutive silence, %d frames)",
                        self._silence_count,
                    )
                    self._reset_state()
                    return "speech_end"

                # ── Short pause → phrase_end (only in extended mode) ──
                if (
                    use_phrase
                    and not self._phrase_fired
                    and self._silence_count >= self.silence_threshold
                ):
                    self._phrase_fired = True
                    logger.debug(
                        "Phrase end (silence %d frames, threshold %d)",
                        self._silence_count, self.silence_threshold,
                    )
                    return "phrase_end"

                # Sliding window (uses long threshold for speech_end)
                if len(self._window) >= self.window_size:
                    n_silence = sum(1 for v in self._window if not v)
                    ratio = n_silence / len(self._window)
                    if ratio >= self.silence_ratio:
                        logger.debug(
                            "Speech end (window: %.0f%% silence in %d frames)",
                            100 * ratio, len(self._window),
                        )
                        self._reset_state()
                        return "speech_end"

        return "silence"

    def _reset_state(self) -> None:
        self._in_speech = False
        self._speech_frame_total = 0
        self._speech_count = 0
        self._silence_count = 0
        self._phrase_fired = False

    def reset(self) -> None:
        """Reset speech/silence counters and window (keeps calibration)."""
        self._reset_state()
        self._window.clear()

    @property
    def active(self) -> bool:
        return self._vad is not None or self._calibrated
