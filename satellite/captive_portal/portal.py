#!/usr/bin/env python3
"""Atlas Satellite — Captive Portal WiFi Setup

A zero-dependency captive portal that runs on first boot when no WiFi is
configured.  Creates a hotspot ("Atlas-Setup"), serves a mobile-friendly
web page where the user picks their network and enters a password, then
configures NetworkManager and exits.

Only uses Python stdlib — no pip packages required.
"""

import http.server
import json
import logging
import os
import re
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AP_SSID = "Atlas-Setup"
AP_PASSWORD = ""  # Open network for easy phone connection
AP_IP = "10.42.0.1"
HTTP_PORT = 80
DNS_PORT = 53
WIFI_INTERFACE = "wlan0"
CONNECTION_TIMEOUT = 30
LOG = logging.getLogger("atlas-captive-portal")

# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def is_wifi_connected() -> bool:
    """Check if wlan0 has an active WiFi connection (not our AP)."""
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"],
            text=True, timeout=10,
        )
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[0] == "wifi":
                if parts[1] == "connected" and parts[2] != AP_SSID:
                    return True
    except Exception:
        pass
    return False


def scan_networks() -> list[dict]:
    """Return list of visible WiFi networks."""
    networks = []
    try:
        # Rescan
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=15,
        )
        time.sleep(2)
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "device", "wifi", "list"],
            text=True, timeout=10,
        )
        seen = set()
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                ssid = parts[0].strip()
                if not ssid or ssid == AP_SSID or ssid in seen or ssid == "--":
                    continue
                seen.add(ssid)
                networks.append({
                    "ssid": ssid,
                    "signal": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] if len(parts) > 2 else "",
                    "freq": parts[3] if len(parts) > 3 else "",
                })
        networks.sort(key=lambda n: n["signal"], reverse=True)
    except Exception as e:
        LOG.warning("WiFi scan failed: %s", e)
    return networks


def connect_wifi(ssid: str, password: str, country: str = "US") -> tuple[bool, str]:
    """Attempt to connect to a WiFi network via NetworkManager."""
    # Set regulatory domain
    try:
        subprocess.run(["iw", "reg", "set", country], capture_output=True, timeout=5)
    except Exception:
        pass

    # Delete any existing connection with same SSID
    try:
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    # Stop the hotspot first so wlan0 is free
    stop_hotspot()
    time.sleep(2)

    # Connect
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    cmd += ["ifname", WIFI_INTERFACE]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CONNECTION_TIMEOUT,
        )
        if result.returncode == 0:
            # Verify connection
            time.sleep(3)
            if is_wifi_connected():
                # Save the WiFi config to boot partition for persistence
                _save_wifi_config(ssid, password, country)
                return True, f"Connected to {ssid}!"
            return False, "Connection appeared to succeed but WiFi is not active."
        else:
            err = result.stderr.strip() or result.stdout.strip()
            # Restart hotspot so user can try again
            start_hotspot()
            return False, f"Connection failed: {err}"
    except subprocess.TimeoutExpired:
        start_hotspot()
        return False, "Connection timed out. Check your password and try again."
    except Exception as e:
        start_hotspot()
        return False, str(e)


def _save_wifi_config(ssid: str, password: str, country: str):
    """Persist WiFi config to boot partition so it survives reboots."""
    for path in ["/boot/firmware/atlas-wifi.txt", "/boot/atlas-wifi.txt"]:
        dirn = os.path.dirname(path)
        if os.path.isdir(dirn):
            try:
                with open(path, "w") as f:
                    f.write(f"WIFI_SSID={ssid}\n")
                    f.write(f"WIFI_PASSWORD={password}\n")
                    f.write(f"WIFI_COUNTRY={country}\n")
                LOG.info("Saved WiFi config to %s", path)
                return
            except OSError as e:
                LOG.warning("Could not save WiFi config to %s: %s", path, e)


# ---------------------------------------------------------------------------
# Hotspot (AP) management via NetworkManager
# ---------------------------------------------------------------------------

