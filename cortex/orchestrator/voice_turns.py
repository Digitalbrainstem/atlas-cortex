"""Bounded early STT admission and durable per-device voice delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
import weakref
from dataclasses import dataclass
from typing import Any

from cortex.orchestrator.conversation import ConversationCoordinator, GenerationOptions
from cortex.orchestrator.turn_store import TurnStore
from cortex.orchestrator.interrupt import classify_interrupt
from cortex.pipeline import prepare_turn
from cortex.pipeline.turns import DispatchResult, RouteStatus, TurnIdentity
from cortex.safety import Severity

logger = logging.getLogger(__name__)
MAX_CAPTURE_BYTES = 480000
MAX_CHUNK_BYTES = 16384
_services: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, VoiceService] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class Capture:
    audio: bytes
    capture_id: str
    activated: bool
    reason: str


def get_voice_service() -> VoiceService | None:
    return _services.get(asyncio.get_running_loop())


class VoiceService:
    def __init__(self, store: TurnStore, conversation: ConversationCoordinator) -> None:
        self.store = store
        self.conversation = conversation
        active = int(os.environ.get("CORTEX_STT_MAX_ACTIVE", "2"))
        self.max_audio_bytes = int(os.environ.get("CORTEX_STT_AUDIO_BUDGET_BYTES", "4194304"))
        if active < 1 or self.max_audio_bytes < MAX_CAPTURE_BYTES:
            raise ValueError("STT limits must be positive and hold at least one complete capture")
        self.slots = asyncio.Semaphore(active)
        self.audio_bytes = 0
        self.sessions: set[VoiceSession] = set()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if loop in _services and _services[loop] is not self:
            raise RuntimeError("A voice service already owns this loop")
        _services[loop] = self

    async def stop(self) -> None:
        for session in list(self.sessions):
            await session.close()
        if get_voice_service() is self:
            del _services[asyncio.get_running_loop()]

    def session(self, conn: Any) -> VoiceSession:
        current = getattr(conn, "voice_session", None)
        if current is None:
            current = VoiceSession(self, conn)
            conn.voice_session = current
            self.sessions.add(current)
        return current


class VoiceSession:
    def __init__(self, service: VoiceService, conn: Any) -> None:
        self.service = service
        self.conn = conn
        self.captures: asyncio.Queue[Capture] = asyncio.Queue(maxsize=2)
        self.ingestion = asyncio.create_task(self._ingest(), name=f"stt-{conn.satellite_id}")
        self.delivery = asyncio.create_task(self._deliver(), name=f"delivery-{conn.satellite_id}")
        self.played = asyncio.Event()
        self.playback_id: str | None = None
        self.current_turn: tuple[str, str, int] | None = None
        self.closed = False
        self.paused_turn: tuple[str, str, int] | None = None
        self.paused_at = 0.0
        self.ack_position: int | None = None

    async def submit(self, capture: Capture) -> None:
        if self.closed or self.ingestion.done() or self.delivery.done():
            raise RuntimeError("Voice connection is unavailable; please reconnect")
        if self.captures.full() or self.service.audio_bytes + len(capture.audio) > self.service.max_audio_bytes:
            logger.warning("STT audio budget reached for %s", self.conn.satellite_id)
            await self.conn.send({"type": "PIPELINE_ERROR", "code": "audio_capacity",
                                  "detail": "Speech was not accepted. Please repeat after processing."})
            return
        self.service.audio_bytes += len(capture.audio)
        self.captures.put_nowait(capture)

    async def close(self) -> None:
        self.closed = True
        tasks = (self.ingestion, self.delivery)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        while not self.captures.empty():
            capture = self.captures.get_nowait()
            self.service.audio_bytes -= len(capture.audio)
            self.captures.task_done()
        self.service.sessions.discard(self)

    async def _ingest(self) -> None:
        from cortex.orchestrator import voice
        from cortex.wake_word import check_wake_word, filter_transcript

        while True:
            capture = await self.captures.get()
            try:
                async with self.service.slots:
                    transcript = (await voice.transcribe(capture.audio, sample_rate=16000)).strip()
                if not transcript or voice.is_hallucinated(transcript):
                    raise ValueError("No clear speech detected")
                if capture.activated:
                    _, transcript = check_wake_word(transcript)
                else:
                    transcript = filter_transcript(transcript)
                if not transcript:
                    raise ValueError("No activated speech detected")
                # Acoustic boundaries are not semantic boundaries. Split only
                # explicit question punctuation after the full capture is transcribed.
                questions = [part.strip() for part in re.split(r"(?<=\?)\s+(?=\S)", transcript) if part.strip()]
                for index, question in enumerate(questions):
                    await self._accept(question, capture, index)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Voice admission failed for %s", self.conn.satellite_id)
                await self.conn.send({"type": "PIPELINE_ERROR", "code": "admission_failed", "detail": str(exc)})
            finally:
                self.service.audio_bytes -= len(capture.audio)
                self.captures.task_done()
                del capture

    async def _accept(self, transcript: str, capture: Capture, index: int) -> None:
        store, coordinator = self.service.store, self.service.conversation
        user = getattr(self.conn, "user_id", "anonymous")
        identity = TurnIdentity(
            f"voice:{self.conn.satellite_id}:{capture.capture_id}:{index}",
            f"voice:{self.conn.satellite_id}", user,
            source="satellite", device_id=self.conn.satellite_id, room=getattr(self.conn, "room", None),
        )
        metadata = {
            "source": "satellite", "turn_id": identity.turn_id,
            "conversation_id": identity.conversation_id,
        }
        if identity.room is not None:
            metadata["room"] = identity.room
        checked = coordinator.safety.check_input(transcript, user, metadata)
        if checked.severity >= Severity.SOFT_BLOCK:
            raise ValueError(checked.suggested_response or "Speech was blocked by household policy")
        existing = store.find_existing(identity)
        if existing is not None:
            if (existing.transcript != transcript or existing.identity.source != identity.source
                    or existing.identity.device_id != identity.device_id or existing.identity.room != identity.room):
                raise ValueError("Capture identity was reused with different speech")
            await self.conn.send({"type": "TURN_ACCEPTED", "turn_id": identity.turn_id,
                                  "revision": existing.identity.revision, "sequence": existing.sequence})
            return
        turn = store.accept(identity, transcript, input_validated=True, context={"voice_ingress": True})
        owner = f"voice-prepare:{identity.turn_id}"
        store.claim(identity.turn_id, user, owner)
        store.begin_action(identity.turn_id, user, owner, 1)
        try:
            prepared = None
            control = await self._control(transcript, identity) if capture.reason != "capture_limit" else None
            if capture.reason != "capture_limit" and control is None:
                prepared = await prepare_turn(
                    transcript, user_id=user, satellite_id=self.conn.satellite_id, metadata=metadata,
                    db_conn=store.conn,
                )
            store.finish_voice_preparation(identity, owner)
            if prepared is not None and prepared.dispatch.status == RouteStatus.NOT_MATCHED:
                coordinator.admit_prepared(prepared, GenerationOptions(
                    os.environ.get("MODEL_FAST", "qwen2.5:14b"),
                    os.environ.get("MODEL_THINKING", "qwen3:30b-a3b"),
                ))
            else:
                result = (prepared.dispatch if prepared is not None else control or DispatchResult(
                    RouteStatus.CLARIFICATION, "The recording limit was reached. Please split your request.",
                ))
                store.accept(identity, transcript, input_validated=True, promote_accepted=True,
                             context=prepared.context_snapshot() if prepared else {
                                 "metadata": metadata, "voice_control": control is not None,
                             })
                store.claim(identity.turn_id, user, owner)
                response = coordinator._validated_output(result.response or "", turn, "")
                store.complete(identity.turn_id, user, owner, 1,
                               DispatchResult(result.status, response, layer=result.layer,
                                              action_outcome=result.action_outcome),
                               output_validated=True)
        except BaseException:
            current = store.get(identity.turn_id, user)
            if current.lease_owner == owner:
                store.fail(identity.turn_id, user, owner, 1, "voice_preparation_failed")
            raise
        await self.conn.send({"type": "TURN_ACCEPTED", "turn_id": identity.turn_id,
                              "revision": 1, "sequence": turn.sequence})

    async def pause(self, msg: dict[str, Any]) -> None:
        target = self.current_turn
        if target is None:
            return
        if msg.get("playback_id") is not None and (
            msg.get("playback_id") != self.playback_id or msg.get("turn_id") != target[0]
            or msg.get("revision") != target[2]
        ):
            logger.warning("Ignored stale barge-in for %s", self.conn.satellite_id)
            return
        turn = self.service.store.get(target[0], target[1])
        self.service.store.set_delivery(*target, "paused", position=turn.playback_position)
        self.paused_turn = target
        self.paused_at = time.monotonic()
        playback_id = self.playback_id
        self.delivery.cancel()
        await asyncio.gather(self.delivery, return_exceptions=True)
        self.delivery = asyncio.create_task(self._deliver(), name=f"delivery-{self.conn.satellite_id}")
        await self.conn.send({"type": "COMMAND", "action": "stop",
                              "params": {"playback_id": playback_id, "turn_id": target[0], "revision": target[2]}})

    async def _control(self, text: str, identity: TurnIdentity) -> DispatchResult | None:
        intent = classify_interrupt(text)
        store = self.service.store
        targets = store.voice_pending_targets(self.conn.satellite_id, identity.user_id, identity.turn_id)
        if intent == "stop":
            if re.fullmatch(r"(?:never\s*mind|forget it|cancel)[.!?,]*", text.strip(), re.IGNORECASE) and len(targets) > 1:
                return DispatchResult(RouteStatus.CLARIFICATION, "Which question should I cancel? Please name it.")
            target = next((turn for turn in targets if self.paused_turn and turn.identity.turn_id == self.paused_turn[0]),
                          targets[0] if targets else None)
            if target is not None:
                key = (target.identity.turn_id, target.identity.user_id, target.identity.revision)
                if self.current_turn == key:
                    await self.pause({})
                current = store.get(key[0], key[1])
                if current.output_validated:
                    store.interrupt_delivery(*key)
                else:
                    self.service.conversation.cancel(*key)
                self.paused_turn = None
            return DispatchResult(RouteStatus.HANDLED, "Stopped.")
        if intent == "resume" and (self.paused_turn or any(
            turn.delivery_state == "awaiting_confirmation" for turn in targets
        )):
            target = next((turn for turn in targets if self.paused_turn and turn.identity.turn_id == self.paused_turn[0]),
                          targets[0] if len(targets) == 1 else None)
            if target is None:
                return DispatchResult(RouteStatus.CLARIFICATION, "Which response should I resume?")
            stale = not self.paused_turn or time.monotonic() - self.paused_at > 30
            if stale and text.strip().lower().rstrip(".!?,") != "resume the previous response":
                return DispatchResult(RouteStatus.CLARIFICATION,
                                      "Say 'resume the previous response' to confirm playback of the old response.")
            store.resume_delivery(target.identity.turn_id, target.identity.user_id, target.identity.revision)
            self.paused_turn = None
            return DispatchResult(RouteStatus.HANDLED, "Continuing from the last confirmed sentence.")
        if intent == "pivot" and self.paused_turn:
            store.interrupt_delivery(*self.paused_turn)
            self.paused_turn = None
        return None

    async def _deliver(self) -> None:
        try:
            await self._delivery_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voice delivery service failed for %s", self.conn.satellite_id)
            await self.conn.send({"type": "PIPELINE_ERROR", "code": "delivery_unavailable",
                                  "detail": "Voice delivery is unavailable. Please reconnect."})

    async def _delivery_loop(self) -> None:
        from cortex.orchestrator import voice
        store = self.service.store
        while True:
            turn = store.voice_delivery_head(self.conn.satellite_id)
            if turn is None or turn.delivery_state != "ready" or not turn.output_validated:
                await asyncio.sleep(0.05)
                continue
            identity = turn.identity
            self.current_turn = (identity.turn_id, identity.user_id, identity.revision)
            try:
                store.set_delivery(*self.current_turn, "delivering")
                suffix = turn.result[turn.playback_position:]
                cursor = turn.playback_position
                for sentence in re.split(r"(?<=[.!?])(?=\s)", suffix):
                    cursor += len(sentence)
                    if not sentence.strip():
                        continue
                    if len(sentence) > 2048:
                        raise ValueError("A voice sentence exceeds the delivery allocation")
                    self.playback_id = uuid.uuid4().hex
                    self.conn.delivery_metadata = {
                        "turn_id": identity.turn_id, "revision": identity.revision,
                        "playback_id": self.playback_id,
                    }
                    self.played.clear()
                    self.ack_position = None
                    audio, rate, provider = await voice.synthesize_speech(
                        sentence.strip(), voice.resolve_voice(satellite_id=self.conn.satellite_id),
                    )
                    if not audio or len(audio) > 4 * 1024 * 1024:
                        raise RuntimeError("Speech audio is empty or exceeds the delivery allocation")
                    self.ack_position = cursor
                    await voice._stream_audio_to_satellite(
                        self.conn, audio, rate, sentence.strip(),
                        auto_listen=cursor == len(turn.result) and voice.should_auto_listen(turn.result),
                    )
                    if "playback_ack" not in getattr(self.conn, "capabilities", []):
                        raise RuntimeError("Device cannot confirm playback; delivery remains unconfirmed")
                    await asyncio.wait_for(self.played.wait(), timeout=max(30, len(audio) / (rate * 2) + 15))
                store.set_delivery(*self.current_turn, "delivered", position=len(turn.result))
            except asyncio.CancelledError:
                if store.get(identity.turn_id, identity.user_id).delivery_state == "delivering":
                    store.set_delivery(*self.current_turn, "interrupted")
                raise
            except Exception:
                logger.exception("Voice delivery failed for %s", self.conn.satellite_id)
                if store.get(identity.turn_id, identity.user_id).delivery_state == "delivering":
                    store.set_delivery(*self.current_turn, "interrupted")
                await self.conn.send({"type": "PIPELINE_ERROR", "detail": "Playback was not confirmed."})
            finally:
                self.current_turn = None
                self.playback_id = None
                self.ack_position = None
                self.conn.delivery_metadata = {}

    def acknowledge(self, msg: dict[str, Any]) -> None:
        if (self.current_turn and msg.get("playback_id") == self.playback_id
                and msg.get("turn_id") == self.current_turn[0]
                and type(msg.get("revision")) is int
                and msg.get("revision") == self.current_turn[2]
                and self.ack_position is not None and not self.played.is_set()):
            self.service.store.checkpoint_playback(*self.current_turn, self.ack_position)
            self.played.set()
