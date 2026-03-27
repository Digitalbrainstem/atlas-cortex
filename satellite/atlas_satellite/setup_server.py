"""Local web server for first-boot WiFi setup.

Serves a touch-friendly web page at http://localhost:8080 that:
1. Scans for WiFi networks
2. Lets user select network and enter password
3. Connects via NetworkManager
4. Once connected, redirects to Atlas avatar
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from aiohttp import web

logger = logging.getLogger(__name__)


class SetupServer:
    """Lightweight local web server for tablet first-boot WiFi setup."""

    def __init__(self, port: int = 8080) -> None:
        self.port = port
        self.app = web.Application()
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/setup", self.index)
        self.app.router.add_get("/api/wifi/scan", self.wifi_scan)
        self.app.router.add_post("/api/wifi/connect", self.wifi_connect)
        self.app.router.add_get("/api/status", self.status)
        self._runner: web.AppRunner | None = None

    # ── Page handler ──────────────────────────────────────────────

    async def index(self, request: web.Request) -> web.Response:
        """Serve the setup page HTML."""
        return web.Response(text=SETUP_HTML, content_type="text/html")

    # ── WiFi scan ─────────────────────────────────────────────────

    async def wifi_scan(self, request: web.Request) -> web.Response:
        """Scan and return available WiFi networks."""
        # Trigger a rescan first (best-effort)
        try:
            rescan = await asyncio.create_subprocess_exec(
                "nmcli", "device", "wifi", "rescan",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(rescan.communicate(), timeout=10)
        except Exception:
            pass

        proc = await asyncio.create_subprocess_exec(
            "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        networks: list[dict] = []
        seen: set[str] = set()
        for line in stdout.decode().strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                ssid = parts[0].strip()
                if not ssid or ssid == "--":
                    continue
                seen.add(ssid)
                networks.append({
                    "ssid": ssid,
                    "signal": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] if len(parts) > 2 else "Open",
                })
        networks.sort(key=lambda n: -n["signal"])
        return web.json_response(networks)

    # ── WiFi connect ──────────────────────────────────────────────

    async def wifi_connect(self, request: web.Request) -> web.Response:
        """Connect to a WiFi network."""
        data = await request.json()
        ssid = data.get("ssid", "").strip()
        password = data.get("password", "").strip()

        if not ssid:
            return web.json_response(
                {"success": False, "message": "No SSID provided"}, status=400,
            )

        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        success = proc.returncode == 0
        return web.json_response({
            "success": success,
            "message": (
                stdout.decode().strip() if success else stderr.decode().strip()
            ),
        })

    # ── Status ────────────────────────────────────────────────────

    async def status(self, request: web.Request) -> web.Response:
        """Return current network and Atlas connection status."""
        wifi_connected = False
        wifi_ssid = ""

        # Check WiFi state
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 4 and parts[1] == "wifi" and parts[2] == "connected":
                    wifi_connected = True
                    wifi_ssid = parts[3]
                    break
        except Exception:
            pass

        # Check Atlas server via mDNS
        atlas_url = None
        if wifi_connected:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "avahi-resolve-host-name", "-4", "atlas-cortex.local",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
                if proc.returncode == 0:
                    host = stdout.decode().strip().split()[-1]
                    atlas_url = f"http://{host}:5100"
            except Exception:
                pass

        return web.json_response({
            "wifi_connected": wifi_connected,
            "wifi_ssid": wifi_ssid,
            "atlas_url": atlas_url,
            "atlas_connected": atlas_url is not None,
        })

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the web server."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("Setup server running on http://0.0.0.0:%d", self.port)

    async def stop(self) -> None:
        """Stop the web server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Setup server stopped")


