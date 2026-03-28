#!/usr/bin/env python3
"""Atlas Tablet first-boot TUI setup wizard.

Runs on first boot before any packages are installed, so this uses ONLY
the Python standard library (curses, subprocess, json, socket, etc.).

Flow:
  1. WiFi setup      — nmcli scan + connect
  2. Browser install — snap install chromium
  3. Atlas discovery  — mDNS (avahi) then subnet scan fallback
  4. Satellite config — room name, device name, write config, start service

A sentinel file (~/.atlas-setup-complete) prevents the wizard from
running on subsequent boots.
"""

from __future__ import annotations

import curses
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# When run as root (via sudo), --user tells us whose home to use for sentinel
_REAL_USER = "atlas"
for i, arg in enumerate(sys.argv):
    if arg == "--user" and i + 1 < len(sys.argv):
        _REAL_USER = sys.argv[i + 1]
_REAL_HOME = os.path.expanduser(f"~{_REAL_USER}")

SETUP_DONE = os.path.join(_REAL_HOME, ".atlas-setup-complete")
SATELLITE_CONFIG = "/opt/atlas-satellite/config.json"
SATELLITE_SERVICE = "atlas-satellite"
ATLAS_PORT = 5100
TOTAL_STEPS = 4

# Chromium kiosk flags
CHROMIUM_FLAGS = [
    "chromium",
    "--kiosk",
    "--no-first-run",
    "--no-sandbox",
    "--disable-translate",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--noerrdialogs",
    "--disable-component-update",
    "--check-for-update-interval=31536000",
    "--autoplay-policy=no-user-gesture-required",
    "--use-fake-ui-for-media-stream",
]


# ---------------------------------------------------------------------------
# Helpers — subprocess wrappers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a command and return the CompletedProcess."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_privileged(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a command that needs root — skips sudo if already root."""
    if os.geteuid() == 0:
        return _run(cmd, timeout=timeout)
    return _run(["sudo"] + cmd, timeout=timeout)


# ---------------------------------------------------------------------------
# TUI drawing helpers
# ---------------------------------------------------------------------------

