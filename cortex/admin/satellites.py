"""Satellite management endpoints."""

from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from cortex.db import get_db
from cortex.admin.helpers import require_admin

router = APIRouter()


# Lazy-init singleton to avoid import-time side effects
_satellite_manager = None


def _get_satellite_manager():
    global _satellite_manager
    if _satellite_manager is None:
        from cortex.satellite.manager import SatelliteManager
        _satellite_manager = SatelliteManager()
    return _satellite_manager


class SatelliteAddRequest(BaseModel):
    ip_address: str
    mode: str = "dedicated"
    ssh_username: str = "atlas"
    ssh_password: str = "atlas"
    service_port: int = 5110


class SatelliteProvisionRequest(BaseModel):
    room: str
    display_name: str = ""
    features: dict = Field(default_factory=dict)
    ssh_password: str = "atlas"


class SatelliteUpdateRequest(BaseModel):
    display_name: str | None = None
    room: str | None = None
    wake_word: str | None = None
    volume: float | None = None
    mic_gain: float | None = None
    vad_sensitivity: float | None = None
    features: dict | None = None
    filler_enabled: bool | None = None
    filler_threshold_ms: int | None = None
    tts_voice: str | None = None
    vad_enabled: bool | None = None
    led_brightness: float | None = None
    audio_device_out: str | None = None
    button_mode: str | None = None  # toggle | press | hold


@router.get("/satellites")
async def list_satellites(
    status: str | None = Query(None),
    mode: str | None = Query(None),
    admin: dict = Depends(require_admin),
):
    """List all satellites with optional filters."""
    mgr = _get_satellite_manager()
    satellites = mgr.list_satellites(status=status, mode=mode)
    # Include announced (undiscovered) count
    announced = await mgr.get_discovered()
    return {
        "satellites": satellites,
        "total": len(satellites),
        "announced_count": len(announced),
    }


@router.get("/satellites/announced")
async def list_announced(admin: dict = Depends(require_admin)):
    """List satellites that have self-announced but aren't yet registered."""
    mgr = _get_satellite_manager()
    announced = await mgr.get_discovered()
    return {
        "announced": [
            {
                "ip_address": s.ip_address,
                "hostname": s.hostname,
                "mac_address": s.mac_address,
                "port": s.port,
                "properties": s.properties,
                "discovered_at": s.discovered_at,
            }
            for s in announced
        ]
    }


@router.get("/satellites/{satellite_id}")
async def get_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Get satellite detail with hardware info."""
    mgr = _get_satellite_manager()
    sat = mgr.get_satellite(satellite_id)
    if not sat:
        raise HTTPException(status_code=404, detail="Satellite not found")
    return sat


@router.post("/satellites/discover")
async def discover_satellites(admin: dict = Depends(require_admin)):
    """Trigger a one-time network scan (fallback for mDNS-blocked networks)."""
    mgr = _get_satellite_manager()
    found = await mgr.scan_now()
    return {
        "found": [
            {
                "ip_address": s.ip_address,
                "hostname": s.hostname,
                "mac_address": s.mac_address,
                "discovery_method": s.discovery_method,
            }
            for s in found
        ],
        "count": len(found),
    }


@router.post("/satellites/add")
async def add_satellite(req: SatelliteAddRequest, admin: dict = Depends(require_admin)):
    """Manually add a satellite by IP address."""
    mgr = _get_satellite_manager()
    sat = await mgr.add_manual(
        ip_address=req.ip_address,
        mode=req.mode,
        ssh_username=req.ssh_username,
        ssh_password=req.ssh_password,
        service_port=req.service_port,
    )
    return sat


@router.post("/satellites/{satellite_id}/detect")
async def detect_hardware(
    satellite_id: str,
    ssh_password: str = "atlas",
    admin: dict = Depends(require_admin),
):
    """SSH into a satellite and detect its hardware."""
    mgr = _get_satellite_manager()
    try:
        profile = await mgr.detect_hardware(satellite_id, ssh_password=ssh_password)
        return {
            "satellite_id": satellite_id,
            "platform": profile.platform_short(),
            "hardware": profile.to_dict(),
            "capabilities": profile.capabilities_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/satellites/{satellite_id}/provision")
async def provision_satellite(
    satellite_id: str,
    req: SatelliteProvisionRequest,
    admin: dict = Depends(require_admin),
):
    """Start provisioning a satellite."""
    mgr = _get_satellite_manager()
    try:
        result = await mgr.provision(
            satellite_id=satellite_id,
            room=req.room,
            display_name=req.display_name,
            features=req.features,
            ssh_password=req.ssh_password,
        )
        return {
            "success": result.success,
            "error": result.error,
            "steps": [
                {"name": s.name, "status": s.status, "detail": s.detail}
                for s in result.steps
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/satellites/{satellite_id}")
async def update_satellite(
    satellite_id: str,
    req: SatelliteUpdateRequest,
    admin: dict = Depends(require_admin),
):
    """Update satellite configuration."""
    mgr = _get_satellite_manager()
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        sat = await mgr.reconfigure(satellite_id, **updates)
        return sat
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/satellites/{satellite_id}/restart")
async def restart_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Restart the satellite agent service."""
    mgr = _get_satellite_manager()
    sent = await mgr.restart_agent(satellite_id)
    return {"sent": sent, "detail": "Restart command sent" if sent else "Satellite not connected"}


