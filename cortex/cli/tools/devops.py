"""DevOps and SRE tools: log analysis, system metrics, incident timelines.

Provides operational insight capabilities for the agent tool system.
"""

# Module ownership: CLI DevOps and SRE tools
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Timestamp patterns for auto-detection
_TS_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # ISO 8601
    ("iso", re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"), "%Y-%m-%dT%H:%M:%S"),
    # Syslog
    ("syslog", re.compile(r"[A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2}"), "%b %d %H:%M:%S"),
    # Nginx / Apache common log
    ("clf", re.compile(r"\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2}"), "%d/%b/%Y:%H:%M:%S"),
    # Python logging default
    ("python", re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"), "%Y-%m-%d %H:%M:%S"),
]

_LEVEL_PATTERNS = re.compile(
    r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|CRITICAL|FATAL|SEVERE|TRACE)\b",
    re.IGNORECASE,
)

_STACK_TRACE_START = re.compile(
    r"^(Traceback|Exception|Error|Caused by|at [\w.$]+\()",
    re.IGNORECASE,
)


def _parse_time_range(spec: str) -> timedelta | None:
    """Parse a human time range like '1h', '24h', '7d' into a timedelta."""
    match = re.match(r"^(\d+)([hHdDmM])$", spec.strip())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    if unit == "m":
        return timedelta(minutes=value)
    return None


def _detect_log_format(line: str) -> str:
    """Detect the log format from a sample line."""
    stripped = line.strip()
    if not stripped:
        return "unknown"
    # JSON log
    if stripped.startswith("{"):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    for fmt_name, pattern, _ in _TS_PATTERNS:
        if pattern.search(stripped):
            return fmt_name
    return "unknown"


def _extract_timestamp(line: str, fmt: str) -> datetime | None:
    """Try to extract a timestamp from a log line."""
    for _, pattern, strp_fmt in _TS_PATTERNS:
        m = pattern.search(line)
        if m:
            try:
                ts_str = m.group(0)
                # Normalise ISO separator
                ts_str = ts_str.replace("T", " ").split(",")[0].split(".")[0]
                fmt_clean = strp_fmt.replace("T", " ")
                return datetime.strptime(ts_str, fmt_clean).replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, IndexError):
                continue
    return None


def _extract_level(line: str) -> str:
    """Extract log level from a line."""
    m = _LEVEL_PATTERNS.search(line)
    if m:
        level = m.group(1).upper()
        if level == "WARNING":
            return "WARN"
        return level
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Log Analyze
# ---------------------------------------------------------------------------