def _init_colors() -> None:
    """Set up curses colour pairs."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)     # title
    curses.init_pair(2, curses.COLOR_GREEN, -1)     # success
    curses.init_pair(3, curses.COLOR_RED, -1)       # error
    curses.init_pair(4, curses.COLOR_YELLOW, -1)    # warning / progress
    curses.init_pair(5, curses.COLOR_WHITE, -1)     # normal


def _draw_header(win: curses.window, step: int) -> int:
    """Draw the top banner and return the next available row."""
    h, w = win.getmaxyx()
    title = " Atlas Tablet Setup "
    box_w = min(w - 2, 50)
    pad = (box_w - len(title)) // 2

    row = 1
    win.attron(curses.color_pair(1) | curses.A_BOLD)
    win.addstr(row, 2, "╔" + "═" * box_w + "╗")
    row += 1
    win.addstr(row, 2, "║" + " " * pad + title + " " * (box_w - pad - len(title)) + "║")
    row += 1
    win.addstr(row, 2, "╚" + "═" * box_w + "╝")
    win.attroff(curses.color_pair(1) | curses.A_BOLD)

    row += 2
    step_text = f"Step {step} of {TOTAL_STEPS}"
    win.addstr(row, 2, step_text, curses.color_pair(4) | curses.A_BOLD)
    row += 1
    win.addstr(row, 2, "─" * len(step_text))
    row += 2
    return row


def _draw_status(win: curses.window, row: int, msg: str, ok: bool | None = None) -> int:
    """Draw a status line. ok=True → green ✔, ok=False → red ✘, None → neutral."""
    h, w = win.getmaxyx()
    if ok is True:
        win.addstr(row, 4, "✔ ", curses.color_pair(2) | curses.A_BOLD)
        win.addstr(row, 8, msg, curses.color_pair(2))
    elif ok is False:
        win.addstr(row, 4, "✘ ", curses.color_pair(3) | curses.A_BOLD)
        win.addstr(row, 8, msg, curses.color_pair(3))
    else:
        win.addstr(row, 4, msg, curses.color_pair(5))
    win.refresh()
    return row + 1


def _draw_progress(win: curses.window, row: int, pct: int, label: str = "") -> int:
    """Draw a progress bar."""
    h, w = win.getmaxyx()
    bar_w = min(w - 12, 40)
    filled = int(bar_w * pct / 100)
    bar = "█" * filled + "░" * (bar_w - filled)
    text = f"{label} {bar} {pct}%"
    win.addstr(row, 4, text, curses.color_pair(4))
    win.refresh()
    return row + 1


def _get_input(win: curses.window, row: int, prompt: str, hidden: bool = False) -> str:
    """Prompt for text input. If hidden, echoes asterisks (for passwords)."""
    h, w = win.getmaxyx()
    win.addstr(row, 4, prompt, curses.color_pair(5) | curses.A_BOLD)
    win.refresh()

    curses.echo()
    if hidden:
        curses.noecho()

    col = 4 + len(prompt)
    buf: list[str] = []
    while True:
        ch = win.getch()
        if ch in (curses.KEY_ENTER, 10, 13):
            break
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
                col -= 1
                win.addstr(row, col, " ")
                win.move(row, col)
        elif 32 <= ch < 127:
            buf.append(chr(ch))
            display = "*" if hidden else chr(ch)
            try:
                win.addstr(row, col, display)
            except curses.error:
                pass
            col += 1
        win.refresh()

    curses.noecho()
    return "".join(buf)


def _wait_for_key(win: curses.window, row: int, msg: str = "Press any key to continue...") -> None:
    win.addstr(row, 4, msg, curses.color_pair(4))
    win.refresh()
    win.getch()


# ---------------------------------------------------------------------------
# Step 1 — WiFi Setup
# ---------------------------------------------------------------------------

def _signal_bars(signal: int) -> str:
    """Convert signal percentage to a bar + quality label."""
    if signal >= 70:
        return "████████ (strong)"
    elif signal >= 50:
        return "██████   (good)"
    elif signal >= 30:
        return "███      (fair)"
    else:
        return "█        (weak)"


def _wifi_scan() -> list[dict[str, str | int]]:
    """Scan WiFi and return a list of {ssid, signal, security}."""
    _run(["nmcli", "device", "wifi", "rescan"])
    time.sleep(2)
    result = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
    networks: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0
        security = parts[2] if parts[2] else "Open"
        networks.append({"ssid": ssid, "signal": signal, "security": security})
    networks.sort(key=lambda n: n["signal"], reverse=True)
    return networks


def _wifi_connect(ssid: str, password: str) -> tuple[bool, str]:
    """Connect to a WiFi network. Returns (success, message)."""
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    result = _run(cmd, timeout=30)
    if result.returncode == 0:
        ip = _get_local_ip()
        return True, f"Connected! IP: {ip}"
    return False, result.stderr.strip() or "Connection failed"


def _get_local_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _is_online() -> bool:
    """Quick check — can we reach the internet?"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except Exception:
        return False


