"""Inbound webhook receiver — accept webhooks and dispatch to pipeline."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db, init_db

logger = logging.getLogger(__name__)


@dataclass
class WebhookChannel:
    """A configured webhook channel."""

    id: str
    name: str
    secret: str
    response_format: str = "json"  # json or text
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


class WebhookReceiver:
    """Accept inbound webhooks and dispatch to pipeline."""

    def create_channel(
        self,
        name: str,
        response_format: str = "json",
        config: dict[str, Any] | None = None,
    ) -> WebhookChannel:
        """Create a new webhook channel with auto-generated ID and secret."""
        channel_id = secrets.token_urlsafe(16)
        secret = secrets.token_hex(32)
        conn = self._db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name, config, enabled) "
            "VALUES (?, 'webhook', ?, ?, 1)",
            (
                channel_id,
                name,
                json.dumps({
                    "secret": secret,
                    "response_format": response_format,
                    **(config or {}),
                }),
            ),
        )
        conn.commit()
        return WebhookChannel(
            id=channel_id,
            name=name,
            secret=secret,
            response_format=response_format,
            config=config or {},
        )

    def get_channel(self, channel_id: str) -> WebhookChannel | None:
        """Look up a webhook channel by ID."""
        conn = self._db()
        row = conn.execute(
            "SELECT id, name, config, enabled FROM legacy_channels "
            "WHERE id = ? AND channel_type = 'webhook'",
            (channel_id,),
        ).fetchone()
        if not row:
            return None
        cfg = json.loads(row["config"] or "{}")
        return WebhookChannel(
            id=row["id"],
            name=row["name"],
            secret=cfg.get("secret", ""),
            response_format=cfg.get("response_format", "json"),
            enabled=bool(row["enabled"]),
            config=cfg,
        )

    def list_channels(self) -> list[WebhookChannel]:
        """List all webhook channels."""
        conn = self._db()
        cur = conn.execute(
            "SELECT id, name, config, enabled FROM legacy_channels "
            "WHERE channel_type = 'webhook' ORDER BY name"
        )
        channels = []
        for row in cur.fetchall():
            cfg = json.loads(row["config"] or "{}")
            channels.append(
                WebhookChannel(
                    id=row["id"],
                    name=row["name"],
                    secret=cfg.get("secret", ""),
                    response_format=cfg.get("response_format", "json"),
                    enabled=bool(row["enabled"]),
                    config=cfg,
                )
            )
        return channels

    def verify_signature(self, channel: WebhookChannel, payload: str, signature: str) -> bool:
        """Verify HMAC-SHA256 signature of webhook payload."""
        if not channel.secret:
            return True
        expected = hmac.new(
            channel.secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, f"sha256={expected}" if signature.startswith("sha256=") else expected)

    async def receive(
        self,
        channel_id: str,
        payload: dict[str, Any],
        raw_body: str = "",
        signature: str = "",
    ) -> dict[str, Any]:
        """Process an inbound webhook.

        Returns a dict with 'response' text and 'success' bool.
        """
        channel = self.get_channel(channel_id)
        if not channel:
            return {"success": False, "error": "Channel not found"}
        if not channel.enabled:
            return {"success": False, "error": "Channel disabled"}

        # Verify signature if provided
        if signature and raw_body:
            if not self.verify_signature(channel, raw_body, signature):
                logger.warning("Invalid webhook signature for channel %s", channel_id)
                return {"success": False, "error": "Invalid signature"}

        # Extract message from payload
        message = self._extract_message(payload)
        if not message:
            # Log the webhook even without a processable message
            self._log_message(channel_id, "inbound", json.dumps(payload))
            return {"success": True, "response": "Webhook received"}

        # Log inbound
        self._log_message(channel_id, "inbound", message)

        # Update last activity
        conn = self._db()
        conn.execute(
            "UPDATE legacy_channels SET last_activity = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), channel_id),
        )
        conn.commit()

        # Process through pipeline
        response = await self._process_message(message, channel_id)

        # Log outbound
        if response:
            self._log_message(channel_id, "outbound", response)

        if channel.response_format == "text":
            return {"success": True, "response": response}
        return {"success": True, "response": response, "channel": channel.name}

    # ── Helpers ──────────────────────────────────────────────────

    def _extract_message(self, payload: dict[str, Any]) -> str:
        """Extract a processable message from the webhook payload."""
        # Try common webhook payload formats
        for key in ("message", "text", "body", "content", "query", "command"):
            if key in payload and isinstance(payload[key], str):
                return payload[key]
        # IFTTT format
        if "value1" in payload:
            return str(payload["value1"])
        return ""

    async def _process_message(self, message: str, channel_id: str) -> str:
        """Run message through the Atlas pipeline."""
        try:
            from cortex.providers import get_provider

            provider = get_provider()
            from cortex.pipeline import run_pipeline

            tokens: list[str] = []
            async for token in run_pipeline(
                message=message,
                provider=provider,
                user_id=f"webhook:{channel_id}",
            ):
                tokens.append(token)
            return "".join(tokens)
        except Exception:
            logger.exception("Pipeline processing failed for webhook")
            return "Error processing webhook"

    def _log_message(
        self,
        channel_id: str,
        direction: str,
        content: str,
        sender: str = "",
        recipient: str = "",
    ) -> None:
        """Log a message to the legacy_messages table."""
        try:
            conn = self._db()
            conn.execute(
                "INSERT INTO legacy_messages (channel_id, direction, sender, recipient, content) "
                "VALUES (?, ?, ?, ?, ?)",
                (channel_id, direction, sender, recipient, content),
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to log legacy message")

    def _db(self) -> sqlite3.Connection:
        init_db()
        return get_db()
