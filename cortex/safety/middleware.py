"""Safety middleware — wires guardrails into the request pipeline.

THIS FILE IS FROZEN. Do not modify without explicit human approval.

This is the single integration point for all safety checks.  It is
imported by ``cortex/server.py`` and used on every request.

Design (from Principle IV — Architectural Integrity):
  The pipeline doesn't check *whether* guardrails are enabled.
  It assumes they exist.  If they don't, it crashes — not silently
  degrades.  The absence of safety is an error, not a configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from cortex.safety import (
    GuardrailResult,
    InputGuardrails,
    OutputGuardrails,
    Severity,
    build_safety_system_prompt,
    log_guardrail_event,
    resolve_content_tier,
    redact_pii,
)

logger = logging.getLogger(__name__)


class SafetySystemOfflineError(RuntimeError):
    """Raised when the safety middleware cannot initialise."""


class PipelineSafetyMiddleware:
    """Single entry-point for all safety checks in the pipeline.

    Usage::

        middleware = PipelineSafetyMiddleware(db_conn)

        # Before pipeline
        result = middleware.check_input(message, user_id, metadata)
        if result.severity >= Severity.SOFT_BLOCK:
            return result.suggested_response

        # Build system prompt for Layer 3
        system_prompt = middleware.build_system_prompt(user_id, metadata)

        # After pipeline (full response collected)
        result = middleware.check_output(response, user_id, message, system_prompt, metadata)
        if result.severity >= Severity.SOFT_BLOCK:
            response = result.suggested_response
    """

    def __init__(self, db_conn: Any) -> None:
        try:
            self._db_conn = db_conn
            self._input_guards = InputGuardrails(db_conn=db_conn)
            self._output_guards = OutputGuardrails()
        except Exception as exc:
            raise SafetySystemOfflineError(
                f"Safety middleware failed to initialise: {exc}"
            ) from exc
        logger.info("Safety middleware initialised")

    def _resolve_profile(
        self,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Resolve user profile and content tier."""
        profile: dict[str, Any] = {}
        try:
            from cortex.profiles import get_user_profile
            row = get_user_profile(self._db_conn, user_id)
            if row:
                profile = dict(row)
        except Exception:
            pass  # default to empty profile (→ unknown tier)

        # Check for forced tier from metadata
        if metadata and "content_tier" in metadata:
            profile["forced_content_tier"] = metadata["content_tier"]

        content_tier = resolve_content_tier(profile)
        return profile, content_tier

    def check_input(
        self,
        message: str,
        user_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailResult:
        """Run input guardrails.  Called before the pipeline."""
        profile, content_tier = self._resolve_profile(user_id, metadata)
        result = self._input_guards.check(message, profile, content_tier)

        if result.severity >= Severity.WARN:
            action = "blocked" if result.severity >= Severity.SOFT_BLOCK else "warned"
            log_guardrail_event(
                self._db_conn,
                user_id=user_id,
                direction="input",
                result=result,
                action_taken=action,
                content_tier=content_tier,
                trigger_text=redact_pii(message[:500]) if not result.redact_input else "[REDACTED]",
            )
            logger.info("Input guardrail %s: %s — %s", action, result.category, result.reason)

        return result

    def build_system_prompt(
        self,
        user_id: str = "default",
        metadata: dict[str, Any] | None = None,
        drift_context: str = "",
    ) -> str:
        """Build the safety-aware system prompt for Layer 3."""
        _, content_tier = self._resolve_profile(user_id, metadata)
        return build_safety_system_prompt(content_tier, drift_context)

    def check_output(
        self,
        response: str,
        user_id: str = "default",
        last_user_message: str = "",
        system_prompt: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailResult:
        """Run output guardrails.  Called after the full LLM response is collected."""
        profile, content_tier = self._resolve_profile(user_id, metadata)
        result = self._output_guards.check(
            response, profile, content_tier, system_prompt, last_user_message,
        )

        if result.severity >= Severity.WARN:
            action = "replaced" if result.severity >= Severity.SOFT_BLOCK else "warned"
            log_guardrail_event(
                self._db_conn,
                user_id=user_id,
                direction="output",
                result=result,
                action_taken=action,
                content_tier=content_tier,
                trigger_text=response[:500],
            )
            logger.info("Output guardrail %s: %s — %s", action, result.category, result.reason)

        return result
