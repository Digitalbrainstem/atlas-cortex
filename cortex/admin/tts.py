"""TTS preview and voice management endpoints."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from cortex.db import get_db
from cortex.admin.helpers import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tts/voices")
async def list_tts_voices(admin: dict = Depends(require_admin)):
    """List available TTS voices from Kokoro + Orpheus + Piper."""
    from cortex.voice.wyoming import WyomingClient
    all_voices = []

    # Kokoro prefix → language mapping
    _kokoro_lang = {
        "a": "en", "b": "en",  # American / British English
        "e": "es", "f": "fr", "g": "de", "h": "hi",
        "i": "it", "j": "ja", "k": "ko", "p": "pt",
        "z": "zh",
    }

    # Kokoro voices (primary)
    try:
        from cortex.voice.kokoro import KokoroClient
        host = os.environ.get("KOKORO_HOST", "localhost")
        port = int(os.environ.get("KOKORO_PORT", "8880"))
        kokoro = KokoroClient(host, port, timeout=5.0)
        kokoro_voices = await kokoro.list_voices()
        for v in kokoro_voices:
            lang = _kokoro_lang.get(v[0], "en") if len(v) > 0 else "en"
            all_voices.append({
                "name": v,
                "provider": "kokoro",
                "description": v,
                "language": lang,
                "installed": True,
            })
    except Exception:
        pass

    # Orpheus voices
    try:
        from cortex.voice.providers.orpheus import _ORPHEUS_VOICES
        for v in _ORPHEUS_VOICES:
            all_voices.append({
                "name": v["id"],
                "provider": "orpheus",
                "description": f"{v['name']} ({v['style']}, {v['gender']})",
                "language": "en",
                "installed": True,
            })
    except Exception:
        pass

    # Piper voices (fallback)
    try:
        host = os.environ.get("PIPER_HOST", os.environ.get("TTS_HOST", "localhost"))
        port = int(os.environ.get("PIPER_PORT", os.environ.get("TTS_PORT", "10200")))
        tts = WyomingClient(host, port)
        piper_voices = await tts.list_voices()
        for v in piper_voices:
            v["provider"] = "piper"
            v["language"] = v.get("language", "en")
            all_voices.append(v)
    except Exception:
        pass

    # Include system default voice
    db = get_db()
    row = db.execute("SELECT value FROM system_settings WHERE key = 'default_tts_voice'").fetchone()
    system_default = row["value"] if row else ""

    return {"voices": all_voices, "system_default": system_default}


@router.put("/tts/default_voice")
async def set_default_voice(body: dict, admin: dict = Depends(require_admin)):
    """Set the system-wide default TTS voice. Body: {\"voice\": \"af_bella\"}"""
    voice = body.get("voice", "")
    if not voice:
        return {"error": "voice is required"}, 400

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('default_tts_voice', ?, CURRENT_TIMESTAMP)",
        (voice,),
    )
    db.commit()

    logger.info("System default TTS voice set to: %s", voice)

    # Kick off background regeneration of cached audio for the new voice
    import asyncio
    asyncio.create_task(_regenerate_cache_for_voice(voice))

    return {"default_voice": voice}


async def _regenerate_cache_for_voice(voice: str) -> None:
    """Background task: re-generate fillers + joke audio for a voice."""
    logger.info("Starting cache regeneration for voice: %s", voice)
    results = {"fillers": 0, "jokes": 0, "errors": []}

    # 1. Regenerate filler cache
    try:
        from cortex.filler.cache import get_filler_cache
        cache = get_filler_cache()
        await cache.initialize(voice=voice, force=True)
        results["fillers"] = sum(len(v) for v in cache._cache.values())
        logger.info("Filler cache regenerated: %d phrases for voice=%s", results["fillers"], voice)
    except Exception as e:
        logger.warning("Filler regeneration failed: %s", e)
        results["errors"].append(f"fillers: {e}")

    # 2. Regenerate joke audio
    try:
        from cortex.jokes import pre_generate_joke_audio
        results["jokes"] = await pre_generate_joke_audio(voice)
        logger.info("Joke audio regenerated: %d segments for voice=%s", results["jokes"], voice)
    except Exception as e:
        logger.warning("Joke audio regeneration failed: %s", e)
        results["errors"].append(f"jokes: {e}")

    logger.info("Cache regeneration complete for voice=%s: %s", voice, results)


