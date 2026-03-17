"""Trigger manager for routines — schedule, HA event, and voice triggers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable

from cortex.db import get_db
from cortex.scheduling.alarms import cron_matches

logger = logging.getLogger(__name__)

# Strip punctuation for fuzzy voice matching
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


class TriggerManager:
    """Centralised trigger evaluation for routines.

    * **Schedule triggers** — cron-based, checked every 30 s in a background task.
    * **HA event triggers** — match entity state changes.
    * **Voice phrase triggers** — fuzzy match spoken phrases.
    """

    _CHECK_INTERVAL = 30  # seconds between cron checks

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._callbacks: list[Callable[..., Any]] = []

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start background cron checker for schedule triggers."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._checker_loop())
        logger.info("TriggerManager started")

    async def stop(self) -> None:
        """Stop background checker."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TriggerManager stopped")

    def on_trigger(self, callback: Callable[..., Any]) -> None:
        """Register ``callback(routine_id)`` called when a schedule trigger fires."""
        self._callbacks.append(callback)

    # ── Voice triggers ───────────────────────────────────────────

    async def check_voice(self, phrase: str) -> int | None:
        """Check if *phrase* matches any voice trigger. Returns routine_id or ``None``."""
        conn = get_db()
        rows = conn.execute(
            "SELECT rt.routine_id, rt.trigger_config, r.enabled "
            "FROM routine_triggers rt "
            "JOIN routines r ON rt.routine_id = r.id "
            "WHERE rt.trigger_type = 'voice_phrase' AND rt.enabled = 1 AND r.enabled = 1",
        ).fetchall()

        phrase_clean = _normalise(phrase)
        if not phrase_clean:
            return None

        best_match: int | None = None
        best_score: float = 0.0

        for row in rows:
            try:
                config = json.loads(row["trigger_config"])
            except (json.JSONDecodeError, TypeError):
                continue

            trigger_phrase = _normalise(config.get("phrase", ""))
            if not trigger_phrase:
                continue

            # Exact match
            if phrase_clean == trigger_phrase:
                return row["routine_id"]

            # Partial / contains match — "good morning atlas" contains "good morning"
            if trigger_phrase in phrase_clean or phrase_clean in trigger_phrase:
                score = 0.85
                if score > best_score:
                    best_score = score
                    best_match = row["routine_id"]
                continue

            # Fuzzy match — require at least 0.75 similarity
            score = SequenceMatcher(None, phrase_clean, trigger_phrase).ratio()
            if score > best_score and score >= 0.75:
                best_score = score
                best_match = row["routine_id"]

        return best_match

    # ── HA event triggers ────────────────────────────────────────

    async def check_ha_event(
        self, entity_id: str, new_state: str, old_state: str
    ) -> list[int]:
        """Check HA state change against event triggers. Returns routine_ids to run."""
        conn = get_db()
        rows = conn.execute(
            "SELECT rt.routine_id, rt.trigger_config "
            "FROM routine_triggers rt "
            "JOIN routines r ON rt.routine_id = r.id "
            "WHERE rt.trigger_type = 'ha_event' AND rt.enabled = 1 AND r.enabled = 1",
        ).fetchall()

        matched: list[int] = []
        for row in rows:
            try:
                config = json.loads(row["trigger_config"])
            except (json.JSONDecodeError, TypeError):
                continue

            if config.get("entity_id") != entity_id:
                continue

            to_state = config.get("to_state")
            from_state = config.get("from_state")

            if to_state and to_state != new_state:
                continue
            if from_state and from_state != old_state:
                continue

            matched.append(row["routine_id"])

        return matched

    # ── Schedule triggers ────────────────────────────────────────

    async def check_schedule(self) -> list[int]:
        """Check for due schedule triggers. Returns routine_ids to run."""
        now = datetime.now(timezone.utc)
        conn = get_db()
        rows = conn.execute(
            "SELECT rt.id AS trigger_id, rt.routine_id, rt.trigger_config "
            "FROM routine_triggers rt "
            "JOIN routines r ON rt.routine_id = r.id "
            "WHERE rt.trigger_type = 'schedule' AND rt.enabled = 1 AND r.enabled = 1",
        ).fetchall()

        due: list[int] = []
        local_now = datetime.now()
        for row in rows:
            try:
                config = json.loads(row["trigger_config"])
            except (json.JSONDecodeError, TypeError):
                continue

            cron_expr = config.get("cron", "")
            if not cron_expr:
                continue

            if not cron_matches(cron_expr, local_now):
                continue

            # Prevent double-firing: check last_fired within the same minute
            last_fired = config.get("last_fired", "")
            if last_fired:
                try:
                    lf_dt = datetime.fromisoformat(last_fired)
                    if (
                        lf_dt.year == local_now.year
                        and lf_dt.month == local_now.month
                        and lf_dt.day == local_now.day
                        and lf_dt.hour == local_now.hour
                        and lf_dt.minute == local_now.minute
                    ):
                        continue  # already fired this minute
                except (ValueError, TypeError):
                    pass

            # Update last_fired in trigger_config
            config["last_fired"] = local_now.isoformat()
            conn.execute(
                "UPDATE routine_triggers SET trigger_config = ? WHERE id = ?",
                (json.dumps(config), row["trigger_id"]),
            )
            conn.commit()

            due.append(row["routine_id"])

        return due

    # ── Internal ─────────────────────────────────────────────────

    async def _checker_loop(self) -> None:
        while self._running:
            try:
                routine_ids = await self.check_schedule()
                for rid in routine_ids:
                    await self._fire(rid)
            except Exception:
                logger.exception("Schedule trigger checker error")
            try:
                await asyncio.sleep(self._CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

    async def _fire(self, routine_id: int) -> None:
        logger.info("Schedule trigger firing routine %d", routine_id)
        for cb in self._callbacks:
            try:
                result = cb(routine_id)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Trigger callback error for routine %d", routine_id)


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and extra whitespace."""
    return _PUNCT_RE.sub("", text.lower()).strip()
