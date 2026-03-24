"""Network engineering tools: scanning, HTTP debug, container logs, SSL, firewall.

Provides network diagnostics and infrastructure inspection for the agent tool
system.  Uses only the Python standard library (socket, ssl, subprocess) — no
external dependencies required.
"""

# Module ownership: Agent tool infrastructure — network operations
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_cmd(
    cmd_parts: list[str],
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd_parts,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace") if stdout else "",
        stderr.decode(errors="replace") if stderr else "",
    )


# ---------------------------------------------------------------------------
# Network Scan
# ---------------------------------------------------------------------------


class NetworkScanTool(AgentTool):
    """Network diagnostics: ping, port scan, DNS lookup, traceroute."""

    tool_id = "network_scan"
    description = "Network diagnostics: ping, port scan, DNS lookup, traceroute"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["ping", "port_scan", "dns", "traceroute"],
                "description": "Action to perform",
            },
            "host": {
                "type": "string",
                "description": "Target hostname or IP",
            },
            "port": {
                "type": "integer",
                "description": "Port number (for port_scan single port)",
            },
            "port_range": {
                "type": "string",
                "description": "Port range (e.g., '80-443')",
            },
        },
        "required": ["action", "host"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action = params.get("action", "")
        host = params.get("host", "").strip()
        if not host:
            return ToolResult(success=False, output="", error="host is required")

        if action == "ping":
            return await self._ping(host)
        if action == "port_scan":
            return await self._port_scan(host, params)
        if action == "dns":
            return self._dns(host)
        if action == "traceroute":
            return await self._traceroute(host)
        return ToolResult(
            success=False, output="", error=f"Unknown action: {action}"
        )

    async def _ping(self, host: str) -> ToolResult:
        try:
            rc, out, err = await _run_cmd(
                ["ping", "-c", "4", "-W", "5", host], timeout=30
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Ping timed out")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        return ToolResult(
            success=rc == 0,
            output=out,
            error=err if rc != 0 else "",
            metadata={"host": host},
        )

    async def _port_scan(
        self, host: str, params: dict[str, Any]
    ) -> ToolResult:
        port = params.get("port")
        port_range = params.get("port_range", "")

        ports: list[int] = []
        if port is not None:
            ports = [port]
        elif port_range:
            try:
                start_s, end_s = port_range.split("-", 1)
                start, end = int(start_s), int(end_s)
                if end - start > 1024:
                    return ToolResult(
                        success=False, output="",
                        error="Port range limited to 1024 ports for safety",
                    )
                ports = list(range(start, end + 1))
            except ValueError:
                return ToolResult(
                    success=False, output="",
                    error="Invalid port_range — use 'start-end' (e.g. '80-443')",
                )
        else:
            return ToolResult(
                success=False, output="",
                error="Provide 'port' or 'port_range' for port_scan",
            )

        open_ports: list[int] = []
        closed_ports: list[int] = []

        for p in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((host, p))
                sock.close()
                if result == 0:
                    open_ports.append(p)
                else:
                    closed_ports.append(p)
            except OSError:
                closed_ports.append(p)

        lines = [f"Scan results for {host}:"]
        if open_ports:
            lines.append(f"  Open ports: {', '.join(str(p) for p in open_ports)}")
        if closed_ports and len(closed_ports) <= 20:
            lines.append(
                f"  Closed ports: {', '.join(str(p) for p in closed_ports)}"
            )
        elif closed_ports:
            lines.append(f"  Closed ports: {len(closed_ports)} total")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "host": host,
                "open_ports": open_ports,
                "closed_count": len(closed_ports),
            },
        )

    def _dns(self, host: str) -> ToolResult:
        try:
            results = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            return ToolResult(
                success=False, output="",
                error=f"DNS lookup failed: {exc}",
            )

        seen: set[str] = set()
        lines: list[str] = [f"DNS lookup for {host}:"]
        for family, _, _, _, sockaddr in results:
            addr = sockaddr[0]
            if addr in seen:
                continue
            seen.add(addr)
            family_name = "IPv4" if family == socket.AF_INET else "IPv6"
            lines.append(f"  {family_name}: {addr}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"host": host, "addresses": sorted(seen)},
        )

    async def _traceroute(self, host: str) -> ToolResult:
        try:
            rc, out, err = await _run_cmd(
                ["traceroute", "-m", "20", "-w", "3", host], timeout=60
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Traceroute timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        return ToolResult(
            success=rc == 0,
            output=out or err,
            error="" if rc == 0 else err,
            metadata={"host": host},
        )


# ---------------------------------------------------------------------------
# HTTP Debug
# ---------------------------------------------------------------------------


class HTTPDebugTool(AgentTool):
    """Detailed HTTP request with headers, timing, TLS info."""

    tool_id = "http_debug"
    description = "Detailed HTTP request with headers, timing, TLS info"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to request"},
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "HEAD", "OPTIONS"],
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Custom headers",
            },
            "follow_redirects": {
                "type": "boolean",
                "default": True,
            },
        },
        "required": ["url"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        url = params.get("url", "").strip()
        if not url:
            return ToolResult(success=False, output="", error="url is required")

        method = params.get("method", "GET").upper()
        custom_headers = params.get("headers") or {}
        follow = params.get("follow_redirects", True)

        try:
            import httpx
        except ImportError:
            return ToolResult(
                success=False, output="",
                error="httpx is required for http_debug",
            )

        redirects: list[str] = []

        try:
            t_start = time.monotonic()
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=follow
            ) as client:
                resp = await client.request(
                    method, url, headers=custom_headers
                )
            t_total = time.monotonic() - t_start
        except httpx.TimeoutException:
            return ToolResult(
                success=False, output="", error="Request timed out"
            )
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        # Redirect chain
        for r in resp.history:
            loc = r.headers.get("location", "?")
            redirects.append(f"  {r.status_code} → {loc}")

        resp_headers = dict(resp.headers)
        body_preview = resp.text[:500] if resp.text else "(empty)"

        parts: list[str] = [
            f"HTTP/{resp.http_version} {resp.status_code}",
            f"Method: {method}",
            f"URL: {url}",
            f"Total time: {t_total:.3f}s",
        ]
        if redirects:
            parts.append("Redirect chain:\n" + "\n".join(redirects))
        parts.append(f"Response headers:\n{json.dumps(resp_headers, indent=2)}")
        parts.append(f"Body preview:\n{body_preview}")

        return ToolResult(
            success=resp.is_success,
            output="\n\n".join(parts),
            error="" if resp.is_success else f"HTTP {resp.status_code}",
            metadata={
                "status_code": resp.status_code,
                "total_time": round(t_total, 4),
                "redirects": len(redirects),
            },
        )


