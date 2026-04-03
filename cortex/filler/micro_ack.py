"""Adaptive micro-acknowledgments during long LLM generations.

When the LLM is taking longer than usual to produce tokens, Atlas injects
brief "thinking" sounds — "Hmm...", "One sec...", "Still looking..." — to
feel alive.  Like how a human says "ummm" while thinking.

The threshold is adaptive: an exponential moving average (EMA) of recent LLM
response times is used so that the micro-ack fires at a natural fraction of
the expected wait, with random jitter to prevent a robotic cadence.

    threshold = ema_latency * 0.6 + random(0, ema_latency * 0.2)

Rules:
  - Never fire if the initial filler already covers the pause.
  - Max 2 micro-acks per generation (escalating tone).
  - Skip for instant answers (Layer 1) and short expected generations.
  - EMA is persisted to ``system_settings`` across restarts.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from cortex.filler.phrases import (
    MICRO_ACK_CASUAL,
    MICRO_ACK_REASSURING,
    MICRO_ACK_COMPLEX,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility and tests
POOL_CASUAL: list[str] = MICRO_ACK_CASUAL
POOL_REASSURING: list[str] = MICRO_ACK_REASSURING
POOL_COMPLEX: list[str] = MICRO_ACK_COMPLEX

# ── Settings keys for DB persistence ────────────────────────────

_SETTINGS_KEY_EMA = "micro_ack_ema_latency"
_DEFAULT_EMA = 2.0  # seconds
_DEFAULT_ALPHA = 0.3


@dataclass
class MicroAckEngine:
    """Adaptive micro-acknowledgments during long LLM generations.

    One instance is created per server process.  Call :meth:`reset` before
    each new generation, then call :meth:`should_ack` periodically during
    the streaming loop.
    """

    ema_latency: float = _DEFAULT_EMA
    alpha: float = _DEFAULT_ALPHA
    ack_count: int = 0
    max_acks: int = 2
    is_complex: bool = False
    _last_phrases: list[str] = field(default_factory=list)

    # ── EMA update ───────────────────────────────────────────────

    def update_latency(self, observed_latency: float) -> None:
        """Update the exponential moving average after each generation."""
        if observed_latency <= 0:
            return
        self.ema_latency = (
            self.alpha * observed_latency
            + (1 - self.alpha) * self.ema_latency
        )

    # ── Threshold calculation ────────────────────────────────────

    def get_threshold(self) -> float:
        """Return the next micro-ack threshold (seconds) with jitter."""
        base = self.ema_latency * 0.6
        jitter = random.uniform(0, self.ema_latency * 0.2)
        return base + jitter

    # ── Decision logic ───────────────────────────────────────────

    def should_ack(self, elapsed: float, filler_played: bool) -> bool:
        """Return *True* if a micro-ack should fire now.

        Args:
            elapsed: Seconds since the LLM generation started (or since
                     the last micro-ack was emitted).
            filler_played: Whether the initial filler phrase was played
                           for this generation.
        """
        if filler_played and self.ack_count == 0:
            # The initial filler covers the first pause — don't double up.
            return False
        if self.ack_count >= self.max_acks:
            return False
        return elapsed >= self.get_threshold()

    # ── Phrase selection ─────────────────────────────────────────

    def get_phrase(self) -> str:
        """Return the next micro-ack phrase based on escalation level.

        * 1st ack → casual pool (or complex pool if flagged).
        * 2nd ack → reassuring pool.
        """
        if self.ack_count == 0:
            pool = POOL_COMPLEX if self.is_complex else POOL_CASUAL
        else:
            pool = POOL_REASSURING

        candidates = [p for p in pool if p not in self._last_phrases]
        if not candidates:
            candidates = pool

        phrase = random.choice(candidates)
        self._last_phrases.append(phrase)
        # Keep dedup window small
        if len(self._last_phrases) > 4:
            self._last_phrases = self._last_phrases[-4:]
        self.ack_count += 1
        return phrase

    @property
    def level(self) -> int:
        """Current escalation level (1-indexed, before next ack)."""
        return min(self.ack_count + 1, 2)

    # ── Lifecycle ────────────────────────────────────────────────

    def reset(self, is_complex: bool = False) -> None:
        """Reset for a new generation."""
        self.ack_count = 0
        self.is_complex = is_complex

    # ── Persistence ──────────────────────────────────────────────

    def save_ema(self, conn: object | None) -> None:
        """Persist the current EMA to ``system_settings``."""
        if conn is None:
            return
        try:
            conn.execute(  # type: ignore[union-attr]
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (_SETTINGS_KEY_EMA, str(self.ema_latency)),
            )
            conn.commit()  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("Failed to persist micro-ack EMA: %s", exc)

    def load_ema(self, conn: object | None) -> None:
        """Restore the EMA from ``system_settings``."""
        if conn is None:
            return
        try:
            row = conn.execute(  # type: ignore[union-attr]
                "SELECT value FROM system_settings WHERE key = ?",
                (_SETTINGS_KEY_EMA,),
            ).fetchone()
            if row:
                self.ema_latency = float(row[0])
                logger.debug("Loaded micro-ack EMA: %.3fs", self.ema_latency)
        except Exception as exc:
            logger.debug("Failed to load micro-ack EMA: %s", exc)


# ── Module-level singleton ───────────────────────────────────────

_engine = MicroAckEngine()


def get_micro_ack_engine() -> MicroAckEngine:
    """Return the module-level :class:`MicroAckEngine` singleton."""
    return _engine
