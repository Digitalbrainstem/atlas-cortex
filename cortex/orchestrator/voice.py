"""Voice pipeline orchestration: STT → Pipeline → TTS → Stream.

OWNERSHIP: This module owns the end-to-end voice interaction flow.
It coordinates speech-to-text, the pipeline, filler playback,
sentence-level TTS streaming, and auto-listen decisions.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any

from cortex.speech import (
    synthesize_speech,
    is_hallucinated,
    to_orpheus_voice,
    resolve_voice,
    transcribe,
)
from cortex.speech.stt import _STT_BACKEND
from cortex.orchestrator.text import _STREAM_SENT_RE, should_auto_listen

logger = logging.getLogger(__name__)

_ORPHEUS_URL = os.environ.get("ORPHEUS_FASTAPI_URL", "http://localhost:5005")


# ── Satellite audio streaming (protocol-specific) ────────────────

async def _stream_audio_to_satellite(
    conn: Any, audio: bytes, rate: int,
    text: str, is_filler: bool = False, auto_listen: bool = False,
) -> None:
    """Stream PCM audio to a satellite as TTS_START/CHUNK/END."""
    await conn.send({
        "type": "TTS_START",
        "session_id": conn.session_id,
        "format": f"pcm_{rate // 1000}k_16bit_mono",
        "sample_rate": rate,
        "text": text,
        "is_filler": is_filler,
    })
    chunk_size = 4096
    for offset in range(0, len(audio), chunk_size):
        chunk = audio[offset:offset + chunk_size]
        await conn.send({
            "type": "TTS_CHUNK",
            "session_id": conn.session_id,
            "audio": base64.b64encode(chunk).decode("ascii"),
        })
    msg: dict[str, Any] = {
        "type": "TTS_END",
        "session_id": conn.session_id,
        "is_filler": is_filler,
    }
    if auto_listen:
        msg["auto_listen"] = True
    await conn.send(msg)


async def _stream_orpheus_to_satellite(
    conn: Any, text: str, voice: str,
    is_filler: bool = False, auto_listen: bool = False,
    expression: str | None = None,
) -> tuple[int, float]:
    """Stream Orpheus TTS directly to satellite — low latency.

    Returns (total_bytes, elapsed_seconds).
    """
    import aiohttp

    bare_voice = to_orpheus_voice(voice)
    payload = {
        "input": text,
        "model": "orpheus",
        "voice": bare_voice,
        "response_format": "wav",
        "stream": True,
    }

    total_bytes = 0
    t_start = time.monotonic()
    sent_start = False
    wav_header_skipped = False
    pcm_buffer = bytearray()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_ORPHEUS_URL}/v1/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.warning("Orpheus stream failed (%d): %s", resp.status, error[:200])
                    return 0, 0.0

                async for chunk in resp.content.iter_any():
                    if not chunk:
                        continue

                    pcm_buffer.extend(chunk)

                    if not wav_header_skipped:
                        if len(pcm_buffer) < 44:
                            continue
                        if pcm_buffer[:4] == b'RIFF':
                            wav_header_skipped = True
                            pcm_buffer = pcm_buffer[44:]
                        else:
                            wav_header_skipped = True

                    if not sent_start and len(pcm_buffer) >= 4096:
                        await conn.send({
                            "type": "TTS_START",
                            "session_id": conn.session_id,
                            "format": "pcm_24k_16bit_mono",
                            "sample_rate": 24000,
                            "text": text,
                            "is_filler": is_filler,
                        })
                        sent_start = True
                        ttfa = (time.monotonic() - t_start) * 1000
                        logger.info("Orpheus TTFA: %.0fms for %r", ttfa, text[:40])

                    while len(pcm_buffer) >= 4096:
                        out = bytes(pcm_buffer[:4096])
                        pcm_buffer = pcm_buffer[4096:]
                        await conn.send({
                            "type": "TTS_CHUNK",
                            "session_id": conn.session_id,
                            "audio": base64.b64encode(out).decode("ascii"),
                        })
                        total_bytes += len(out)

                if pcm_buffer and sent_start:
                    out = bytes(pcm_buffer)
                    await conn.send({
                        "type": "TTS_CHUNK",
                        "session_id": conn.session_id,
                        "audio": base64.b64encode(out).decode("ascii"),
                    })
                    total_bytes += len(out)

        if sent_start:
            msg: dict[str, Any] = {
                "type": "TTS_END",
                "session_id": conn.session_id,
                "is_filler": is_filler,
            }
            if auto_listen:
                msg["auto_listen"] = True
            if expression:
                msg["expression"] = expression
            await conn.send(msg)

    except Exception as e:
        logger.warning("Orpheus streaming failed: %s", e)

    elapsed = time.monotonic() - t_start
    return total_bytes, elapsed


# ── Main voice pipeline ──────────────────────────────────────────

async def process_voice_pipeline(conn: Any, audio_data: bytes) -> None:
    """Full STT → Pipeline → TTS → stream back to satellite.

    This is the main orchestration function. It:
    1. Transcribes audio via STT (cortex.speech)
    2. Filters hallucinations and wake words
    3. Runs the pipeline (which yields text tokens)
    4. Streams TTS audio back sentence-by-sentence
    5. Handles filler phrases and auto-listen
    """
    from fastapi import WebSocketDisconnect

    satellite_id = conn.satellite_id
    t_start = time.monotonic()

    # Reject very short audio clips — likely echo/noise
    min_audio_bytes = 48000  # ~1.5s at 16kHz 16-bit mono
    if len(audio_data) < min_audio_bytes:
        audio_sec = len(audio_data) / 32000
        logger.info("Audio too short from %s (%.1fs, %d bytes) — dropping",
                     satellite_id, audio_sec, len(audio_data))
        try:
            await conn.send({"type": "PIPELINE_ERROR", "detail": "Audio too short"})
        except Exception:
            pass
        return

    try:
        # ── Step 1: STT ──────────────────────────────────────────
        t_stt_start = time.monotonic()
        logger.info("Running STT on %d bytes (%.1fs audio) from %s (backend=%s)",
                     len(audio_data), len(audio_data) / 32000,
                     satellite_id, _STT_BACKEND)

        try:
            transcript = await transcribe(audio_data, sample_rate=16000)
        except Exception as e:
            logger.error("STT failed for %s: %s", satellite_id, e)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": f"STT failed: {e}"})
            except Exception:
                pass
            return

        transcript = transcript.strip()
        t_stt_end = time.monotonic()
        stt_ms = (t_stt_end - t_stt_start) * 1000

        if not transcript:
            logger.info("Empty transcript from %s (STT took %.0fms), ignoring", satellite_id, stt_ms)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "Empty transcript"})
            except Exception:
                pass
            return

        if is_hallucinated(transcript):
            logger.warning("Hallucinated transcript from %s (%.0fms): %r — dropping",
                           satellite_id, stt_ms, transcript[:100])
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "No clear speech detected"})
            except Exception:
                pass
            return

        logger.info("STT result from %s (%.0fms): %r", satellite_id, stt_ms, transcript)

        # Server-side wake word filter
        if not conn.has_wake_word:
            wake_keywords = {"atlas", "atmos", "alice"}
            transcript_lower = transcript.lower()
            has_keyword = any(kw in transcript_lower for kw in wake_keywords)
            if not has_keyword:
                logger.info("No wake keyword in transcript from %s (no local wake word), dropping: %r",
                            satellite_id, transcript[:80])
                try:
                    await conn.send({"type": "PIPELINE_ERROR", "detail": "No wake word detected"})
                except Exception:
                    pass
                return
            for kw in sorted(wake_keywords, key=len, reverse=True):
                idx = transcript_lower.find(kw)
                if idx != -1:
                    prefix = transcript[:idx].strip().lower()
                    clean_prefixes = {"hey", "ok", "okay", "hi", "yo", ""}
                    if prefix in clean_prefixes:
                        transcript = transcript[idx + len(kw):].strip()
                    else:
                        transcript = (transcript[:idx] + transcript[idx + len(kw):]).strip()
                    break
            if not transcript:
                logger.info("Transcript empty after wake word removal from %s", satellite_id)
                try:
                    await conn.send({"type": "PIPELINE_ERROR", "detail": "Empty after wake word"})
                except Exception:
                    pass
                return
            logger.info("After wake word filter from %s: %r", satellite_id, transcript)

        # ── Step 2: Pipeline (filler-first streaming) ─────────────
        t_llm_start = time.monotonic()
        from cortex.providers import get_provider
        from cortex.pipeline import run_pipeline

        provider = get_provider()

        # HOT path: recall relevant memories for this query
        memory_context = ""
        memory_hit_count = 0
        try:
            from cortex.memory import get_memory_system, format_memory_context
            mem = get_memory_system()
            if mem is not None:
                hits = await mem.recall(transcript, user_id="default", top_k=5)
                memory_hit_count = len(hits) if hits else 0
                if hits:
                    memory_context = format_memory_context(hits, max_chars=800)
                    logger.info("Memory recall for %s: %d hits (%.0f chars)",
                                satellite_id, len(hits), len(memory_context))
        except Exception as e:
            logger.debug("Memory recall failed (non-fatal): %s", e)

        # Evolution: get personality modifiers for this user
        personality_context = ""
        try:
            from cortex.evolution import EmotionalProfile
            from cortex.db import get_db as _get_db
            profile = EmotionalProfile(_get_db())
            mods = profile.get_personality_modifiers(user_id="default")
            if mods and mods.get("tone"):
                personality_context = (
                    f"Personality: tone={mods['tone']}, "
                    f"formality={mods.get('formality', 'moderate')}, "
                    f"humor={mods.get('humor_level', 'occasional')}, "
                    f"verbosity={mods.get('verbosity', 'concise')}."
                )
        except Exception as e:
            logger.debug("Evolution personality failed (non-fatal): %s", e)

        # Combine memory + personality into extra context
        extra_context = memory_context
        if personality_context:
            extra_context = f"{personality_context}\n{extra_context}" if extra_context else personality_context

        pipeline_gen = await run_pipeline(
            message=transcript,
            provider=provider,
            satellite_id=satellite_id,
            model_fast="qwen2.5:7b",
            memory_context=extra_context,
        )

        filler_text = ""
        tts_voice = resolve_voice(satellite_id=satellite_id)
        token_buf = ""
        response_parts: list[str] = []
        sentences_sent = 0
        total_tts_bytes = 0
        tts_used = "none"
        _filler_task: asyncio.Task | None = None

        # Peek: collect up to 2 tokens to distinguish instant vs LLM
        tokens = []
        async for token in pipeline_gen:
            tokens.append(token)
            if len(tokens) == 1:
                continue
            break

        if len(tokens) == 1 and tokens[0].strip():
            # ── Instant answer (Layer 1/2) ──
            instant_text = tokens[0].strip()
            _joke_expression = None
            if "\n" in instant_text:
                _joke_expression = "laughing"
                instant_text = instant_text + " <chuckle>"
            t_tts = time.monotonic()
            total_tts_bytes, elapsed = await _stream_orpheus_to_satellite(
                conn, instant_text, tts_voice, is_filler=False,
                auto_listen=instant_text.rstrip().endswith("?"),
                expression=_joke_expression,
            )
            tts_ms = elapsed * 1000
            if total_tts_bytes > 0:
                logger.info("Instant TTS [orpheus-stream] %.0fms (%d bytes): %r",
                            tts_ms, total_tts_bytes, instant_text[:60])
                tts_used = "orpheus"
                sentences_sent = 1
            else:
                audio, rate, prov = await synthesize_speech(instant_text, tts_voice, fast=True)
                tts_ms = (time.monotonic() - t_tts) * 1000
                if audio:
                    logger.info("Instant TTS [%s] %.0fms (%d bytes): %r",
                                prov, tts_ms, len(audio), instant_text[:60])
                    await _stream_audio_to_satellite(
                        conn, audio, rate, instant_text, is_filler=False,
                        auto_listen=instant_text.rstrip().endswith("?"),
                    )
                    total_tts_bytes = len(audio)
                    tts_used = prov
                    sentences_sent = 1
                else:
                    await conn.send({
                        "type": "TTS_END",
                        "session_id": conn.session_id,
                        "is_filler": False,
                    })

            t_total = time.monotonic() - t_stt_start
            logger.info("Pipeline complete for %s: total=%.1fs (STT=%.0fms instant [%s] %d bytes)",
                        satellite_id, t_total, stt_ms, tts_used, total_tts_bytes)
            return

        # ── LLM streaming path ──
        for i, token in enumerate(tokens):
            if i == 0:
                filler_text = token.strip()
                if filler_text:
                    from cortex.orchestrator.filler import play_filler
                    _filler_task = asyncio.create_task(
                        play_filler(conn, filler_text, tts_voice, satellite_id)
                    )
            else:
                response_parts.append(token)
                token_buf += token

        async for token in pipeline_gen:
            response_parts.append(token)
            token_buf += token

            while True:
                m = _STREAM_SENT_RE.search(token_buf)
                if not m:
                    break
                sentence = token_buf[:m.end()].strip()
                token_buf = token_buf[m.end():]
                if len(sentence) < 20:
                    token_buf = sentence + " " + token_buf
                    break

                if _filler_task and not _filler_task.done():
                    await _filler_task
                    _filler_task = None

                t_sent = time.monotonic()
                sent_bytes, sent_elapsed = await _stream_orpheus_to_satellite(
                    conn, sentence, tts_voice, is_filler=False)
                if sent_bytes > 0:
                    logger.info("Sentence TTS [orpheus-stream] %.0fms (%d bytes): %r",
                                sent_elapsed * 1000, sent_bytes, sentence[:60])
                    sentences_sent += 1
                    total_tts_bytes += sent_bytes
                    tts_used = "orpheus"
                else:
                    audio, rate, prov = await synthesize_speech(sentence, tts_voice)
                    if audio:
                        logger.info("Sentence TTS [%s] %.0fms (%d bytes): %r",
                                    prov, (time.monotonic() - t_sent) * 1000,
                                    len(audio), sentence[:60])
                        await _stream_audio_to_satellite(conn, audio, rate, sentence, is_filler=False)
                        sentences_sent += 1
                        total_tts_bytes += len(audio)
                        tts_used = prov

        full_response = "".join(response_parts).strip()
        t_llm_end = time.monotonic()
        llm_ms = (t_llm_end - t_llm_start) * 1000

        if _filler_task and not _filler_task.done():
            await _filler_task

        if not full_response:
            logger.warning("Empty pipeline response for %r (LLM took %.0fms)", transcript, llm_ms)
            return

        logger.info("Pipeline response for %s (LLM %.0fms): %r", satellite_id, llm_ms, full_response[:200])

        # ── Step 3: Final sentence + auto-listen ─────────────────
        is_question = should_auto_listen(full_response)

        if token_buf.strip():
            final_text = token_buf.strip()
            t_sent = time.monotonic()
            sent_bytes, sent_elapsed = await _stream_orpheus_to_satellite(
                conn, final_text, tts_voice,
                is_filler=False, auto_listen=is_question,
            )
            if sent_bytes > 0:
                logger.info("Final sentence TTS [orpheus-stream] %.0fms (%d bytes): %r",
                            sent_elapsed * 1000, sent_bytes, final_text[:60])
                total_tts_bytes += sent_bytes
                tts_used = "orpheus"
                sentences_sent += 1
            else:
                audio, rate, prov = await synthesize_speech(final_text, tts_voice)
                if audio:
                    logger.info("Final sentence TTS [%s] %.0fms (%d bytes): %r",
                                prov, (time.monotonic() - t_sent) * 1000,
                                len(audio), final_text[:60])
                    await _stream_audio_to_satellite(
                        conn, audio, rate, final_text,
                        is_filler=False, auto_listen=is_question,
                    )
                    total_tts_bytes += len(audio)
                    tts_used = prov or tts_used
                    sentences_sent += 1
        elif sentences_sent > 0:
            msg: dict[str, Any] = {
                "type": "TTS_END",
                "session_id": conn.session_id,
                "is_filler": False,
            }
            if is_question:
                msg["auto_listen"] = True
            await conn.send(msg)
        else:
            logger.warning("No sentences synthesized for %s", satellite_id)
            return

        if is_question:
            logger.info("Response was a question — auto_listen flag set for %s", satellite_id)

        t_total = time.monotonic() - t_start
        logger.info(
            "Pipeline complete for %s: total=%.1fs (STT=%.0fms LLM=%.0fms %d sentences [%s] %d bytes)",
            satellite_id, t_total, stt_ms, llm_ms, sentences_sent, tts_used, total_tts_bytes,
        )

        # COLD path: remember this interaction for future recall
        try:
            from cortex.memory import get_memory_system
            mem = get_memory_system()
            if mem is not None and full_response:
                await mem.remember(
                    f"User asked: {transcript}\nAtlas replied: {full_response[:500]}",
                    user_id="default",
                )
        except Exception as e:
            logger.debug("Memory remember failed (non-fatal): %s", e)

        # Evolution: record interaction for rapport tracking
        try:
            from cortex.evolution import EmotionalProfile
            from cortex.db import get_db as _get_db
            profile = EmotionalProfile(_get_db())
            sentiment = "positive" if is_question else "neutral"
            profile.record_interaction(user_id="default", sentiment=sentiment)
        except Exception as e:
            logger.debug("Evolution record_interaction failed (non-fatal): %s", e)

        # Grounding: assess confidence of response
        try:
            from cortex.grounding import assess_confidence
            confidence = assess_confidence(
                response=full_response,
                layer=tts_used if tts_used else "llm",
                memory_hits=memory_hit_count,
            )
            if confidence < 0.4:
                logger.info("Low confidence (%.2f) for %s: %s",
                            confidence, satellite_id, transcript[:80])
        except Exception as e:
            logger.debug("Grounding assess_confidence failed (non-fatal): %s", e)

    except Exception as exc:
        # Check for WebSocketDisconnect
        from fastapi import WebSocketDisconnect
        if isinstance(exc, WebSocketDisconnect):
            logger.warning("Satellite %s disconnected during pipeline", satellite_id)
        else:
            logger.exception("Voice pipeline error for %s", satellite_id)
            try:
                await conn.send({"type": "PIPELINE_ERROR", "detail": "Voice pipeline failed"})
            except Exception:
                pass
