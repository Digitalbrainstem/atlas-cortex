#!/usr/bin/env python3
"""Captive portal web application for WiFi setup.

Flask-based portal that runs on first boot when no WiFi is configured.
Creates a hotspot and serves a mobile-friendly web page where the user
picks their network and enters a password.  Uses NetworkManager (nmcli)
for all WiFi operations — compatible with Raspberry Pi OS Bookworm.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request

app = Flask(__name__, template_folder="templates")
LOG = logging.getLogger("atlas-captive-portal")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AP_IP = os.environ.get("PORTAL_IP", "10.42.0.1")
WIFI_INTERFACE = "wlan0"
CONNECTION_TIMEOUT = 30
MARKER_FILE = "/var/lib/atlas/wifi-configured"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Main setup page — scan and show available networks."""
    networks = scan_wifi_networks()
    hostname = get_hostname()
    return render_template("setup.html", networks=networks, hostname=hostname)


@app.route("/scan")
def scan():
    """Rescan WiFi networks."""
    return jsonify({"networks": scan_wifi_networks()})


@app.route("/connect", methods=["POST"])
def connect():
    """Connect to selected WiFi network."""
    data = request.get_json(silent=True) or {}
    ssid = (request.form.get("ssid") or data.get("ssid", "")).strip()
    password = (request.form.get("password") or data.get("password", "")).strip()
    country = (request.form.get("country") or data.get("country", "US")).strip()

    if not ssid:
        return jsonify({"success": False, "error": "No SSID provided"})

    LOG.info("Connecting to WiFi: %s (country: %s)", ssid, country)
    success, message = configure_wifi(ssid, password, country)
    result = {"success": success, "message": message}

    if success:
        _mark_configured()
        LOG.info("WiFi connected — shutting down captive portal...")
        threading.Thread(target=_delayed_shutdown, daemon=True).start()

    return jsonify(result)


@app.route("/status")
def status():
    """Check connection status."""
    connected, ip = check_connection()
    return jsonify({"connected": connected, "ip": ip})


# ---------------------------------------------------------------------------
# Captive portal detection endpoints
#
# Phones/OSes probe these URLs to detect captive portals.  By NOT returning
# the expected "success" response we trigger the captive-portal sheet so
# the user is automatically redirected to our setup page.
# ---------------------------------------------------------------------------

@app.route("/hotspot-detect.html")    # Apple iOS / macOS
@app.route("/library/test/success")   # Apple (alternate)
@app.route("/generate_204")           # Android
@app.route("/gen_204")                # Android (alternate)
@app.route("/ncsi.txt")              # Windows
@app.route("/connecttest.txt")       # Windows
@app.route("/redirect")             # Windows
@app.route("/success.txt")          # Firefox
@app.route("/canonical.html")       # Ubuntu / NetworkManager
def captive_detect():
    """Redirect all captive portal detection probes to the setup page."""
    return redirect("/")


# ---------------------------------------------------------------------------
# WiFi operations (nmcli — Bookworm-compatible, NOT wpa_supplicant)
# ---------------------------------------------------------------------------

def scan_wifi_networks() -> list[dict]:
    """Use nmcli to scan available networks."""
    networks: list[dict] = []
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=15,
        )
        time.sleep(2)
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            text=True, timeout=10,
        )
        seen: set[str] = set()
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                ssid = parts[0].strip()
                if not ssid or ssid in seen or ssid == "--":
                    continue
                # Filter out our own hotspot SSID
                if ssid.startswith("Atlas-Satellite-"):
                    continue
                seen.add(ssid)
                networks.append({
                    "ssid": ssid,
                    "signal": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] if len(parts) > 2 else "",
                })
        networks.sort(key=lambda n: n["signal"], reverse=True)
    except Exception as exc:
        LOG.warning("WiFi scan failed: %s", exc)
    return networks


def configure_wifi(ssid: str, password: str, country: str = "US") -> tuple[bool, str]:
    """Configure WiFi using NetworkManager (nmcli).

    This is the Bookworm-compatible way — NOT wpa_supplicant.
    """
    # Set regulatory domain
    try:
        subprocess.run(["iw", "reg", "set", country], capture_output=True, timeout=5)
    except Exception:
        pass

    # Remove any existing connection with same SSID
    try:
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    # Stop hotspot so wlan0 is free for client connection
    _stop_hotspot_nm()
    time.sleep(2)

    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    cmd += ["ifname", WIFI_INTERFACE]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CONNECTION_TIMEOUT,
        )
        if result.returncode == 0:
            time.sleep(3)
            connected, ip = check_connection()
            if connected:
                _save_wifi_config(ssid, password, country)
                return True, f"Connected to {ssid}! IP: {ip}"
            return False, "Connection appeared to succeed but WiFi is not active."
        err = result.stderr.strip() or result.stdout.strip()
        _start_hotspot_nm()
        return False, f"Connection failed: {err}"
    except subprocess.TimeoutExpired:
        _start_hotspot_nm()
        return False, "Connection timed out. Check your password and try again."
    except Exception as exc:
        _start_hotspot_nm()
        return False, str(exc)


def check_connection() -> tuple[bool, str]:
    """Check if connected to WiFi and get IP."""
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "STATE", "general"],
            text=True, timeout=10,
        ).strip()
        if "connected" not in out:
            return False, ""
    except Exception:
        return False, ""

    ip = ""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", WIFI_INTERFACE],
            text=True, timeout=5,
        ).strip()
        for part in out.split():
            if "/" in part and "." in part:
                ip = part.split("/")[0]
                break
    except Exception:
        pass

    return True, ip


