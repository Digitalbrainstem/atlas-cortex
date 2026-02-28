"""Tests for the satellite system — discovery, hardware, provisioning, manager, admin API."""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
import pytest_asyncio

from cortex.db import get_db, init_db, set_db_path


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    yield
    # Cleanup handled by tmp_path


# ── DB Schema Tests ───────────────────────────────────────────────


class TestSatelliteSchema:
    def test_satellites_table_exists(self):
        db = get_db()
        cur = db.execute("PRAGMA table_info(satellites)")
        cols = [r[1] for r in cur.fetchall()]
        assert "id" in cols
        assert "display_name" in cols
        assert "mode" in cols
        assert "status" in cols
        assert "service_port" in cols
        assert "filler_enabled" in cols
        assert "filler_threshold_ms" in cols

    def test_satellite_audio_sessions_table_exists(self):
        db = get_db()
        cur = db.execute("PRAGMA table_info(satellite_audio_sessions)")
        cols = [r[1] for r in cur.fetchall()]
        assert "id" in cols
        assert "satellite_id" in cols
        assert "transcription" in cols

    def test_insert_satellite(self):
        db = get_db()
        db.execute(
            "INSERT INTO satellites (id, display_name, mode, status) VALUES (?, ?, ?, ?)",
            ("sat-test", "Test", "dedicated", "new"),
        )
        db.commit()
        row = db.execute("SELECT * FROM satellites WHERE id = 'sat-test'").fetchone()
        assert row is not None
        assert row[1] == "Test"  # display_name

    def test_mode_check_constraint(self):
        db = get_db()
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO satellites (id, display_name, mode) VALUES (?, ?, ?)",
                ("bad", "Bad", "invalid"),
            )

    def test_status_check_constraint(self):
        db = get_db()
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO satellites (id, display_name, status) VALUES (?, ?, ?)",
                ("bad", "Bad", "invalid_status"),
            )

    def test_audio_session_foreign_key(self):
        db = get_db()
        db.execute(
            "INSERT INTO satellites (id, display_name) VALUES ('sat-1', 'Sat 1')"
        )
        db.execute(
            "INSERT INTO satellite_audio_sessions (id, satellite_id) VALUES ('sess-1', 'sat-1')"
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM satellite_audio_sessions WHERE id = 'sess-1'"
        ).fetchone()
        assert row is not None

    def test_defaults(self):
        db = get_db()
        db.execute(
            "INSERT INTO satellites (id, display_name) VALUES ('sat-def', 'Default Test')"
        )
        db.commit()
        row = db.execute("SELECT * FROM satellites WHERE id = 'sat-def'").fetchone()
        cols = [d[0] for d in db.execute("PRAGMA table_info(satellites)").fetchall()]
        # Build dict
        vals = dict(zip([c[1] for c in db.execute("PRAGMA table_info(satellites)").fetchall()], row))
        assert vals["mode"] == "dedicated"
        assert vals["status"] == "new"
        assert vals["service_port"] == 5110
        assert vals["volume"] == 0.7
        assert vals["filler_enabled"] == 1  # SQLite stores as int
        assert vals["filler_threshold_ms"] == 1500


# ── Discovery Tests ───────────────────────────────────────────────


class TestDiscovery:
    def test_discovered_satellite_dataclass(self):
        from cortex.satellite.discovery import DiscoveredSatellite

        sat = DiscoveredSatellite(
            ip_address="192.168.3.100",
            hostname="atlas-satellite",
            mac_address="aa:bb:cc:dd:ee:ff",
        )
        assert sat.ip_address == "192.168.3.100"
        assert sat.discovery_method == "mdns"
        assert sat.port == 5110

    def test_discovery_init(self):
        from cortex.satellite.discovery import SatelliteDiscovery

        disc = SatelliteDiscovery()
        assert disc.get_announced() == []

    def test_on_service_found(self):
        from cortex.satellite.discovery import SatelliteDiscovery

        disc = SatelliteDiscovery()
        found = []
        disc.on_discovered(lambda s: found.append(s))

        disc._on_service_found(
            "test-sat._atlas-satellite._tcp.local.",
            "192.168.3.55",
            5110,
            {"mac": "aa:bb:cc:dd:ee:ff", "status": "new"},
        )

        assert len(found) == 1
        assert found[0].ip_address == "192.168.3.55"
        assert found[0].mac_address == "aa:bb:cc:dd:ee:ff"

        announced = disc.get_announced()
        assert len(announced) == 1

    def test_duplicate_announcement_ignored(self):
        from cortex.satellite.discovery import SatelliteDiscovery

        disc = SatelliteDiscovery()
        found = []
        disc.on_discovered(lambda s: found.append(s))

        disc._on_service_found("sat1", "192.168.3.55", 5110, {})
        disc._on_service_found("sat1", "192.168.3.55", 5110, {})

        assert len(found) == 1  # callback only fired once

    def test_clear_announced(self):
        from cortex.satellite.discovery import SatelliteDiscovery

        disc = SatelliteDiscovery()
        disc._on_service_found("sat1", "192.168.3.55", 5110, {})
        assert len(disc.get_announced()) == 1

        disc.clear_announced("192.168.3.55")
        assert len(disc.get_announced()) == 0

    def test_get_announced_since(self):
        from cortex.satellite.discovery import SatelliteDiscovery

        disc = SatelliteDiscovery()
        before = time.time()
        disc._on_service_found("sat1", "192.168.3.55", 5110, {})

        assert len(disc.get_announced_since(before - 1)) == 1
        assert len(disc.get_announced_since(time.time() + 1)) == 0


