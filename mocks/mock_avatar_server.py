"""Mock avatar WebSocket server for GPU-free development.

Simulates the ``/ws/avatar`` endpoint with realistic viseme/expression
frames when the mock pipeline produces a response.

Usage:
    Included automatically when running ``python -m mocks.run``.
    Connect a browser to ``ws://localhost:5100/ws/avatar?room=default``.

Standalone testing:
    python -m mocks.mock_avatar_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock Avatar WebSocket")

# Simple connected-clients tracker
_clients: list[WebSocket] = []

DEMO_VISEME_SEQUENCE = [
    {"viseme": "DD", "start_ms": 0, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "EH", "start_ms": 80, "duration_ms": 80, "intensity": 0.7},
    {"viseme": "SS", "start_ms": 160, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "DD", "start_ms": 240, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "IH", "start_ms": 320, "duration_ms": 80, "intensity": 0.7},
    {"viseme": "NN", "start_ms": 400, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "KK", "start_ms": 480, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "IDLE", "start_ms": 560, "duration_ms": 200, "intensity": 0.0},
    {"viseme": "AA", "start_ms": 760, "duration_ms": 80, "intensity": 0.7},
    {"viseme": "DD", "start_ms": 840, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "NN", "start_ms": 920, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "AA", "start_ms": 1000, "duration_ms": 80, "intensity": 0.7},
    {"viseme": "SS", "start_ms": 1080, "duration_ms": 80, "intensity": 0.4},
    {"viseme": "IDLE", "start_ms": 1160, "duration_ms": 200, "intensity": 0.0},
]


@app.websocket("/ws/avatar")
async def avatar_ws(ws: WebSocket):
    await ws.accept()
    room = ws.query_params.get("room", "default")
    logger.info("Mock avatar WS connect: room=%s", room)
    _clients.append(ws)

    try:
        # Send initial skin
        await ws.send_json({
            "type": "SKIN",
            "skin_id": "default",
            "skin_url": "/avatar/skin/default.svg",
            "skin_name": "Atlas Default",
        })
        await ws.send_json({"type": "EXPRESSION", "expression": "neutral", "intensity": 1.0})

        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "PING":
                await ws.send_json({"type": "PONG", "ts": time.time()})
            elif msg.get("type") == "DEMO":
                # Client can request a demo sequence
                await ws.send_json({"type": "SPEAKING_START", "skin_id": "default", "skin_url": "/avatar/skin/default.svg"})
                await ws.send_json({"type": "EXPRESSION", "expression": "happy", "intensity": 0.8})
                for frame in DEMO_VISEME_SEQUENCE:
                    await ws.send_json({"type": "VISEME", **frame})
                    await asyncio.sleep(frame["duration_ms"] / 1000.0)
                await ws.send_json({"type": "SPEAKING_END"})
                await ws.send_json({"type": "EXPRESSION", "expression": "neutral", "intensity": 1.0})
    except WebSocketDisconnect:
        logger.info("Mock avatar WS disconnect")
    except Exception:
        logger.exception("Mock avatar WS error")
    finally:
        _clients.remove(ws)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-avatar", "clients": len(_clients)}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=5101)