def step_wifi(win: curses.window) -> bool:
    """WiFi setup step. Returns True if connected."""
    win.clear()
    row = _draw_header(win, 1)
    win.addstr(row, 4, "WiFi Setup", curses.color_pair(1) | curses.A_BOLD)
    row += 2

    # Check if already connected
    if _is_online():
        ip = _get_local_ip()
        row = _draw_status(win, row, f"Already connected! IP: {ip}", ok=True)
        row += 1
        _wait_for_key(win, row)
        return True

    win.addstr(row, 4, "Scanning for networks...", curses.color_pair(4))
    win.refresh()
    networks = _wifi_scan()

    win.clear()
    row = _draw_header(win, 1)
    win.addstr(row, 4, "WiFi Setup", curses.color_pair(1) | curses.A_BOLD)
    row += 2

    if not networks:
        row = _draw_status(win, row, "No WiFi networks found!", ok=False)
        row += 1
        win.addstr(row, 4, "Options:", curses.color_pair(5))
        row += 1
        win.addstr(row, 6, "1. Retry scan", curses.color_pair(5))
        row += 1
        win.addstr(row, 6, "2. Continue without WiFi (limited setup)", curses.color_pair(5))
        row += 1
        choice = _get_input(win, row + 1, "Enter number: ")
        if choice == "1":
            return step_wifi(win)
        return False

    win.addstr(row, 4, "Available networks:", curses.color_pair(5) | curses.A_BOLD)
    row += 1

    max_show = min(len(networks), 15)
    for i, net in enumerate(networks[:max_show], 1):
        ssid = net["ssid"]
        bars = _signal_bars(int(net["signal"]))
        sec = f" [{net['security']}]" if net["security"] != "Open" else " [Open]"
        line = f"  {i:2d}. {ssid:<24s} {bars}{sec}"
        win.addstr(row, 4, line, curses.color_pair(5))
        row += 1

    row += 1
    win.addstr(row, 4, "  0. Rescan", curses.color_pair(4))
    row += 2

    choice = _get_input(win, row, "Enter number: ")
    if choice == "0":
        return step_wifi(win)

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(networks[:max_show]):
            raise ValueError
    except ValueError:
        row += 1
        row = _draw_status(win, row, "Invalid selection", ok=False)
        row += 1
        _wait_for_key(win, row)
        return step_wifi(win)

    selected = networks[idx]
    ssid = str(selected["ssid"])

    password = ""
    if selected["security"] != "Open":
        row += 1
        password = _get_input(win, row, "Password: ", hidden=True)

    row += 2
    win.addstr(row, 4, f"Connecting to {ssid}...", curses.color_pair(4))
    win.refresh()

    ok, msg = _wifi_connect(ssid, password)
    row += 1
    row = _draw_status(win, row, msg, ok=ok)

    if not ok:
        row += 1
        win.addstr(row, 4, "Options:", curses.color_pair(5))
        row += 1
        win.addstr(row, 6, "1. Try again", curses.color_pair(5))
        row += 1
        win.addstr(row, 6, "2. Pick a different network", curses.color_pair(5))
        row += 1
        retry = _get_input(win, row + 1, "Enter number: ")
        if retry == "2":
            return step_wifi(win)
        return step_wifi(win)

    row += 1
    _wait_for_key(win, row)
    return True


# ---------------------------------------------------------------------------
# Step 2 — Display Stack Install
# ---------------------------------------------------------------------------

def _is_ubuntu_core() -> bool:
    """Detect if running on Ubuntu Core (snap-based OS)."""
    return os.path.exists("/snap/snapd/current") and not os.path.exists("/usr/bin/apt-get")


