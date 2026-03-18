"""Tests for the satellite captive portal WiFi setup."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from satellite.captive_portal.portal import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Index / template
# ---------------------------------------------------------------------------

def test_portal_index(client):
    """Index page renders with 200 status."""
    with patch("satellite.captive_portal.portal.scan_wifi_networks", return_value=[]):
        r = client.get("/")
    assert r.status_code == 200
    assert b"Atlas" in r.data


def test_portal_index_contains_hostname(client):
    """Index page shows the hostname."""
    with patch("satellite.captive_portal.portal.scan_wifi_networks", return_value=[]):
        with patch("satellite.captive_portal.portal.get_hostname", return_value="atlas-sat-test"):
            r = client.get("/")
    assert b"atlas-sat-test" in r.data


def test_portal_index_preloads_networks(client):
    """Index page includes pre-scanned network data for JS."""
    nets = [{"ssid": "HomeWiFi", "signal": 90, "security": "WPA2"}]
    with patch("satellite.captive_portal.portal.scan_wifi_networks", return_value=nets):
        r = client.get("/")
    assert b"HomeWiFi" in r.data


# ---------------------------------------------------------------------------
# /scan endpoint
# ---------------------------------------------------------------------------

def test_scan_returns_network_list(client):
    """GET /scan returns JSON list of networks."""
    fake = [
        {"ssid": "MyWiFi", "signal": 85, "security": "WPA2"},
        {"ssid": "Neighbor", "signal": 42, "security": "WPA"},
    ]
    with patch("satellite.captive_portal.portal.scan_wifi_networks", return_value=fake):
        r = client.get("/scan")
    assert r.status_code == 200
    data = r.get_json()
    assert "networks" in data
    assert len(data["networks"]) == 2
    assert data["networks"][0]["ssid"] == "MyWiFi"


def test_scan_returns_empty_list(client):
    """GET /scan works when no networks are visible."""
    with patch("satellite.captive_portal.portal.scan_wifi_networks", return_value=[]):
        r = client.get("/scan")
    data = r.get_json()
    assert data["networks"] == []


# ---------------------------------------------------------------------------
# /connect endpoint
# ---------------------------------------------------------------------------

def test_connect_no_ssid(client):
    """POST /connect without SSID returns error."""
    r = client.post(
        "/connect",
        data=json.dumps({"ssid": "", "password": "pass"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is False
    assert "SSID" in data.get("error", "")


def test_connect_accepts_credentials(client):
    """POST /connect with valid SSID triggers connection attempt."""
    with patch(
        "satellite.captive_portal.portal.configure_wifi",
        return_value=(True, "Connected to TestNet! IP: 192.168.1.42"),
    ):
        with patch("satellite.captive_portal.portal._mark_configured"):
            with patch("satellite.captive_portal.portal._delayed_shutdown"):
                r = client.post(
                    "/connect",
                    data=json.dumps({"ssid": "TestNet", "password": "secret"}),
                    content_type="application/json",
                )
    data = r.get_json()
    assert data["success"] is True
    assert "Connected" in data["message"]


def test_connect_failure_returns_message(client):
    """POST /connect reports connection failure with message."""
    with patch(
        "satellite.captive_portal.portal.configure_wifi",
        return_value=(False, "Wrong password"),
    ):
        r = client.post(
            "/connect",
            data=json.dumps({"ssid": "TestNet", "password": "wrong"}),
            content_type="application/json",
        )
    data = r.get_json()
    assert data["success"] is False
    assert "Wrong password" in data["message"]


def test_connect_form_encoded(client):
    """POST /connect also accepts form-encoded data."""
    with patch(
        "satellite.captive_portal.portal.configure_wifi",
        return_value=(True, "Connected!"),
    ):
        with patch("satellite.captive_portal.portal._mark_configured"):
            with patch("satellite.captive_portal.portal._delayed_shutdown"):
                r = client.post("/connect", data={"ssid": "TestNet", "password": "pw"})
    data = r.get_json()
    assert data["success"] is True


# ---------------------------------------------------------------------------
# /status endpoint
# ---------------------------------------------------------------------------

def test_status_disconnected(client):
    """GET /status when not connected."""
    with patch(
        "satellite.captive_portal.portal.check_connection",
        return_value=(False, ""),
    ):
        r = client.get("/status")
    data = r.get_json()
    assert data["connected"] is False
    assert data["ip"] == ""


def test_status_connected(client):
    """GET /status when connected reports IP."""
    with patch(
        "satellite.captive_portal.portal.check_connection",
        return_value=(True, "192.168.1.42"),
    ):
        r = client.get("/status")
    data = r.get_json()
    assert data["connected"] is True
    assert data["ip"] == "192.168.1.42"


# ---------------------------------------------------------------------------
# Captive portal detection endpoints
# ---------------------------------------------------------------------------

def test_apple_hotspot_detect(client):
    """/hotspot-detect.html returns non-success (Apple captive portal trigger)."""
    r = client.get("/hotspot-detect.html")
    assert r.status_code == 302


def test_apple_library_test(client):
    """/library/test/success returns redirect."""
    r = client.get("/library/test/success")
    assert r.status_code == 302


def test_android_generate_204(client):
    """/generate_204 returns non-204 (Android captive portal trigger)."""
    r = client.get("/generate_204")
    assert r.status_code != 204


def test_android_gen_204(client):
    """/gen_204 returns non-204."""
    r = client.get("/gen_204")
    assert r.status_code != 204


def test_windows_ncsi(client):
    """/ncsi.txt returns redirect."""
    r = client.get("/ncsi.txt")
    assert r.status_code == 302


def test_windows_connecttest(client):
    """/connecttest.txt returns redirect."""
    r = client.get("/connecttest.txt")
    assert r.status_code == 302


def test_firefox_success(client):
    """/success.txt returns redirect."""
    r = client.get("/success.txt")
    assert r.status_code == 302


def test_ubuntu_canonical(client):
    """/canonical.html returns redirect."""
    r = client.get("/canonical.html")
    assert r.status_code == 302


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

def test_get_hostname_fallback():
    """get_hostname returns a string even when socket fails."""
    from satellite.captive_portal.portal import get_hostname

    with patch("satellite.captive_portal.portal.socket.gethostname", side_effect=OSError):
        assert get_hostname() == "atlas-satellite"


def test_scan_wifi_networks_handles_nmcli_failure():
    """scan_wifi_networks returns empty list when nmcli is unavailable."""
    from satellite.captive_portal.portal import scan_wifi_networks

    with patch("satellite.captive_portal.portal.subprocess.run", side_effect=FileNotFoundError):
        result = scan_wifi_networks()
    assert result == []


def test_check_connection_handles_failure():
    """check_connection returns (False, '') when commands fail."""
    from satellite.captive_portal.portal import check_connection

    with patch(
        "satellite.captive_portal.portal.subprocess.check_output",
        side_effect=FileNotFoundError,
    ):
        connected, ip = check_connection()
    assert connected is False
    assert ip == ""
