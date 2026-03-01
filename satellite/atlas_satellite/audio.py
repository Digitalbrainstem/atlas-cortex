"""Audio capture and playback via ALSA (pyalsaaudio).

Lightweight audio I/O designed for resource-constrained devices like
Raspberry Pi Zero 2 W. Uses direct ALSA bindings — no PortAudio or numpy.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import alsaaudio
except ImportError:
    alsaaudio = None  # type: ignore[assignment]
    logger.warning("pyalsaaudio not installed — audio disabled")


class AudioCapture:
    """Captures audio from an ALSA device.

    Some codecs (e.g. WM8960 on ReSpeaker) only support stereo capture.
    When the hardware requires stereo but the pipeline needs mono, we
    capture in stereo and downmix by averaging the two channels.
    """

    def __init__(
        self,
        device: str = "default",
        sample_rate: int = 16000,
        channels: int = 1,
        period_size: int = 480,
        mic_gain: float = 0.8,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.period_size = period_size
        self.mic_gain = mic_gain
        self._pcm: Optional[alsaaudio.PCM] = None
        self._running = False
        self._hw_channels = channels  # actual channels opened on hardware

    def start(self) -> None:
        if alsaaudio is None:
            raise RuntimeError("pyalsaaudio not installed")
        self._hw_channels = self.channels
        try:
            self._open_pcm(self._hw_channels)
            # Verify actual channel count by reading a test frame
            length, data = self._pcm.read()
            expected_mono = self.period_size * 2
            if len(data) > expected_mono and self.channels == 1:
                logger.info("Hardware returns stereo despite mono request, enabling downmix")
                self._hw_channels = 2
        except alsaaudio.ALSAAudioError:
            if self.channels == 1:
                logger.info("Mono capture failed, trying stereo with downmix")
                self._hw_channels = 2
                self._open_pcm(self._hw_channels)
            else:
                raise
        self._running = True
        logger.info(
            "Audio capture started: device=%s rate=%d hw_ch=%d out_ch=%d period=%d",
            self.device, self.sample_rate, self._hw_channels,
            self.channels, self.period_size,
        )

    def _open_pcm(self, channels: int) -> None:
        if self._pcm:
            self._pcm.close()
        self._pcm = alsaaudio.PCM(
            alsaaudio.PCM_CAPTURE,
            alsaaudio.PCM_NORMAL,
            device=self.device,
        )
        self._pcm.setchannels(channels)
        self._pcm.setrate(self.sample_rate)
        self._pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        self._pcm.setperiodsize(self.period_size)

    def stop(self) -> None:
        self._running = False
        if self._pcm:
            self._pcm.close()
            self._pcm = None
        logger.info("Audio capture stopped")

    def read(self) -> Optional[bytes]:
        """Read one period of audio. Returns raw mono PCM bytes or None."""
        if not self._pcm or not self._running:
            return None
        try:
            length, data = self._pcm.read()
            if length > 0:
                # Detect actual stereo even if we requested mono
                expected_mono = self.period_size * 2  # 16-bit mono
                if len(data) > expected_mono and self.channels == 1:
                    data = self._stereo_to_mono(data)
                elif self._hw_channels == 2 and self.channels == 1:
                    data = self._stereo_to_mono(data)
                if self.mic_gain != 1.0:
                    data = self._apply_gain(data, self.mic_gain)
                return data
        except alsaaudio.ALSAAudioError as e:
            logger.warning("ALSA read error: %s", e)
        return None

    @staticmethod
    def _stereo_to_mono(data: bytes) -> bytes:
        """Downmix interleaved stereo S16_LE to mono by averaging channels."""
        samples = struct.unpack(f"<{len(data) // 2}h", data)
        mono = [
            (samples[i] + samples[i + 1]) // 2
            for i in range(0, len(samples), 2)
        ]
        return struct.pack(f"<{len(mono)}h", *mono)

    @staticmethod
    def _apply_gain(data: bytes, gain: float) -> bytes:
        """Apply gain to 16-bit PCM audio."""
        samples = struct.unpack(f"<{len(data) // 2}h", data)
        amplified = [max(-32768, min(32767, int(s * gain))) for s in samples]
        return struct.pack(f"<{len(amplified)}h", *amplified)


class AudioPlayback:
    """Plays audio through an ALSA device."""

    def __init__(
        self,
        device: str = "default",
        sample_rate: int = 22050,
        channels: int = 1,
        volume: float = 0.7,
    ):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.volume = volume
        self._lock = threading.Lock()

    async def play_pcm(
        self, audio_data: bytes, sample_rate: int = 22050
    ) -> None:
        """Play raw PCM audio asynchronously."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._play_pcm_sync, audio_data, sample_rate)

    def _play_pcm_sync(self, audio_data: bytes, sample_rate: int) -> None:
        with self._lock:
            try:
                logger.info("Playing %d bytes at %dHz on %s", len(audio_data), sample_rate, self.device)
                # Try stereo first — many codecs (WM8960) only support stereo
                # and setchannels(1) silently succeeds but data is misinterpreted
                hw_channels = 2
                pcm = self._open_playback(hw_channels, sample_rate)
                if pcm is None:
                    hw_channels = self.channels
                    pcm = self._open_playback(hw_channels, sample_rate)
                if pcm is None:
                    logger.error("Could not open playback device %s", self.device)
                    return

                if self.volume != 1.0:
                    audio_data = AudioCapture._apply_gain(audio_data, self.volume)

                # Upmix mono to stereo if hardware requires it
                if hw_channels == 2 and self.channels == 1:
                    audio_data = self._mono_to_stereo(audio_data)

                # Write in chunks
                period = 1024
                chunk_bytes = period * 2 * hw_channels
                for i in range(0, len(audio_data), chunk_bytes):
                    chunk = audio_data[i : i + chunk_bytes]
                    pcm.write(chunk)

                pcm.close()
                logger.info("Playback complete (%d bytes)", len(audio_data))
            except Exception:
                logger.exception("Audio playback error")

    def _open_playback(self, channels: int, sample_rate: int):
        """Try to open ALSA playback device. Returns PCM or None."""
        try:
            pcm = alsaaudio.PCM(
                alsaaudio.PCM_PLAYBACK,
                alsaaudio.PCM_NORMAL,
                device=self.device,
            )
            pcm.setchannels(channels)
            pcm.setrate(sample_rate)
            pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
            pcm.setperiodsize(1024)
            return pcm
        except alsaaudio.ALSAAudioError:
            return None

    @staticmethod
    def _mono_to_stereo(data: bytes) -> bytes:
        """Upmix mono S16_LE to stereo by duplicating each sample."""
        samples = struct.unpack(f"<{len(data) // 2}h", data)
        stereo = []
        for s in samples:
            stereo.extend([s, s])
        return struct.pack(f"<{len(stereo)}h", *stereo)

    async def play_wav(self, path: str | Path) -> None:
        """Play a WAV file asynchronously."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._play_wav_sync, str(path))

    def _play_wav_sync(self, path: str) -> None:
        with self._lock:
            try:
                with wave.open(path, "rb") as wf:
                    wav_ch = wf.getnchannels()
                    rate = wf.getframerate()
                    pcm = self._open_playback(wav_ch, rate)
                    if pcm is None and wav_ch == 1:
                        pcm = self._open_playback(2, rate)
                        wav_ch = 2  # will upmix

                    if pcm is None:
                        logger.error("Could not open playback for WAV: %s", path)
                        return

                    need_upmix = wav_ch == 2 and wf.getnchannels() == 1
                    period = 1024
                    data = wf.readframes(period)
                    while data:
                        if self.volume != 1.0:
                            data = AudioCapture._apply_gain(data, self.volume)
                        if need_upmix:
                            data = self._mono_to_stereo(data)
                        pcm.write(data)
                        data = wf.readframes(period)

                    pcm.close()
            except Exception:
                logger.exception("WAV playback error: %s", path)