def start_hotspot() -> bool:
    """Create a WiFi hotspot using NetworkManager."""
    LOG.info("Starting hotspot: %s", AP_SSID)
    try:
        # Remove any previous atlas hotspot connection
        subprocess.run(
            ["nmcli", "connection", "delete", AP_SSID],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    try:
        cmd = [
            "nmcli", "device", "wifi", "hotspot",
            "ifname", WIFI_INTERFACE,
            "ssid", AP_SSID,
            "con-name", AP_SSID,
        ]
        # Open network (no password) for zero-friction setup
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            LOG.error("Hotspot failed: %s", result.stderr)
            return False

        # Set the IP to a predictable address
        subprocess.run(
            ["nmcli", "connection", "modify", AP_SSID,
             "ipv4.addresses", f"{AP_IP}/24",
             "ipv4.method", "shared"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["nmcli", "connection", "up", AP_SSID],
            capture_output=True, timeout=10,
        )
        LOG.info("Hotspot active: %s at %s", AP_SSID, AP_IP)
        return True
    except Exception as e:
        LOG.error("Failed to start hotspot: %s", e)
        return False


def stop_hotspot():
    """Stop the WiFi hotspot."""
    LOG.info("Stopping hotspot")
    try:
        subprocess.run(
            ["nmcli", "connection", "down", AP_SSID],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["nmcli", "connection", "delete", AP_SSID],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Minimal DNS server — redirects ALL lookups to the AP IP (captive portal)
# ---------------------------------------------------------------------------

class CaptiveDNSHandler(socketserver.BaseRequestHandler):
    """Responds to all DNS queries with the AP's IP address.

    This makes phones/laptops detect a captive portal and auto-open the
    setup page.
    """

    def handle(self):
        data = self.request[0]
        sock = self.request[1]
        try:
            response = self._build_response(data)
            sock.sendto(response, self.client_address)
        except Exception:
            pass

    def _build_response(self, data: bytes) -> bytes:
        # Parse the DNS header
        tx_id = data[:2]
        flags = b"\x81\x80"  # Standard response, no error
        qdcount = struct.unpack("!H", data[4:6])[0]
        ancount = struct.pack("!H", qdcount)

        # Build response header
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
            # Pointer to the question name
            answers += b"\xc0\x0c"
            answers += b"\x00\x01"  # Type A
            answers += b"\x00\x01"  # Class IN
            answers += struct.pack("!I", 60)  # TTL
            answers += struct.pack("!H", 4)   # Data length
            answers += ip_bytes
            # Skip to next question
            while data[offset] != 0:
                offset += data[offset] + 1
            offset += 5

        return header + question + answers


class ThreadedDNSServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# HTTP handler — serves the captive portal page + API
# ---------------------------------------------------------------------------

class CaptiveHTTPHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the captive portal."""

    server_version = "AtlasSetup/1.0"

    def log_message(self, format, *args):
        LOG.debug(format, *args)

    def do_GET(self):
        # Captive portal detection endpoints — redirect to our page
        captive_checks = [
            "/generate_204",           # Android
            "/gen_204",                # Android
            "/hotspot-detect.html",    # Apple
            "/library/test/success",   # Apple
            "/success.txt",            # Firefox
            "/ncsi.txt",               # Windows
            "/connecttest.txt",        # Windows
            "/redirect",               # Windows
            "/canonical.html",         # Ubuntu
        ]

        if self.path in captive_checks or self.path == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", f"http://{AP_IP}/")
            self.end_headers()
            return

        if self.path == "/api/scan":
            networks = scan_networks()
            self._json_response({"networks": networks})
            return

        if self.path == "/api/status":
            connected = is_wifi_connected()
            self._json_response({"connected": connected})
            return

        # Serve the main setup page
        self._serve_portal()

    def do_POST(self):
        if self.path == "/api/connect":
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._json_response({"success": False, "error": "Invalid JSON"}, 400)
                return

            ssid = data.get("ssid", "").strip()
            password = data.get("password", "").strip()
            country = data.get("country", "US").strip()

            if not ssid:
                self._json_response({"success": False, "error": "SSID is required"}, 400)
                return

            LOG.info("Connecting to WiFi: %s (country: %s)", ssid, country)
            success, message = connect_wifi(ssid, password, country)
            self._json_response({"success": success, "message": message})

            if success:
                # Signal the main loop to exit
                LOG.info("WiFi connected! Shutting down captive portal...")
                threading.Thread(target=self._delayed_shutdown, daemon=True).start()
            return

        self.send_error(404)

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_portal(self):
        html = PORTAL_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _delayed_shutdown(self):
        time.sleep(3)
        os.kill(os.getpid(), signal.SIGTERM)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# Portal HTML — self-contained, mobile-friendly, dark theme
# ---------------------------------------------------------------------------

PORTAL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Atlas Satellite Setup</title>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --success: #22c55e;
    --error: #ef4444;
    --radius: 12px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
  }
  .container {
    width: 100%;
    max-width: 420px;
  }
  .logo {
    text-align: center;
    margin: 20px 0 30px;
  }
  .logo svg {
    width: 64px;
    height: 64px;
    margin-bottom: 12px;
  }
  .logo h1 {
    font-size: 1.5em;
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .logo p {
    color: var(--text-dim);
    font-size: 0.9em;
    margin-top: 4px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    margin-bottom: 16px;
  }
  .card h2 {
    font-size: 1em;
    font-weight: 600;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .network-list {
    max-height: 240px;
    overflow-y: auto;
    margin: 0 -20px;
    padding: 0 20px;
  }
  .network-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    margin: 4px 0;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.2s;
    border: 2px solid transparent;
  }
  .network-item:hover { background: rgba(99, 102, 241, 0.1); }
  .network-item.selected {
    background: rgba(99, 102, 241, 0.15);
    border-color: var(--accent);
  }
  .network-name {
    font-weight: 500;
    font-size: 0.95em;
  }
  .network-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-dim);
    font-size: 0.8em;
  }
  .signal-bars {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 16px;
  }
  .signal-bar {
    width: 3px;
    background: var(--border);
    border-radius: 1px;
  }
  .signal-bar.active { background: var(--accent); }
  .lock-icon { font-size: 0.85em; }
  input, select {
    width: 100%;
    padding: 12px 16px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 0.95em;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 12px;
  }
  input:focus, select:focus { border-color: var(--accent); }
  input::placeholder { color: var(--text-dim); }
  select { appearance: none; cursor: pointer; }
  label {
    display: block;
    color: var(--text-dim);
    font-size: 0.85em;
    margin-bottom: 6px;
    font-weight: 500;
  }
  .btn {
    width: 100%;
    padding: 14px;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    font-size: 1em;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s, transform 0.1s;
    margin-top: 4px;
  }
  .btn:hover { background: var(--accent-hover); }
  .btn:active { transform: scale(0.98); }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
  }
  .btn-scan {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 8px 16px;
    font-size: 0.85em;
    width: auto;
    margin: 0;
  }
  .btn-scan:hover { border-color: var(--accent); color: var(--text); }
  .status {
    text-align: center;
    padding: 16px;
    border-radius: 8px;
    font-weight: 500;
    display: none;
    margin-top: 12px;
  }
  .status.show { display: block; }
  .status.success { background: rgba(34, 197, 94, 0.15); color: var(--success); }
  .status.error { background: rgba(239, 68, 68, 0.15); color: var(--error); }
  .status.loading { background: rgba(99, 102, 241, 0.15); color: var(--accent); }
  .spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid transparent;
    border-top: 2px solid currentColor;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .pulse-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    background: var(--accent);
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
    margin-right: 6px;
    vertical-align: middle;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.8); }
  }
  .empty-state {
    text-align: center;
    padding: 30px 20px;
    color: var(--text-dim);
  }
  .flex-between {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .manual-ssid {
    display: none;
    margin-bottom: 12px;
  }
  .manual-ssid.show { display: block; }
  .link-btn {
    background: none;
    border: none;
    color: var(--accent);
    cursor: pointer;
    font-size: 0.85em;
    padding: 4px 0;
    text-decoration: underline;
  }
  footer {
    text-align: center;
    color: var(--text-dim);
    font-size: 0.75em;
    margin-top: 20px;
    opacity: 0.6;
  }
</style>
</head>
<body>
<div class="container">

  <div class="logo">
    <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="32" cy="32" r="30" stroke="#6366f1" stroke-width="2"/>
      <circle cx="32" cy="32" r="20" stroke="#6366f1" stroke-width="1.5" opacity="0.6"/>
      <circle cx="32" cy="32" r="10" fill="#6366f1" opacity="0.8"/>
      <circle cx="32" cy="32" r="4" fill="white"/>
      <line x1="32" y1="2" x2="32" y2="14" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
      <line x1="32" y1="50" x2="32" y2="62" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
      <line x1="2" y1="32" x2="14" y2="32" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
      <line x1="50" y1="32" x2="62" y2="32" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
    </svg>
    <h1>Atlas Satellite</h1>
    <p><span class="pulse-dot"></span>WiFi Setup</p>
  </div>

  <!-- Network Selection -->
  <div class="card">
    <div class="flex-between">
      <h2>📡 Available Networks</h2>
      <button class="btn btn-scan" onclick="scanNetworks()">Scan</button>
    </div>
    <div id="networkList" class="network-list">
      <div class="empty-state">
        <div class="spinner"></div> Scanning for networks...
      </div>
    </div>
    <div style="margin-top:8px">
      <button class="link-btn" onclick="toggleManualSSID()">Enter network name manually</button>
    </div>
  </div>

  <!-- Connection Form -->
  <div class="card">
    <h2>🔐 Connect</h2>
    <div id="manualSSID" class="manual-ssid">
      <label for="ssid">Network Name (SSID)</label>
      <input type="text" id="ssid" placeholder="Enter network name">
    </div>
    <div id="selectedDisplay" style="margin-bottom:12px; display:none;">
      <label>Selected Network</label>
      <div style="padding:10px 16px; background:var(--bg); border-radius:8px; font-weight:500;" id="selectedName"></div>
    </div>

    <label for="password">Password</label>
    <input type="password" id="password" placeholder="Enter WiFi password">

    <label for="country">Country</label>
    <select id="country">
      <option value="US" selected>United States (US)</option>
      <option value="GB">United Kingdom (GB)</option>
      <option value="CA">Canada (CA)</option>
      <option value="AU">Australia (AU)</option>
      <option value="DE">Germany (DE)</option>
      <option value="FR">France (FR)</option>
      <option value="JP">Japan (JP)</option>
      <option value="NL">Netherlands (NL)</option>
      <option value="SE">Sweden (SE)</option>
      <option value="NZ">New Zealand (NZ)</option>
      <option value="IN">India (IN)</option>
      <option value="BR">Brazil (BR)</option>
      <option value="MX">Mexico (MX)</option>
      <option value="IT">Italy (IT)</option>
      <option value="ES">Spain (ES)</option>
    </select>

    <button class="btn" id="connectBtn" onclick="connectWifi()" disabled>
      Connect
    </button>

    <div id="status" class="status"></div>
  </div>

  <footer>
    Atlas Cortex — Satellite Setup v1.0
  </footer>
</div>

<script>
  let selectedSSID = '';
  let manualMode = false;

  async function scanNetworks() {
    const list = document.getElementById('networkList');
    list.innerHTML = '<div class="empty-state"><div class="spinner"></div> Scanning...</div>';
    try {
      const res = await fetch('/api/scan');
      const data = await res.json();
      if (!data.networks || data.networks.length === 0) {
        list.innerHTML = '<div class="empty-state">No networks found. Try scanning again.</div>';
        return;
      }
      list.innerHTML = '';
      data.networks.forEach(net => {
        const div = document.createElement('div');
        div.className = 'network-item';
        div.onclick = () => selectNetwork(net.ssid, div);

        const signal = net.signal;
        const bars = [signal > 0, signal > 25, signal > 50, signal > 75];
        const barsHTML = bars.map((active, i) =>
          `<div class="signal-bar${active ? ' active' : ''}" style="height:${4 + i * 4}px"></div>`
        ).join('');

        const secured = net.security && net.security !== '' && net.security !== '--';
        div.innerHTML = `
          <span class="network-name">${escapeHtml(net.ssid)}</span>
          <span class="network-meta">
            ${secured ? '<span class="lock-icon">🔒</span>' : ''}
            <span class="signal-bars">${barsHTML}</span>
          </span>
        `;
        list.appendChild(div);
      });
    } catch (e) {
      list.innerHTML = '<div class="empty-state">Scan failed. Try again.</div>';
    }
  }

  function selectNetwork(ssid, el) {
    // Deselect previous
    document.querySelectorAll('.network-item').forEach(item => item.classList.remove('selected'));
    el.classList.add('selected');
    selectedSSID = ssid;
    manualMode = false;

    document.getElementById('manualSSID').classList.remove('show');
    document.getElementById('selectedDisplay').style.display = 'block';
    document.getElementById('selectedName').textContent = ssid;
    document.getElementById('connectBtn').disabled = false;
    document.getElementById('password').focus();
  }

  function toggleManualSSID() {
    manualMode = !manualMode;
    document.getElementById('manualSSID').classList.toggle('show', manualMode);
    if (manualMode) {
      document.getElementById('selectedDisplay').style.display = 'none';
      selectedSSID = '';
      document.querySelectorAll('.network-item').forEach(item => item.classList.remove('selected'));
      document.getElementById('ssid').focus();
      document.getElementById('connectBtn').disabled = false;
    }
  }

  async function connectWifi() {
    const ssid = manualMode
      ? document.getElementById('ssid').value.trim()
      : selectedSSID;
    const password = document.getElementById('password').value;
    const country = document.getElementById('country').value;

    if (!ssid) {
      showStatus('Please select or enter a network name.', 'error');
      return;
    }

    const btn = document.getElementById('connectBtn');
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner"></div> Connecting...';
    showStatus('Connecting to ' + escapeHtml(ssid) + '...', 'loading');

    try {
      const res = await fetch('/api/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid, password, country }),
      });
      const data = await res.json();
      if (data.success) {
        showStatus('✅ ' + data.message + ' The satellite will now configure itself.', 'success');
        btn.innerHTML = '✅ Connected!';
      } else {
        showStatus('❌ ' + data.message, 'error');
        btn.disabled = false;
        btn.innerHTML = 'Connect';
      }
    } catch (e) {
      // Connection drop expected if WiFi switches — that's success!
      showStatus('✅ WiFi configured! The satellite is connecting...', 'success');
      btn.innerHTML = '✅ Connected!';
    }
  }

  function showStatus(msg, type) {
    const el = document.getElementById('status');
    el.className = 'status show ' + type;
    el.innerHTML = msg;
  }

  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  // Auto-scan on load
  scanNetworks();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main — orchestrate DNS + HTTP + AP
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Check if WiFi is already connected
    if is_wifi_connected():
        LOG.info("WiFi already connected — captive portal not needed.")
        sys.exit(0)

    # Also check if atlas-wifi.txt has valid values (wifi-setup service may
    # not have run yet, or NM may still be connecting)
    for path in ["/boot/firmware/atlas-wifi.txt", "/boot/atlas-wifi.txt"]:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    content = f.read()
                    lines = [l.strip() for l in content.splitlines()
                             if l.strip() and not l.strip().startswith("#")]
                    cfg = {}
                    for line in lines:
                        if "=" in line:
                            k, v = line.split("=", 1)
                            cfg[k.strip()] = v.strip()
                    ssid = cfg.get("WIFI_SSID", "")
                    if ssid and ssid != "YOUR_WIFI_NAME":
                        LOG.info("WiFi configured in %s — waiting for connection...", path)
                        # Give NetworkManager a chance to connect
                        for _ in range(15):
                            if is_wifi_connected():
                                LOG.info("WiFi connected!")
                                sys.exit(0)
                            time.sleep(2)
                        LOG.warning("WiFi configured but not connected after 30s — starting portal")
            except Exception:
                pass

    LOG.info("No WiFi connection — starting captive portal")

    # Start the hotspot
    if not start_hotspot():
        LOG.error("Could not start WiFi hotspot. Is wlan0 available?")
        # Retry a few times
        for attempt in range(3):
            time.sleep(5)
            if start_hotspot():
                break
        else:
            LOG.error("Failed to start hotspot after retries — exiting")
            sys.exit(1)

    # Start DNS server
    dns_server = None
    try:
        dns_server = ThreadedDNSServer(("0.0.0.0", DNS_PORT), CaptiveDNSHandler)
        dns_thread = threading.Thread(target=dns_server.serve_forever, daemon=True)
        dns_thread.start()
        LOG.info("DNS server running on port %d", DNS_PORT)
    except Exception as e:
        LOG.warning("Could not start DNS server (port %d): %s — captive portal detection may not work", DNS_PORT, e)

    # Start HTTP server
    http_server = ThreadedHTTPServer(("0.0.0.0", HTTP_PORT), CaptiveHTTPHandler)

    def shutdown_handler(signum, frame):
        LOG.info("Shutdown signal received")
        stop_hotspot()
        if dns_server:
            dns_server.shutdown()
        http_server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    LOG.info("Captive portal running at http://%s/", AP_IP)
    LOG.info("Connect to WiFi network '%s' to configure this satellite", AP_SSID)

    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_hotspot()
        if dns_server:
            dns_server.shutdown()
        LOG.info("Captive portal stopped")


if __name__ == "__main__":
    main()