# ---------------------------------------------------------------------------
# Container / Service Logs
# ---------------------------------------------------------------------------


class ContainerLogsTool(AgentTool):
    """Tail Docker container or systemd service logs."""

    tool_id = "container_logs"
    description = "Tail Docker container or systemd service logs"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["docker", "systemd"],
            },
            "name": {
                "type": "string",
                "description": "Container name or service name",
            },
            "lines": {
                "type": "integer",
                "description": "Number of lines to show",
                "default": 50,
            },
            "filter": {
                "type": "string",
                "description": "Grep filter pattern",
            },
        },
        "required": ["source", "name"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        source = params.get("source", "")
        name = params.get("name", "").strip()
        lines = params.get("lines", 50)
        grep_filter = params.get("filter", "")

        if not name:
            return ToolResult(success=False, output="", error="name is required")

        if source == "docker":
            cmd = ["docker", "logs", "--tail", str(lines), name]
        elif source == "systemd":
            cmd = [
                "journalctl", "-u", name, "-n", str(lines), "--no-pager",
            ]
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown source: {source}",
            )

        try:
            rc, out, err = await _run_cmd(cmd, timeout=15)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Log fetch timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        combined = out + err
        if grep_filter:
            import re as _re

            try:
                pattern = _re.compile(grep_filter, _re.IGNORECASE)
                combined = "\n".join(
                    line
                    for line in combined.splitlines()
                    if pattern.search(line)
                )
            except _re.error:
                pass  # If filter is invalid, return unfiltered

        if not combined.strip():
            combined = "(no log output)"

        return ToolResult(
            success=rc == 0,
            output=combined,
            error="" if rc == 0 else f"Log command failed (exit {rc})",
            metadata={"source": source, "name": name, "lines": lines},
        )


# ---------------------------------------------------------------------------
# SSL / TLS Check
# ---------------------------------------------------------------------------


