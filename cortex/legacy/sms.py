"""SMS/MMS gateway with pluggable backends (Twilio, Vonage, local GSM)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    import httpx

    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    _HAS_HTTPX = False


# ── Configuration ────────────────────────────────────────────────

SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "twilio")
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
SMS_API_SECRET = os.environ.get("SMS_API_SECRET", "")
SMS_FROM_NUMBER = os.environ.get("SMS_FROM_NUMBER", "")
SMS_WEBHOOK_URL = os.environ.get("SMS_WEBHOOK_URL", "")


@dataclass
class SMSMessage:
    """Represents an SMS/MMS message."""

    to: str
    body: str
    from_number: str = ""
    media_urls: list[str] = field(default_factory=list)
    provider_id: str = ""
    timestamp: str = ""


class SMSGateway:
    """Send/receive SMS via configurable backend."""

    def __init__(
        self,
        provider: str = "",
        api_key: str = "",
        api_secret: str = "",
        from_number: str = "",
    ) -> None:
        self.provider = provider or SMS_PROVIDER
        self.api_key = api_key or SMS_API_KEY
        self.api_secret = api_secret or SMS_API_SECRET
        self.from_number = from_number or SMS_FROM_NUMBER

    # ── Public API ───────────────────────────────────────────────

    async def send(self, to: str, message: str, media_urls: list[str] | None = None) -> bool:
        """Send an SMS/MMS message. Returns True on success."""
        if self.provider == "twilio":
            return await self._send_twilio(to, message, media_urls)
        if self.provider == "vonage":
            return await self._send_vonage(to, message)
        if self.provider == "local_gsm":
            return await self._send_local_gsm(to, message)
        logger.error("Unknown SMS provider: %s", self.provider)
        return False

    async def receive_webhook(self, data: dict[str, Any]) -> SMSMessage | None:
        """Parse an inbound SMS webhook payload. Returns parsed message."""
        if self.provider == "twilio":
            return self._parse_twilio_webhook(data)
        if self.provider == "vonage":
            return self._parse_vonage_webhook(data)
        logger.warning("Unknown SMS provider for webhook: %s", self.provider)
        return None

    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        """Verify that a webhook request is authentic."""
        if self.provider == "twilio":
            expected = hmac.new(
                self.api_secret.encode(),
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature)
        return True

    # ── Twilio Backend ───────────────────────────────────────────

    async def _send_twilio(
        self, to: str, message: str, media_urls: list[str] | None = None
    ) -> bool:
        if not _HAS_HTTPX:
            logger.error("httpx required for Twilio SMS")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.api_key}/Messages.json"
        data: dict[str, Any] = {
            "To": to,
            "From": self.from_number,
            "Body": message,
        }
        if media_urls:
            for i, murl in enumerate(media_urls):
                data[f"MediaUrl{i}"] = murl
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    data=data,
                    auth=(self.api_key, self.api_secret),
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    logger.info("SMS sent to %s via Twilio", to)
                    return True
                logger.error("Twilio SMS error %s: %s", resp.status_code, resp.text)
                return False
        except Exception:
            logger.exception("Twilio SMS send failed")
            return False

    def _parse_twilio_webhook(self, data: dict[str, Any]) -> SMSMessage:
        media_urls = []
        num_media = int(data.get("NumMedia", 0))
        for i in range(num_media):
            url = data.get(f"MediaUrl{i}", "")
            if url:
                media_urls.append(url)
        return SMSMessage(
            to=data.get("To", ""),
            body=data.get("Body", ""),
            from_number=data.get("From", ""),
            media_urls=media_urls,
            provider_id=data.get("MessageSid", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ── Vonage Backend ───────────────────────────────────────────

    async def _send_vonage(self, to: str, message: str) -> bool:
        if not _HAS_HTTPX:
            logger.error("httpx required for Vonage SMS")
            return False
        url = "https://rest.nexmo.com/sms/json"
        data = {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "from": self.from_number,
            "to": to,
            "text": message,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=data, timeout=15)
                result = resp.json()
                messages = result.get("messages", [{}])
                if messages and messages[0].get("status") == "0":
                    logger.info("SMS sent to %s via Vonage", to)
                    return True
                logger.error("Vonage SMS error: %s", result)
                return False
        except Exception:
            logger.exception("Vonage SMS send failed")
            return False

    def _parse_vonage_webhook(self, data: dict[str, Any]) -> SMSMessage:
        return SMSMessage(
            to=data.get("to", ""),
            body=data.get("text", ""),
            from_number=data.get("msisdn", data.get("from", "")),
            provider_id=data.get("messageId", ""),
            timestamp=data.get("message-timestamp", datetime.now(timezone.utc).isoformat()),
        )

    # ── Local GSM Backend ────────────────────────────────────────

    async def _send_local_gsm(self, to: str, message: str) -> bool:
        """Send via local GSM modem (AT commands over serial)."""
        try:
            from cortex.legacy.serial_bridge import SerialBridge

            bridge = SerialBridge()
            port = os.environ.get("GSM_MODEM_PORT", "/dev/ttyUSB0")
            baud = int(os.environ.get("GSM_MODEM_BAUD", "9600"))
            # AT command sequence for sending SMS
            commands = [
                "AT",
                "AT+CMGF=1",  # Text mode
                f'AT+CMGS="{to}"',
                f"{message}\x1a",  # Message + Ctrl+Z
            ]
            for cmd in commands:
                await bridge.send_command(port, cmd, baud=baud)
            logger.info("SMS sent to %s via local GSM", to)
            return True
        except Exception:
            logger.exception("Local GSM SMS send failed")
            return False
