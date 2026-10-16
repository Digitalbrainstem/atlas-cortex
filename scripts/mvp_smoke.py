"""Isolated production-server/mock-provider qualification; no household services used.

Run after building the UI: python3 -m scripts.mvp_smoke --browser
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import secrets
import tempfile
import wave

import httpx
import websockets

from mocks.conftest import _free_port, _MockServerRunner


async def browser_check(port: int) -> None:
    import numpy as np
    from playwright.async_api import async_playwright
    from cortex.server import app
    from cortex.db import get_db
    from cortex.speech.recognition import identify_audio

    coordinator = app.state.conversation_coordinator
    original = coordinator.provider.chat
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def blocked(**kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(**kwargs)
    coordinator.provider.chat = blocked
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            errors, sockets, closed = [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def opened(socket):
                if socket.url.endswith("/ws/chat"):
                    sockets.append(socket.url)
                    socket.on("close", lambda *args: closed.append(socket.url))
            page.on("websocket", opened)
            await page.goto(f"http://127.0.0.1:{port}/chat")
            await page.get_by_role("button", name="Guest").click()
            await page.get_by_role("status").filter(has_text="Connected").wait_for()
            await page.get_by_placeholder("Talk to Atlas...").fill("Explain the lifecycle of a synthetic garden.")
            await page.get_by_role("button", name="Send", exact=True).click()
            await asyncio.wait_for(entered.wait(), 10)
            pending = await page.evaluate("JSON.parse(sessionStorage.getItem('atlas-pending:guest'))")
            await page.reload()
            await page.get_by_role("status").filter(has_text="Connected").wait_for()
            release.set()
            await page.locator(".message.assistant .bubble").wait_for(timeout=30_000)
            await page.wait_for_function("sessionStorage.getItem('atlas-pending:guest') === null")
            assert await page.locator(".message.assistant .bubble").count() == 1
            assert coordinator.store.get(pending["turn_id"], "guest").delivery_state == "delivered"
            assert calls == 1
            coordinator.provider.chat = original
            await page.evaluate("document.querySelector('#app').__vue_app__.unmount()")
            await page.wait_for_timeout(3300)
            assert len(sockets) == len(closed) == 2
            print("PASS Chromium: pending reload, one answer/generation, receipt, unmount without reconnect")

            await page.goto(f"http://127.0.0.1:{port}/admin/#/login")
            await page.get_by_label("Username", exact=True).fill("admin")
            await page.get_by_label("Password", exact=True).fill("atlas-admin")
            await page.get_by_role("button", name="Sign In", exact=True).click()
            await page.wait_for_url("**/#/dashboard")
            db = get_db()
            db.execute("INSERT INTO user_profiles(user_id,display_name) VALUES('synthetic-speaker','Synthetic Speaker')")
            db.commit()
            samples = np.arange(16000 * 3) / 16000
            signal = sum(np.sin(2 * np.pi * hz * samples) / (i + 1)
                         for i, hz in enumerate((125, 250, 375, 500, 625, 750)))
            signal *= 0.4 + 0.6 * np.sin(2 * np.pi * 3 * samples) ** 2
            pcm = (signal / np.max(np.abs(signal)) * 16000).astype("<i2").tobytes()
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            await page.goto(f"http://127.0.0.1:{port}/admin/#/voice")
            await page.get_by_label("Household user ID").fill("synthetic-speaker")
            await page.get_by_label("Voice sample").set_input_files({
                "name": "synthetic.wav", "mimeType": "audio/wav", "buffer": output.getvalue(),
            })
            await page.get_by_role("button", name="Enroll voice", exact=True).click()
            await page.get_by_text("Voice enrolled.", exact=False).wait_for(timeout=30_000)
            user, speaker = await identify_audio(pcm, db)
            assert user == "synthetic-speaker" and speaker
            assert not errors, errors
            print("PASS Chromium: synthetic upload, PCM conversion, CPU embedding, persisted speaker match")
        finally:
            release.set()
            coordinator.provider.chat = original
            await browser.close()


async def main(*, browser: bool = False) -> None:
    llm_port, tts_port, stt_port, server_port = [_free_port() for _ in range(4)]
    with tempfile.TemporaryDirectory(prefix="atlas-mvp-smoke-") as directory:
        os.environ.update({
            "CORTEX_DATA_DIR": directory, "CORTEX_CONFIG_DIR": directory,
            "CORTEX_JWT_SECRET": secrets.token_urlsafe(32),
            "SNAPSHOT_DIR": f"{directory}/snapshots", "SNAPSHOT_NAS_ENABLED": "false",
            "LLM_PROVIDER": "openai_compatible", "LLM_URL": f"http://127.0.0.1:{llm_port}",
            "MODEL_FAST": "mock", "MODEL_THINKING": "mock", "TTS_PROVIDER": "kokoro",
            "KOKORO_HOST": "127.0.0.1", "KOKORO_PORT": str(tts_port),
            "STT_HOST": "127.0.0.1", "STT_PORT": str(stt_port),
            "CORTEX_TURN_MAX_ACTIVE": "1", "CORTEX_TURN_PREFETCH": "8",
        })
        mocks = [
            _MockServerRunner("mocks.mock_llm_server:app", llm_port),
            _MockServerRunner("mocks.mock_tts_server:app", tts_port),
            _MockServerRunner("mocks.mock_stt_server:app", stt_port),
        ]
        try:
            for runner in mocks:
                await runner.start()
            from mocks import mock_stt_server
            mock_stt_server._TRANSCRIPTIONS[:] = [{
                "text": "Explain photosynthesis for a household garden.", "utterance_ms": 0,
            }]
            for cycle in range(2):
                server = _MockServerRunner("cortex.server:app", server_port)
                try:
                    await server.start()
                    from cortex.server import app
                    from cortex.orchestrator.voice_turns import get_voice_service
                    service = get_voice_service()
                    assert service is app.state.voice_service
                    async with httpx.AsyncClient() as client:
                        assert (await client.get(f"http://127.0.0.1:{server_port}/live")).status_code == 200
                        assert (await client.get(f"http://127.0.0.1:{server_port}/ready")).status_code == 200
                        if cycle == 0:
                            await mocks[0].stop()
                            assert (await client.get(f"http://127.0.0.1:{server_port}/ready")).status_code == 503
                            assert (await client.get(f"http://127.0.0.1:{server_port}/live")).status_code == 200
                            mocks[0] = _MockServerRunner("mocks.mock_llm_server:app", llm_port)
                            await mocks[0].start()
                            assert (await client.get(f"http://127.0.0.1:{server_port}/ready")).status_code == 200
                            print("PASS liveness/readiness across provider outage and recovery")
                    if cycle == 0 and browser:
                        await browser_check(server_port)
                    async with websockets.connect(f"ws://127.0.0.1:{server_port}/ws/satellite") as socket:
                        await socket.send(json.dumps({
                            "type": "ANNOUNCE", "satellite_id": f"smoke-pi-{cycle}",
                            "room": "smoke-room", "capabilities": ["playback_ack"],
                        }))
                        assert json.loads(await socket.recv())["type"] == "ACCEPTED"
                        for frame in [
                            {"type": "WAKE"},
                            {"type": "AUDIO_START", "capture_id": f"capture-{cycle}"},
                            {"type": "AUDIO_CHUNK", "audio": base64.b64encode(b"\x01\x00" * 160).decode()},
                            {"type": "AUDIO_END"},
                        ]:
                            await socket.send(json.dumps(frame))
                        turn_id = None
                        deadline = asyncio.get_running_loop().time() + 60
                        while asyncio.get_running_loop().time() < deadline:
                            if turn_id and service.store.get(turn_id, "anonymous").delivery_state == "delivered":
                                break
                            try:
                                frame = json.loads(await asyncio.wait_for(socket.recv(), 0.25))
                            except TimeoutError:
                                continue
                            assert frame["type"] not in ("PIPELINE_ERROR", "ERROR"), frame
                            if frame["type"] == "TURN_ACCEPTED":
                                turn_id = frame["turn_id"]
                            if frame["type"] == "TTS_END":
                                await socket.send(json.dumps({
                                    **{key: frame[key] for key in ("turn_id", "revision", "playback_id")},
                                    "type": "PLAYBACK_ACK",
                                }))
                        assert turn_id is not None
                        turn = service.store.get(turn_id, "anonymous")
                        assert turn.output_validated and turn.delivery_state == "delivered"
                        assert turn.playback_position == len(turn.result)
                        print(f"PASS cycle {cycle}: WS -> HTTP STT -> durable LLM -> HTTP TTS -> simulated receipt")
                finally:
                    await server.stop()
                assert get_voice_service() is None
                assert not service.sessions and service.audio_bytes == 0
                print(f"PASS cycle {cycle}: lifecycle cleanup")
        finally:
            for runner in reversed(mocks):
                if runner._task is not None:
                    await runner.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    asyncio.run(main(browser=parser.parse_args().browser))