@router.post("/tts/regenerate")
async def regenerate_tts_cache(body: dict, admin: dict = Depends(require_admin)):
    """Regenerate all pre-cached TTS audio for a voice.

    Body: {"voice": "af_bella"}  (optional — defaults to system default)
    """
    voice = body.get("voice", "")
    if not voice:
        db = get_db()
        row = db.execute("SELECT value FROM system_settings WHERE key = 'default_tts_voice'").fetchone()
        voice = row["value"] if row else ""
    if not voice:
        return {"error": "No voice specified and no system default set"}, 400

    import asyncio
    asyncio.create_task(_regenerate_cache_for_voice(voice))
    return {"status": "regenerating", "voice": voice}


@router.post("/tts/preview")
async def preview_tts(body: dict, admin: dict = Depends(require_admin)):
    """Synthesize text and return WAV audio for browser playback or push to satellite."""
    import io
    import wave
    from fastapi.responses import Response
    from cortex.voice.wyoming import WyomingClient, WyomingError

    text = body.get("text", "Hello, I am Atlas.")
    voice = body.get("voice")
    target = body.get("target", "browser")  # "browser" or satellite_id

    audio_data = b""
    rate = 22050
    width = 2
    channels = 1

    # Try Orpheus for orpheus voices
    orpheus_voices = {"tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"}
    use_orpheus = voice and voice.replace("orpheus_", "") in orpheus_voices

    # Try Kokoro for Kokoro voices (af_*, am_*, bf_*, bm_*, jf_*, etc.)
    kokoro_prefixes = ("af_", "am_", "bf_", "bm_", "ef_", "em_", "ff_", "gf_",
                       "hf_", "if_", "jf_", "pf_", "zf_", "zm_")
    use_kokoro = voice and voice.startswith(kokoro_prefixes) and not use_orpheus

    if use_kokoro:
        try:
            from cortex.voice.kokoro import KokoroClient, KokoroError
            kokoro_host = os.environ.get("KOKORO_HOST", "localhost")
            kokoro_port = int(os.environ.get("KOKORO_PORT", "8880"))
            client = KokoroClient(host=kokoro_host, port=kokoro_port)
            wav_data, info = await client.synthesize(text, voice=voice, response_format="wav")
            if wav_data and wav_data[:4] == b"RIFF":
                with wave.open(io.BytesIO(wav_data), "rb") as wf:
                    rate = wf.getframerate()
                    width = wf.getsampwidth()
                    channels = wf.getnchannels()
                    audio_data = wf.readframes(wf.getnframes())
            elif wav_data:
                audio_data = wav_data
                rate = info.get("rate", 24000)
        except Exception as e:
            logger.warning("Kokoro preview failed, falling back to Piper: %s", e)
            audio_data = b""

    if use_orpheus and not audio_data:
        try:
            from cortex.voice.providers import get_tts_provider, _env_config
            provider = get_tts_provider(_env_config())
            chunks = []
            async for chunk in provider.synthesize(text, voice=voice):
                chunks.append(chunk)
            audio_data = b"".join(chunks)
            if audio_data and audio_data[:4] == b"RIFF":
                with wave.open(io.BytesIO(audio_data), "rb") as wf:
                    rate = wf.getframerate()
                    width = wf.getsampwidth()
                    channels = wf.getnchannels()
                    audio_data = wf.readframes(wf.getnframes())
            elif audio_data:
                rate = 24000  # SNAC decoder output
        except Exception as e:
            logger.warning("Orpheus preview failed, falling back to Piper: %s", e)
            audio_data = b""

    # Piper fallback
    if not audio_data:
        host = os.environ.get("PIPER_HOST", os.environ.get("TTS_HOST", "localhost"))
        port = int(os.environ.get("PIPER_PORT", os.environ.get("TTS_PORT", "10200")))
        tts = WyomingClient(host, port, timeout=30.0)
        piper_voice = voice if voice and not voice.startswith("orpheus_") else None
        try:
            audio_data, audio_info = await tts.synthesize(text, voice=piper_voice)
        except WyomingError as e:
            raise HTTPException(status_code=502, detail=f"TTS error: {e}")
        rate = audio_info.get("rate", 22050)
        width = audio_info.get("width", 2)
        channels = audio_info.get("channels", 1)

    if not audio_data:
        raise HTTPException(status_code=500, detail="TTS returned empty audio")

    if target != "browser":
        # Push to satellite speaker at native TTS rate (hardware handles conversion)
        import base64
        conn = _connected_satellites_ref().get(target)
        if not conn:
            raise HTTPException(status_code=404, detail="Satellite not connected")
        await conn.send({"type": "TTS_START", "sample_rate": rate, "format": f"pcm_{rate}_{width*8}bit_{channels}ch"})
        for off in range(0, len(audio_data), 4096):
            await conn.send({"type": "TTS_CHUNK", "audio": base64.b64encode(audio_data[off:off+4096]).decode()})
        await conn.send({"type": "TTS_END"})
        return {"sent": True, "bytes": len(audio_data)}

    # Return WAV for browser playback
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(audio_data)
    wav_bytes = buf.getvalue()

    return Response(content=wav_bytes, media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=preview.wav"})


@router.post("/tts/filler_preview")
async def preview_filler(body: dict, admin: dict = Depends(require_admin)):
    """Synthesize a filler phrase and optionally push to satellite."""
    from cortex.filler import select_filler
    from cortex.voice.wyoming import WyomingClient

    sentiment = body.get("sentiment", "greeting")
    target = body.get("target", "browser")

    # Pick a filler
    filler_text = select_filler(sentiment, confidence=0.8, user_id="admin")
    if not filler_text:
        filler_text = "Hmm, let me think..."

    host = os.environ.get("TTS_HOST", "localhost")
    port = int(os.environ.get("TTS_PORT", "10200"))
    tts = WyomingClient(host, port, timeout=15.0)

    # Use the target satellite's configured voice (if pushing to a satellite)
    voice = body.get("voice")
    if not voice and target != "browser":
        from cortex.db import get_db
        try:
            db = get_db()
            row = db.execute("SELECT tts_voice FROM satellites WHERE id = ?", (target,)).fetchone()
            voice = (row["tts_voice"] or "") if row else ""
        except Exception:
            voice = ""
    audio_data, audio_info = await tts.synthesize(filler_text, voice=voice or None)

    if target != "browser":
        import base64
        from cortex.satellite.websocket import get_connection
        rate = audio_info.get("rate", 22050)
        conn = get_connection(target)
        if not conn:
            raise HTTPException(status_code=404, detail="Satellite not connected")
        await conn.send({"type": "TTS_START", "sample_rate": rate, "format": f"pcm_{rate//1000}k_16bit_mono"})
        for off in range(0, len(audio_data), 4096):
            await conn.send({"type": "TTS_CHUNK", "audio": base64.b64encode(audio_data[off:off+4096]).decode()})
        await conn.send({"type": "TTS_END"})
        return {"sent": True, "filler": filler_text}

    import io
    import wave
    from fastapi.responses import Response
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(audio_info.get("channels", 1))
        wf.setsampwidth(audio_info.get("width", 2))
        wf.setframerate(audio_info.get("rate", 22050))
        wf.writeframes(audio_data)
    return Response(content=buf.getvalue(), media_type="audio/wav")


def _connected_satellites_ref():
    from cortex.satellite.websocket import get_connected_satellites
    return get_connected_satellites()
