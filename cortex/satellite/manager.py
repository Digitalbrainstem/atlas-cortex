"""Satellite lifecycle manager.

Orchestrates discovery, hardware detection, provisioning, and ongoing
management of all satellite devices. This is the main entry point that
the admin API and server startup use.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from cortex.db import get_db, init_db
from cortex.satellite.discovery import DiscoveredSatellite, SatelliteDiscovery
from cortex.satellite.hardware import (
    HardwareDetector,
    HardwareProfile,
    connect_ssh,
)
from cortex.satellite.provisioning import ProvisionConfig, ProvisionResult, ProvisioningEngine
from cortex.satellite.websocket import (
    get_connected_satellites,
    send_command,
    send_config,
)

logger = logging.getLogger(__name__)


class SatelliteManager:
    """Central manager for all satellite operations."""

    def __init__(self) -> None:
        self.discovery = SatelliteDiscovery()
        self.detector = HardwareDetector()
        self.provisioner = ProvisioningEngine()
        self._server_url: str = ""

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self, server_url: str = "") -> None:
        """Start the satellite subsystem (mDNS listener, etc.)."""
        self._server_url = server_url
        self.discovery.on_discovered(self._on_satellite_discovered)
        await self.discovery.start()
        logger.info("SatelliteManager started")

    async def stop(self) -> None:
        """Stop the satellite subsystem."""
        await self.discovery.stop()
        logger.info("SatelliteManager stopped")

    # ── Discovery ──────────────────────────────────────────────────

    async def get_discovered(self) -> list[DiscoveredSatellite]:
        """Return all satellites that have self-announced."""
        return self.discovery.get_announced()

    async def scan_now(self, timeout: float = 5.0) -> list[DiscoveredSatellite]:
        """Admin-triggered one-time network scan."""
        return await self.discovery.scan_now(timeout)

    async def add_manual(
        self,
        ip_address: str,
        mode: str = "dedicated",
        ssh_username: str = "atlas",
        ssh_password: str = "atlas-setup",
        service_port: int = 5110,
    ) -> dict:
        """Manually add a satellite by IP address."""
        satellite_id = f"sat-{uuid.uuid4().hex[:8]}"

        db = get_db()
        db.execute(
            """INSERT INTO satellites
               (id, display_name, ip_address, mode, ssh_username, service_port, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (satellite_id, f"Satellite ({ip_address})", ip_address, mode,
             ssh_username, service_port, "new"),
        )
        db.commit()

        return self._get_satellite(satellite_id)

    # ── Hardware detection ─────────────────────────────────────────

    async def detect_hardware(
        self,
        satellite_id: str,
        ssh_username: str | None = None,
        ssh_password: str | None = None,
    ) -> HardwareProfile:
        """SSH into a satellite and detect its hardware."""
        sat = self._get_satellite(satellite_id)
        if not sat:
            raise ValueError(f"Satellite not found: {satellite_id}")

        username = ssh_username or sat.get("ssh_username", "atlas")
        password = ssh_password or "atlas-setup"
        ip = sat["ip_address"]

        self._update(satellite_id, status="detecting")

        try:
            ssh = await connect_ssh(ip, username=username, password=password)
            profile = await self.detector.detect(ssh)
            await ssh.close()

            # Store results
            self._update(
                satellite_id,
                platform=profile.platform_short(),
                hardware_info=json.dumps(profile.to_dict()),
                capabilities=json.dumps(profile.capabilities_dict()),
                status="new",
            )

            return profile
        except Exception as e:
            self._update(satellite_id, status="error")
            raise

    # ── Provisioning ───────────────────────────────────────────────

    async def provision(
        self,
        satellite_id: str,
        room: str,
        display_name: str = "",
        features: dict | None = None,
        ssh_password: str = "atlas-setup",
    ) -> ProvisionResult:
        """Provision a satellite with the given configuration."""
        sat = self._get_satellite(satellite_id)
        if not sat:
            raise ValueError(f"Satellite not found: {satellite_id}")

        self._update(satellite_id, status="provisioning")

        config = ProvisionConfig(
            satellite_id=satellite_id,
            ip_address=sat["ip_address"],
            mode=sat.get("mode", "dedicated"),
            room=room,
            display_name=display_name or f"{room.title()} Satellite",
            ssh_username=sat.get("ssh_username", "atlas"),
            ssh_password=ssh_password,
            service_port=sat.get("service_port", 5110),
            server_url=self._server_url,
            features=features or {},
        )

        result = await self.provisioner.provision(config)

        if result.success:
            self._update(
                satellite_id,
                display_name=config.display_name,
                hostname=config.hostname or f"atlas-sat-{room}".lower().replace(" ", "-"),
                room=room,
                features=json.dumps(features or {}),
                status="online",
                ssh_key_installed=sat.get("mode", "dedicated") == "dedicated",
                provision_state=json.dumps([
                    {"name": s.name, "status": s.status, "detail": s.detail}
                    for s in result.steps
                ]),
                provisioned_at=datetime.now(timezone.utc).isoformat(),
            )
            # Remove from discovery announced list
            self.discovery.clear_announced(sat["ip_address"])
        else:
            self._update(
                satellite_id,
                status="error",
                provision_state=json.dumps([
                    {"name": s.name, "status": s.status, "detail": s.detail}
                    for s in result.steps
                ]),
            )

        return result

    # ── Management ─────────────────────────────────────────────────

    async def reconfigure(self, satellite_id: str, **kwargs) -> dict:
        """Update satellite configuration."""
        allowed = {
            "display_name", "room", "wake_word", "volume", "mic_gain",
            "vad_sensitivity", "features", "filler_enabled", "filler_threshold_ms",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            raise ValueError("No valid fields to update")

        self._update(satellite_id, **updates)

        # Push config to connected satellite
        await send_config(satellite_id, updates)

        return self._get_satellite(satellite_id)

    async def restart_agent(self, satellite_id: str) -> bool:
        """Restart the satellite agent service."""
        return await send_command(satellite_id, "reboot")

    async def identify(self, satellite_id: str) -> bool:
        """Blink LEDs on a satellite for physical identification."""
        return await send_command(satellite_id, "led", {"pattern": "identify", "duration": 10})

    async def test_audio(self, satellite_id: str) -> bool:
        """Trigger an audio test on the satellite."""
        return await send_command(satellite_id, "test_audio")

    async def remove(self, satellite_id: str) -> None:
        """Remove a satellite and optionally uninstall the agent."""
        sat = self._get_satellite(satellite_id)
        if not sat:
            return

        # TODO: SSH in and uninstall agent for dedicated mode

        db = get_db()
        db.execute("DELETE FROM satellite_audio_sessions WHERE satellite_id = ?", (satellite_id,))
        db.execute("DELETE FROM satellites WHERE id = ?", (satellite_id,))
        db.commit()
        logger.info("Removed satellite: %s", satellite_id)

    # ── Queries ────────────────────────────────────────────────────

    def list_satellites(
        self,
        status: str | None = None,
        mode: str | None = None,
    ) -> list[dict]:
        """List all satellites, optionally filtered."""
        db = get_db()
        query = "SELECT * FROM satellites WHERE is_active = 1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if mode:
            query += " AND mode = ?"
            params.append(mode)
        query += " ORDER BY registered_at DESC"

        cur = db.execute(query, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # Augment with connection status
        connected = get_connected_satellites()
        for row in rows:
            row["is_connected"] = row["id"] in connected

        return rows

    def get_satellite(self, satellite_id: str) -> dict | None:
        """Get a satellite by ID, augmented with connection status."""
        sat = self._get_satellite(satellite_id)
        if sat:
            connected = get_connected_satellites()
            sat["is_connected"] = satellite_id in connected
        return sat

    # ── Internal ───────────────────────────────────────────────────

    def _on_satellite_discovered(self, sat: DiscoveredSatellite) -> None:
        """Callback when a new satellite announces via mDNS."""
        db = get_db()
        satellite_id = f"sat-{uuid.uuid4().hex[:8]}"

        # Check if we already have this IP
        cur = db.execute("SELECT id FROM satellites WHERE ip_address = ?", (sat.ip_address,))
        existing = cur.fetchone()
        if existing:
            # Update last_seen for existing satellite
            db.execute(
                "UPDATE satellites SET last_seen = ?, status = CASE WHEN status = 'offline' THEN 'online' ELSE status END WHERE ip_address = ?",
                (datetime.now(timezone.utc).isoformat(), sat.ip_address),
            )
            db.commit()
            return

        # Insert new satellite
        db.execute(
            """INSERT INTO satellites
               (id, display_name, hostname, ip_address, mac_address, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                satellite_id,
                sat.hostname or f"Satellite ({sat.ip_address})",
                sat.hostname,
                sat.ip_address,
                sat.mac_address,
                "announced",
            ),
        )
        db.commit()
        logger.info("New satellite registered: %s at %s", satellite_id, sat.ip_address)

    def _get_satellite(self, satellite_id: str) -> dict | None:
        """Get a satellite row as a dict."""
        db = get_db()
        cur = db.execute("SELECT * FROM satellites WHERE id = ?", (satellite_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _update(self, satellite_id: str, **kwargs) -> None:
        """Update satellite fields."""
        if not kwargs:
            return
        db = get_db()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [satellite_id]
        db.execute(f"UPDATE satellites SET {sets} WHERE id = ?", values)
        db.commit()