class LogAnalyzeTool(AgentTool):
    """Analyze log files: error frequency, patterns, timeline."""

    tool_id = "log_analyze"
    description = "Analyze log files: error frequency, patterns, timeline"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the log file",
            },
            "pattern": {
                "type": "string",
                "description": "Filter pattern (regex, optional)",
            },
            "time_range": {
                "type": "string",
                "description": "Time range to analyze (e.g., '1h', '24h', '7d')",
            },
        },
        "required": ["path"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        log_path = Path(params.get("path", ""))
        if not log_path.is_file():
            return ToolResult(
                success=False, output="", error=f"Log file not found: {log_path}"
            )

        filter_pattern = None
        if params.get("pattern"):
            try:
                filter_pattern = re.compile(params["pattern"], re.IGNORECASE)
            except re.error as exc:
                return ToolResult(
                    success=False, output="",
                    error=f"Invalid filter pattern: {exc}",
                )

        time_delta = None
        if params.get("time_range"):
            time_delta = _parse_time_range(params["time_range"])

        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        lines = content.splitlines()
        if not lines:
            return ToolResult(
                success=True, output="Log file is empty.",
                metadata={"total_lines": 0},
            )

        # Detect format
        fmt = _detect_log_format(lines[0])

        # Analyze
        level_counts: Counter[str] = Counter()
        error_messages: Counter[str] = Counter()
        total = 0
        filtered = 0
        stack_traces: list[str] = []
        current_stack: list[str] = []
        in_stack = False
        now = datetime.now(tz=timezone.utc)
        cutoff = (now - time_delta) if time_delta else None
        first_ts: datetime | None = None
        last_ts: datetime | None = None

        for line in lines:
            # Time filter
            if cutoff:
                ts = _extract_timestamp(line, fmt)
                if ts and ts < cutoff:
                    continue
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

            # Pattern filter
            if filter_pattern and not filter_pattern.search(line):
                if not in_stack:
                    continue

            total += 1

            # Stack trace detection
            if _STACK_TRACE_START.search(line):
                in_stack = True
                current_stack = [line]
            elif in_stack:
                if line.startswith((" ", "\t")) or not line.strip():
                    current_stack.append(line)
                else:
                    if current_stack:
                        stack_traces.append("\n".join(current_stack))
                    in_stack = False
                    current_stack = []

            level = _extract_level(line)
            level_counts[level] += 1

            if level in ("ERROR", "CRITICAL", "FATAL", "SEVERE"):
                # Extract error message (after the level keyword)
                m = _LEVEL_PATTERNS.search(line)
                if m:
                    msg = line[m.end():].strip()[:120]
                    error_messages[msg] += 1

        if in_stack and current_stack:
            stack_traces.append("\n".join(current_stack))

        # Build report
        sections: list[str] = []
        sections.append(f"Log Analysis: {log_path.name} (format: {fmt})")
        sections.append(f"Lines analyzed: {total}")

        if first_ts and last_ts:
            sections.append(f"Time range: {first_ts} → {last_ts}")

        # Level distribution
        if level_counts:
            sections.append("\nLog Level Distribution:")
            for level in ["CRITICAL", "FATAL", "SEVERE", "ERROR", "WARN",
                          "INFO", "DEBUG", "TRACE", "UNKNOWN"]:
                count = level_counts.get(level, 0)
                if count:
                    sections.append(f"  {level}: {count}")

        # Top errors
        if error_messages:
            sections.append(f"\nTop Errors ({len(error_messages)} unique):")
            for msg, count in error_messages.most_common(10):
                sections.append(f"  [{count}x] {msg}")

        # Stack traces
        if stack_traces:
            sections.append(f"\nStack Traces ({len(stack_traces)} found):")
            for i, trace in enumerate(stack_traces[:3], 1):
                sections.append(f"\n  --- Stack Trace {i} ---")
                for trace_line in trace.splitlines()[:10]:
                    sections.append(f"  {trace_line}")

        return ToolResult(
            success=True,
            output="\n".join(sections),
            metadata={
                "total_lines": total,
                "format": fmt,
                "level_counts": dict(level_counts),
                "unique_errors": len(error_messages),
                "stack_traces": len(stack_traces),
            },
        )


# ---------------------------------------------------------------------------
# Metrics Query
# ---------------------------------------------------------------------------


class MetricsQueryTool(AgentTool):
    """Query system metrics: CPU, memory, disk, network, process stats."""

    tool_id = "metrics_query"
    description = "Query system metrics: CPU, memory, disk, network, process stats"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "enum": ["cpu", "memory", "disk", "network", "process", "all"],
                "description": "Metric type to query (default: all)",
            },
            "pid": {
                "type": "integer",
                "description": "Process ID (for process metric)",
            },
        },
        "required": [],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        metric = params.get("metric", "all")

        sections: list[str] = []
        metadata: dict[str, Any] = {}

        collectors = {
            "cpu": self._cpu,
            "memory": self._memory,
            "disk": self._disk,
            "network": self._network,
            "process": lambda: self._process(params.get("pid")),
        }

        if metric == "all":
            for name, collector in collectors.items():
                if name == "process" and not params.get("pid"):
                    continue
                section, data = collector()
                if section:
                    sections.append(section)
                metadata[name] = data
        elif metric in collectors:
            section, data = collectors[metric]() if metric != "process" \
                else self._process(params.get("pid"))
            if section:
                sections.append(section)
            metadata[metric] = data
        else:
            return ToolResult(
                success=False, output="",
                error=f"Unknown metric: {metric}",
            )

        return ToolResult(
            success=True,
            output="\n\n".join(sections) if sections else "No metrics available.",
            metadata=metadata,
        )

    def _cpu(self) -> tuple[str, dict[str, Any]]:
        """Read CPU info from /proc/stat."""
        data: dict[str, Any] = {}
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            if parts[0] == "cpu" and len(parts) >= 5:
                user, nice, system, idle = (int(x) for x in parts[1:5])
                total = user + nice + system + idle
                usage_pct = round(((total - idle) / total) * 100, 1) if total else 0
                data = {
                    "user": user, "nice": nice, "system": system,
                    "idle": idle, "usage_percent": usage_pct,
                }
                return f"CPU: {usage_pct}% used (user={user}, system={system}, idle={idle})", data
        except (OSError, ValueError, IndexError):
            pass

        # Fallback: load average
        try:
            load = os.getloadavg()
            data = {"load_1m": load[0], "load_5m": load[1], "load_15m": load[2]}
            return f"Load Average: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}", data
        except OSError:
            return "CPU: unable to read metrics", data

    def _memory(self) -> tuple[str, dict[str, Any]]:
        """Read memory info from /proc/meminfo."""
        data: dict[str, Any] = {}
        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        meminfo[key] = int(parts[1])  # in kB

            total = meminfo.get("MemTotal", 0)
            avail = meminfo.get("MemAvailable", 0)
            used = total - avail
            pct = round((used / total) * 100, 1) if total else 0

            data = {
                "total_mb": round(total / 1024, 1),
                "used_mb": round(used / 1024, 1),
                "available_mb": round(avail / 1024, 1),
                "usage_percent": pct,
            }
            return (
                f"Memory: {pct}% used "
                f"({data['used_mb']}MB / {data['total_mb']}MB, "
                f"{data['available_mb']}MB available)"
            ), data
        except (OSError, ValueError, KeyError):
            return "Memory: unable to read metrics", data

    def _disk(self) -> tuple[str, dict[str, Any]]:
        """Read disk usage via os.statvfs."""
        data: dict[str, Any] = {}
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bfree * st.f_frsize
            used = total - free
            pct = round((used / total) * 100, 1) if total else 0

            data = {
                "total_gb": round(total / (1024 ** 3), 1),
                "used_gb": round(used / (1024 ** 3), 1),
                "free_gb": round(free / (1024 ** 3), 1),
                "usage_percent": pct,
            }
            return (
                f"Disk (/): {pct}% used "
                f"({data['used_gb']}GB / {data['total_gb']}GB, "
                f"{data['free_gb']}GB free)"
            ), data
        except OSError:
            return "Disk: unable to read metrics", data

    def _network(self) -> tuple[str, dict[str, Any]]:
        """Read network stats from /proc/net/dev."""
        data: dict[str, Any] = {}
        try:
            ifaces: dict[str, dict[str, int]] = {}
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:  # Skip headers
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    name = parts[0].rstrip(":")
                    if name == "lo":
                        continue
                    ifaces[name] = {
                        "rx_bytes": int(parts[1]),
                        "rx_packets": int(parts[2]),
                        "tx_bytes": int(parts[9]),
                        "tx_packets": int(parts[10]),
                    }

            if not ifaces:
                return "Network: no interfaces found", data

            lines = ["Network:"]
            for name, stats in ifaces.items():
                rx_mb = round(stats["rx_bytes"] / (1024 ** 2), 1)
                tx_mb = round(stats["tx_bytes"] / (1024 ** 2), 1)
                lines.append(f"  {name}: RX {rx_mb}MB / TX {tx_mb}MB")
            data = ifaces
            return "\n".join(lines), data
        except (OSError, ValueError):
            return "Network: unable to read metrics", data

    def _process(self, pid: int | None) -> tuple[str, dict[str, Any]]:
        """Read process info from /proc/{pid}/status."""
        data: dict[str, Any] = {}
        if not pid:
            return "Process: pid is required", data

        status_path = Path(f"/proc/{pid}/status")
        if not status_path.is_file():
            return f"Process {pid}: not found", data

        try:
            info: dict[str, str] = {}
            for line in status_path.read_text().splitlines():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    info[parts[0].strip()] = parts[1].strip()

            data = {
                "name": info.get("Name", "unknown"),
                "state": info.get("State", "unknown"),
                "pid": pid,
                "ppid": info.get("PPid", ""),
                "threads": info.get("Threads", ""),
                "vm_rss_kb": info.get("VmRSS", "0 kB").split()[0],
            }
            return (
                f"Process {pid} ({data['name']}): state={data['state']}, "
                f"threads={data['threads']}, RSS={data['vm_rss_kb']}kB"
            ), data
        except (OSError, IndexError, ValueError):
            return f"Process {pid}: unable to read status", data