@router.post("/satellites/{satellite_id}/identify")
async def identify_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Blink LEDs on a satellite for physical identification."""
    mgr = _get_satellite_manager()
    sent = await mgr.identify(satellite_id)
    return {"sent": sent}


@router.post("/satellites/{satellite_id}/test")
async def test_satellite_audio(satellite_id: str, admin: dict = Depends(require_admin)):
    """Run an audio test on the satellite."""
    mgr = _get_satellite_manager()
    sent = await mgr.test_audio(satellite_id)
    return {"sent": sent}


@router.post("/satellites/{satellite_id}/command")
async def send_satellite_command(satellite_id: str, body: dict, admin: dict = Depends(require_admin)):
    """Send a remote management command or arbitrary command to a connected satellite."""
    from cortex.satellite.websocket import send_command, send_remote_command, REMOTE_CMD_TYPES

    cmd_type = body.get("type", "")
    # Remote management command (new protocol)
    if cmd_type and cmd_type in REMOTE_CMD_TYPES:
        payload = body.get("payload", {})
        try:
            result = await send_remote_command(satellite_id, cmd_type, payload)
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Legacy arbitrary command (existing protocol)
    action = body.get("action", "")
    params = body.get("params")
    if not action and not cmd_type:
        raise HTTPException(status_code=400, detail="Missing 'type' or 'action'")
    if not action:
        raise HTTPException(status_code=400, detail=f"Unknown command type: {cmd_type}")
    sent = await send_command(satellite_id, action, params)
    return {"sent": sent}


@router.get("/satellites/{satellite_id}/commands")
async def get_satellite_commands(
    satellite_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """Get command history for a satellite."""
    from cortex.satellite.websocket import get_command_history
    commands = get_command_history(satellite_id, limit=limit, offset=offset)
    return {"commands": commands, "total": len(commands)}


@router.post("/satellites/{satellite_id}/config")
async def push_satellite_config(
    satellite_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
):
    """Push a CONFIG_UPDATE command to a satellite."""
    from cortex.satellite.websocket import send_remote_command
    payload = body.get("payload", body)
    # Strip the 'type' key if it came through from the body
    payload = {k: v for k, v in payload.items() if k != "type"}
    if not payload:
        raise HTTPException(status_code=400, detail="Empty config payload")
    result = await send_remote_command(satellite_id, "CONFIG_UPDATE", payload)
    return result


@router.post("/satellites/{satellite_id}/update")
async def update_satellite_agent(
    satellite_id: str,
    admin: dict = Depends(require_admin),
):
    """Trigger a git pull + pip install + restart on the satellite."""
    from cortex.satellite.websocket import send_remote_command
    result = await send_remote_command(satellite_id, "UPDATE_AGENT", {})
    return result


@router.post("/satellites/{satellite_id}/reboot")
async def reboot_satellite(
    satellite_id: str,
    admin: dict = Depends(require_admin),
):
    """Reboot the satellite device."""
    from cortex.satellite.websocket import send_remote_command
    result = await send_remote_command(satellite_id, "REBOOT", {})
    return result


@router.post("/satellites/{satellite_id}/kiosk-url")
async def set_kiosk_url(
    satellite_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
):
    """Change the kiosk display URL on a satellite."""
    from cortex.satellite.websocket import send_remote_command
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'")
    result = await send_remote_command(satellite_id, "KIOSK_URL", {"url": url})
    return result


@router.patch("/satellites/{satellite_id}/led_config")
async def update_led_config(satellite_id: str, body: dict, admin: dict = Depends(require_admin)):
    """Update LED pattern colors for a satellite and push live."""
    from cortex.satellite.websocket import send_command
    patterns = body.get("patterns", {})
    if not patterns:
        raise HTTPException(status_code=400, detail="Missing patterns")
    # Store in DB
    db = get_db()
    existing = db.execute("SELECT led_config FROM satellites WHERE id = ?", (satellite_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Satellite not found")
    current = _json.loads(existing["led_config"]) if existing["led_config"] else {}
    current.update(patterns)
    db.execute("UPDATE satellites SET led_config = ? WHERE id = ?", (_json.dumps(current), satellite_id))
    db.commit()
    # Push to satellite
    sent = await send_command(satellite_id, "led_config", {"patterns": patterns})
    return {"saved": True, "pushed": sent}


@router.get("/satellites/{satellite_id}/led_config")
async def get_led_config(satellite_id: str, admin: dict = Depends(require_admin)):
    """Get the LED pattern configuration for a satellite."""
    db = get_db()
    row = db.execute("SELECT led_config FROM satellites WHERE id = ?", (satellite_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Satellite not found")
    config = _json.loads(row["led_config"]) if row["led_config"] else {}
    # Return defaults merged with custom
    defaults = {
        "idle": {"r": 0, "g": 0, "b": 0, "brightness": 0.0},
        "listening": {"r": 0, "g": 100, "b": 255, "brightness": 0.4},
        "thinking": {"r": 255, "g": 165, "b": 0, "brightness": 0.3},
        "speaking": {"r": 0, "g": 200, "b": 100, "brightness": 0.4},
        "error": {"r": 255, "g": 0, "b": 0, "brightness": 0.5},
        "muted": {"r": 255, "g": 0, "b": 0, "brightness": 0.1},
        "wakeword": {"r": 0, "g": 200, "b": 255, "brightness": 0.6},
    }
    merged = {**defaults, **config}
    return {"patterns": merged}


@router.delete("/satellites/{satellite_id}")
async def remove_satellite(satellite_id: str, admin: dict = Depends(require_admin)):
    """Remove and deregister a satellite."""
    mgr = _get_satellite_manager()
    await mgr.remove(satellite_id)
    return {"removed": True}


# ── SSH provisioning & password rotation ─────────────────────────


@router.post("/satellites/{satellite_id}/push-ssh-key")
async def push_ssh_key(satellite_id: str, admin: dict = Depends(require_admin)):
    """Push the Atlas server SSH public key to the satellite and rotate password."""
    from cortex.satellite.provisioning import ProvisioningEngine, provision_ssh_key

    key_path = await ProvisioningEngine.ensure_server_key()
    public_key = key_path.with_suffix(".pub").read_text().strip()

    try:
        await provision_ssh_key(satellite_id, public_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "message": "SSH key installed and password rotated"}


@router.post("/satellites/{satellite_id}/rotate-password")
async def rotate_satellite_password(
    satellite_id: str, admin: dict = Depends(require_admin)
):
    """Generate a new random password for the satellite."""
    from cortex.satellite.provisioning import rotate_password

    try:
        new_pw = await rotate_password(satellite_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "password": new_pw}


@router.get("/satellites/{satellite_id}/ssh-info")
async def get_ssh_info(satellite_id: str, admin: dict = Depends(require_admin)):
    """Return SSH connection details for a satellite."""
    db = get_db()
    row = db.execute(
        "SELECT ip_address, ssh_username, ssh_password, ssh_key_installed "
        "FROM satellites WHERE id = ?",
        (satellite_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Satellite not found")
    return {
        "ip_address": row[0],
        "ssh_username": row[1] or "atlas",
        "ssh_password": row[2],
        "ssh_key_installed": bool(row[3]),
        "ssh_command": f"ssh {row[1] or 'atlas'}@{row[0]}" if row[0] else None,
    }
