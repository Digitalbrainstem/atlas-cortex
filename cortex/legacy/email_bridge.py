"""Email bridge — IMAP polling + SMTP sending for Atlas commands."""

from __future__ import annotations

import asyncio
import email
import email.mime.multipart
import email.mime.text
import imaplib
import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

EMAIL_IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "")
EMAIL_IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_POLL_INTERVAL = int(os.environ.get("EMAIL_POLL_INTERVAL", "60"))
EMAIL_ALLOWED_SENDERS = os.environ.get("EMAIL_ALLOWED_SENDERS", "")


@dataclass
class EmailMessage:
    """Represents an email message."""

    message_id: str
    from_addr: str
    to_addr: str
    subject: str
    body: str
    timestamp: str = ""
    attachments: list[dict[str, str]] = field(default_factory=list)


class EmailBridge:
    """Monitor inbox, process commands, send responses."""

    def __init__(
        self,
        imap_host: str = "",
        imap_port: int = 0,
        smtp_host: str = "",
        smtp_port: int = 0,
        user: str = "",
        password: str = "",
        poll_interval: int = 0,
        allowed_senders: list[str] | None = None,
    ) -> None:
        self.imap_host = imap_host or EMAIL_IMAP_HOST
        self.imap_port = imap_port or EMAIL_IMAP_PORT
        self.smtp_host = smtp_host or EMAIL_SMTP_HOST
        self.smtp_port = smtp_port or EMAIL_SMTP_PORT
        self.user = user or EMAIL_USER
        self.password = password or EMAIL_PASSWORD
        self.poll_interval = poll_interval or EMAIL_POLL_INTERVAL
        if allowed_senders is not None:
            self._allowed_senders = allowed_senders
        elif EMAIL_ALLOWED_SENDERS:
            self._allowed_senders = [
                s.strip() for s in EMAIL_ALLOWED_SENDERS.split(",") if s.strip()
            ]
        else:
            self._allowed_senders = []
        self._running = False

    # ── Public API ───────────────────────────────────────────────

    async def send(self, to: str, subject: str, body: str) -> bool:
        """Send an email via SMTP. Returns True on success."""
        if not self.smtp_host or not self.user:
            logger.error("Email SMTP not configured")
            return False
        try:
            return await asyncio.to_thread(self._send_sync, to, subject, body)
        except Exception:
            logger.exception("Email send failed")
            return False

    async def check_inbox(self) -> list[EmailMessage]:
        """Poll IMAP for unseen messages. Returns list of parsed emails."""
        if not self.imap_host or not self.user:
            logger.warning("Email IMAP not configured")
            return []
        try:
            return await asyncio.to_thread(self._check_inbox_sync)
        except Exception:
            logger.exception("Email inbox check failed")
            return []

    async def process_email(self, email_msg: EmailMessage) -> str:
        """Process an inbound email through the Atlas pipeline.

        Returns the response text that should be sent back.
        """
        if self._allowed_senders and email_msg.from_addr not in self._allowed_senders:
            logger.warning("Email from unauthorized sender: %s", email_msg.from_addr)
            return ""

        # Extract command from subject or body
        command = email_msg.subject.strip()
        if not command or command.lower() in ("re:", "fwd:", "fw:"):
            command = email_msg.body.strip()
        if not command:
            return ""

        # Run through the pipeline
        try:
            from cortex.providers import get_provider

            provider = get_provider()
            from cortex.pipeline import run_pipeline

            tokens: list[str] = []
            async for token in run_pipeline(
                message=command,
                provider=provider,
                user_id=f"email:{email_msg.from_addr}",
            ):
                tokens.append(token)
            return "".join(tokens)
        except Exception:
            logger.exception("Pipeline processing failed for email")
            return "Sorry, I couldn't process your request."

    def is_sender_allowed(self, sender: str) -> bool:
        """Check if a sender is in the allowed list (empty = allow all)."""
        if not self._allowed_senders:
            return True
        return sender in self._allowed_senders

    # ── Synchronous Helpers ──────────────────────────────────────

    def _send_sync(self, to: str, subject: str, body: str) -> bool:
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "plain"))

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.user, self.password)
            server.send_message(msg)
        logger.info("Email sent to %s: %s", to, subject)
        return True

    def _check_inbox_sync(self) -> list[EmailMessage]:
        messages: list[EmailMessage] = []
        mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        try:
            mail.login(self.user, self.password)
            mail.select("INBOX")
            _, data = mail.search(None, "UNSEEN")
            if not data or not data[0]:
                return messages

            for num in data[0].split():
                _, msg_data = mail.fetch(num, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0]
                if isinstance(raw, tuple) and len(raw) >= 2:
                    raw_email = raw[1]
                else:
                    continue
                parsed = email.message_from_bytes(raw_email)

                subject = ""
                raw_subject = parsed.get("Subject", "")
                if raw_subject:
                    decoded_parts = decode_header(raw_subject)
                    subject = "".join(
                        part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                        for part, enc in decoded_parts
                    )

                from_addr = parsed.get("From", "")
                # Extract email from "Name <email>" format
                if "<" in from_addr and ">" in from_addr:
                    from_addr = from_addr.split("<")[1].rstrip(">")

                body = ""
                if parsed.is_multipart():
                    for part in parsed.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode("utf-8", errors="replace")
                            break
                else:
                    payload = parsed.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")

                messages.append(
                    EmailMessage(
                        message_id=parsed.get("Message-ID", ""),
                        from_addr=from_addr,
                        to_addr=parsed.get("To", ""),
                        subject=subject,
                        body=body,
                        timestamp=parsed.get("Date", datetime.now(timezone.utc).isoformat()),
                    )
                )
                # Mark as seen
                mail.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                mail.logout()
            except Exception:
                pass
        return messages