def get_hostname() -> str:
    """Get the current hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "atlas-satellite"


# ---------------------------------------------------------------------------
# Hotspot helpers (called during connect / reconnect)
# ---------------------------------------------------------------------------

def _get_hotspot_ssid() -> str:
    """Build SSID from MAC address: Atlas-Satellite-XXXX."""
    try:
        with open(f"/sys/class/net/{WIFI_INTERFACE}/address") as f:
            mac = f.read().strip()
        suffix = mac.replace(":", "")[-4:].upper()
    except Exception:
        suffix = "0000"
    return f"Atlas-Satellite-{suffix}"


def _start_hotspot_nm() -> bool:
    """Restart the hotspot via NetworkManager (used after a failed connect)."""
    ssid = _get_hotspot_ssid()
    try:
        subprocess.run(["rfkill", "unblock", "wifi"], capture_output=True, timeout=5)
    except Exception:
        pass

    try:
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nmcli", "device", "wifi", "hotspot",
             "ifname", WIFI_INTERFACE, "ssid", ssid, "con-name", ssid],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            LOG.error("Hotspot restart failed: %s", result.stderr)
            return False
        subprocess.run(
            ["nmcli", "connection", "modify", ssid,
             "ipv4.addresses", f"{AP_IP}/24", "ipv4.method", "shared"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["nmcli", "connection", "up", ssid],
            capture_output=True, timeout=10,
        )
        return True
    except Exception as exc:
        LOG.error("Hotspot restart error: %s", exc)
        return False


def _stop_hotspot_nm():
    """Stop the NM hotspot connection."""
    ssid = _get_hotspot_ssid()
    try:
        subprocess.run(
            ["nmcli", "connection", "down", ssid],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _save_wifi_config(ssid: str, password: str, country: str):
    """Persist WiFi config to boot partition so it survives reboots."""
    for path in ["/boot/firmware/atlas-wifi.txt", "/boot/atlas-wifi.txt"]:
        if os.path.isdir(os.path.dirname(path)):
            try:
                with open(path, "w") as f:
                    f.write(f"WIFI_SSID={ssid}\n")
                    f.write(f"WIFI_PASSWORD={password}\n")
                    f.write(f"WIFI_COUNTRY={country}\n")
                LOG.info("Saved WiFi config to %s", path)
                return
            except OSError as exc:
                LOG.warning("Could not save to %s: %s", path, exc)


def _mark_configured():
    """Create marker file so the service does not start again."""
    try:
        os.makedirs(os.path.dirname(MARKER_FILE), exist_ok=True)
        with open(MARKER_FILE, "w") as f:
            f.write("configured\n")
    except OSError as exc:
        LOG.warning("Could not create marker %s: %s", MARKER_FILE, exc)


def _delayed_shutdown():
    """Give the response time to reach the client, then exit."""
    time.sleep(3)
    os.kill(os.getpid(), signal.SIGTERM)


# ---------------------------------------------------------------------------
# DNS redirect server
#
# Redirects ALL DNS lookups to the portal IP so phones detect the captive
# portal and auto-open the setup page.
# ---------------------------------------------------------------------------

class _CaptiveDNSHandler(socketserver.BaseRequestHandler):
    """Responds to every DNS query with the AP's IP address."""

    def handle(self):
        data = self.request[0]
        sock = self.request[1]
        try:
            response = self._build_response(data)
            sock.sendto(response, self.client_address)
        except Exception:
            pass

    def _build_response(self, data: bytes) -> bytes:
        tx_id = data[:2]
        flags = b"\x81\x80"  # Standard response, no error
        qdcount = struct.unpack("!H", data[4:6])[0]
        ancount = struct.pack("!H", qdcount)

        header = tx_id + flags + data[4:6] + ancount + b"\x00\x00\x00\x00"

        # Copy the question section
        question_end = 12
        for _ in range(qdcount):
            while data[question_end] != 0:
                question_end += data[question_end] + 1
            question_end += 5  # null byte + qtype (2) + qclass (2)
        question = data[12:question_end]

        # Build answer section — point everything to AP_IP
        ip_bytes = socket.inet_aton(AP_IP)
        answers = b""
        offset = 12
        for _ in range(qdcount):
            answers += b"\xc0\x0c"            # Pointer to question name
            answers += b"\x00\x01"            # Type A
            answers += b"\x00\x01"            # Class IN
            answers += struct.pack("!I", 60)  # TTL
            answers += struct.pack("!H", 4)   # Data length
            answers += ip_bytes
            while data[offset] != 0:
                offset += data[offset] + 1
            offset += 5

        return header + question + answers


class _ThreadedDNSServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_dns_server(port: int = 53) -> _ThreadedDNSServer | None:
    """Start the captive DNS redirect server in a background thread."""
    try:
        server = _ThreadedDNSServer(("0.0.0.0", port), _CaptiveDNSHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        LOG.info("DNS redirect server running on port %d", port)
        return server
    except Exception as exc:
        LOG.warning(
            "Could not start DNS server on port %d: %s — "
            "captive portal detection may not work", port, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Start DNS redirect server
    dns_server = start_dns_server()

    LOG.info("Starting captive portal on http://%s/", AP_IP)

    try:
        app.run(host="0.0.0.0", port=80, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        if dns_server:
            dns_server.shutdown()
        LOG.info("Captive portal stopped")


if __name__ == "__main__":
    main()