# ── Hardware Detector Tests ───────────────────────────────────────


class TestHardwareDetector:
    @pytest.mark.asyncio
    async def test_detect_platform_rpi4(self):
        from cortex.satellite.hardware import (
            HardwareDetector,
            MockSSHConnection,
            SSHResult,
        )

        mock = MockSSHConnection({
            "cat /proc/device-tree/model 2>/dev/null || echo unknown":
                SSHResult(stdout="Raspberry Pi 4 Model B Rev 1.4\x00"),
            "uname -m": SSHResult(stdout="aarch64\n"),
            "nproc": SSHResult(stdout="4\n"),
            "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2":
                SSHResult(stdout="ARMv7 Processor rev 3 (v7l)\n"),
            "grep MemTotal /proc/meminfo | awk '{print $2}'":
                SSHResult(stdout="3884000\n"),
            "df / --output=size,avail -B M | tail -1":
                SSHResult(stdout="  29300M  15200M\n"),
            "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'":
                SSHResult(stdout="Raspbian GNU/Linux 12 (bookworm)\n"),
            "uname -r": SSHResult(stdout="6.1.0-rpi7-rpi-v8\n"),
        })

        detector = HardwareDetector()
        info = await detector.detect_platform(mock)

        assert "Raspberry Pi 4" in info.model
        assert info.arch == "aarch64"
        assert info.cpu_cores == 4
        assert info.ram_mb == 3792  # 3884000 // 1024
        assert info.disk_free_mb == 15200

    @pytest.mark.asyncio
    async def test_detect_audio(self):
        from cortex.satellite.hardware import (
            HardwareDetector,
            MockSSHConnection,
            SSHResult,
        )

        mock = MockSSHConnection({
            "arecord -l 2>/dev/null || true": SSHResult(stdout=(
                "card 1: seeed2micvoicec [seeed-2mic-voicecard], device 0: "
                "bcm2835-i2s-wm8960-hifi wm8960-hifi-0 [bcm2835-i2s-wm8960-hifi wm8960-hifi-0]\n"
            )),
            "aplay -l 2>/dev/null || true": SSHResult(stdout=(
                "card 0: Headphones [bcm2835 Headphones], device 0: "
                "bcm2835 Headphones bcm2835 Headphones-0 [bcm2835 Headphones bcm2835 Headphones-0]\n"
            )),
            "pactl info 2>/dev/null && echo PA_OK || true": SSHResult(stdout="PA_OK\n"),
            "pw-cli info 0 2>/dev/null && echo PW_OK || true": SSHResult(stdout="\n"),
        })

        detector = HardwareDetector()
        audio = await detector.detect_audio(mock)

        assert len(audio.capture_devices) == 1
        assert "wm8960" in audio.capture_devices[0].name
        assert len(audio.playback_devices) == 1
        assert audio.has_pulseaudio
        assert not audio.has_pipewire

    @pytest.mark.asyncio
    async def test_full_detect(self):
        from cortex.satellite.hardware import (
            HardwareDetector,
            HardwareProfile,
            MockSSHConnection,
            SSHResult,
        )

        # Minimal mock that returns empty for most things
        mock = MockSSHConnection({
            "cat /proc/device-tree/model 2>/dev/null || echo unknown":
                SSHResult(stdout="Raspberry Pi 3 Model B Plus Rev 1.3\x00"),
            "uname -m": SSHResult(stdout="armv7l\n"),
            "nproc": SSHResult(stdout="4\n"),
        })

        detector = HardwareDetector()
        profile = await detector.detect(mock)

        assert isinstance(profile, HardwareProfile)
        assert profile.platform_short() == "rpi3"
        d = profile.to_dict()
        assert "platform" in d
        assert "audio" in d

    @pytest.mark.asyncio
    async def test_capabilities_dict(self):
        from cortex.satellite.hardware import (
            AudioDevice,
            AudioInfo,
            HardwareProfile,
            LEDInfo,
            PlatformInfo,
        )

        profile = HardwareProfile(
            platform=PlatformInfo(model="Raspberry Pi 4 Model B"),
            audio=AudioInfo(
                capture_devices=[AudioDevice(name="ReSpeaker", device_type="capture")],
                playback_devices=[AudioDevice(name="Headphones", device_type="playback")],
            ),
            leds=LEDInfo(led_type="respeaker_apa102", count=12),
        )

        caps = profile.capabilities_dict()
        assert caps["mic"] is True
        assert caps["speaker"] is True
        assert caps["led"] is True
        assert caps["led_type"] == "respeaker_apa102"
        assert caps["aec"] is True  # "respeaker" in device name