# ---------------------------------------------------------------------------
# Incident Timeline
# ---------------------------------------------------------------------------


class IncidentTimelineTool(AgentTool):
    """Build an incident timeline from logs, git commits, and deploy events."""

    tool_id = "incident_timeline"
    description = "Build an incident timeline from logs, git commits, and deploy events"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "start_time": {
                "type": "string",
                "description": "Start time (ISO 8601 or relative like '2h ago')",
            },
            "end_time": {
                "type": "string",
                "description": "End time (ISO 8601 or 'now', default: now)",
            },
            "sources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["logs", "git", "docker"],
                },
                "description": "Event sources to include (default: all)",
            },
            "log_path": {
                "type": "string",
                "description": "Path to log file (for logs source)",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (for git source)",
            },
        },
        "required": ["start_time"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        start_str = params.get("start_time", "")
        if not start_str:
            return ToolResult(
                success=False, output="", error="start_time is required"
            )

        start_time = self._parse_time(start_str)
        end_str = params.get("end_time", "now")
        end_time = self._parse_time(end_str) if end_str != "now" else datetime.now(tz=timezone.utc)

        if not start_time:
            return ToolResult(
                success=False, output="",
                error=f"Could not parse start_time: {start_str}",
            )
        if not end_time:
            return ToolResult(
                success=False, output="",
                error=f"Could not parse end_time: {end_str}",
            )

        sources = params.get("sources") or ["logs", "git", "docker"]
        cwd = params.get("cwd") or (context or {}).get("cwd") or os.getcwd()
        log_path = params.get("log_path", "")

        events: list[dict[str, Any]] = []

        if "git" in sources:
            events.extend(await self._git_events(cwd, start_time, end_time))

        if "logs" in sources and log_path:
            events.extend(self._log_events(log_path, start_time, end_time))

        if "docker" in sources:
            events.extend(await self._docker_events(start_time))

        # Sort chronologically
        events.sort(key=lambda e: e.get("time", ""))

        if not events:
            return ToolResult(
                success=True,
                output="No events found in the specified time range.",
                metadata={"event_count": 0},
            )

        # Format timeline
        lines: list[str] = [
            f"Incident Timeline: {start_time.isoformat()} → {end_time.isoformat()}",
            f"Events: {len(events)}",
            "",
        ]
        for event in events:
            ts = event.get("time", "?")
            source = event.get("source", "?")
            desc = event.get("description", "")
            lines.append(f"  [{ts}] ({source}) {desc}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "event_count": len(events),
                "sources": list({e["source"] for e in events}),
            },
        )

    def _parse_time(self, spec: str) -> datetime | None:
        """Parse a time specification (ISO 8601 or relative)."""
        spec = spec.strip()
        if spec == "now":
            return datetime.now(tz=timezone.utc)

        # Relative: "2h ago", "30m ago"
        m = re.match(r"(\d+)([hHdDmM])\s*ago$", spec)
        if m:
            delta = _parse_time_range(m.group(1) + m.group(2))
            if delta:
                return datetime.now(tz=timezone.utc) - delta

        # ISO 8601
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(spec, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        return None

    async def _git_events(
        self, cwd: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Get git commits in the time range."""
        events: list[dict[str, Any]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "--no-pager", "log",
                f"--since={start.isoformat()}",
                f"--until={end.isoformat()}",
                "--format=%aI|%an|%s",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            return events

        if stdout:
            for line in stdout.decode(errors="replace").splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    events.append({
                        "time": parts[0],
                        "source": "git",
                        "description": f"commit by {parts[1]}: {parts[2]}",
                    })

        return events

    def _log_events(
        self, log_path: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Extract error/critical events from a log file in the time range."""
        events: list[dict[str, Any]] = []
        path = Path(log_path)
        if not path.is_file():
            return events

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return events

        for line in content.splitlines():
            level = _extract_level(line)
            if level not in ("ERROR", "CRITICAL", "FATAL", "SEVERE"):
                continue

            ts = _extract_timestamp(line, "")
            if ts and start <= ts <= end:
                events.append({
                    "time": ts.isoformat(),
                    "source": "logs",
                    "description": f"[{level}] {line[:200]}",
                })

        return events

    async def _docker_events(
        self, start: datetime
    ) -> list[dict[str, Any]]:
        """Get Docker events since start time."""
        events: list[dict[str, Any]] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "events",
                f"--since={start.isoformat()}",
                "--until=0s",  # Now
                "--format", "{{.Time}} {{.Type}} {{.Action}} {{.Actor.Attributes.name}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return events

        if stdout:
            for line in stdout.decode(errors="replace").splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    events.append({
                        "time": parts[0],
                        "source": "docker",
                        "description": " ".join(parts[1:]),
                    })

        return events
