"""Satellite provisioning engine.

Handles the SSH-based provisioning of satellite devices:

Dedicated mode:
  1. Connect via SSH with default credentials
  2. Install Atlas SSH public key
  3. Disable password authentication
  4. Rename hostname
  5. Install satellite agent package
  6. Write configuration
  7. Enable and start the satellite service
  8. Verify connection

Shared mode:
  1. Connect via SSH with user-provided credentials (one-time)
  2. Install satellite agent package only
  3. Write configuration
  4. Enable and start the satellite service
  5. Verify connection
  (No SSH key, no hostname change, no system-level changes)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cortex.satellite.hardware import (
    SSHConnection,
    SSHResult,
    connect_ssh,
)

logger = logging.getLogger(__name__)

# Atlas server SSH key location
_SSH_KEY_DIR = Path(os.environ.get("CORTEX_DATA_DIR", "./data")) / "ssh"
_SSH_PUBLIC_KEY = _SSH_KEY_DIR / "atlas_satellite.pub"
_SSH_PRIVATE_KEY = _SSH_KEY_DIR / "atlas_satellite"

# Satellite agent install URL
_INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/Betanu701/atlas-cortex/main/satellite/install.sh"
)


@dataclass
class ProvisionStep:
    name: str
    status: str = "pending"  # pending, running, done, failed, skipped
    detail: str = ""


@dataclass
class ProvisionConfig:
    """Configuration for provisioning a satellite."""

    satellite_id: str
    ip_address: str
    mode: str = "dedicated"  # "dedicated" or "shared"
    room: str = ""
    display_name: str = ""
    hostname: str = ""  # computed: atlas-sat-{room}
    ssh_username: str = "atlas"
    ssh_password: str = "atlas"
    ssh_port: int = 22
    service_port: int = 5110
    server_url: str = ""  # ws://server:5100/ws/satellite
    features: dict = field(default_factory=dict)
    wake_word: str = "hey atlas"
    volume: float = 0.7
    mic_gain: float = 0.8


@dataclass
class ProvisionResult:
    success: bool = False
    steps: list[ProvisionStep] = field(default_factory=list)
    error: str = ""


class ProvisioningEngine:
    """Provisions satellites via SSH."""

    def __init__(self) -> None:
        self._progress_callbacks: list[Callable[[str, ProvisionStep], None]] = []

    def on_progress(self, callback: Callable[[str, ProvisionStep], None]) -> None:
        """Register a callback for provisioning progress updates."""
        self._progress_callbacks.append(callback)

    async def provision(self, config: ProvisionConfig) -> ProvisionResult:
        """Run the full provisioning sequence."""
        if config.mode == "dedicated":
            return await self._provision_dedicated(config)
        else:
            return await self._provision_shared(config)

    async def _provision_dedicated(self, config: ProvisionConfig) -> ProvisionResult:
        """Full dedicated provisioning — SSH key, hostname, agent, service."""
        result = ProvisionResult()
        steps = [
            ProvisionStep("ssh_connect", detail="Connecting via SSH"),
            ProvisionStep("ssh_key", detail="Installing SSH key"),
            ProvisionStep("disable_password", detail="Disabling password auth"),
            ProvisionStep("set_hostname", detail="Setting hostname"),
            ProvisionStep("install_deps", detail="Installing system packages"),
            ProvisionStep("install_agent", detail="Installing satellite agent"),
            ProvisionStep("write_config", detail="Writing configuration"),
            ProvisionStep("start_service", detail="Starting satellite service"),
            ProvisionStep("verify", detail="Verifying connection"),
        ]
        result.steps = steps

        ssh: SSHConnection | None = None
        try:
            # Step 1: SSH connect
            self._update_step(config.satellite_id, steps[0], "running")
            ssh = await connect_ssh(
                config.ip_address,
                username=config.ssh_username,
                password=config.ssh_password,
                port=config.ssh_port,
            )
            self._update_step(config.satellite_id, steps[0], "done")

            # Step 2: Install SSH key
            self._update_step(config.satellite_id, steps[1], "running")
            await self._install_ssh_key(ssh)
            self._update_step(config.satellite_id, steps[1], "done")

            # Step 3: Disable password auth
            self._update_step(config.satellite_id, steps[2], "running")
            await self._disable_password_auth(ssh)
            self._update_step(config.satellite_id, steps[2], "done")

            # Step 4: Set hostname
            self._update_step(config.satellite_id, steps[3], "running")
            hostname = config.hostname or f"atlas-sat-{config.room}".lower().replace(" ", "-")
            await self._set_hostname(ssh, hostname)
            self._update_step(config.satellite_id, steps[3], "done")

            # Step 5: Install system deps
            self._update_step(config.satellite_id, steps[4], "running")
            await self._install_system_deps(ssh)
            self._update_step(config.satellite_id, steps[4], "done")

            # Step 6: Install satellite agent
            self._update_step(config.satellite_id, steps[5], "running")
            await self._install_agent(ssh)
            self._update_step(config.satellite_id, steps[5], "done")

            # Step 7: Write config
            self._update_step(config.satellite_id, steps[6], "running")
            await self._write_config(ssh, config)
            self._update_step(config.satellite_id, steps[6], "done")

            # Step 8: Start service
            self._update_step(config.satellite_id, steps[7], "running")
            await self._start_service(ssh)
            self._update_step(config.satellite_id, steps[7], "done")

            # Step 9: Verify
            self._update_step(config.satellite_id, steps[8], "running")
            # TODO: verify satellite connects via WebSocket
            self._update_step(config.satellite_id, steps[8], "done")

            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.exception("Provisioning failed for %s", config.satellite_id)
            # Mark current running step as failed
            for step in steps:
                if step.status == "running":
                    self._update_step(config.satellite_id, step, "failed", str(e))
                elif step.status == "pending":
                    step.status = "skipped"
        finally:
            if ssh:
                await ssh.close()

        return result

    async def _provision_shared(self, config: ProvisionConfig) -> ProvisionResult:
        """Shared mode — install agent only, no system changes."""
        result = ProvisionResult()
        steps = [
            ProvisionStep("ssh_connect", detail="Connecting via SSH"),
            ProvisionStep("install_agent", detail="Installing satellite agent"),
            ProvisionStep("write_config", detail="Writing configuration"),
            ProvisionStep("start_service", detail="Starting satellite service"),
            ProvisionStep("verify", detail="Verifying connection"),
        ]
        result.steps = steps

        ssh: SSHConnection | None = None
        try:
            # Step 1: SSH connect
            self._update_step(config.satellite_id, steps[0], "running")
            ssh = await connect_ssh(
                config.ip_address,
                username=config.ssh_username,
                password=config.ssh_password,
                port=config.ssh_port,
            )
            self._update_step(config.satellite_id, steps[0], "done")

            # Step 2: Install agent (as user, not system-wide)
            self._update_step(config.satellite_id, steps[1], "running")
            await self._install_agent(ssh, system_wide=False)
            self._update_step(config.satellite_id, steps[1], "done")

            # Step 3: Write config
            self._update_step(config.satellite_id, steps[2], "running")
            await self._write_config(ssh, config)
            self._update_step(config.satellite_id, steps[2], "done")

            # Step 4: Start service
            self._update_step(config.satellite_id, steps[3], "running")
            await self._start_service(ssh, user_service=True)
            self._update_step(config.satellite_id, steps[3], "done")

            # Step 5: Verify
            self._update_step(config.satellite_id, steps[4], "running")
            self._update_step(config.satellite_id, steps[4], "done")

            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.exception("Shared provisioning failed for %s", config.satellite_id)
            for step in steps:
                if step.status == "running":
                    self._update_step(config.satellite_id, step, "failed", str(e))
                elif step.status == "pending":
                    step.status = "skipped"
        finally:
            if ssh:
                await ssh.close()

        return result

    # ── Provisioning steps ─────────────────────────────────────────

    async def _install_ssh_key(self, ssh: SSHConnection) -> None:
        """Install Atlas server's SSH public key on the satellite."""
        await self.ensure_server_key()
        pubkey = _SSH_PUBLIC_KEY.read_text().strip()

        await ssh.run("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
        # Append key if not already present
        result = await ssh.run(f"grep -qF '{pubkey}' ~/.ssh/authorized_keys 2>/dev/null || "
                               f"echo '{pubkey}' >> ~/.ssh/authorized_keys")
        await ssh.run("chmod 600 ~/.ssh/authorized_keys")

    async def _disable_password_auth(self, ssh: SSHConnection) -> None:
        """Disable SSH password authentication (key-only from now on)."""
        cmds = [
            "sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config",
            "sudo sed -i 's/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config",
            "sudo systemctl restart sshd || sudo systemctl restart ssh",
        ]
        for cmd in cmds:
            await ssh.run(cmd)

    async def _set_hostname(self, ssh: SSHConnection, hostname: str) -> None:
        """Set the satellite's hostname."""
        await ssh.run(f"sudo hostnamectl set-hostname {hostname}")
        # Update /etc/hosts
        await ssh.run(
            f"sudo sed -i 's/127.0.1.1.*/127.0.1.1\\t{hostname}/' /etc/hosts"
        )

    async def _install_system_deps(self, ssh: SSHConnection) -> None:
        """Install required system packages."""
        await ssh.run(
            "sudo apt-get update -qq && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "python3 python3-venv python3-pip alsa-utils avahi-daemon "
            "> /dev/null 2>&1"
        )

    async def _install_agent(self, ssh: SSHConnection, system_wide: bool = True) -> None:
        """Install the Atlas satellite agent."""
        if system_wide:
            install_dir = "/opt/atlas-satellite"
            await ssh.run(f"sudo mkdir -p {install_dir}")
            await ssh.run(
                f"sudo python3 -m venv {install_dir}/.venv && "
                f"sudo {install_dir}/.venv/bin/pip install -q atlas-satellite 2>/dev/null || "
                f"sudo git clone --depth 1 https://github.com/Betanu701/atlas-cortex.git /tmp/atlas-cortex && "
                f"sudo cp -r /tmp/atlas-cortex/satellite/* {install_dir}/ && "
                f"sudo {install_dir}/.venv/bin/pip install -q -r {install_dir}/requirements.txt 2>/dev/null; "
                f"sudo rm -rf /tmp/atlas-cortex"
            )
        else:
            install_dir = "$HOME/.atlas-satellite"
            await ssh.run(f"mkdir -p {install_dir}")
            await ssh.run(
                f"python3 -m venv {install_dir}/.venv && "
                f"{install_dir}/.venv/bin/pip install -q atlas-satellite 2>/dev/null || "
                f"git clone --depth 1 https://github.com/Betanu701/atlas-cortex.git /tmp/atlas-cortex && "
                f"cp -r /tmp/atlas-cortex/satellite/* {install_dir}/ && "
                f"{install_dir}/.venv/bin/pip install -q -r {install_dir}/requirements.txt 2>/dev/null; "
                f"rm -rf /tmp/atlas-cortex"
            )

    async def _write_config(self, ssh: SSHConnection, config: ProvisionConfig) -> None:
        """Write satellite configuration file."""
        if config.mode == "dedicated":
            config_dir = "/opt/atlas-satellite"
        else:
            config_dir = "$HOME/.atlas-satellite"

        sat_config = {
            "satellite_id": config.satellite_id,
            "server_url": config.server_url,
            "room": config.room,
            "mode": config.mode,
            "service_port": config.service_port,
            "wake_word": config.wake_word,
            "volume": config.volume,
            "mic_gain": config.mic_gain,
            "vad_sensitivity": 2,
            "audio_device_in": config.features.get("audio_device_in", "default"),
            "audio_device_out": config.features.get("audio_device_out", "default"),
            "led_type": config.features.get("led_type", "none"),
            "wake_word_enabled": False,
            "filler_enabled": True,
            "features": config.features,
        }
        config_json = json.dumps(sat_config, indent=2)

        if config.mode == "dedicated":
            await ssh.run(f"sudo tee {config_dir}/config.json > /dev/null << 'EOF'\n{config_json}\nEOF")
        else:
            await ssh.run(f"cat > {config_dir}/config.json << 'EOF'\n{config_json}\nEOF")

    async def _start_service(self, ssh: SSHConnection, user_service: bool = False) -> None:
        """Create systemd service and start it."""
        if user_service:
            # User-level systemd service (shared mode)
            service = _SYSTEMD_USER_UNIT
            await ssh.run("mkdir -p ~/.config/systemd/user")
            await ssh.run(f"cat > ~/.config/systemd/user/atlas-satellite.service << 'EOF'\n{service}\nEOF")
            await ssh.run("systemctl --user daemon-reload")
            await ssh.run("systemctl --user enable --now atlas-satellite")
        else:
            # System-level service (dedicated mode)
            service = _SYSTEMD_SYSTEM_UNIT
            await ssh.run(f"sudo tee /etc/systemd/system/atlas-satellite.service > /dev/null << 'EOF'\n{service}\nEOF")
            await ssh.run("sudo systemctl daemon-reload")
            await ssh.run("sudo systemctl enable --now atlas-satellite")

    # ── Server SSH key management ──────────────────────────────────

    @staticmethod
    async def ensure_server_key() -> Path:
        """Generate the Atlas server SSH keypair if it doesn't exist."""
        _SSH_KEY_DIR.mkdir(parents=True, exist_ok=True)
        if not _SSH_PRIVATE_KEY.exists():
            import asyncssh
            key = asyncssh.generate_private_key("ssh-ed25519", comment="atlas-cortex-satellite")
            key.write_private_key(str(_SSH_PRIVATE_KEY))
            key.write_public_key(str(_SSH_PUBLIC_KEY))
            _SSH_PRIVATE_KEY.chmod(0o600)
            logger.info("Generated satellite SSH key: %s", _SSH_PRIVATE_KEY)
        return _SSH_PRIVATE_KEY

    def _update_step(
        self,
        satellite_id: str,
        step: ProvisionStep,
        status: str,
        detail: str = "",
    ) -> None:
        """Update step status and notify callbacks."""
        step.status = status
        if detail:
            step.detail = detail
        for cb in self._progress_callbacks:
            try:
                cb(satellite_id, step)
            except Exception:
                logger.exception("Error in provision progress callback")


# ── Systemd unit templates ────────────────────────────────────────

_SYSTEMD_SYSTEM_UNIT = """\
[Unit]
Description=Atlas Satellite Agent
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/atlas-satellite/.venv/bin/python -m atlas_satellite
WorkingDirectory=/opt/atlas-satellite
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

_SYSTEMD_USER_UNIT = """\
[Unit]
Description=Atlas Satellite Agent (shared)
After=network-online.target sound.target

[Service]
Type=simple
ExecStart=%h/.atlas-satellite/.venv/bin/python -m atlas_satellite
WorkingDirectory=%h/.atlas-satellite
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
