"""Atlas Cortex — standalone OpenAI-compatible server.

Exposes:
  GET  /v1/models                — list "atlas-cortex" model
  POST /v1/chat/completions      — full pipeline (streaming SSE or JSON)
  GET  /health                   — liveness check

Start with::

    python -m cortex.server
    # or
    uvicorn cortex.server:app --host 0.0.0.0 --port 5100
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cortex.admin_api import router as admin_router
from cortex.db import get_db, init_db
from cortex.pipeline import run_pipeline
from cortex.providers import get_provider
from cortex.satellite.discovery import ServerAnnouncer
from cortex.satellite.websocket import satellite_ws_handler, on_barge_in
from cortex.avatar.websocket import avatar_ws_handler
from cortex.avatar.broadcast import broadcast_playback_stop
from cortex.voice.providers import get_tts_provider

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Safety middleware (global — initialised during startup)
# ──────────────────────────────────────────────────────────────────

_safety_middleware = None

# Configure logging for all cortex modules (needed when running under uvicorn
# directly, since main() isn't called and basicConfig isn't triggered).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ──────────────────────────────────────────────────────────────────
# Server mDNS announcer (so satellites can auto-discover us)
# ──────────────────────────────────────────────────────────────────

_server_announcer = ServerAnnouncer(port=5100)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop server-level services."""
    from cortex.scheduler import register_startup_task, register_service, start_all, stop_all

    init_db()

    # ── Integrity verification (blocking — Atlas won't start if this fails)
    from cortex.db import get_db
    from cortex.integrity import verify_startup_integrity, IntegrityError, IntegrityMonitor
    try:
        integrity_result = await verify_startup_integrity(get_db())
        logger.info("Integrity verified: %s", integrity_result)
    except IntegrityError as exc:
        logger.critical("INTEGRITY CHECK FAILED — Atlas cannot start: %s", exc)
        raise SystemExit(1) from exc

    # ── Safety middleware initialisation
    global _safety_middleware
    from cortex.safety.middleware import PipelineSafetyMiddleware, SafetySystemOfflineError
    try:
        _safety_middleware = PipelineSafetyMiddleware(db_conn=get_db())
    except SafetySystemOfflineError as exc:
        logger.critical("SAFETY SYSTEM OFFLINE — Atlas cannot start: %s", exc)
        raise SystemExit(1) from exc

    # Register integrity monitor as a background service
    _integrity_monitor = IntegrityMonitor(conn=get_db(), interval_minutes=60)
    async def _start_integrity():
        import asyncio
        asyncio.create_task(_integrity_monitor.start())
    register_service("integrity-monitor", _start_integrity, _integrity_monitor.stop)

    # Register mDNS announcer as a lifecycle service
    register_service("mDNS", _server_announcer.start, _server_announcer.stop)

    # Initialize memory system (HOT recall + COLD write queue)
    from cortex.memory import MemorySystem, set_memory_system
    _memory = MemorySystem(conn=get_db())
    set_memory_system(_memory)
    register_service("memory", _memory.start, _memory.stop)

    # Register filler cache warm-up as non-blocking background task
    async def _warm_filler_cache() -> None:
        try:
            from cortex.content.jokes import _migrate_flat_cache
            _migrate_flat_cache()
        except Exception as e:
            logger.warning("TTS cache migration failed: %s", e)
        try:
            from cortex.filler.cache import get_filler_cache
            await get_filler_cache().initialize()
        except Exception as e:
            logger.warning("Filler cache init failed (will use live TTS): %s", e)

    register_startup_task("filler-cache", _warm_filler_cache)

    # Register knowledge sync as background service (syncs WebDAV/CalDAV)
    _knowledge_sync = None
    try:
        from cortex.integrations.knowledge.scheduler import KnowledgeSyncScheduler
        _knowledge_sync = KnowledgeSyncScheduler(conn=get_db(), interval_minutes=60)
        register_service("knowledge-sync", _knowledge_sync.start, _knowledge_sync.stop)
    except Exception as e:
        logger.debug("Knowledge sync not available: %s", e)

    # ── Plugin loader ────────────────────────────────────────────
    from cortex.plugins.loader import load_plugins
    try:
        loaded = await load_plugins()
        logger.info("Loaded %d plugins: %s", len(loaded), loaded)
    except Exception as exc:
        logger.warning("Plugin loading failed (non-fatal): %s", exc)

    # Register nightly evolution as background startup task
    async def _run_nightly_evolution() -> None:
        try:
            from cortex.learning import NightlyEvolution
            evo = NightlyEvolution(conn=get_db())
            result = await evo.run()
            logger.info("Nightly evolution: %s", result)
        except Exception as e:
            logger.debug("Nightly evolution failed (non-fatal): %s", e)

    register_startup_task("nightly-evolution", _run_nightly_evolution)

    await start_all()
    yield
    await stop_all()


# ──────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────

app = FastAPI(title="Atlas Cortex", version="1.0.0", lifespan=lifespan)

