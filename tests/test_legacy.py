"""Tests for Part 13 — Legacy Protocol (SMS, email, webhooks, serial bridge)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def client(db_path):
    from unittest.mock import patch as _patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cortex.admin_api import router
    from cortex.auth import seed_admin as _seed

    test_app = FastAPI()
    test_app.include_router(router)

    def get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _seed(conn)
        return conn

    with _patch("cortex.admin.helpers._db", get_test_db):
        yield TestClient(test_app)


@pytest.fixture()
def auth_header(db):
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


# ══════════════════════════════════════════════════════════════════
# SMS Gateway
# ══════════════════════════════════════════════════════════════════


class TestSMSGateway:
    """Test SMS gateway send/receive with mocked backends."""

    def test_init_defaults(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="twilio", api_key="key", api_secret="secret")
        assert gw.provider == "twilio"
        assert gw.api_key == "key"
        assert gw.api_secret == "secret"

    @pytest.mark.asyncio
    async def test_send_twilio_success(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(
            provider="twilio",
            api_key="ACtest",
            api_secret="secret",
            from_number="+15550001234",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.text = '{"sid": "SMxxx"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("cortex.legacy.sms._HAS_HTTPX", True), patch(
            "cortex.legacy.sms.httpx.AsyncClient", return_value=mock_client
        ):
            ok = await gw.send("+15559998888", "Hello from Atlas!")
            assert ok is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_twilio_failure(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(
            provider="twilio",
            api_key="ACtest",
            api_secret="secret",
            from_number="+15550001234",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("cortex.legacy.sms._HAS_HTTPX", True), patch(
            "cortex.legacy.sms.httpx.AsyncClient", return_value=mock_client
        ):
            ok = await gw.send("+15559998888", "Hello")
            assert ok is False

    @pytest.mark.asyncio
    async def test_send_vonage_success(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(
            provider="vonage",
            api_key="key",
            api_secret="secret",
            from_number="+15550001234",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "messages": [{"status": "0", "message-id": "abc"}]
        })

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("cortex.legacy.sms._HAS_HTTPX", True), patch(
            "cortex.legacy.sms.httpx.AsyncClient", return_value=mock_client
        ):
            ok = await gw.send("+15559998888", "Hello from Atlas!")
            assert ok is True

    @pytest.mark.asyncio
    async def test_send_vonage_failure(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(
            provider="vonage",
            api_key="key",
            api_secret="secret",
            from_number="+15550001234",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={
            "messages": [{"status": "1", "error-text": "Throttled"}]
        })

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("cortex.legacy.sms._HAS_HTTPX", True), patch(
            "cortex.legacy.sms.httpx.AsyncClient", return_value=mock_client
        ):
            ok = await gw.send("+15559998888", "Hello")
            assert ok is False

    @pytest.mark.asyncio
    async def test_send_unknown_provider(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="unknown")
        ok = await gw.send("+15559998888", "Hello")
        assert ok is False

    def test_parse_twilio_webhook(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="twilio")
        data = {
            "From": "+15559998888",
            "To": "+15550001234",
            "Body": "Turn on the lights",
            "MessageSid": "SMxxx",
            "NumMedia": "1",
            "MediaUrl0": "https://example.com/photo.jpg",
        }
        msg = gw._parse_twilio_webhook(data)
        assert msg.from_number == "+15559998888"
        assert msg.body == "Turn on the lights"
        assert msg.media_urls == ["https://example.com/photo.jpg"]
        assert msg.provider_id == "SMxxx"

    def test_parse_vonage_webhook(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="vonage")
        data = {
            "msisdn": "15559998888",
            "to": "15550001234",
            "text": "What time is it?",
            "messageId": "abc123",
        }
        msg = gw._parse_vonage_webhook(data)
        assert msg.from_number == "15559998888"
        assert msg.body == "What time is it?"
        assert msg.provider_id == "abc123"

    @pytest.mark.asyncio
    async def test_receive_webhook(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="twilio")
        data = {
            "From": "+15559998888",
            "To": "+15550001234",
            "Body": "Hello Atlas",
            "MessageSid": "SMyyy",
            "NumMedia": "0",
        }
        msg = await gw.receive_webhook(data)
        assert msg is not None
        assert msg.body == "Hello Atlas"

    @pytest.mark.asyncio
    async def test_receive_webhook_unknown_provider(self):
        from cortex.legacy.sms import SMSGateway

        gw = SMSGateway(provider="unknown")
        msg = await gw.receive_webhook({"Body": "test"})
        assert msg is None

    def test_sms_message_dataclass(self):
        from cortex.legacy.sms import SMSMessage

        msg = SMSMessage(to="+1555", body="hi")
        assert msg.to == "+1555"
        assert msg.body == "hi"
        assert msg.media_urls == []
        assert msg.from_number == ""


# ══════════════════════════════════════════════════════════════════
# Email Bridge
# ══════════════════════════════════════════════════════════════════


class TestEmailBridge:
    """Test email bridge IMAP polling and SMTP sending (mocked)."""

    def test_init_defaults(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge(imap_host="imap.test.com", user="test@test.com")
        assert bridge.imap_host == "imap.test.com"
        assert bridge.user == "test@test.com"

    def test_init_allowed_senders(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge(allowed_senders=["a@b.com", "c@d.com"])
        assert bridge._allowed_senders == ["a@b.com", "c@d.com"]

    def test_is_sender_allowed_empty(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge()
        assert bridge.is_sender_allowed("anyone@example.com") is True

    def test_is_sender_allowed_restricted(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge(allowed_senders=["trusted@example.com"])
        assert bridge.is_sender_allowed("trusted@example.com") is True
        assert bridge.is_sender_allowed("hacker@evil.com") is False

    @pytest.mark.asyncio
    async def test_send_success(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge(
            smtp_host="smtp.test.com",
            smtp_port=587,
            user="bot@test.com",
            password="secret",
        )
        with patch.object(bridge, "_send_sync", return_value=True):
            ok = await bridge.send("user@test.com", "Test Subject", "Hello!")
            assert ok is True

    @pytest.mark.asyncio
    async def test_send_no_config(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge()
        ok = await bridge.send("user@test.com", "Subject", "Body")
        assert ok is False

    @pytest.mark.asyncio
    async def test_check_inbox_no_config(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge()
        msgs = await bridge.check_inbox()
        assert msgs == []

    @pytest.mark.asyncio
    async def test_check_inbox_success(self):
        from cortex.legacy.email_bridge import EmailBridge

        bridge = EmailBridge(
            imap_host="imap.test.com",
            imap_port=993,
            user="bot@test.com",
            password="secret",
        )
        mock_messages = [
            MagicMock(
                message_id="<abc@test>",
                from_addr="user@test.com",
                to_addr="bot@test.com",
                subject="Turn on lights",
                body="Please turn on the kitchen lights",
            )
        ]
        with patch.object(bridge, "_check_inbox_sync", return_value=mock_messages):
            msgs = await bridge.check_inbox()
            assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_process_email_unauthorized(self):
        from cortex.legacy.email_bridge import EmailBridge, EmailMessage

        bridge = EmailBridge(allowed_senders=["trusted@example.com"])
        email_msg = EmailMessage(
            message_id="<1>",
            from_addr="hacker@evil.com",
            to_addr="bot@test.com",
            subject="delete everything",
            body="rm -rf /",
        )
        result = await bridge.process_email(email_msg)
        assert result == ""

    @pytest.mark.asyncio
    async def test_process_email_empty(self):
        from cortex.legacy.email_bridge import EmailBridge, EmailMessage

        bridge = EmailBridge()
        email_msg = EmailMessage(
            message_id="<1>",
            from_addr="user@test.com",
            to_addr="bot@test.com",
            subject="",
            body="",
        )
        result = await bridge.process_email(email_msg)
        assert result == ""

    @pytest.mark.asyncio
    async def test_process_email_pipeline(self):
        from cortex.legacy.email_bridge import EmailBridge, EmailMessage

        bridge = EmailBridge()
        email_msg = EmailMessage(
            message_id="<1>",
            from_addr="user@test.com",
            to_addr="bot@test.com",
            subject="What time is it?",
            body="",
        )

        async def mock_pipeline(*args, **kwargs):
            yield "It's 3 PM."

        with patch("cortex.providers.get_provider") as mock_prov, patch(
            "cortex.pipeline.run_pipeline", side_effect=mock_pipeline
        ):
            mock_prov.return_value = MagicMock()
            result = await bridge.process_email(email_msg)
            assert "3 PM" in result

    def test_email_message_dataclass(self):
        from cortex.legacy.email_bridge import EmailMessage

        msg = EmailMessage(
            message_id="<1>",
            from_addr="a@b.com",
            to_addr="c@d.com",
            subject="Hi",
            body="Hello",
        )
        assert msg.message_id == "<1>"
        assert msg.attachments == []


# ══════════════════════════════════════════════════════════════════
# Webhook Receiver
# ══════════════════════════════════════════════════════════════════


class TestWebhookReceiver:
    """Test webhook receive + pipeline dispatch."""

    def test_create_channel(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("Test Hook", response_format="json")
        assert ch.id
        assert ch.name == "Test Hook"
        assert ch.secret
        assert ch.response_format == "json"

    def test_get_channel(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("My Webhook")
        found = receiver.get_channel(ch.id)
        assert found is not None
        assert found.name == "My Webhook"

    def test_get_channel_not_found(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        assert receiver.get_channel("nonexistent") is None

    def test_list_channels(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        receiver.create_channel("Hook A")
        receiver.create_channel("Hook B")
        channels = receiver.list_channels()
        assert len(channels) == 2
        names = {c.name for c in channels}
        assert names == {"Hook A", "Hook B"}

    @pytest.mark.asyncio
    async def test_receive_not_found(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        result = await receiver.receive("nonexistent", {"message": "hi"})
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_receive_disabled(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("Disabled Hook")
        conn = get_db()
        conn.execute(
            "UPDATE legacy_channels SET enabled = 0 WHERE id = ?", (ch.id,)
        )
        conn.commit()
        result = await receiver.receive(ch.id, {"message": "hi"})
        assert result["success"] is False
        assert "disabled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_receive_no_message(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("Data Hook")
        result = await receiver.receive(ch.id, {"some_data": 123})
        assert result["success"] is True
        assert result["response"] == "Webhook received"

    @pytest.mark.asyncio
    async def test_receive_with_message(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("CMD Hook")

        async def mock_pipeline(*args, **kwargs):
            yield "Lights turned on."

        with patch("cortex.providers.get_provider") as mock_prov, patch(
            "cortex.pipeline.run_pipeline", side_effect=mock_pipeline
        ):
            mock_prov.return_value = MagicMock()
            result = await receiver.receive(ch.id, {"message": "turn on lights"})
            assert result["success"] is True
            assert "Lights turned on" in result["response"]

    @pytest.mark.asyncio
    async def test_receive_ifttt_format(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("IFTTT Hook")

        async def mock_pipeline(*args, **kwargs):
            yield "Done."

        with patch("cortex.providers.get_provider") as mock_prov, patch(
            "cortex.pipeline.run_pipeline", side_effect=mock_pipeline
        ):
            mock_prov.return_value = MagicMock()
            result = await receiver.receive(ch.id, {"value1": "do something"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_receive_text_format(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        ch = receiver.create_channel("Text Hook", response_format="text")

        async def mock_pipeline(*args, **kwargs):
            yield "Hello!"

        with patch("cortex.providers.get_provider") as mock_prov, patch(
            "cortex.pipeline.run_pipeline", side_effect=mock_pipeline
        ):
            mock_prov.return_value = MagicMock()
            result = await receiver.receive(ch.id, {"message": "hi"})
            assert result["success"] is True
            assert "channel" not in result  # text format omits channel name

    def test_extract_message_variants(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        assert receiver._extract_message({"message": "hi"}) == "hi"
        assert receiver._extract_message({"text": "hello"}) == "hello"
        assert receiver._extract_message({"body": "test"}) == "test"
        assert receiver._extract_message({"content": "yo"}) == "yo"
        assert receiver._extract_message({"query": "what"}) == "what"
        assert receiver._extract_message({"command": "do"}) == "do"
        assert receiver._extract_message({"value1": "ifttt"}) == "ifttt"
        assert receiver._extract_message({"random_key": 42}) == ""

    def test_verify_signature(self, db_path):
        from cortex.legacy.webhook import WebhookChannel, WebhookReceiver

        receiver = WebhookReceiver()
        ch = WebhookChannel(id="t", name="t", secret="mysecret")
        # Signature check with no secret always passes
        ch_no_secret = WebhookChannel(id="t", name="t", secret="")
        assert receiver.verify_signature(ch_no_secret, "payload", "anysig") is True

    def test_log_message(self, db_path):
        from cortex.legacy.webhook import WebhookReceiver

        receiver = WebhookReceiver()
        # Create a channel first
        ch = receiver.create_channel("Log Test")
        receiver._log_message(ch.id, "inbound", "test message", "sender", "recipient")
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM legacy_messages WHERE channel_id = ?", (ch.id,)
        ).fetchone()
        assert row is not None
        assert row["content"] == "test message"
        assert row["direction"] == "inbound"


# ══════════════════════════════════════════════════════════════════
# Serial Bridge
# ══════════════════════════════════════════════════════════════════


class TestSerialBridge:
    """Test serial bridge discover/send (mocked)."""

    def test_init(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        assert bridge._connections == {}

    @pytest.mark.asyncio
    async def test_discover_ports_no_pyserial(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        with patch("cortex.legacy.serial_bridge._HAS_SERIAL", False):
            ports = await bridge.discover_ports()
            assert ports == []

    @pytest.mark.asyncio
    async def test_discover_ports(self):
        from cortex.legacy.serial_bridge import SerialBridge, SerialPort

        bridge = SerialBridge()
        mock_ports = [
            SerialPort(device="/dev/ttyUSB0", description="USB Serial"),
            SerialPort(device="/dev/ttyACM0", description="Arduino"),
        ]
        with patch.object(bridge, "_discover_sync", return_value=mock_ports):
            with patch("cortex.legacy.serial_bridge._HAS_SERIAL", True):
                ports = await bridge.discover_ports()
                assert len(ports) == 2
                assert ports[0].device == "/dev/ttyUSB0"

    @pytest.mark.asyncio
    async def test_send_command_no_pyserial(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        with patch("cortex.legacy.serial_bridge._HAS_SERIAL", False):
            result = await bridge.send_command("/dev/ttyUSB0", "AT")
            assert result == ""

    @pytest.mark.asyncio
    async def test_send_command(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        with patch.object(bridge, "_send_sync", return_value="OK"):
            with patch("cortex.legacy.serial_bridge._HAS_SERIAL", True):
                result = await bridge.send_command("/dev/ttyUSB0", "AT")
                assert result == "OK"

    @pytest.mark.asyncio
    async def test_send_raw_no_pyserial(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        with patch("cortex.legacy.serial_bridge._HAS_SERIAL", False):
            result = await bridge.send_raw("/dev/ttyUSB0", b"\x01\x02")
            assert result == b""

    @pytest.mark.asyncio
    async def test_send_raw(self):
        from cortex.legacy.serial_bridge import SerialBridge

        bridge = SerialBridge()
        with patch.object(bridge, "_send_raw_sync", return_value=b"\x03"):
            with patch("cortex.legacy.serial_bridge._HAS_SERIAL", True):
                result = await bridge.send_raw("/dev/ttyUSB0", b"\x01")
                assert result == b"\x03"

    def test_serial_port_dataclass(self):
        from cortex.legacy.serial_bridge import SerialPort

        port = SerialPort(device="/dev/ttyS0")
        assert port.device == "/dev/ttyS0"
        assert port.description == ""
        assert port.manufacturer == ""

    def test_serial_config_dataclass(self):
        from cortex.legacy.serial_bridge import SerialConfig

        cfg = SerialConfig(port="/dev/ttyUSB0", baud=115200)
        assert cfg.port == "/dev/ttyUSB0"
        assert cfg.baud == 115200
        assert cfg.timeout == 2.0


# ══════════════════════════════════════════════════════════════════
# SIP Bridge (stub)
# ══════════════════════════════════════════════════════════════════


class TestSIPBridge:
    @pytest.mark.asyncio
    async def test_health_false(self):
        from cortex.legacy.sip import SIPBridge

        bridge = SIPBridge()
        assert await bridge.health() is False


# ══════════════════════════════════════════════════════════════════
# IR Blaster (stub)
# ══════════════════════════════════════════════════════════════════


class TestIRBlaster:
    @pytest.mark.asyncio
    async def test_health_false(self):
        from cortex.legacy.ir_blaster import IRBlaster

        blaster = IRBlaster()
        assert await blaster.health() is False


# ══════════════════════════════════════════════════════════════════
# Channel CRUD (DB layer)
# ══════════════════════════════════════════════════════════════════


class TestChannelDB:
    """Test legacy channel database operations."""

    def test_create_channel_table_exists(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('c1', 'sms', 'Test SMS')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM legacy_channels WHERE id = 'c1'").fetchone()
        assert row is not None
        assert row["channel_type"] == "sms"
        assert row["name"] == "Test SMS"
        assert row["enabled"] == 1

    def test_create_message(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('c1', 'sms', 'Test')"
        )
        conn.execute(
            "INSERT INTO legacy_messages (channel_id, direction, content) "
            "VALUES ('c1', 'inbound', 'Hello')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM legacy_messages WHERE channel_id = 'c1'").fetchone()
        assert row is not None
        assert row["content"] == "Hello"
        assert row["direction"] == "inbound"
        assert row["processed"] == 0

    def test_message_fk(self, db_path):
        conn = get_db()
        conn.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO legacy_messages (channel_id, direction, content) "
                "VALUES ('nonexistent', 'inbound', 'Hello')"
            )

    def test_channel_defaults(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('c2', 'webhook', 'Hook')"
        )
        conn.commit()
        row = conn.execute("SELECT * FROM legacy_channels WHERE id = 'c2'").fetchone()
        assert row["config"] == "{}"
        assert row["enabled"] == 1
        assert row["last_activity"] is None


# ══════════════════════════════════════════════════════════════════
# Admin API Endpoints
# ══════════════════════════════════════════════════════════════════


class TestAdminLegacyAPI:
    """Test admin CRUD endpoints for legacy channels and messages."""

    def test_list_channels_empty(self, client, auth_header):
        resp = client.get("/admin/legacy/channels", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == []

    def test_create_channel(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "webhook", "name": "My Webhook"},
            headers=auth_header,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Webhook"
        assert data["channel_type"] == "webhook"
        assert "id" in data

    def test_create_channel_invalid_type(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "fax", "name": "Fax Machine"},
            headers=auth_header,
        )
        assert resp.status_code == 400

    def test_get_channel(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "sms", "name": "SMS Channel"},
            headers=auth_header,
        )
        channel_id = resp.json()["id"]

        resp = client.get(f"/admin/legacy/channels/{channel_id}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["channel"]["name"] == "SMS Channel"

    def test_get_channel_not_found(self, client, auth_header):
        resp = client.get("/admin/legacy/channels/nonexistent", headers=auth_header)
        assert resp.status_code == 404

    def test_update_channel(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "email", "name": "Email Channel"},
            headers=auth_header,
        )
        channel_id = resp.json()["id"]

        resp = client.patch(
            f"/admin/legacy/channels/{channel_id}",
            json={"name": "Updated Email", "enabled": False},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify update
        resp = client.get(f"/admin/legacy/channels/{channel_id}", headers=auth_header)
        data = resp.json()["channel"]
        assert data["name"] == "Updated Email"
        assert data["enabled"] is False

    def test_update_channel_no_fields(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "sms", "name": "SMS"},
            headers=auth_header,
        )
        channel_id = resp.json()["id"]

        resp = client.patch(
            f"/admin/legacy/channels/{channel_id}",
            json={},
            headers=auth_header,
        )
        assert resp.status_code == 400

    def test_update_channel_not_found(self, client, auth_header):
        resp = client.patch(
            "/admin/legacy/channels/nonexistent",
            json={"name": "New Name"},
            headers=auth_header,
        )
        assert resp.status_code == 404

    def test_delete_channel(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "serial", "name": "Serial Port"},
            headers=auth_header,
        )
        channel_id = resp.json()["id"]

        resp = client.delete(f"/admin/legacy/channels/{channel_id}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify deleted
        resp = client.get(f"/admin/legacy/channels/{channel_id}", headers=auth_header)
        assert resp.status_code == 404

    def test_delete_channel_not_found(self, client, auth_header):
        resp = client.delete("/admin/legacy/channels/nonexistent", headers=auth_header)
        assert resp.status_code == 404

    def test_list_messages_empty(self, client, auth_header):
        resp = client.get("/admin/legacy/messages", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_list_messages_with_filter(self, client, auth_header):
        resp = client.get(
            "/admin/legacy/messages?direction=inbound&limit=10",
            headers=auth_header,
        )
        assert resp.status_code == 200

    def test_webhook_auto_secret(self, client, auth_header):
        """Webhook channels auto-generate a secret."""
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "webhook", "name": "Auto Secret Hook"},
            headers=auth_header,
        )
        assert resp.status_code == 201
        channel_id = resp.json()["id"]

        resp = client.get(f"/admin/legacy/channels/{channel_id}", headers=auth_header)
        config = resp.json()["channel"]["config"]
        assert "secret" in config
        assert len(config["secret"]) > 0

    def test_test_channel_webhook(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels",
            json={"channel_type": "webhook", "name": "Test Hook"},
            headers=auth_header,
        )
        channel_id = resp.json()["id"]

        resp = client.post(
            f"/admin/legacy/channels/{channel_id}/test",
            json={"message": "test"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_test_channel_not_found(self, client, auth_header):
        resp = client.post(
            "/admin/legacy/channels/nonexistent/test",
            json={"message": "test"},
            headers=auth_header,
        )
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════
# Legacy Plugin
# ══════════════════════════════════════════════════════════════════


class TestLegacyPlugin:
    """Test the Legacy Protocol Layer 2 plugin."""

    @pytest.mark.asyncio
    async def test_setup(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        ok = await plugin.setup({})
        assert ok is True

    @pytest.mark.asyncio
    async def test_health(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        assert await plugin.health() is True

    def test_health_message(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        assert plugin.health_message == "OK"

    def test_plugin_metadata(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        assert plugin.plugin_id == "legacy"
        assert plugin.display_name == "Legacy Protocol"
        assert plugin.plugin_type == "action"
        assert plugin.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_match_sms(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match("send text to +15551234567 saying hello", {})
        assert m.matched is True
        assert m.intent == "legacy_sms"
        assert "+15551234567" in m.metadata["to"]
        assert m.metadata["message"] == "hello"

    @pytest.mark.asyncio
    async def test_match_sms_variant(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match("send sms to +15551234567: dinner is ready", {})
        assert m.matched is True
        assert m.intent == "legacy_sms"

    @pytest.mark.asyncio
    async def test_match_email(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match(
            "send email to john@example.com about Meeting saying See you at 3pm", {}
        )
        assert m.matched is True
        assert m.intent == "legacy_email"
        assert m.metadata["to"] == "john@example.com"
        assert "Meeting" in m.metadata.get("subject", "")
        assert "3pm" in m.metadata.get("body", "")

    @pytest.mark.asyncio
    async def test_match_serial_command(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match("send command to /dev/ttyUSB0: AT+GMR", {})
        assert m.matched is True
        assert m.intent == "legacy_serial"
        assert m.metadata["port"] == "/dev/ttyUSB0"
        assert m.metadata["command"] == "AT+GMR"

    @pytest.mark.asyncio
    async def test_match_serial_discover(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match("list serial ports", {})
        assert m.matched is True
        assert m.intent == "legacy_serial_discover"

    @pytest.mark.asyncio
    async def test_match_discover_variants(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        for phrase in ["discover serial ports", "find ports", "show serial ports"]:
            m = await plugin.match(phrase, {})
            assert m.matched is True, f"Failed on: {phrase}"

    @pytest.mark.asyncio
    async def test_no_match(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        m = await plugin.match("what's the weather?", {})
        assert m.matched is False

    @pytest.mark.asyncio
    async def test_handle_sms_success(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({"sms_provider": "twilio"})

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=True)
        plugin._sms_gateway = mock_gw

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_sms",
            metadata={"to": "+15551234567", "message": "Hello!"},
        )
        result = await plugin.handle("send text to +15551234567 saying Hello!", match, {})
        assert result.success is True
        assert "sent" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_sms_failure(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=False)
        plugin._sms_gateway = mock_gw

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_sms",
            metadata={"to": "+15551234567", "message": "Hello!"},
        )
        result = await plugin.handle("send text", match, {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_sms_no_number(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_sms",
            metadata={"to": "", "message": "Hello!"},
        )
        result = await plugin.handle("send text", match, {})
        assert result.success is False
        assert "phone number" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_sms_no_message(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_sms",
            metadata={"to": "+1555", "message": ""},
        )
        result = await plugin.handle("send text", match, {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_email_success(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_bridge = AsyncMock()
        mock_bridge.send = AsyncMock(return_value=True)
        plugin._email_bridge = mock_bridge

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_email",
            metadata={"to": "john@test.com", "subject": "Hi", "body": "Hello there"},
        )
        result = await plugin.handle("send email", match, {})
        assert result.success is True
        assert "sent" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_email_no_address(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_email",
            metadata={"to": "", "subject": "", "body": "hi"},
        )
        result = await plugin.handle("send email", match, {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_email_no_body(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_email",
            metadata={"to": "a@b.com", "subject": "", "body": ""},
        )
        result = await plugin.handle("send email", match, {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_serial_command(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_bridge = AsyncMock()
        mock_bridge.send_command = AsyncMock(return_value="OK")
        plugin._serial_bridge = mock_bridge

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_serial",
            metadata={"port": "/dev/ttyUSB0", "command": "AT"},
        )
        result = await plugin.handle("send command", match, {})
        assert result.success is True
        assert "OK" in result.response

    @pytest.mark.asyncio
    async def test_handle_serial_no_response(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_bridge = AsyncMock()
        mock_bridge.send_command = AsyncMock(return_value="")
        plugin._serial_bridge = mock_bridge

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_serial",
            metadata={"port": "/dev/ttyUSB0", "command": "AT"},
        )
        result = await plugin.handle("send command", match, {})
        assert result.success is True
        assert "sent" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_serial_discover(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.legacy.serial_bridge import SerialPort

        mock_bridge = AsyncMock()
        mock_bridge.discover_ports = AsyncMock(
            return_value=[
                SerialPort(device="/dev/ttyUSB0", description="USB Serial"),
                SerialPort(device="/dev/ttyACM0", description="Arduino"),
            ]
        )
        plugin._serial_bridge = mock_bridge

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(matched=True, intent="legacy_serial_discover")
        result = await plugin.handle("list serial ports", match, {})
        assert result.success is True
        assert "/dev/ttyUSB0" in result.response
        assert "/dev/ttyACM0" in result.response

    @pytest.mark.asyncio
    async def test_handle_serial_discover_empty(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_bridge = AsyncMock()
        mock_bridge.discover_ports = AsyncMock(return_value=[])
        plugin._serial_bridge = mock_bridge

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(matched=True, intent="legacy_serial_discover")
        result = await plugin.handle("list serial ports", match, {})
        assert result.success is True
        assert "no serial ports" in result.response.lower()

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(matched=True, intent="legacy_unknown")
        result = await plugin.handle("do something", match, {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_handle_exception(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        await plugin.setup({})

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(side_effect=RuntimeError("boom"))
        plugin._sms_gateway = mock_gw

        from cortex.plugins.base import CommandMatch

        match = CommandMatch(
            matched=True,
            intent="legacy_sms",
            metadata={"to": "+1555", "message": "hi"},
        )
        result = await plugin.handle("send text", match, {})
        assert result.success is False
        assert "error" in result.response.lower()

    def test_lazy_init_sms_gateway(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        plugin._config = {"sms_provider": "vonage"}
        gw = plugin._get_sms_gateway()
        assert gw is not None
        assert gw.provider == "vonage"
        # Second call returns same instance
        assert plugin._get_sms_gateway() is gw

    def test_lazy_init_email_bridge(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        bridge = plugin._get_email_bridge()
        assert bridge is not None
        assert plugin._get_email_bridge() is bridge

    def test_lazy_init_serial_bridge(self):
        from cortex.plugins.legacy import LegacyPlugin

        plugin = LegacyPlugin()
        bridge = plugin._get_serial_bridge()
        assert bridge is not None
        assert plugin._get_serial_bridge() is bridge


# ══════════════════════════════════════════════════════════════════
# Message Logging
# ══════════════════════════════════════════════════════════════════


class TestMessageLogging:
    """Test message logging in the legacy_messages table."""

    def test_log_inbound_message(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('ch1', 'sms', 'SMS')"
        )
        conn.execute(
            "INSERT INTO legacy_messages (channel_id, direction, sender, content) "
            "VALUES ('ch1', 'inbound', '+15551234567', 'Hello Atlas')"
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM legacy_messages WHERE channel_id = 'ch1'"
        ).fetchone()
        assert row["direction"] == "inbound"
        assert row["sender"] == "+15551234567"
        assert row["content"] == "Hello Atlas"

    def test_log_outbound_message(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('ch1', 'sms', 'SMS')"
        )
        conn.execute(
            "INSERT INTO legacy_messages (channel_id, direction, recipient, content) "
            "VALUES ('ch1', 'outbound', '+15551234567', 'Response from Atlas')"
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM legacy_messages WHERE direction = 'outbound'"
        ).fetchone()
        assert row["recipient"] == "+15551234567"
        assert row["content"] == "Response from Atlas"

    def test_message_metadata(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('ch1', 'webhook', 'Hook')"
        )
        meta = json.dumps({"source": "ifttt", "trigger": "motion"})
        conn.execute(
            "INSERT INTO legacy_messages (channel_id, direction, content, metadata) "
            "VALUES ('ch1', 'inbound', 'motion detected', ?)",
            (meta,),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM legacy_messages WHERE channel_id = 'ch1'").fetchone()
        parsed = json.loads(row["metadata"])
        assert parsed["source"] == "ifttt"

    def test_message_count_by_channel(self, db_path):
        conn = get_db()
        conn.execute(
            "INSERT INTO legacy_channels (id, channel_type, name) VALUES ('ch1', 'sms', 'SMS')"
        )
        for i in range(5):
            conn.execute(
                "INSERT INTO legacy_messages (channel_id, direction, content) "
                "VALUES ('ch1', 'inbound', ?)",
                (f"msg{i}",),
            )
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM legacy_messages WHERE channel_id = 'ch1'"
        ).fetchone()[0]
        assert count == 5