# ══════════════════════════════════════════════════════════════════
# Embedded HTML — complete, touch-friendly setup page
# ══════════════════════════════════════════════════════════════════

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Atlas — Setup</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  html{font-size:18px;-webkit-text-size-adjust:100%;-webkit-tap-highlight-color:transparent}
  body{
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,sans-serif;
    background:#0a0a1a;color:#e8e8f0;min-height:100vh;
    display:flex;flex-direction:column;align-items:center;
    padding:2rem 1.5rem;overflow-x:hidden;
  }

  /* ── Logo ─────────────────────── */
  .logo{margin-bottom:.8rem;text-align:center}
  .logo svg{width:72px;height:72px}
  .logo h1{font-size:1.6rem;font-weight:700;letter-spacing:.04em;margin-top:.3rem;
    background:linear-gradient(135deg,#64c8ff,#a78bfa);-webkit-background-clip:text;
    -webkit-text-fill-color:transparent;background-clip:text}
  .logo p{font-size:.85rem;color:#8888a0;margin-top:.15rem}

  /* ── Status bar ───────────────── */
  .status-bar{display:flex;gap:1.2rem;margin-bottom:1.5rem;flex-wrap:wrap;justify-content:center}
  .status-item{display:flex;align-items:center;gap:.4rem;font-size:.85rem;color:#8888a0}
  .status-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
  .status-dot.off{background:#444}
  .status-dot.on{background:#34d399;box-shadow:0 0 8px #34d39966}
  .status-dot.searching{background:#fbbf24;animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}

  /* ── Card ──────────────────────── */
  .card{
    background:#12122a;border:1px solid #222244;border-radius:16px;
    width:100%;max-width:480px;padding:1.5rem;margin-bottom:1.2rem;
    box-shadow:0 4px 24px #00000040;
  }
  .card h2{font-size:1.1rem;margin-bottom:1rem;color:#c0c0d8}

  /* ── Network list ─────────────── */
  .network-list{list-style:none;max-height:40vh;overflow-y:auto;
    -webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:#333 transparent}
  .network-list::-webkit-scrollbar{width:6px}
  .network-list::-webkit-scrollbar-thumb{background:#444;border-radius:3px}

  .network-item{
    display:flex;align-items:center;gap:.8rem;
    padding:.85rem 1rem;border-radius:10px;cursor:pointer;
    transition:background .15s;border:2px solid transparent;
    min-height:52px;
  }
  .network-item:hover,.network-item:active{background:#1a1a3a}
  .network-item.selected{border-color:#64c8ff;background:#1a1a3a}

  .signal-bars{display:flex;align-items:flex-end;gap:2px;width:20px;height:18px;flex-shrink:0}
  .signal-bars span{width:4px;border-radius:1px;background:#444;transition:background .2s}
  .signal-bars[data-level="1"] span:nth-child(1){background:#64c8ff}
  .signal-bars[data-level="2"] span:nth-child(1),
  .signal-bars[data-level="2"] span:nth-child(2){background:#64c8ff}
  .signal-bars[data-level="3"] span:nth-child(1),
  .signal-bars[data-level="3"] span:nth-child(2),
  .signal-bars[data-level="3"] span:nth-child(3){background:#64c8ff}
  .signal-bars[data-level="4"] span{background:#64c8ff}

  .network-name{flex:1;font-size:1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .network-lock{font-size:.85rem;color:#8888a0;flex-shrink:0}

  .scan-msg{text-align:center;padding:2rem 0;color:#8888a0;font-size:.95rem}

  /* ── Password input ───────────── */
  .password-section{margin-top:1rem;display:none}
  .password-section.visible{display:block}
  .input-wrap{position:relative;margin-bottom:1rem}
  .input-wrap input{
    width:100%;padding:.9rem 3rem .9rem 1rem;
    background:#0a0a1a;border:1px solid #333355;border-radius:10px;
    color:#e8e8f0;font-size:1rem;outline:none;
    transition:border-color .2s;
  }
  .input-wrap input:focus{border-color:#64c8ff}
  .input-wrap input::placeholder{color:#555}
  .toggle-pw{
    position:absolute;right:.8rem;top:50%;transform:translateY(-50%);
    background:none;border:none;color:#8888a0;cursor:pointer;font-size:1.2rem;
    padding:.3rem;min-width:36px;min-height:36px;display:flex;align-items:center;justify-content:center;
  }

  /* ── Buttons ──────────────────── */
  .btn{
    display:inline-flex;align-items:center;justify-content:center;gap:.5rem;
    width:100%;padding:.9rem 1.5rem;border:none;border-radius:10px;
    font-size:1rem;font-weight:600;cursor:pointer;
    transition:all .2s;min-height:52px;
  }
  .btn-primary{background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff}
  .btn-primary:hover{filter:brightness(1.1)}
  .btn-primary:active{transform:scale(.98)}
  .btn-primary:disabled{opacity:.5;cursor:not-allowed;transform:none}
  .btn-secondary{background:#1a1a3a;color:#a0a0c0;border:1px solid #333355}
  .btn-secondary:hover{background:#222244}

  /* ── Connecting overlay ────────── */
  .overlay{
    position:fixed;inset:0;background:#0a0a1aee;
    display:none;flex-direction:column;align-items:center;justify-content:center;
    z-index:100;gap:1.5rem;padding:2rem;
  }
  .overlay.visible{display:flex}
  .overlay .spinner{
    width:56px;height:56px;border:4px solid #222;border-top-color:#64c8ff;
    border-radius:50%;animation:spin .8s linear infinite;
  }
  @keyframes spin{to{transform:rotate(360deg)}}
  .overlay h2{font-size:1.3rem;text-align:center}
  .overlay p{font-size:.95rem;color:#8888a0;text-align:center;max-width:360px}

  /* ── Error toast ───────────────── */
  .toast{
    position:fixed;bottom:2rem;left:50%;transform:translateX(-50%);
    background:#dc2626;color:#fff;padding:.8rem 1.5rem;border-radius:10px;
    font-size:.9rem;display:none;z-index:200;max-width:90%;text-align:center;
    box-shadow:0 4px 16px #00000060;
  }
  .toast.visible{display:block;animation:slideUp .3s ease-out}
  @keyframes slideUp{from{transform:translateX(-50%) translateY(1rem);opacity:0}
    to{transform:translateX(-50%) translateY(0);opacity:1}}

  /* ── Connected state ──────────── */
  .connected-card{text-align:center;padding:2rem 1.5rem}
  .connected-card .check{font-size:3rem;margin-bottom:.5rem}
  .connected-card h2{font-size:1.3rem;color:#34d399;margin-bottom:.3rem}
  .connected-card p{color:#8888a0;font-size:.95rem}
</style>
</head>
<body>

<!-- ── Logo ─────────────────────────────────────── -->
<div class="logo">
  <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="50" cy="50" r="46" stroke="url(#g1)" stroke-width="3" fill="#12122a"/>
    <path d="M50 20L72 72H58L54 60H46L42 72H28L50 20Z" fill="url(#g2)"/>
    <path d="M46 54H54L50 40L46 54Z" fill="#12122a"/>
    <defs>
      <linearGradient id="g1" x1="0" y1="0" x2="100" y2="100">
        <stop offset="0%" stop-color="#64c8ff"/>
        <stop offset="100%" stop-color="#a78bfa"/>
      </linearGradient>
      <linearGradient id="g2" x1="40" y1="20" x2="60" y2="72">
        <stop offset="0%" stop-color="#64c8ff"/>
        <stop offset="100%" stop-color="#a78bfa"/>
      </linearGradient>
    </defs>
  </svg>
  <h1>Atlas</h1>
  <p>Tablet Setup</p>
</div>

<!-- ── Status bar ──────────────────────────────── -->
<div class="status-bar">
  <div class="status-item">
    <span class="status-dot off" id="dot-wifi"></span>
    <span id="label-wifi">WiFi</span>
  </div>
  <div class="status-item">
    <span class="status-dot off" id="dot-atlas"></span>
    <span id="label-atlas">Atlas Server</span>
  </div>
</div>

<!-- ── Network selection card ──────────────────── -->
<div class="card" id="card-wifi">
  <h2>Select WiFi Network</h2>
  <ul class="network-list" id="network-list">
    <li class="scan-msg" id="scan-msg">Scanning for networks…</li>
  </ul>

  <div class="password-section" id="pw-section">
    <div class="input-wrap">
      <input type="password" id="pw-input" placeholder="Enter WiFi password"
             autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
      <button class="toggle-pw" id="toggle-pw" type="button" aria-label="Show password">👁</button>
    </div>
    <button class="btn btn-primary" id="btn-connect" disabled>
      Connect
    </button>
  </div>

  <button class="btn btn-secondary" id="btn-rescan" style="margin-top:.8rem">
    ↻ Rescan
  </button>
</div>

<!-- ── Connected card (hidden initially) ────────── -->
<div class="card connected-card" id="card-connected" style="display:none">
  <div class="check">✓</div>
  <h2>WiFi Connected</h2>
  <p id="connected-ssid"></p>
  <p style="margin-top:1rem" id="atlas-search-msg">Searching for Atlas server…</p>
</div>

<!-- ── Connecting overlay ─────────────────────── -->
<div class="overlay" id="overlay-connecting">
  <div class="spinner"></div>
  <h2 id="overlay-title">Connecting to WiFi…</h2>
  <p id="overlay-subtitle">This may take a few seconds</p>
</div>

<!-- ── Redirect overlay ──────────────────────── -->
<div class="overlay" id="overlay-redirect">
  <div class="spinner"></div>
  <h2>Atlas Found!</h2>
  <p id="redirect-msg">Connecting to your Atlas server…</p>
</div>

<!-- ── Error toast ────────────────────────────── -->
<div class="toast" id="toast"></div>

<script>
(function() {
  'use strict';

  // ── State ──────────────────────────
  let selectedSSID = '';
  let networks = [];
  let scanTimer = null;
  let statusTimer = null;
  let isConnecting = false;

  // ── DOM refs ───────────────────────
  const $ = id => document.getElementById(id);
  const networkList    = $('network-list');
  const scanMsg        = $('scan-msg');
  const pwSection      = $('pw-section');
  const pwInput        = $('pw-input');
  const btnConnect     = $('btn-connect');
  const btnRescan      = $('btn-rescan');
  const togglePw       = $('toggle-pw');
  const cardWifi       = $('card-wifi');
  const cardConnected  = $('card-connected');
  const overlayConn    = $('overlay-connecting');
  const overlayRedir   = $('overlay-redirect');
  const toast          = $('toast');
  const dotWifi        = $('dot-wifi');
  const dotAtlas       = $('dot-atlas');
  const labelWifi      = $('label-wifi');
  const labelAtlas     = $('label-atlas');

  // ── Helpers ────────────────────────
  function signalLevel(signal) {
    if (signal >= 75) return 4;
    if (signal >= 50) return 3;
    if (signal >= 25) return 2;
    return 1;
  }

  function showToast(msg, duration) {
    toast.textContent = msg;
    toast.classList.add('visible');
    setTimeout(() => toast.classList.remove('visible'), duration || 4000);
  }

  // ── Render networks ────────────────
  function renderNetworks(nets) {
    networks = nets;
    networkList.innerHTML = '';

    if (!nets.length) {
      networkList.innerHTML = '<li class="scan-msg">No networks found. Tap Rescan.</li>';
      return;
    }

    nets.forEach(n => {
      const li = document.createElement('li');
      li.className = 'network-item' + (n.ssid === selectedSSID ? ' selected' : '');
      const level = signalLevel(n.signal);
      const hasLock = n.security && n.security !== '' && n.security !== '--';
      li.innerHTML =
        '<div class="signal-bars" data-level="' + level + '">' +
          '<span style="height:25%"></span>' +
          '<span style="height:50%"></span>' +
          '<span style="height:75%"></span>' +
          '<span style="height:100%"></span>' +
        '</div>' +
        '<span class="network-name">' + escapeHtml(n.ssid) + '</span>' +
        (hasLock ? '<span class="network-lock">🔒</span>' : '');

      li.addEventListener('click', () => selectNetwork(n.ssid, hasLock));
      networkList.appendChild(li);
    });
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function selectNetwork(ssid, needsPassword) {
    selectedSSID = ssid;
    // Re-render to update selection
    renderNetworks(networks);

    if (needsPassword) {
      pwSection.classList.add('visible');
      pwInput.value = '';
      pwInput.focus();
      btnConnect.disabled = false;
    } else {
      pwSection.classList.remove('visible');
      btnConnect.disabled = false;
      // Auto-show connect button for open networks
      pwSection.classList.add('visible');
      pwInput.value = '';
      btnConnect.disabled = false;
    }
  }

  // ── WiFi scan ──────────────────────
  async function scanWifi() {
    try {
      const resp = await fetch('/api/wifi/scan');
      const nets = await resp.json();
      renderNetworks(nets);
    } catch (e) {
      networkList.innerHTML = '<li class="scan-msg">Scan failed. Tap Rescan.</li>';
    }
  }

  // ── WiFi connect ───────────────────
  async function connectWifi() {
    if (!selectedSSID || isConnecting) return;
    isConnecting = true;

    overlayConn.classList.add('visible');
    $('overlay-title').textContent = 'Connecting to ' + selectedSSID + '…';

    try {
      const resp = await fetch('/api/wifi/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ssid: selectedSSID, password: pwInput.value})
      });
      const data = await resp.json();

      overlayConn.classList.remove('visible');
      isConnecting = false;

      if (data.success) {
        cardWifi.style.display = 'none';
        cardConnected.style.display = 'block';
        $('connected-ssid').textContent = 'Connected to ' + selectedSSID;
        dotWifi.className = 'status-dot on';
        labelWifi.textContent = selectedSSID;
        // Start looking for Atlas
        startStatusPolling();
      } else {
        showToast(data.message || 'Connection failed. Check your password.');
      }
    } catch (e) {
      overlayConn.classList.remove('visible');
      isConnecting = false;
      showToast('Connection error. Please try again.');
    }
  }

  // ── Status polling ─────────────────
  async function checkStatus() {
    try {
      const resp = await fetch('/api/status');
      const data = await resp.json();

      // WiFi status
      if (data.wifi_connected) {
        dotWifi.className = 'status-dot on';
        labelWifi.textContent = data.wifi_ssid || 'Connected';
        if (cardWifi.style.display !== 'none') {
          cardWifi.style.display = 'none';
          cardConnected.style.display = 'block';
          $('connected-ssid').textContent = 'Connected to ' + (data.wifi_ssid || 'WiFi');
        }
      }

      // Atlas status
      if (data.atlas_connected && data.atlas_url) {
        dotAtlas.className = 'status-dot on';
        labelAtlas.textContent = 'Atlas Found';
        $('atlas-search-msg').textContent = 'Atlas server found!';

        // Redirect to Atlas avatar
        clearInterval(statusTimer);
        clearInterval(scanTimer);
        overlayRedir.classList.add('visible');
        $('redirect-msg').textContent = 'Loading Atlas interface…';
        setTimeout(() => {
          window.location.href = data.atlas_url + '/avatar#skin=nick';
        }, 2000);
      } else if (data.wifi_connected) {
        dotAtlas.className = 'status-dot searching';
        labelAtlas.textContent = 'Searching…';
      }
    } catch (e) {
      // Silently ignore status check failures
    }
  }

  function startStatusPolling() {
    if (statusTimer) clearInterval(statusTimer);
    checkStatus();
    statusTimer = setInterval(checkStatus, 5000);
  }

  // ── Toggle password visibility ─────
  togglePw.addEventListener('click', () => {
    const isHidden = pwInput.type === 'password';
    pwInput.type = isHidden ? 'text' : 'password';
    togglePw.textContent = isHidden ? '🔒' : '👁';
  });

  // ── Connect button ─────────────────
  btnConnect.addEventListener('click', connectWifi);

  // Allow Enter key in password field
  pwInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') connectWifi();
  });

  // ── Rescan button ──────────────────
  btnRescan.addEventListener('click', () => {
    networkList.innerHTML = '<li class="scan-msg">Scanning for networks…</li>';
    scanWifi();
  });

  // ── Init ───────────────────────────
  scanWifi();
  scanTimer = setInterval(scanWifi, 15000);

  // Start status check immediately to handle already-connected case
  startStatusPolling();
})();
</script>
</body>
</html>
"""
