"""Legacy Protocol plugin — send SMS, email, and serial commands via Layer 2."""

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, ConfigField, CortexPlugin

logger = logging.getLogger(__name__)

# ── Regex Patterns ──────────────────────────────────────────────

_SMS_RE = re.compile(
    r"(?:send\s+(?:a\s+)?(?:text|sms|text\s+message|message)\s+(?:to\s+)?)"
    r"([+\d\s()-]+?)"
    r"(?:\s+(?:saying|that\s+says|with)\s+|:\s*)"
    r"(.*)",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(
    r"(?:send\s+(?:an?\s+)?email\s+(?:to\s+)?)"
    r"([^\s]+@[^\s]+)"
    r"(?:\s+(?:about|subject|with\s+subject)\s+(.+?))?"
    r"\s+(?:saying|body|that\s+says|with|:)\s+"
    r"(.*)",
    re.IGNORECASE,
)

_SERIAL_RE = re.compile(
    r"(?:send\s+(?:serial\s+)?command\s+(?:to\s+)?)"
    r"(\S+?)"
    r"(?:\s*:\s+|\s+)"
    r"(.*)",
    re.IGNORECASE,
)

_SERIAL_DISCOVER_RE = re.compile(
    r"(?:list|discover|find|show)\s+(?:serial\s+)?ports",
    re.IGNORECASE,
)


class LegacyPlugin(CortexPlugin):
    """Legacy Protocol plugin for SMS, email, and serial commands."""

    plugin_id = "legacy"
    display_name = "Legacy Protocol"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"
    config_fields = [
        ConfigField(
            key="sms_provider",
            label="SMS Provider",
            field_type="select",
            options=[
                {"value": "twilio", "label": "Twilio"},
                {"value": "vonage", "label": "Vonage"},
                {"value": "local_gsm", "label": "Local GSM Modem"},
            ],
            default="twilio",
        ),
        ConfigField(
            key="sms_api_key",
            label="SMS API Key",
            field_type="password",
        ),
        ConfigField(
            key="sms_api_secret",
            label="SMS API Secret",
            field_type="password",
        ),
        ConfigField(
            key="sms_from_number",
            label="SMS From Number",
            field_type="text",
            placeholder="+15551234567",
        ),
    ]

    def __init__(self) -> None:
        self._sms_gateway: Any = None
        self._email_bridge: Any = None
        self._serial_bridge: Any = None
        self._config: dict[str, Any] = {}

    async def setup(self, config: dict[str, Any]) -> bool:
        self._config = config
        return True

    async def health(self) -> bool:
        return True

    @property
    def health_message(self) -> str:
        return "OK"

    async def match(
        self, message: str, context: dict[str, Any]
    ) -> CommandMatch:
        msg = message.strip()

        if _SMS_RE.match(msg):
            m = _SMS_RE.match(msg)
            assert m is not None
            return CommandMatch(
                matched=True,
                intent="legacy_sms",
                metadata={
                    "to": m.group(1).strip(),
                    "message": m.group(2).strip() if m.group(2) else "",
                },
            )

        if _EMAIL_RE.match(msg):
            m = _EMAIL_RE.match(msg)
            assert m is not None
            return CommandMatch(
                matched=True,
                intent="legacy_email",
                metadata={
                    "to": m.group(1).strip(),
                    "subject": (m.group(2) or "").strip(),
                    "body": (m.group(3) or "").strip(),
                },
            )

        if _SERIAL_RE.match(msg):
            m = _SERIAL_RE.match(msg)
            assert m is not None
            return CommandMatch(
                matched=True,
                intent="legacy_serial",
                metadata={
                    "port": m.group(1).strip(),
                    "command": m.group(2).strip(),
                },
            )

        if _SERIAL_DISCOVER_RE.search(msg):
            return CommandMatch(matched=True, intent="legacy_serial_discover")

        return CommandMatch(matched=False)

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any]
    ) -> CommandResult:
        intent = match.intent
        meta = match.metadata

        try:
            if intent == "legacy_sms":
                return await self._handle_sms(meta)
            if intent == "legacy_email":
                return await self._handle_email(meta)
            if intent == "legacy_serial":
                return await self._handle_serial(meta)
            if intent == "legacy_serial_discover":
                return await self._handle_serial_discover()
        except Exception:
            logger.exception("Legacy plugin error")
            return CommandResult(
                success=False,
                response="Sorry, the legacy protocol ran into an error.",
            )

        return CommandResult(success=False, response="Unknown legacy command.")

    # ── Intent Handlers ─────────────────────────────────────────

    async def _handle_sms(self, meta: dict[str, Any]) -> CommandResult:
        to = meta.get("to", "")
        msg = meta.get("message", "")
        if not to:
            return CommandResult(success=False, response="I need a phone number to send the SMS to.")
        if not msg:
            return CommandResult(success=False, response="What should the message say?")

        gw = self._get_sms_gateway()
        ok = await gw.send(to, msg)
        if ok:
            return CommandResult(success=True, response=f"Text message sent to {to}.")
        return CommandResult(success=False, response=f"Failed to send text to {to}.")

    async def _handle_email(self, meta: dict[str, Any]) -> CommandResult:
        to = meta.get("to", "")
        subject = meta.get("subject", "") or "Message from Atlas"
        body = meta.get("body", "")
        if not to:
            return CommandResult(success=False, response="I need an email address to send to.")
        if not body:
            return CommandResult(success=False, response="What should the email say?")

        bridge = self._get_email_bridge()
        ok = await bridge.send(to, subject, body)
        if ok:
            return CommandResult(success=True, response=f"Email sent to {to}.")
        return CommandResult(success=False, response=f"Failed to send email to {to}.")

    async def _handle_serial(self, meta: dict[str, Any]) -> CommandResult:
        port = meta.get("port", "")
        command = meta.get("command", "")
        if not port or not command:
            return CommandResult(success=False, response="I need a port and command.")

        bridge = self._get_serial_bridge()
        response = await bridge.send_command(port, command)
        if response:
            return CommandResult(
                success=True,
                response=f"Serial response from {port}: {response}",
            )
        return CommandResult(success=True, response=f"Command sent to {port}.")

    async def _handle_serial_discover(self) -> CommandResult:
        bridge = self._get_serial_bridge()
        ports = await bridge.discover_ports()
        if not ports:
            return CommandResult(success=True, response="No serial ports found.")
        lines = [f"• {p.device} — {p.description}" for p in ports]
        return CommandResult(
            success=True,
            response="Serial ports found:\n" + "\n".join(lines),
        )

    # ── Lazy Initialization ─────────────────────────────────────

    def _get_sms_gateway(self) -> Any:
        if self._sms_gateway is None:
            from cortex.legacy.sms import SMSGateway

            self._sms_gateway = SMSGateway(
                provider=self._config.get("sms_provider", ""),
                api_key=self._config.get("sms_api_key", ""),
                api_secret=self._config.get("sms_api_secret", ""),
                from_number=self._config.get("sms_from_number", ""),
            )
        return self._sms_gateway

    def _get_email_bridge(self) -> Any:
        if self._email_bridge is None:
            from cortex.legacy.email_bridge import EmailBridge

            self._email_bridge = EmailBridge()
        return self._email_bridge

    def _get_serial_bridge(self) -> Any:
        if self._serial_bridge is None:
            from cortex.legacy.serial_bridge import SerialBridge

            self._serial_bridge = SerialBridge()
        return self._serial_bridge