def _install_snap_with_progress(win: curses.window, progress_row: int,
                                 snap_name: str, pct_start: int, pct_end: int) -> bool:
    """Install a snap with progress updates."""
    _draw_progress(win, progress_row, pct_start, f"Installing {snap_name}...")
    proc = subprocess.Popen(
        ["snap", "install", snap_name],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    pct = pct_start
    while proc.poll() is None:
        pct = min(pct + 2, pct_end - 5)
        _draw_progress(win, progress_row, pct, f"Installing {snap_name}...")
        time.sleep(2.0)
    proc.communicate()
    _draw_progress(win, progress_row, pct_end, f"Installing {snap_name}...")
    return proc.returncode == 0


def step_browser(win: curses.window) -> bool:
    """Install display stack — detects platform and installs accordingly."""
    win.clear()
    row = _draw_header(win, 2)
    win.addstr(row, 4, "Installing Display Stack", curses.color_pair(1) | curses.A_BOLD)
    row += 2

    if _is_ubuntu_core():
        # Ubuntu Core: install kiosk snaps
        win.addstr(row, 4, "Ubuntu Core detected — installing kiosk snaps", curses.color_pair(4))
        row += 2

        progress_row = row
        row = _draw_progress(win, row, 0, "Setting up kiosk...")

        # Install ubuntu-frame (Wayland compositor)
        ok1 = _install_snap_with_progress(win, progress_row, "ubuntu-frame", 0, 40)
        if ok1:
            row = _draw_status(win, row, "ubuntu-frame installed", ok=True)
        else:
            row = _draw_status(win, row, "ubuntu-frame failed", ok=False)
        row += 1

        # Install wpe-webkit-mir-kiosk (web browser)
        ok2 = _install_snap_with_progress(win, progress_row, "wpe-webkit-mir-kiosk", 40, 80)
        if ok2:
            row = _draw_status(win, row, "wpe-webkit-mir-kiosk installed", ok=True)
        else:
            row = _draw_status(win, row, "wpe-webkit-mir-kiosk failed", ok=False)
        row += 1

        # Install network-manager if not present
        nm_check = _run(["snap", "list", "network-manager"])
        if nm_check.returncode != 0:
            ok3 = _install_snap_with_progress(win, progress_row, "network-manager", 80, 95)
            if ok3:
                row = _draw_status(win, row, "network-manager installed", ok=True)
            else:
                row = _draw_status(win, row, "network-manager failed", ok=False)
            row += 1

        _draw_progress(win, progress_row, 100, "Kiosk ready!")
        row += 1
        _wait_for_key(win, row)
        return ok1 and ok2

    else:
        # Classic Ubuntu: install Chromium deb
        check = _run(["which", "chromium"])
        if check.returncode == 0:
            row = _draw_status(win, row, "Chromium already installed", ok=True)
            row += 1
            _wait_for_key(win, row)
            return True

        row = _draw_progress(win, row, 0, "Installing Chromium...")

        _run_privileged(["add-apt-repository", "-y", "ppa:xtradeb/apps"], timeout=60)
        _run_privileged(["apt-get", "update", "-qq"], timeout=120)

        proc = subprocess.Popen(
            ["apt-get", "install", "-y", "-qq", "chromium"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        progress_row = row - 1
        pct = 5
        while proc.poll() is None:
            pct = min(pct + 2, 95)
            _draw_progress(win, progress_row, pct, "Installing Chromium...")
            time.sleep(2.0)
        stdout, stderr = proc.communicate()

        if proc.returncode == 0:
            _draw_progress(win, progress_row, 100, "Installing Chromium...")
            row += 1
            row = _draw_status(win, row, "Chromium installed successfully", ok=True)
        else:
            _draw_progress(win, progress_row, 100, "Installing Chromium...")
            row += 1
            err_msg = stderr.strip()[:60] if stderr else "Install failed"
            row = _draw_status(win, row, f"Install failed: {err_msg}", ok=False)
            row += 1
            win.addstr(row, 4, "You can install manually later:", curses.color_pair(4))
            row += 1
            win.addstr(row, 6, "sudo apt install chromium", curses.color_pair(5))

        row += 1
        _wait_for_key(win, row)
        return proc.returncode == 0


# ---------------------------------------------------------------------------
# Step 3 — Atlas Server Discovery
# ---------------------------------------------------------------------------

def _discover_all_servers() -> list[dict[str, str]]:
    """Find all Atlas servers on the network. Returns list of {ip, source, version}."""
    found: dict[str, dict[str, str]] = {}

    # avahi-browse for _atlas-cortex._tcp
    try:
        result = _run(["avahi-browse", "-t", "-r", "-p", "_atlas-cortex._tcp"], timeout=8)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("="):
                    fields = line.split(";")
                    if len(fields) >= 8:
                        ip = fields[7]
                        if ip and ip not in found:
                            found[ip] = {"ip": ip, "source": "mDNS", "version": ""}
    except Exception:
        pass

    # avahi-resolve for atlas-cortex.local
    try:
        result = _run(["avahi-resolve", "-n", "atlas-cortex.local"], timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                ip = parts[1]
                if ip not in found:
                    found[ip] = {"ip": ip, "source": "mDNS", "version": ""}
    except Exception:
        pass

    # Subnet scan
    local_ip = _get_local_ip()
    if local_ip != "127.0.0.1":
        prefix = ".".join(local_ip.split(".")[:3])
        for last_octet in range(1, 255):
            ip = f"{prefix}.{last_octet}"
            if ip in found:
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.15)
                if s.connect_ex((ip, ATLAS_PORT)) == 0:
                    s.close()
                    import urllib.request
                    req = urllib.request.Request(f"http://{ip}:{ATLAS_PORT}/health")
                    req.add_header("User-Agent", "atlas-setup")
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        if resp.status == 200:
                            found[ip] = {"ip": ip, "source": "scan", "version": ""}
                else:
                    s.close()
            except Exception:
                pass

    # Test each and get version
    for ip, info in found.items():
        ok, version = _test_atlas_connection(f"http://{ip}:{ATLAS_PORT}")
        if ok:
            info["version"] = version

    return [v for v in found.values() if v.get("version")]


def step_discover(win: curses.window) -> str | None:
    """Find Atlas servers on the network, let user pick. Returns base URL or None."""
    win.clear()
    row = _draw_header(win, 3)
    win.addstr(row, 4, "Find Atlas Server", curses.color_pair(1) | curses.A_BOLD)
    row += 2

    win.addstr(row, 4, "Searching for Atlas on your network...", curses.color_pair(4))
    win.refresh()
    row += 2

    servers = _discover_all_servers()

    if len(servers) == 1:
        s = servers[0]
        row = _draw_status(win, row, f"Found: {s['ip']}:{ATLAS_PORT} ({s['source']}, {s['version']})", ok=True)
        row += 1
        _wait_for_key(win, row)
        return f"http://{s['ip']}:{ATLAS_PORT}"

    elif len(servers) > 1:
        win.addstr(row, 4, f"Found {len(servers)} Atlas servers:", curses.color_pair(1) | curses.A_BOLD)
        row += 2
        for i, s in enumerate(servers):
            win.addstr(row, 6, f"  {i + 1}. {s['ip']}:{ATLAS_PORT}", curses.color_pair(1))
            win.addstr(row, 40, f"({s['source']}, {s['version']})", curses.color_pair(5))
            row += 1
        row += 1
        choice = _get_input(win, row, f"Select server (1-{len(servers)}): ")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return f"http://{servers[idx]['ip']}:{ATLAS_PORT}"
        except (ValueError, IndexError):
            pass
        return f"http://{servers[0]['ip']}:{ATLAS_PORT}"

    # No servers found — manual entry
    row = _draw_status(win, row, "No Atlas server found automatically", ok=False)
    row += 2
    win.addstr(row, 4, "Enter Atlas server URL manually:", curses.color_pair(5) | curses.A_BOLD)
    row += 1
    win.addstr(row, 4, "(e.g. http://192.168.1.100:5100)", curses.color_pair(4))
    row += 1
    manual = _get_input(win, row, "URL: ")

    if not manual:
        row += 1
        row = _draw_status(win, row, "No URL entered — skipping server config", ok=False)
        row += 1
        _wait_for_key(win, row)
        return None

    if not manual.startswith("http"):
        manual = f"http://{manual}"
    if ":" not in manual.split("//", 1)[-1]:
        manual = f"{manual}:{ATLAS_PORT}"

    row += 1
    win.addstr(row, 4, "Testing connection...", curses.color_pair(4))
    win.refresh()
    ok, version = _test_atlas_connection(manual)
    row += 1

    if ok:
        row = _draw_status(win, row, f"Atlas {version} responding", ok=True)
    else:
        row = _draw_status(win, row, f"Failed: {version}", ok=False)
        win.addstr(row + 1, 4, "Saving URL anyway — you can fix later.", curses.color_pair(4))

    row += 1
    _wait_for_key(win, row)
    return manual


# ---------------------------------------------------------------------------
# Step 4 — Satellite Setup
# ---------------------------------------------------------------------------

def step_satellite(win: curses.window, server_url: str | None) -> bool:
    """Configure and install the satellite agent. Returns True on success."""
    win.clear()
    row = _draw_header(win, 4)
    win.addstr(row, 4, "Satellite Setup", curses.color_pair(1) | curses.A_BOLD)
    row += 2

    # Room name
    room = _get_input(win, row, "Room name [Living Room]: ") or "Living Room"
    row += 1
    device = _get_input(win, row, "Device name [Atlas Tablet]: ") or "Atlas Tablet"
    row += 2

    # Build config
    if server_url:
        # Convert HTTP URL to WebSocket URL
        ws_url = server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/ws/satellite"
    else:
        ws_url = "ws://localhost:5100/ws/satellite"

    config = {
        "server_url": ws_url,
        "room": room,
        "satellite_id": f"sat-tablet-{room.lower().replace(' ', '-')}",
        "mode": "tablet",
        "device_name": device,
    }

    # Write config
    win.addstr(row, 4, "Writing satellite config...", curses.color_pair(4))
    win.refresh()

    config_dir = os.path.dirname(SATELLITE_CONFIG)
    _run_privileged(["mkdir", "-p", config_dir])

    # Write to temp then move (need sudo for /opt)
    tmp_path = "/tmp/atlas-satellite-config.json"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)
    result = _run_privileged(["cp", tmp_path, SATELLITE_CONFIG])
    os.unlink(tmp_path)

    row += 1
    if result.returncode == 0:
        row = _draw_status(win, row, "Config written", ok=True)
    else:
        row = _draw_status(win, row, f"Config write failed: {result.stderr.strip()[:40]}", ok=False)

    # Enable and start satellite service
    row += 1
    win.addstr(row, 4, "Enabling satellite service...", curses.color_pair(4))
    win.refresh()

    enable_result = _run_privileged(["systemctl", "enable", SATELLITE_SERVICE], timeout=15)
    start_result = _run_privileged(["systemctl", "start", SATELLITE_SERVICE], timeout=15)

    row += 1
    if start_result.returncode == 0:
        row = _draw_status(win, row, "Satellite service started", ok=True)
    else:
        err = start_result.stderr.strip()[:50] if start_result.stderr else "start failed"
        row = _draw_status(win, row, f"Service issue: {err}", ok=False)
        row = _draw_status(win, row, "You can start manually: sudo systemctl start atlas-satellite", ok=None)
        row += 1

    # Summary
    row += 1
    win.addstr(row, 4, "─" * 40, curses.color_pair(1))
    row += 1
    win.addstr(row, 4, "Setup Complete!", curses.color_pair(2) | curses.A_BOLD)
    row += 2
    win.addstr(row, 6, f"Room:   {room}", curses.color_pair(5))
    row += 1
    win.addstr(row, 6, f"Device: {device}", curses.color_pair(5))
    row += 1
    win.addstr(row, 6, f"Server: {ws_url}", curses.color_pair(5))
    row += 2
    win.addstr(row, 4, "Launching kiosk mode...", curses.color_pair(4) | curses.A_BOLD)
    win.refresh()
    time.sleep(2)

    return True


# ---------------------------------------------------------------------------
# Kiosk launcher
# ---------------------------------------------------------------------------

def launch_kiosk() -> None:
    """Launch kiosk display — Chromium on classic, wpe-webkit on Ubuntu Core."""
    config_path = Path(SATELLITE_CONFIG)
    server_url = "http://localhost:5100"
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            ws = cfg.get("server_url", "")
            server_url = ws.replace("ws://", "http://").replace("wss://", "https://")
            server_url = re.sub(r"/ws/satellite$", "", server_url)
        except Exception:
            pass

    kiosk_url = f"{server_url}/avatar#skin=nick"

    if _is_ubuntu_core():
        # Ubuntu Core: configure wpe-webkit-mir-kiosk via snap set
        subprocess.run(["snap", "set", "wpe-webkit-mir-kiosk", f"url={kiosk_url}"],
                       capture_output=True)
        # ubuntu-frame and wpe-webkit run as daemons — just exit, they handle display
        return

    # Classic: launch Chromium kiosk
    cmd = CHROMIUM_FLAGS + [kiosk_url]
    os.execvp("chromium", cmd)


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def _addstr_safe(win: curses.window, row: int, col: int, text: str, attr: int = 0) -> None:
    """addstr that silently ignores writes beyond window boundaries."""
    try:
        win.addstr(row, col, text, attr)
    except curses.error:
        pass


def run_setup(stdscr: curses.window) -> None:
    """Run the full setup wizard inside curses."""
    _init_colors()
    curses.curs_set(0)
    stdscr.keypad(True)

    # Step 1: WiFi
    wifi_ok = step_wifi(stdscr)

    if wifi_ok:
        # Step 2: Browser
        step_browser(stdscr)

        # Step 3: Discover Atlas
        server_url = step_discover(stdscr)

        # Step 4: Satellite config
        step_satellite(stdscr, server_url)
    else:
        # No WiFi — show limited options
        stdscr.clear()
        row = _draw_header(stdscr, 2)
        _draw_status(
            stdscr, row,
            "No internet — skipping browser install and server discovery.",
            ok=False,
        )
        row += 2
        _draw_status(stdscr, row + 1, "Re-run setup after connecting WiFi manually.", ok=None)
        row += 3
        _addstr_safe(
            stdscr, row + 1, 4,
            "Run: python3 /opt/atlas-satellite/atlas-setup-tui.py",
        )
        row += 2
        _wait_for_key(stdscr, row + 1)


def main() -> None:
    # Check if setup is already done
    if os.path.exists(SETUP_DONE):
        launch_kiosk()
        return

    # Run the TUI wizard
    try:
        curses.wrapper(run_setup)
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(1)
    except Exception as exc:
        # Fallback: if curses fails (e.g. no terminal), print the error
        print(f"\nSetup error: {exc}")
        print("You can re-run: python3 /opt/atlas-satellite/atlas-setup-tui.py")
        sys.exit(1)

    # Mark setup as complete
    Path(SETUP_DONE).touch()

    # Launch kiosk
    launch_kiosk()


if __name__ == "__main__":
    main()
