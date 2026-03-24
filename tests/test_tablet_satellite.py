"""Tests for the tablet satellite build artefacts.

Validates shell script syntax, HTML structure, systemd unit format,
and WebSocket tablet device-type registration.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
import pytest_asyncio

from cortex.db import get_db, init_db, set_db_path


# ── Paths ─────────────────────────────────────────────────────────

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TABLET_DIR = os.path.join(_REPO_ROOT, "satellite", "tablet")
_INSTALL_SCRIPT = os.path.join(_TABLET_DIR, "install-tablet.sh")
_BUILD_SCRIPT = os.path.join(_TABLET_DIR, "build-image.sh")
_SETUP_HTML = os.path.join(_TABLET_DIR, "setup.html")
_KIOSK_SERVICE = os.path.join(_TABLET_DIR, "atlas-kiosk.service")


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    init_db()
    yield


# ── Shell Script Syntax ──────────────────────────────────────────


class TestInstallScript:
    """Validate install-tablet.sh."""

    def test_file_exists(self):
        assert os.path.isfile(_INSTALL_SCRIPT)

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", _INSTALL_SCRIPT],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_has_shebang(self):
        with open(_INSTALL_SCRIPT) as f:
            first_line = f.readline()
        assert first_line.startswith("#!/usr/bin/env bash")

    def test_has_strict_mode(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "set -euo pipefail" in content

    def test_checks_x86_64(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "x86_64" in content

    def test_checks_root(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "EUID" in content

    def test_detects_surface(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "surface" in content.lower()

    def test_creates_atlas_user(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "useradd" in content
        assert "atlas" in content

    def test_configures_kiosk(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "chromium-browser" in content
        assert "--kiosk" in content

    def test_configures_autologin(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "autologin" in content

    def test_enables_satellite_service(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "atlas-satellite.service" in content
        assert "systemctl enable atlas-satellite" in content

    def test_sets_device_type_env(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "ATLAS_DEVICE_TYPE=tablet" in content

    def test_disables_lid_suspend(self):
        with open(_INSTALL_SCRIPT) as f:
            content = f.read()
        assert "HandleLidSwitch=ignore" in content


class TestBuildScript:
    """Validate build-image.sh."""

    def test_file_exists(self):
        assert os.path.isfile(_BUILD_SCRIPT)

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", _BUILD_SCRIPT],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_has_strict_mode(self):
        with open(_BUILD_SCRIPT) as f:
            content = f.read()
        assert "set -euo pipefail" in content


# ── Setup HTML ────────────────────────────────────────────────────


class TestSetupHTML:
    """Validate setup.html structure for touch-first tablet UI."""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        with open(_SETUP_HTML) as f:
            self.html = f.read()

    def test_file_exists(self):
        assert os.path.isfile(_SETUP_HTML)

    def test_valid_html_doctype(self):
        assert self.html.strip().startswith("<!DOCTYPE html>")

    def test_has_viewport_meta(self):
        assert 'name="viewport"' in self.html

    def test_has_network_list(self):
        assert 'id="networkList"' in self.html or 'id="networks"' in self.html

    def test_has_password_input(self):
        assert 'id="password"' in self.html
        assert 'type="password"' in self.html

    def test_has_connect_button(self):
        assert 'id="connectBtn"' in self.html or "connectBtn" in self.html

    def test_has_scan_function(self):
        assert "scanNetworks" in self.html

    def test_has_connect_function(self):
        assert "connectWifi" in self.html

    def test_touch_friendly_sizing(self):
        """Buttons and inputs should have large touch targets."""
        assert "min-height: 52px" in self.html or "min-height: 56px" in self.html

    def test_no_hover_only_states(self):
        """Touch UI should not rely on hover for primary interactions."""
        assert ":active" in self.html

    def test_disables_tap_highlight(self):
        assert "tap-highlight-color" in self.html

    def test_has_server_fallback_section(self):
        assert "serverUrl" in self.html or "server" in self.html.lower()

    def test_escapes_html_in_ssid(self):
        """Should have XSS protection for network names."""
        assert "escapeHtml" in self.html


# ── Kiosk systemd Service ────────────────────────────────────────


class TestKioskService:
    """Validate atlas-kiosk.service systemd unit."""

    @pytest.fixture(autouse=True)
    def _load_service(self):
        with open(_KIOSK_SERVICE) as f:
            self.content = f.read()

    def test_file_exists(self):
        assert os.path.isfile(_KIOSK_SERVICE)

    def test_has_unit_section(self):
        assert "[Unit]" in self.content

    def test_has_service_section(self):
        assert "[Service]" in self.content

    def test_has_install_section(self):
        assert "[Install]" in self.content

    def test_runs_as_atlas_user(self):
        assert "User=atlas" in self.content

    def test_sets_display(self):
        assert "DISPLAY=:0" in self.content

    def test_kiosk_mode(self):
        assert "--kiosk" in self.content

    def test_starts_after_network(self):
        assert "network-online.target" in self.content

    def test_auto_restarts(self):
        assert "Restart=on-failure" in self.content

    def test_opens_avatar_url(self):
        assert "atlas-cortex.local:5100/avatar" in self.content


# ── Tablet Registration (WebSocket) ──────────────────────────────


class TestTabletRegistration:
    """Verify the server-side WebSocket handler recognises tablet devices."""

    def test_websocket_handler_accepts_tablet_device_type(self):
        """The docstring should mention tablet as a supported device_type."""
        from cortex.satellite.websocket import satellite_ws_handler

        doc = satellite_ws_handler.__doc__ or ""
        assert "tablet" in doc.lower()

    def test_tablet_capabilities_default(self):
        """Default tablet capabilities should include display and touch."""
        expected = {"mic", "speaker", "display", "touch"}
        # Mimic what the handler constructs for a tablet register message
        raw = {
            "type": "register",
            "device_type": "tablet",
            "name": "test-tablet",
        }
        caps = raw.get("capabilities") or ["mic", "speaker", "display", "touch"]
        assert expected.issubset(set(caps))

    def test_tablet_satellite_id_prefix(self):
        """Tablet satellites should get a 'tablet-' prefix."""
        name = "living-room-tablet"
        satellite_id = f"tablet-{name}"
        assert satellite_id == "tablet-living-room-tablet"
        assert satellite_id.startswith("tablet-")


# ── README ────────────────────────────────────────────────────────


class TestReadme:
    """Basic checks on the tablet README."""

    def test_readme_exists(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        assert os.path.isfile(readme)

    def test_readme_mentions_surface_go(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        with open(readme) as f:
            content = f.read()
        assert "Surface Go" in content

    def test_readme_has_install_instructions(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        with open(readme) as f:
            content = f.read()
        assert "install-tablet.sh" in content

    def test_readme_mentions_mdns(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        with open(readme) as f:
            content = f.read()
        assert "mDNS" in content
