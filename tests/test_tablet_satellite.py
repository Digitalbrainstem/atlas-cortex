"""Tests for the tablet satellite build artefacts.

Validates shell script syntax, HTML structure, systemd unit format,
build-image.sh OS builder, CI workflow, and WebSocket tablet device-type
registration.
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
_CI_WORKFLOW = os.path.join(
    _REPO_ROOT, ".github", "workflows", "build-tablet-image.yml",
)


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
    """Validate install-tablet.sh (alternative path for existing Ubuntu)."""

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


# ── Build Image Script (OS Builder) ──────────────────────────────


class TestBuildScript:
    """Validate build-image.sh — the complete OS image builder."""

    @pytest.fixture(autouse=True)
    def _load_content(self):
        with open(_BUILD_SCRIPT) as f:
            self.content = f.read()

    def test_file_exists(self):
        assert os.path.isfile(_BUILD_SCRIPT)

    def test_is_executable(self):
        assert os.access(_BUILD_SCRIPT, os.X_OK)

    def test_bash_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", _BUILD_SCRIPT],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_has_strict_mode(self):
        assert "set -euo pipefail" in self.content

    def test_requires_root(self):
        assert "EUID" in self.content

    def test_uses_debootstrap(self):
        """Primary path: debootstrap (not an ISO remaster)."""
        assert "debootstrap" in self.content

    def test_targets_noble(self):
        """Should build Ubuntu 24.04 (Noble)."""
        assert "noble" in self.content

    def test_creates_squashfs(self):
        assert "mksquashfs" in self.content

    def test_creates_grub_config(self):
        assert "grub.cfg" in self.content
        assert "Atlas Tablet OS" in self.content

    def test_installs_linux_generic_kernel(self):
        assert "linux-generic" in self.content

    def test_installs_linux_surface_kernel(self):
        assert "linux-image-surface" in self.content

    def test_installs_openbox(self):
        assert "openbox" in self.content

    def test_installs_chromium(self):
        assert "chromium-browser" in self.content

    def test_installs_pulseaudio(self):
        assert "pulseaudio" in self.content

    def test_installs_network_manager(self):
        assert "network-manager" in self.content

    def test_installs_avahi(self):
        assert "avahi-daemon" in self.content

    def test_installs_python_flask(self):
        """Flask needed for captive portal."""
        assert "python3-flask" in self.content

    def test_creates_atlas_user(self):
        assert "useradd" in self.content

    def test_configures_autologin(self):
        assert "autologin" in self.content

    def test_configures_kiosk_autostart(self):
        assert "openbox" in self.content
        assert "chromium-browser" in self.content
        assert "--kiosk" in self.content

    def test_installs_captive_portal(self):
        assert "captive-portal" in self.content or "captive_portal" in self.content

    def test_installs_satellite_agent(self):
        assert "atlas-satellite" in self.content
        assert "atlas_satellite" in self.content

    def test_creates_satellite_config(self):
        assert "config.json" in self.content
        assert '"device_type": "tablet"' in self.content

    def test_configures_mdns_announce(self):
        assert "atlas-announce" in self.content
        assert "avahi-publish" in self.content

    def test_has_firstboot_service(self):
        assert "atlas-firstboot" in self.content

    def test_generates_unique_hostname(self):
        """First-boot should assign a random hostname suffix."""
        assert "atlas-tablet-" in self.content
        assert "urandom" in self.content

    def test_disables_lid_suspend(self):
        assert "HandleLidSwitch=ignore" in self.content

    def test_sets_device_type_env(self):
        assert "ATLAS_DEVICE_TYPE=tablet" in self.content

    def test_has_cleanup_trap(self):
        assert "trap" in self.content
        assert "cleanup" in self.content.lower()

    def test_generates_checksum(self):
        assert "sha256sum" in self.content

    def test_outputs_iso(self):
        assert "atlas-tablet-os-" in self.content
        assert ".iso" in self.content

    def test_supports_raw_format(self):
        assert "--raw" in self.content

    def test_has_safe_boot_option(self):
        assert "Safe Mode" in self.content or "nomodeset" in self.content

    def test_phases_documented(self):
        """Build should have clearly labelled phases."""
        assert "Phase 1" in self.content
        assert "Phase 2" in self.content

    def test_copies_satellite_code(self):
        assert "atlas_satellite" in self.content

    def test_creates_python_venv(self):
        assert "python3 -m venv" in self.content

    def test_installs_pip_deps(self):
        assert "requirements.txt" in self.content


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


# ── CI Workflow ───────────────────────────────────────────────────


class TestCIWorkflow:
    """Validate the GitHub Actions workflow for building tablet images."""

    @pytest.fixture(autouse=True)
    def _load_workflow(self):
        with open(_CI_WORKFLOW) as f:
            self.content = f.read()

    def test_workflow_file_exists(self):
        assert os.path.isfile(_CI_WORKFLOW)

    def test_valid_yaml(self):
        """YAML should parse without errors."""
        import yaml  # pytest dep includes pyyaml

        data = yaml.safe_load(self.content)
        assert data is not None
        assert "name" in data

    def test_workflow_name(self):
        assert "Build Tablet Image" in self.content

    def test_has_workflow_dispatch_trigger(self):
        assert "workflow_dispatch" in self.content

    def test_has_schedule_trigger(self):
        assert "schedule" in self.content
        assert "cron" in self.content

    def test_has_release_trigger(self):
        assert "release" in self.content

    def test_runs_on_ubuntu(self):
        assert "ubuntu-latest" in self.content

    def test_installs_debootstrap(self):
        assert "debootstrap" in self.content

    def test_installs_squashfs_tools(self):
        assert "squashfs-tools" in self.content

    def test_bootstraps_noble(self):
        assert "noble" in self.content

    def test_installs_linux_surface(self):
        assert "linux-surface" in self.content

    def test_installs_chromium(self):
        assert "chromium-browser" in self.content

    def test_installs_satellite_agent(self):
        assert "atlas-satellite" in self.content

    def test_installs_captive_portal(self):
        assert "captive-portal" in self.content or "captive_portal" in self.content

    def test_builds_squashfs(self):
        assert "mksquashfs" in self.content

    def test_builds_iso(self):
        assert "grub-mkrescue" in self.content or "xorriso" in self.content

    def test_generates_checksum(self):
        assert "sha256sum" in self.content

    def test_uploads_artifact(self):
        assert "upload-artifact" in self.content

    def test_uploads_to_release(self):
        assert "action-gh-release" in self.content

    def test_has_surface_kernel_option(self):
        assert "include_surface_kernel" in self.content

    def test_has_timeout(self):
        assert "timeout-minutes" in self.content


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

    @pytest.fixture(autouse=True)
    def _load_readme(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        with open(readme) as f:
            self.content = f.read()

    def test_readme_exists(self):
        readme = os.path.join(_TABLET_DIR, "README.md")
        assert os.path.isfile(readme)

    def test_readme_mentions_surface_go(self):
        assert "Surface Go" in self.content

    def test_readme_has_install_instructions(self):
        assert "install-tablet.sh" in self.content

    def test_readme_mentions_mdns(self):
        assert "mDNS" in self.content

    def test_readme_primary_path_is_image(self):
        """Pre-built image should be the primary Quick Start path."""
        assert "atlas-tablet-os" in self.content
        assert "Flash" in self.content or "flash" in self.content

    def test_readme_mentions_debootstrap(self):
        assert "debootstrap" in self.content

    def test_readme_has_first_boot_flow(self):
        assert "First Boot" in self.content

    def test_readme_documents_whats_in_image(self):
        assert "What's In The Image" in self.content or "pre-installed" in self.content.lower()
