"""Installation and hardware diagnostics for Raspberry Pi satellites."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .config import SatelliteConfig


@dataclass(frozen=True)
class Diagnostic:
    name: str
    status: str
    detail: str
    required: bool = True

    @property
    def passed(self) -> bool:
        return self.status in ("pass", "info")


def _command_output(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode, output


def _device_check(name: str, path: str, *, required: bool) -> Diagnostic:
    exists = Path(path).exists()
    return Diagnostic(
        name=name,
        status="pass" if exists else ("fail" if required else "warn"),
        detail=f"{path} {'is available' if exists else 'is unavailable'}",
        required=required,
    )


def _audio_listing(command: str) -> tuple[bool, str]:
    if shutil.which(command) is None:
        return False, f"{command} is not installed"
    code, output = _command_output([command, "-l"])
    if code != 0:
        return False, output or f"{command} -l failed with exit code {code}"
    return True, output


def _server_check(server_url: str) -> Diagnostic:
    parsed = urlparse(server_url)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return Diagnostic("server", "fail", f"Invalid WebSocket URL: {server_url}")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=3):
            pass
    except OSError as exc:
        return Diagnostic(
            "server",
            "fail",
            f"Cannot connect to {parsed.hostname}:{port}: {exc}",
        )
    return Diagnostic(
        "server",
        "pass",
        f"TCP connection to {parsed.hostname}:{port} succeeded",
    )


def run_diagnostics(
    config_path: str | Path,
    *,
    check_server: bool = True,
) -> list[Diagnostic]:
    """Run non-destructive installation checks."""
    path = Path(config_path)
    results: list[Diagnostic] = [
        Diagnostic(
            "python",
            "pass" if sys.version_info >= (3, 11) else "fail",
            f"Python {platform.python_version()} (requires 3.11+)",
        ),
        Diagnostic(
            "agent",
            "pass",
            f"Atlas Satellite {__version__}",
        ),
    ]

    if not path.is_file():
        results.append(Diagnostic("config", "fail", f"{path} does not exist"))
        return results
    try:
        config = SatelliteConfig.load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        results.append(Diagnostic("config", "fail", f"{path}: {exc}"))
        return results

    results.append(Diagnostic("config", "pass", f"Loaded {path}"))
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o022:
            results.append(Diagnostic(
                "config_permissions",
                "warn",
                f"{path} is group/world writable ({mode:o})",
                required=False,
            ))
        else:
            results.append(Diagnostic(
                "config_permissions", "pass", f"{path} mode is {mode:o}",
            ))
    except OSError as exc:
        results.append(Diagnostic("config_permissions", "fail", str(exc)))

    model_path = Path("/proc/device-tree/model")
    model = ""
    try:
        model = model_path.read_text(errors="replace").rstrip("\0")
    except OSError:
        model = platform.machine()
    results.append(Diagnostic(
        "platform",
        "info",
        model or "Unknown Linux platform",
        required=False,
    ))

    dependencies = ("aiohttp", "alsaaudio", "websockets", "webrtcvad", "zeroconf")
    missing = [name for name in dependencies if importlib.util.find_spec(name) is None]
    results.append(Diagnostic(
        "dependencies",
        "fail" if missing else "pass",
        f"Missing: {', '.join(missing)}" if missing else "Required Python modules are installed",
    ))

    capture_ok, capture_listing = _audio_listing("arecord")
    expected_card = {
        "respeaker_2mic": ("seeed2micvoicec", "seeed-2mic"),
        "respeaker_4mic": ("seeed4micvoicec", "seeed-4mic", "ac108"),
    }.get(config.hardware_profile, ())
    if capture_ok and expected_card:
        capture_ok = any(token.lower() in capture_listing.lower() for token in expected_card)
    results.append(Diagnostic(
        "capture",
        "pass" if capture_ok else "fail",
        (
            f"Capture device listing contains {config.hardware_profile}"
            if capture_ok and expected_card
            else capture_listing or "No capture device found"
        ),
    ))

    playback_ok, playback_listing = _audio_listing("aplay")
    results.append(Diagnostic(
        "playback",
        "pass" if playback_ok else "fail",
        "At least one playback device is available" if playback_ok else playback_listing,
    ))

    if config.hardware_profile in ("respeaker_2mic", "respeaker_4mic"):
        results.extend([
            _device_check("spi", "/dev/spidev0.0", required=config.led_count > 0),
            _device_check("gpio", "/dev/gpiochip0", required=(
                config.button_enabled or config.hardware_profile == "respeaker_4mic"
            )),
            _device_check("i2c", "/dev/i2c-1", required=True),
        ])

    data_dir = Path(config.data_dir) if config.data_dir else (
        Path("/var/lib/atlas-satellite")
        if config.mode == "dedicated"
        else Path.home() / ".atlas-satellite"
    )
    writable = data_dir.exists() and os.access(data_dir, os.W_OK)
    results.append(Diagnostic(
        "data_directory",
        "pass" if writable else "fail",
        f"{data_dir} {'is writable' if writable else 'is not writable'}",
    ))

    if check_server:
        results.append(_server_check(config.server_url))
    else:
        results.append(Diagnostic(
            "server",
            "info",
            "Server connectivity check skipped",
            required=False,
        ))
    return results


def _default_config_path() -> Path:
    candidates = (
        Path("/etc/atlas-satellite/config.json"),
        Path("/opt/atlas-satellite/config.json"),
        Path.home() / ".atlas-satellite" / "config.json",
    )
    return next((path for path in candidates if path.exists()), candidates[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose an Atlas satellite installation")
    parser.add_argument("--config", default=str(_default_config_path()))
    parser.add_argument("--skip-server", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    results = run_diagnostics(args.config, check_server=not args.skip_server)
    failed = [result for result in results if result.required and not result.passed]
    if args.as_json:
        print(json.dumps({
            "ok": not failed,
            "results": [asdict(result) for result in results],
        }, indent=2))
    else:
        symbols = {"pass": "PASS", "info": "INFO", "warn": "WARN", "fail": "FAIL"}
        for result in results:
            print(f"[{symbols[result.status]}] {result.name}: {result.detail}")
        print("Satellite diagnostics passed." if not failed else (
            f"Satellite diagnostics failed: {len(failed)} required check(s)."
        ))
    raise SystemExit(0 if not failed else 1)


if __name__ == "__main__":
    main()
