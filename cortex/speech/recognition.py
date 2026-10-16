"""Local CPU voice embeddings shared by enrollment and satellite recognition."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)
_encoder: Any = None
_encoder_lock = threading.Lock()


def _encode(audio: bytes) -> bytes:
    from resemblyzer import VoiceEncoder, preprocess_wav
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            _encoder = VoiceEncoder(device="cpu")
        waveform = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768
        waveform = preprocess_wav(waveform, source_sr=16000)
        if not waveform.size:
            raise ValueError("No speech available for speaker recognition")
        embedding = np.asarray(_encoder.embed_utterance(waveform), dtype="<f4")
        if embedding.shape != (256,) or not np.isfinite(embedding).all():
            raise ValueError("Invalid speaker embedding")
        return embedding.tobytes()


async def audio_embedding(audio: bytes) -> bytes:
    if not 8000 <= len(audio) <= 480000 or len(audio) % 2:
        raise ValueError("Speaker audio must be 0.25-15 seconds of 16 kHz mono PCM16")
    return await asyncio.to_thread(_encode, audio)


async def identify_audio(audio: bytes, conn: Any) -> tuple[str, str | None]:
    """Resolve only enrolled local profiles; absence/ambiguity never elevates policy."""
    rows = conn.execute(
        "SELECT s.* FROM speaker_profiles s JOIN user_profiles u ON u.user_id=s.user_id"
    ).fetchall()
    if not rows or len(audio) < 8000:
        return "anonymous", None
    try:
        query = np.frombuffer(await audio_embedding(audio), dtype="<f4")
    except (ImportError, OSError, RuntimeError, ValueError):
        logger.warning("Speaker recognition unavailable; using anonymous child-safe policy", exc_info=True)
        return "anonymous", None
    scores: dict[str, tuple[float, str, float]] = {}
    for row in rows:
        raw = row["embedding"]
        if not isinstance(raw, bytes) or len(raw) != 1024:
            logger.warning("Speaker %s has an incompatible embedding; re-enrollment required", row["id"])
            continue
        embedding = np.frombuffer(raw, dtype="<f4")
        norm = float(np.linalg.norm(query) * np.linalg.norm(embedding))
        if not norm or not np.isfinite(embedding).all():
            logger.warning("Speaker %s has an invalid embedding", row["id"])
            continue
        score = float(np.dot(query, embedding) / norm)
        threshold = max(0.85, float(row["confidence_threshold"]))
        previous = scores.get(row["user_id"])
        if previous is None or score > previous[0]:
            scores[row["user_id"]] = (score, row["id"], threshold)
    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)
    if not ranked:
        return "anonymous", None
    user, (score, speaker, threshold) = ranked[0]
    if score < threshold or len(ranked) > 1 and score - ranked[1][1][0] < 0.03:
        return "anonymous", None
    # Enrollment/profile may have been removed while embedding was running.
    if conn.execute(
        "SELECT 1 FROM speaker_profiles s JOIN user_profiles u ON u.user_id=s.user_id "
        "WHERE s.id=? AND s.user_id=?", (speaker, user),
    ).fetchone() is None:
        return "anonymous", None
    conn.execute("UPDATE speaker_profiles SET last_verified=CURRENT_TIMESTAMP WHERE id=?", (speaker,))
    conn.commit()
    return user, speaker