# ── Provisioning Tests ────────────────────────────────────────────


class TestProvisioning:
    @pytest.mark.asyncio
    async def test_provision_config(self):
        from cortex.satellite.provisioning import ProvisionConfig

        config = ProvisionConfig(
            satellite_id="sat-test",
            ip_address="192.168.3.100",
            room="kitchen",
        )
        assert config.mode == "dedicated"
        assert config.ssh_username == "atlas"
        assert config.ssh_password == "atlas-setup"

    @pytest.mark.asyncio
    async def test_ensure_server_key(self, tmp_path, monkeypatch):
        from cortex.satellite import provisioning

        monkeypatch.setattr(provisioning, "_SSH_KEY_DIR", tmp_path / "ssh")
        monkeypatch.setattr(provisioning, "_SSH_PRIVATE_KEY", tmp_path / "ssh" / "atlas_satellite")
        monkeypatch.setattr(provisioning, "_SSH_PUBLIC_KEY", tmp_path / "ssh" / "atlas_satellite.pub")

        engine = provisioning.ProvisioningEngine()
        key_path = await engine.ensure_server_key()

        assert key_path.exists()
        assert (tmp_path / "ssh" / "atlas_satellite.pub").exists()


# ── Manager Tests ─────────────────────────────────────────────────


class TestSatelliteManager:
    def test_list_satellites_empty(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        result = mgr.list_satellites()
        assert result == []

    @pytest.mark.asyncio
    async def test_add_manual(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = await mgr.add_manual("192.168.3.100", mode="dedicated")

        assert sat is not None
        assert sat["ip_address"] == "192.168.3.100"
        assert sat["mode"] == "dedicated"
        assert sat["status"] == "new"

    @pytest.mark.asyncio
    async def test_add_manual_shared(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = await mgr.add_manual("192.168.3.100", mode="shared")
        assert sat["mode"] == "shared"

    def test_list_after_add(self):
        from cortex.satellite.manager import SatelliteManager
        import asyncio

        mgr = SatelliteManager()
        asyncio.get_event_loop().run_until_complete(
            mgr.add_manual("192.168.3.100")
        )
        asyncio.get_event_loop().run_until_complete(
            mgr.add_manual("192.168.3.101")
        )

        result = mgr.list_satellites()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_satellite(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        added = await mgr.add_manual("192.168.3.100")
        sat = mgr.get_satellite(added["id"])
        assert sat is not None
        assert sat["ip_address"] == "192.168.3.100"

    @pytest.mark.asyncio
    async def test_get_satellite_not_found(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        assert mgr.get_satellite("nonexistent") is None

    @pytest.mark.asyncio
    async def test_remove(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = await mgr.add_manual("192.168.3.100")
        await mgr.remove(sat["id"])

        assert mgr.get_satellite(sat["id"]) is None

    @pytest.mark.asyncio
    async def test_reconfigure(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = await mgr.add_manual("192.168.3.100")

        updated = await mgr.reconfigure(
            sat["id"],
            display_name="Kitchen Speaker",
            room="kitchen",
            volume=0.5,
        )
        assert updated["display_name"] == "Kitchen Speaker"
        assert updated["room"] == "kitchen"
        assert updated["volume"] == 0.5

    @pytest.mark.asyncio
    async def test_reconfigure_no_valid_fields(self):
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = await mgr.add_manual("192.168.3.100")

        with pytest.raises(ValueError, match="No valid fields"):
            await mgr.reconfigure(sat["id"], invalid_field="test")

    def test_on_satellite_discovered_creates_record(self):
        from cortex.satellite.discovery import DiscoveredSatellite
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = DiscoveredSatellite(
            ip_address="192.168.3.55",
            hostname="atlas-satellite",
            mac_address="aa:bb:cc:dd:ee:ff",
        )
        mgr._on_satellite_discovered(sat)

        db = get_db()
        row = db.execute(
            "SELECT * FROM satellites WHERE ip_address = '192.168.3.55'"
        ).fetchone()
        assert row is not None

    def test_on_satellite_discovered_dedup(self):
        from cortex.satellite.discovery import DiscoveredSatellite
        from cortex.satellite.manager import SatelliteManager

        mgr = SatelliteManager()
        sat = DiscoveredSatellite(ip_address="192.168.3.55", hostname="atlas-satellite")
        mgr._on_satellite_discovered(sat)
        mgr._on_satellite_discovered(sat)  # duplicate

        db = get_db()
        count = db.execute(
            "SELECT COUNT(*) FROM satellites WHERE ip_address = '192.168.3.55'"
        ).fetchone()[0]
        assert count == 1


# ── Admin API Tests ───────────────────────────────────────────────


class TestSatelliteAdminAPI:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from cortex.server import app
        return TestClient(app)

    @pytest.fixture
    def auth_header(self, client):
        resp = client.post("/admin/auth/login", json={"username": "admin", "password": "atlas-admin"})
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}

    def test_list_satellites(self, client, auth_header):
        resp = client.get("/admin/satellites", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "satellites" in data
        assert "total" in data
        assert "announced_count" in data

    def test_add_satellite(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.100", "mode": "dedicated"},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ip_address"] == "192.168.3.100"
        assert data["mode"] == "dedicated"
        assert "id" in data

    def test_get_satellite(self, client, auth_header):
        # Add first
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.101"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        # Get
        resp = client.get(f"/admin/satellites/{sat_id}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["id"] == sat_id

    def test_get_satellite_not_found(self, client, auth_header):
        resp = client.get("/admin/satellites/nonexistent", headers=auth_header)
        assert resp.status_code == 404

    def test_update_satellite(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.102"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        resp = client.patch(
            f"/admin/satellites/{sat_id}",
            json={"display_name": "Kitchen", "room": "kitchen", "volume": 0.5},
            headers=auth_header,
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Kitchen"
        assert resp.json()["volume"] == 0.5

    def test_update_satellite_empty(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.103"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        resp = client.patch(
            f"/admin/satellites/{sat_id}",
            json={},
            headers=auth_header,
        )
        assert resp.status_code == 400

    def test_delete_satellite(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.104"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        resp = client.delete(f"/admin/satellites/{sat_id}", headers=auth_header)
        assert resp.status_code == 200

        resp = client.get(f"/admin/satellites/{sat_id}", headers=auth_header)
        assert resp.status_code == 404

    def test_discover_satellites(self, client, auth_header):
        resp = client.post("/admin/satellites/discover", headers=auth_header)
        assert resp.status_code == 200
        assert "found" in resp.json()

    def test_restart_satellite_not_connected(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.105"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        resp = client.post(f"/admin/satellites/{sat_id}/restart", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["sent"] is False

    def test_identify_satellite(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={"ip_address": "192.168.3.106"},
            headers=auth_header,
        )
        sat_id = resp.json()["id"]

        resp = client.post(f"/admin/satellites/{sat_id}/identify", headers=auth_header)
        assert resp.status_code == 200

    def test_auth_required(self, client):
        resp = client.get("/admin/satellites")
        assert resp.status_code in (401, 403)

    def test_add_shared_mode(self, client, auth_header):
        resp = client.post(
            "/admin/satellites/add",
            json={
                "ip_address": "192.168.3.110",
                "mode": "shared",
                "ssh_username": "derek",
                "service_port": 5110,
            },
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "shared"
        assert data["service_port"] == 5110
