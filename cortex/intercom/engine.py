"""Intercom engine — announce, broadcast, two-way calling, drop-in.

Coordinates TTS synthesis and satellite WebSocket delivery for all
intercom actions.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db
from cortex.intercom.zones import ZoneManager

logger = logging.getLogger(__name__)


class IntercomEngine:
    """Central intercom controller."""

    def __init__(self) -> None:
        self.zone_manager = ZoneManager()
        self._active_calls: dict[int, dict] = {}

    # ── Announce / Broadcast ─────────────────────────────────────

    async def announce(
        self,
        message: str,
        room: str,
        user_id: str = "",
        priority: str = "normal",
    ) -> bool:
        """Send a TTS message to a specific room's satellite."""
        sat = self._get_satellite_for_room(room)
        if sat is None:
            logger.warning("No satellite found for room '%s'", room)
            return False

        ok = await self._send_tts(sat, message, priority)
        self._log_action("announce", room, room, message, user_id)
        return ok

    async def broadcast(
        self,
        message: str,
        user_id: str = "",
        priority: str = "normal",
    ) -> int:
        """Send a TTS message to ALL connected satellites. Returns count reached."""
        from cortex.satellite.websocket import get_connected_satellites

        satellites = get_connected_satellites()
        if not satellites:
            logger.info("Broadcast requested but no satellites connected")
            self._log_action("broadcast", "", "all", message, user_id)
            return 0

        count = 0
        tasks = [
            self._send_tts(conn, message, priority)
            for conn in satellites.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if r is True:
                count += 1

        self._log_action("broadcast", "", "all", message, user_id)
        return count

    async def zone_broadcast(
        self,
        message: str,
        zone_name: str,
        user_id: str = "",
        priority: str = "normal",
    ) -> int:
        """Send a TTS message to all satellites in a zone."""
        from cortex.satellite.websocket import get_connection

        sat_ids = await self.zone_manager.get_satellites_for_zone(zone_name)
        if not sat_ids:
            logger.info("Zone '%s' has no satellites", zone_name)
            self._log_action("broadcast", "", zone_name, message, user_id)
            return 0

        count = 0
        tasks = []
        for sid in sat_ids:
            conn = get_connection(sid)
            if conn is not None:
                tasks.append(self._send_tts(conn, message, priority))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if r is True:
                    count += 1

        self._log_action("broadcast", "", zone_name, message, user_id)
        return count

    # ── Two-way calling ──────────────────────────────────────────

    async def start_call(self, source_room: str, target_room: str) -> int:
        """Start a two-way audio call between two satellites.

        Returns the call_id for tracking.
        """
        caller = self._get_satellite_for_room(source_room)
        callee = self._get_satellite_for_room(target_room)
        if caller is None or callee is None:
            raise ValueError(
                f"Cannot establish call: missing satellite for "
                f"{'source' if caller is None else 'target'} room"
            )
        if caller.satellite_id == callee.satellite_id:
            raise ValueError("Cannot call the same satellite")

        db = get_db()
        cur = db.execute(
            "INSERT INTO active_calls (caller_satellite, callee_satellite, status) "
            "VALUES (?, ?, 'ringing')",
            (caller.satellite_id, callee.satellite_id),
        )
        db.commit()
        call_id: int = cur.lastrowid  # type: ignore[assignment]

        # Notify both satellites about the call
        await caller.send_command("call_start", {
            "call_id": call_id,
            "role": "caller",
            "peer": callee.satellite_id,
        })
        await callee.send_command("call_start", {
            "call_id": call_id,
            "role": "callee",
            "peer": caller.satellite_id,
        })

        # Mark active
        db.execute(
            "UPDATE active_calls SET status = 'active' WHERE id = ?",
            (call_id,),
        )
        db.commit()

        self._active_calls[call_id] = {
            "caller": caller.satellite_id,
            "callee": callee.satellite_id,
            "started_at": time.time(),
        }

        self._log_action("call", source_room, target_room, "", "")
        return call_id

    async def end_call(self, call_id: int) -> bool:
        """End an active call."""
        call = self._active_calls.pop(call_id, None)
        if call is None:
            # Try DB lookup for calls we might not have in memory
            db = get_db()
            row = db.execute(
                "SELECT * FROM active_calls WHERE id = ? AND status != 'ended'",
                (call_id,),
            ).fetchone()
            if row is None:
                return False
            call = {
                "caller": row["caller_satellite"],
                "callee": row["callee_satellite"],
            }

        # Notify both satellites to end
        from cortex.satellite.websocket import get_connection

        for sat_id in (call["caller"], call["callee"]):
            conn = get_connection(sat_id)
            if conn is not None:
                try:
                    await conn.send_command("call_end", {"call_id": call_id})
                except Exception:
                    logger.debug("Failed to notify %s of call end", sat_id)

        db = get_db()
        db.execute(
            "UPDATE active_calls SET status = 'ended', ended_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (call_id,),
        )
        db.commit()

        self._log_action("call", "", "", f"ended call {call_id}", "")
        return True

    # ── Drop-in ──────────────────────────────────────────────────

    async def start_drop_in(self, target_room: str, listener_room: str) -> int:
        """Start one-way audio monitoring.

        Sends an LED indicator to the monitored satellite for
        transparency (the occupant knows they are being listened to).
        Returns the call_id.
        """
        target = self._get_satellite_for_room(target_room)
        listener = self._get_satellite_for_room(listener_room)
        if target is None or listener is None:
            raise ValueError("Cannot establish drop-in: missing satellite")

        db = get_db()
        cur = db.execute(
            "INSERT INTO active_calls (caller_satellite, callee_satellite, status) "
            "VALUES (?, ?, 'active')",
            (listener.satellite_id, target.satellite_id),
        )
        db.commit()
        call_id: int = cur.lastrowid  # type: ignore[assignment]

        # LED indicator on monitored satellite for transparency
        await target.send_command("led_indicator", {
            "pattern": "drop_in_active",
            "color": "amber",
        })
        await listener.send_command("drop_in_start", {
            "call_id": call_id,
            "target": target.satellite_id,
        })

        self._active_calls[call_id] = {
            "caller": listener.satellite_id,
            "callee": target.satellite_id,
            "started_at": time.time(),
            "type": "drop_in",
        }

        self._log_action("drop_in", listener_room, target_room, "", "")
        return call_id

    # ── Query ────────────────────────────────────────────────────

    async def get_active_calls(self) -> list[dict]:
        """List active calls / drop-ins."""
        db = get_db()
        rows = db.execute(
            "SELECT id, caller_satellite, callee_satellite, status, started_at "
            "FROM active_calls WHERE status != 'ended' ORDER BY id"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "caller_satellite": r["caller_satellite"],
                "callee_satellite": r["callee_satellite"],
                "status": r["status"],
                "started_at": r["started_at"],
            }
            for r in rows
        ]

    # ── Internals ────────────────────────────────────────────────

    async def _send_tts(self, sat: Any, message: str, priority: str) -> bool:
        """Generate TTS audio and send it to a satellite."""
        try:
            from cortex.speech.tts import synthesize_speech

            pcm, sample_rate, provider = await synthesize_speech(
                message, voice="", fast=True
            )
            audio_b64 = base64.b64encode(pcm).decode()
            await sat.send({
                "type": "TTS_START",
                "session_id": sat.session_id or "",
                "priority": priority,
            })
            await sat.send({
                "type": "TTS_CHUNK",
                "audio": audio_b64,
                "sample_rate": sample_rate,
                "session_id": sat.session_id or "",
            })
            await sat.send({
                "type": "TTS_END",
                "session_id": sat.session_id or "",
            })
            return True
        except Exception:
            logger.debug(
                "TTS delivery failed for satellite %s",
                getattr(sat, "satellite_id", "?"),
                exc_info=True,
            )
            return False

    def _log_action(
        self, action: str, source: str, target: str, message: str, user_id: str
    ) -> None:
        """Log an intercom action to the database (best-effort)."""
        try:
            db = get_db()
            db.execute(
                "INSERT INTO intercom_log (action, source_room, target, message, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (action, source, target, message, user_id),
            )
            db.commit()
        except Exception:
            logger.debug("Failed to log intercom action", exc_info=True)

    def _get_satellite_for_room(self, room: str) -> Any:
        """Find the satellite connection for a room name."""
        try:
            from cortex.satellite.websocket import get_connected_satellites

            db = get_db()
            row = db.execute(
                "SELECT id FROM satellites WHERE room = ? AND status = 'online'",
                (room,),
            ).fetchone()
            if row is None:
                return None
            sat_id = row["id"]
            sats = get_connected_satellites()
            return sats.get(sat_id)
        except Exception:
            logger.debug("Failed to resolve satellite for room '%s'", room, exc_info=True)
            return None
