"""Security engineering tools: secret scanning, vulnerability checks, permission auditing.

Provides security analysis capabilities for the agent tool system.
"""

# Module ownership: CLI security engineering tools
from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)

# Compile secret-detection patterns once at module level
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key", re.compile(
        r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
        re.IGNORECASE,
    )),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}")),
    ("GitHub Personal Access Token (classic)", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("Generic API Key", re.compile(
        r"(?:api[_-]?key|apikey)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?",
        re.IGNORECASE,
    )),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("JWT Secret", re.compile(
        r"(?:jwt[_-]?secret|jwt[_-]?key)\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?",
        re.IGNORECASE,
    )),
    ("Password in Code", re.compile(
        r"(?:password|passwd|pwd)\s*[=:]\s*['\"]([^'\"]{4,})['\"]",
        re.IGNORECASE,
    )),
    ("Connection String", re.compile(
        r"(?:mysql|postgres|mongodb|redis)://[^\s'\"]{10,}",
        re.IGNORECASE,
    )),
    ("Slack Token", re.compile(r"xox[bpors]-[A-Za-z0-9\-]{10,}")),
    ("Generic Secret", re.compile(
        r"(?:secret|token|credential)\s*[=:]\s*['\"]([^'\"]{8,})['\"]",
        re.IGNORECASE,
    )),
]

# File extensions to skip during scanning
_BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".dat",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".mp3", ".mp4", ".zip", ".gz", ".tar", ".bz2",
    ".db", ".sqlite", ".sqlite3",
})

_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info",
})


def _should_skip(path: Path) -> bool:
    """Check if a file should be skipped during scanning."""
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    for part in path.parts:
        if part in _SKIP_DIRS:
            return True
    return False


def _redact(text: str, max_show: int = 4) -> str:
    """Redact a secret value, showing only first few characters."""
    if len(text) <= max_show:
        return "*" * len(text)
    return text[:max_show] + "*" * (len(text) - max_show)


# ---------------------------------------------------------------------------
# Secret Scan
# ---------------------------------------------------------------------------


class SecretScanTool(AgentTool):
    """Scan files for accidentally committed secrets."""

    tool_id = "secret_scan"
    description = (
        "Scan files for accidentally committed secrets "
        "(API keys, tokens, passwords)"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to scan (default: current directory)",
            },
            "patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional regex patterns to check (optional)",
            },
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        scan_path = Path(
            params.get("path", "")
            or (context or {}).get("cwd", "")
            or "."
        )
        if not scan_path.exists():
            return ToolResult(
                success=False, output="", error=f"Path not found: {scan_path}"
            )

        # Build patterns list
        patterns = list(_SECRET_PATTERNS)
        for extra in params.get("patterns") or []:
            try:
                patterns.append(("Custom", re.compile(extra)))
            except re.error as exc:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid custom pattern '{extra}': {exc}",
                )

        # Collect files
        if scan_path.is_file():
            files = [scan_path]
        else:
            files = [
                f for f in scan_path.rglob("*")
                if f.is_file() and not _should_skip(f)
            ]

        findings: list[dict[str, Any]] = []
        files_scanned = 0

        for fpath in files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue

            files_scanned += 1
            for line_num, line in enumerate(content.splitlines(), 1):
                for pattern_name, regex in patterns:
                    match = regex.search(line)
                    if match:
                        # Use the first capturing group if it exists, else the full match
                        secret_value = match.group(1) if match.lastindex else match.group(0)
                        findings.append({
                            "file": str(fpath),
                            "line": line_num,
                            "pattern": pattern_name,
                            "preview": _redact(secret_value),
                        })

        if not findings:
            return ToolResult(
                success=True,
                output=f"No secrets found ({files_scanned} files scanned).",
                metadata={"files_scanned": files_scanned, "findings": 0},
            )

        lines: list[str] = [
            f"⚠ Found {len(findings)} potential secret(s) in {files_scanned} files:\n"
        ]
        for f in findings:
            lines.append(
                f"  {f['file']}:{f['line']} [{f['pattern']}] {f['preview']}"
            )

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "files_scanned": files_scanned,
                "findings": len(findings),
            },
        )


