"""Admin API router for Legacy Protocol channels and messages."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cortex.admin.helpers import _db, _row, _rows, require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request Models ──────────────────────────────────────────────

class ChannelCreateRequest(BaseModel):
    channel_type: str  # sms, email, webhook, serial
    name: str
    config: dict[str, Any] = {}
    enabled: bool = True


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ChannelTestRequest(BaseModel):
    message: str = "Hello from Atlas!"


# ── Channel CRUD ────────────────────────────────────────────────

@router.get("/legacy/channels")
async def list_channels(_: dict = Depends(require_admin)):
    """List all legacy channels."""
    conn = _db()
    cur = conn.execute(
        "SELECT id, channel_type, name, config, enabled, last_activity, created_at "
        "FROM legacy_channels ORDER BY created_at DESC"
    )
    channels = []
    for r in _rows(cur):
        r["config"] = json.loads(r.get("config", "{}") or "{}")
        r["enabled"] = bool(r.get("enabled", 1))
        channels.append(r)
    return {"channels": channels}


@router.post("/legacy/channels", status_code=201)
async def create_channel(req: ChannelCreateRequest, _: dict = Depends(require_admin)):
    """Create a new legacy channel."""
    import secrets

    valid_types = ("sms", "email", "webhook", "serial")
    if req.channel_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid type. Must be one of: {valid_types}")

    channel_id = secrets.token_urlsafe(16)

    # Auto-generate secret for webhook channels
    config = dict(req.config)
    if req.channel_type == "webhook" and "secret" not in config:
        config["secret"] = secrets.token_hex(32)

    conn = _db()
    conn.execute(
        "INSERT INTO legacy_channels (id, channel_type, name, config, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        (channel_id, req.channel_type, req.name, json.dumps(config), int(req.enabled)),
    )
    conn.commit()
    return {"id": channel_id, "name": req.name, "channel_type": req.channel_type}


@router.get("/legacy/channels/{channel_id}")
async def get_channel(channel_id: str, _: dict = Depends(require_admin)):
    """Get a single channel."""
    conn = _db()
    cur = conn.execute(
        "SELECT id, channel_type, name, config, enabled, last_activity, created_at "
        "FROM legacy_channels WHERE id = ?",
        (channel_id,),
    )
    row = _row(cur)
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")
    row["config"] = json.loads(row.get("config", "{}") or "{}")
    row["enabled"] = bool(row.get("enabled", 1))
    return {"channel": row}


@router.patch("/legacy/channels/{channel_id}")
async def update_channel(
    channel_id: str, req: ChannelUpdateRequest, _: dict = Depends(require_admin)
):
    """Update a legacy channel (PATCH — only provided fields)."""
    conn = _db()
    parts: list[str] = []
    params: list[object] = []

    if req.name is not None:
        parts.append("name = ?")
        params.append(req.name)
    if req.config is not None:
        parts.append("config = ?")
        params.append(json.dumps(req.config))
    if req.enabled is not None:
        parts.append("enabled = ?")
        params.append(int(req.enabled))

    if not parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(channel_id)
    cur = conn.execute(
        f"UPDATE legacy_channels SET {', '.join(parts)} WHERE id = ?", params
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True}


@router.delete("/legacy/channels/{channel_id}")
async def delete_channel(channel_id: str, _: dict = Depends(require_admin)):
    """Delete a legacy channel and its messages."""
    conn = _db()
    conn.execute("DELETE FROM legacy_messages WHERE channel_id = ?", (channel_id,))
    cur = conn.execute("DELETE FROM legacy_channels WHERE id = ?", (channel_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True}


# ── Messages ────────────────────────────────────────────────────

@router.get("/legacy/messages")
async def list_messages(
    channel_id: str | None = None,
    direction: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_admin),
):
    """List legacy messages with optional filters."""
    conn = _db()
    query = (
        "SELECT m.id, m.channel_id, m.direction, m.sender, m.recipient, "
        "m.content, m.metadata, m.processed, m.created_at, c.name as channel_name, "
        "c.channel_type FROM legacy_messages m "
        "LEFT JOIN legacy_channels c ON m.channel_id = c.id "
    )
    conditions: list[str] = []
    params: list[object] = []

    if channel_id:
        conditions.append("m.channel_id = ?")
        params.append(channel_id)
    if direction:
        conditions.append("m.direction = ?")
        params.append(direction)

    if conditions:
        query += "WHERE " + " AND ".join(conditions) + " "
    query += "ORDER BY m.created_at DESC LIMIT ?"
    params.append(min(limit, 500))

    cur = conn.execute(query, params)
    messages = _rows(cur)
    for msg in messages:
        msg["metadata"] = json.loads(msg.get("metadata", "{}") or "{}")
        msg["processed"] = bool(msg.get("processed", 0))
    return {"messages": messages}


# ── Test Channel ────────────────────────────────────────────────

@router.post("/legacy/channels/{channel_id}/test")
async def test_channel(
    channel_id: str, req: ChannelTestRequest, _: dict = Depends(require_admin)
):
    """Test a legacy channel by sending a test message."""
    conn = _db()
    cur = conn.execute(
        "SELECT id, channel_type, name, config, enabled FROM legacy_channels WHERE id = ?",
        (channel_id,),
    )
    row = _row(cur)
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel_type = row["channel_type"]
    config = json.loads(row.get("config", "{}") or "{}")

    try:
        if channel_type == "sms":
            from cortex.legacy.sms import SMSGateway

            gw = SMSGateway(
                provider=config.get("provider", "twilio"),
                api_key=config.get("api_key", ""),
                api_secret=config.get("api_secret", ""),
                from_number=config.get("from_number", ""),
            )
            to = config.get("test_number", config.get("to", ""))
            if not to:
                return {"ok": False, "error": "No test number configured"}
            ok = await gw.send(to, req.message)
            return {"ok": ok, "message": f"SMS {'sent' if ok else 'failed'}"}

        if channel_type == "email":
            from cortex.legacy.email_bridge import EmailBridge

            bridge = EmailBridge(
                smtp_host=config.get("smtp_host", ""),
                smtp_port=int(config.get("smtp_port", 587)),
                user=config.get("user", ""),
                password=config.get("password", ""),
            )
            to = config.get("test_email", config.get("to", ""))
            if not to:
                return {"ok": False, "error": "No test email configured"}
            ok = await bridge.send(to, "Atlas Test", req.message)
            return {"ok": ok, "message": f"Email {'sent' if ok else 'failed'}"}

        if channel_type == "webhook":
            return {"ok": True, "message": "Webhook channels receive, not send"}

        if channel_type == "serial":
            from cortex.legacy.serial_bridge import SerialBridge

            bridge = SerialBridge()
            port = config.get("port", "")
            if not port:
                return {"ok": False, "error": "No serial port configured"}
            baud = int(config.get("baud", 9600))
            resp = await bridge.send_command(port, req.message, baud=baud)
            return {"ok": True, "message": f"Response: {resp}"}

        return {"ok": False, "error": f"Unknown channel type: {channel_type}"}

    except Exception as exc:
        logger.exception("Channel test failed")
        return {"ok": False, "error": str(exc)}