# CORS for admin SPA dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount admin API router
app.include_router(admin_router)

# Mount satellite WebSocket endpoint
app.add_api_websocket_route("/ws/satellite", satellite_ws_handler)

# Mount avatar display WebSocket endpoint
app.add_api_websocket_route("/ws/avatar", avatar_ws_handler)


# ──────────────────────────────────────────────────────────────────
# Browser chat WebSocket
# ──────────────────────────────────────────────────────────────────

async def chat_ws_handler(websocket: WebSocket) -> None:
    """Browser chat WebSocket — streams pipeline responses and voice I/O."""
    await websocket.accept()
    audio_buffer = bytearray()

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "chat")

            # ── Voice input: STT ─────────────────────────────────
            if msg_type == "audio_start":
                audio_buffer.clear()
                continue

            if msg_type == "audio_data":
                try:
                    audio_buffer.extend(base64.b64decode(data.get("data", "")))
                except Exception:
                    pass
                continue

            if msg_type == "audio_end":
                transcript = ""
                try:
                    from cortex.speech.stt import transcribe, is_hallucinated
                    sample_rate = data.get("sample_rate", 16000)
                    transcript = await transcribe(
                        bytes(audio_buffer), sample_rate=sample_rate,
                    )
                    if is_hallucinated(transcript):
                        transcript = ""
                except Exception as exc:
                    logger.warning("STT failed: %s", exc)
                audio_buffer.clear()
                await websocket.send_json({
                    "type": "transcript",
                    "text": transcript,
                })
                continue

            # ── Voice output: TTS ────────────────────────────────
            if msg_type == "tts_request":
                text = data.get("text", "")
                if not text:
                    continue
                try:
                    from cortex.speech.tts import synthesize_speech
                    voice = data.get("voice", "af_bella")
                    pcm, sample_rate, provider_name = await synthesize_speech(
                        text, voice,
                    )
                    if pcm:
                        await websocket.send_json({
                            "type": "tts_audio",
                            "data": base64.b64encode(pcm).decode(),
                            "sample_rate": sample_rate,
                            "provider": provider_name,
                        })
                    else:
                        await websocket.send_json({
                            "type": "tts_error",
                            "text": "No TTS provider available",
                        })
                except Exception as exc:
                    logger.warning("TTS failed: %s", exc)
                    await websocket.send_json({
                        "type": "tts_error",
                        "text": str(exc),
                    })
                continue

            # ── Text chat (default) ──────────────────────────────
            message = data.get("message", "")
            user_id = data.get("user_id", "web_user")
            history = data.get("history", [])

            if not message:
                await websocket.send_json({"type": "error", "text": "Empty message"})
                continue

            provider = _get_provider()
            db_conn = _get_db()

            await websocket.send_json({"type": "start"})

            full_response = ""
            async for chunk in run_pipeline(
                message=message,
                provider=provider,
                user_id=user_id,
                conversation_history=history,
                db_conn=db_conn,
            ):
                full_response += chunk
                await websocket.send_json({"type": "token", "text": chunk})

            await websocket.send_json({
                "type": "end",
                "full_text": full_response,
            })
    except WebSocketDisconnect:
        logger.info("Chat WebSocket client disconnected")
    except Exception:
        logger.exception("Chat WebSocket error")


app.add_api_websocket_route("/ws/chat", chat_ws_handler)

# Wire barge-in: satellite → avatar PLAYBACK_STOP
async def _on_barge_in(satellite_id: str, room: str | None) -> None:
    if room:
        await broadcast_playback_stop(room)

on_barge_in(_on_barge_in)

_provider = None
_db_conn = None


def _get_provider():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


def _get_db():
    global _db_conn
    if _db_conn is None:
        init_db()
        _db_conn = get_db()
    return _db_conn