# ---------------------------------------------------------------------------
# Vulnerability Scan
# ---------------------------------------------------------------------------


class VulnScanTool(AgentTool):
    """Check project dependencies for known vulnerabilities."""

    tool_id = "vuln_scan"
    description = "Check project dependencies for known vulnerabilities"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path (default: current directory)",
            },
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        cwd = (
            params.get("path", "")
            or (context or {}).get("cwd", "")
            or os.getcwd()
        )
        cwd_path = Path(cwd)

        if not cwd_path.is_dir():
            return ToolResult(
                success=False, output="", error=f"Not a directory: {cwd}"
            )

        # Auto-detect project type and run appropriate audit
        results: list[str] = []
        errors: list[str] = []
        detected: list[str] = []

        if (cwd_path / "requirements.txt").is_file() or (cwd_path / "pyproject.toml").is_file():
            detected.append("python")
            out, err = await self._run_audit(
                ["pip", "audit"], cwd, "pip audit"
            )
            results.append(out)
            if err:
                errors.append(err)

        if (cwd_path / "package.json").is_file():
            detected.append("node")
            out, err = await self._run_audit(
                ["npm", "audit", "--json"], cwd, "npm audit"
            )
            results.append(out)
            if err:
                errors.append(err)

        if (cwd_path / "Cargo.toml").is_file():
            detected.append("rust")
            out, err = await self._run_audit(
                ["cargo", "audit"], cwd, "cargo audit"
            )
            results.append(out)
            if err:
                errors.append(err)

        if (cwd_path / "go.sum").is_file():
            detected.append("go")
            out, err = await self._run_audit(
                ["govulncheck", "./..."], cwd, "govulncheck"
            )
            results.append(out)
            if err:
                errors.append(err)

        if not detected:
            return ToolResult(
                success=True,
                output="No supported project files found "
                "(requirements.txt, package.json, Cargo.toml, go.sum).",
                metadata={"detected": []},
            )

        combined_output = "\n\n".join(r for r in results if r)
        combined_errors = "; ".join(errors)

        return ToolResult(
            success=not errors or bool(combined_output),
            output=combined_output or "No vulnerabilities reported.",
            error=combined_errors,
            metadata={"detected": detected},
        )

    async def _run_audit(
        self, cmd: list[str], cwd: str, label: str
    ) -> tuple[str, str]:
        """Run an audit command, returning (output, error)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
        except FileNotFoundError:
            return "", f"{label}: command not found (install {cmd[0]})"
        except asyncio.TimeoutError:
            return "", f"{label}: timed out"
        except OSError as exc:
            return "", f"{label}: {exc}"

        out = stdout.decode(errors="replace") if stdout else ""
        err = stderr.decode(errors="replace") if stderr else ""

        if proc.returncode not in (0, 1):
            # returncode 1 often means "vulnerabilities found"
            return out, f"{label} exited with code {proc.returncode}: {err}"

        header = f"── {label} ──\n"
        return header + (out or "(no output)"), ""


# ---------------------------------------------------------------------------
# Permission Audit
# ---------------------------------------------------------------------------


class PermissionAuditTool(AgentTool):
    """Audit file permissions, find world-writable files, open ports."""

    tool_id = "permission_audit"
    description = (
        "Audit file permissions, find world-writable files, "
        "SUID binaries, open ports"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to audit (default: current directory)",
            },
            "check_type": {
                "type": "string",
                "enum": ["files", "ports", "all"],
                "description": "What to check (default: all)",
            },
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        check_type = params.get("check_type", "all")
        scan_path = Path(
            params.get("path", "")
            or (context or {}).get("cwd", "")
            or "."
        )

        sections: list[str] = []
        issues: list[dict[str, str]] = []

        if check_type in ("files", "all"):
            file_issues = self._check_files(scan_path)
            issues.extend(file_issues)
            if file_issues:
                sections.append("File Permission Issues:")
                for issue in file_issues:
                    sections.append(
                        f"  [{issue['severity']}] {issue['file']}: {issue['issue']}"
                    )
            else:
                sections.append("File Permissions: No issues found.")

        if check_type in ("ports", "all"):
            port_lines, port_issues = await self._check_ports()
            issues.extend(port_issues)
            if port_lines:
                sections.append("Listening Ports:")
                sections.extend(f"  {line}" for line in port_lines)
            else:
                sections.append("Listening Ports: Could not determine.")

        severity_counts = {}
        for issue in issues:
            sev = issue.get("severity", "info")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return ToolResult(
            success=True,
            output="\n".join(sections),
            metadata={
                "issue_count": len(issues),
                "severity_counts": severity_counts,
            },
        )

    def _check_files(self, scan_path: Path) -> list[dict[str, str]]:
        """Find world-writable files, SUID binaries, loose permissions."""
        issues: list[dict[str, str]] = []

        if not scan_path.exists():
            return issues

        # Sensitive file patterns
        sensitive_patterns = [
            "*.pem", "*.key", "*.p12", "*.pfx", ".env", "*.env",
            "id_rsa", "id_ed25519", "id_dsa", ".htpasswd",
        ]

        try:
            for fpath in scan_path.rglob("*"):
                if not fpath.is_file():
                    continue
                if _should_skip(fpath):
                    continue

                try:
                    stat = fpath.stat()
                except OSError:
                    continue

                mode = stat.st_mode
                # World-writable
                if mode & 0o002:
                    issues.append({
                        "file": str(fpath),
                        "issue": f"World-writable (mode {oct(mode)[-3:]})",
                        "severity": "high",
                    })

                # SUID/SGID
                if mode & 0o4000:
                    issues.append({
                        "file": str(fpath),
                        "issue": "SUID bit set",
                        "severity": "high",
                    })
                if mode & 0o2000:
                    issues.append({
                        "file": str(fpath),
                        "issue": "SGID bit set",
                        "severity": "medium",
                    })

                # Sensitive files with loose permissions
                name = fpath.name
                is_sensitive = any(
                    fpath.match(pat) for pat in sensitive_patterns
                )
                if is_sensitive and (mode & 0o077):
                    issues.append({
                        "file": str(fpath),
                        "issue": (
                            f"Sensitive file with loose permissions "
                            f"(mode {oct(mode)[-3:]})"
                        ),
                        "severity": "medium",
                    })
        except PermissionError:
            pass

        return issues

    async def _check_ports(self) -> tuple[list[str], list[dict[str, str]]]:
        """Check for listening ports using /proc/net or ss."""
        lines: list[str] = []
        issues: list[dict[str, str]] = []

        # Try reading /proc/net/tcp for listening sockets
        tcp_path = Path("/proc/net/tcp")
        if tcp_path.is_file():
            try:
                content = tcp_path.read_text()
                for line in content.splitlines()[1:]:  # Skip header
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    # State 0A = LISTEN
                    if parts[3] == "0A":
                        local_addr = parts[1]
                        ip_hex, port_hex = local_addr.split(":")
                        port = int(port_hex, 16)
                        # Convert IP
                        ip_int = int(ip_hex, 16)
                        ip = socket.inet_ntoa(ip_int.to_bytes(4, "little"))
                        lines.append(f"{ip}:{port}")
                        if ip in ("0.0.0.0", "::"):
                            issues.append({
                                "file": f"port {port}",
                                "issue": f"Listening on all interfaces ({ip}:{port})",
                                "severity": "info",
                            })
            except (OSError, ValueError):
                pass

        # Fallback: try ss command
        if not lines:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ss", "-tlnp",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
                if stdout:
                    ss_output = stdout.decode(errors="replace")
                    for line in ss_output.splitlines()[1:]:
                        lines.append(line.strip())
            except (FileNotFoundError, asyncio.TimeoutError, OSError):
                pass

        return lines, issues