class SSLCheckTool(AgentTool):
    """Check SSL/TLS certificate: expiry, chain, protocol."""

    tool_id = "ssl_check"
    description = "Check SSL/TLS certificate: expiry, chain, protocol"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Hostname to check"},
            "port": {"type": "integer", "default": 443},
        },
        "required": ["host"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        host = params.get("host", "").strip()
        port = params.get("port", 443)
        if not host:
            return ToolResult(success=False, output="", error="host is required")

        loop = asyncio.get_running_loop()
        try:
            cert_info, protocol, cipher = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._get_cert_info, host, port
                ),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="SSL connection timed out"
            )
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))

        # Parse expiry
        not_after = cert_info.get("notAfter", "")
        not_before = cert_info.get("notBefore", "")
        subject = dict(x[0] for x in cert_info.get("subject", ()))
        issuer = dict(x[0] for x in cert_info.get("issuer", ()))

        # Days until expiry
        days_left = None
        if not_after:
            try:
                expiry_dt = datetime.strptime(
                    not_after, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                days_left = (expiry_dt - datetime.now(timezone.utc)).days
            except ValueError:
                pass

        parts: list[str] = [
            f"SSL Certificate for {host}:{port}",
            f"  Subject:  {subject.get('commonName', '?')}",
            f"  Issuer:   {issuer.get('organizationName', '?')} "
            f"({issuer.get('commonName', '')})",
            f"  Valid from: {not_before}",
            f"  Valid to:   {not_after}",
        ]
        if days_left is not None:
            status = "OK" if days_left > 30 else "WARNING" if days_left > 0 else "EXPIRED"
            parts.append(f"  Days left:  {days_left} ({status})")
        parts.append(f"  Protocol:   {protocol}")
        parts.append(f"  Cipher:     {cipher}")

        # SAN (Subject Alternative Names)
        san = cert_info.get("subjectAltName", ())
        if san:
            names = [v for t, v in san if t == "DNS"]
            if names:
                parts.append(f"  SANs:       {', '.join(names[:10])}")

        return ToolResult(
            success=True,
            output="\n".join(parts),
            metadata={
                "host": host,
                "port": port,
                "days_left": days_left,
                "protocol": protocol,
                "subject": subject.get("commonName", ""),
                "issuer": issuer.get("organizationName", ""),
            },
        )

    @staticmethod
    def _get_cert_info(
        host: str, port: int
    ) -> tuple[dict[str, Any], str, str]:
        """Blocking SSL handshake — run in executor."""
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert() or {}
                protocol = ssock.version() or "unknown"
                cipher_info = ssock.cipher() or ("unknown", "unknown", 0)
                cipher_str = f"{cipher_info[0]} ({cipher_info[1]})"
        return cert, protocol, cipher_str


# ---------------------------------------------------------------------------
# Firewall Read (read-only)
# ---------------------------------------------------------------------------


class FirewallReadTool(AgentTool):
    """Read firewall rules (iptables/nftables) — read-only."""

    tool_id = "firewall_read"
    description = "Read firewall rules (iptables/nftables) — read-only"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "backend": {
                "type": "string",
                "enum": ["iptables", "nftables", "auto"],
                "default": "auto",
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        backend = params.get("backend", "auto")

        if backend == "auto":
            backend = await self._detect_backend()

        if backend == "nftables":
            cmd = ["nft", "list", "ruleset"]
        elif backend == "iptables":
            cmd = ["iptables", "-L", "-n", "--line-numbers"]
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown backend: {backend}",
            )

        try:
            rc, out, err = await _run_cmd(cmd, timeout=15)
        except asyncio.TimeoutError:
            return ToolResult(
                success=False, output="", error="Firewall query timed out"
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        if rc != 0:
            return ToolResult(
                success=False, output="",
                error=err or f"{backend} command failed (exit {rc})",
            )

        return ToolResult(
            success=True,
            output=out,
            metadata={"backend": backend},
        )

    @staticmethod
    async def _detect_backend() -> str:
        """Detect whether nftables or iptables is available."""
        for name, cmd in [("nftables", ["nft", "--version"]),
                          ("iptables", ["iptables", "--version"])]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode == 0:
                    return name
            except (OSError, asyncio.TimeoutError):
                continue
        return "iptables"  # Default fallback