# ──────────────────────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "atlas-cortex"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int | None = None
    # Atlas-specific extensions
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    provider = _get_provider()
    healthy = await provider.health()
    return {"status": "ok" if healthy else "degraded", "provider_healthy": healthy}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": "atlas-cortex",
                "object": "model",
                "created": 1700000000,
                "owned_by": "atlas-cortex",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw: Request):
    # Extract conversation history and the latest user message
    messages = request.messages
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Last user message is the current query
    user_message = ""
    history: list[dict[str, str]] = []
    for msg in messages:
        if msg.role == "user":
            user_message = msg.content
        history.append({"role": msg.role, "content": msg.content})

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")

    # History excludes the final user turn (it's passed separately)
    conversation_history = history[:-1]

    provider = _get_provider()
    db_conn = _get_db()

    user_id = request.user or request.metadata.get("user_id", "default")
    metadata = request.metadata

    # ── Input guardrails (Principle II, IV — safety before pipeline) ──
    from cortex.safety import Severity
    if _safety_middleware is not None:
        input_result = _safety_middleware.check_input(user_message, user_id, metadata)
        if input_result.severity >= Severity.SOFT_BLOCK:
            blocked_text = input_result.suggested_response or (
                "I'm not able to help with that. Is there something else I can help you with?"
            )
            if request.stream:
                async def _blocked_stream():
                    yield blocked_text
                return StreamingResponse(
                    _sse_stream(_blocked_stream(), request.model),
                    media_type="text/event-stream",
                )
            else:
                return _json_response(blocked_text, request.model)

    # ── Build safety-aware system prompt for Layer 3 ──
    system_prompt = ""
    if _safety_middleware is not None:
        system_prompt = _safety_middleware.build_system_prompt(user_id, metadata)

    pipeline = await run_pipeline(
        message=user_message,
        provider=provider,
        user_id=user_id,
        conversation_history=conversation_history,
        metadata=metadata,
        db_conn=db_conn,
        system_prompt=system_prompt,
    )

    if request.stream:
        return StreamingResponse(
            _sse_stream(pipeline, request.model),
            media_type="text/event-stream",
        )
    else:
        full = "".join([chunk async for chunk in pipeline])
        # ── Output guardrails (Principle II, IV — safety after LLM) ──
        if _safety_middleware is not None:
            output_result = _safety_middleware.check_output(
                full, user_id, user_message, system_prompt, metadata,
            )
            if output_result.severity >= Severity.SOFT_BLOCK:
                full = output_result.suggested_response or (
                    "I'm not able to provide that kind of content. "
                    "Is there something else I can help you with?"
                )
        return _json_response(full, request.model)


async def _sse_stream(
    gen: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """Wrap token stream as Server-Sent Events (OpenAI SSE format)."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async for chunk in gen:
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(data)}\n\n"

    # Final chunk
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


def _json_response(content: str, model: str) -> JSONResponse:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


# ──────────────────────────────────────────────────────────────────
# Voice / TTS endpoints
# ──────────────────────────────────────────────────────────────────

class SpeechRequest(BaseModel):
    model: str = "orpheus"
    input: str
    voice: str | None = None
    speed: float = 1.0
    response_format: str = "wav"
    emotion: str | None = None


@app.get("/v1/audio/voices")
async def list_voices():
    provider = get_tts_provider()
    voices = provider.list_voices()
    if asyncio.iscoroutine(voices):
        voices = await voices
    return {"voices": voices}


@app.post("/v1/audio/speech")
async def create_speech(req: SpeechRequest):
    try:
        provider = get_tts_provider(req.model)
    except (ValueError, KeyError):
        provider = get_tts_provider()

    # Prepend emotion tag for tag-based providers
    text = req.input
    if req.emotion and provider.get_emotion_format() == "tags":
        text = f"{req.emotion}: {text}"

    async def _stream():
        async for chunk in provider.synthesize(
            text=text,
            voice=req.voice,
            emotion=req.emotion,
            speed=req.speed,
            stream=True,
        ):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="audio/wav",
        headers={"Transfer-Encoding": "chunked"},
    )


# ──────────────────────────────────────────────────────────────────
# Admin SPA static files
# ──────────────────────────────────────────────────────────────────

_ADMIN_DIST = Path(__file__).resolve().parent.parent / "admin" / "dist"

if _ADMIN_DIST.is_dir():
    app.mount("/admin", StaticFiles(directory=str(_ADMIN_DIST), html=True), name="admin-spa")


# ──────────────────────────────────────────────────────────────────
# Avatar skin file serving
# ──────────────────────────────────────────────────────────────────

@app.get("/api/avatar/config")
async def get_avatar_config_endpoint(user: str = ""):
    """Return resolved feature flags for the avatar page."""
    from cortex.avatar.flags import get_avatar_config
    return get_avatar_config(user)


@app.get("/avatar/skin/{skin_id}.svg")
async def serve_avatar_skin(skin_id: str):
    """Serve an avatar skin SVG file by skin ID."""
    from cortex.db import get_db, init_db
    init_db()
    conn = get_db()
    row = conn.execute("SELECT path FROM avatar_skins WHERE id = ?", (skin_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Skin not found")
    skin_path = Path(row[0])
    if not skin_path.is_absolute():
        skin_path = Path(__file__).resolve().parent.parent / skin_path
    if not skin_path.is_file():
        raise HTTPException(status_code=404, detail="Skin file not found")
    return FileResponse(skin_path, media_type="image/svg+xml")


@app.get("/avatar")
async def serve_avatar_display():
    """Serve the fullscreen avatar display page."""
    _display_html = Path(__file__).resolve().parent / "avatar" / "display.html"
    return FileResponse(
        _display_html,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/avatar/web-satellite.js")
async def serve_web_satellite_js():
    """Serve the web satellite overlay script."""
    _js = Path(__file__).resolve().parent / "avatar" / "web-satellite.js"
    return FileResponse(
        _js,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    host = os.environ.get("CORTEX_HOST", "0.0.0.0")
    port = int(os.environ.get("CORTEX_PORT", "5100"))
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Atlas Cortex server on %s:%d", host, port)
    uvicorn.run("cortex.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
