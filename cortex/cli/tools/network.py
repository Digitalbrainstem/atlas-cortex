"""Network tools: web search, fetch, API calls, SSH.

Provides HTTP and remote execution capabilities for the agent tool system.
"""

# Module ownership: Agent tool infrastructure — network operations
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace into readable text."""
    # Remove script/style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    # Replace block-level tags with newlines
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*/?>", "\n", text, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebSearchTool(AgentTool):
    """Search the web for information via SearXNG."""

    tool_id = "web_search"
    description = "Search the web for information"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        query = params.get("query", "").strip()
        if not query:
            return ToolResult(success=False, output="", error="query is required")

        searxng_url = os.environ.get("SEARXNG_URL", "").rstrip("/")
        if not searxng_url:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "No search engine configured. Set the SEARXNG_URL environment "
                    "variable to a SearXNG instance (e.g. http://localhost:8888)."
                ),
            )

        try:
            import httpx  # noqa: E402
        except ImportError:
            return ToolResult(
                success=False, output="", error="httpx is required for web search"
            )

        url = f"{searxng_url}/search"
        request_params = {"q": query, "format": "json"}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=request_params)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(
                success=False, output="", error="Search request timed out"
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False, output="",
                error=f"Search returned HTTP {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ToolResult(
                success=False, output="", error="Invalid JSON from search engine"
            )

        results = data.get("results", [])
        if not results:
            return ToolResult(success=True, output="No results found.")

        lines: list[str] = []
        for i, r in enumerate(results[:10], 1):
            title = r.get("title", "(no title)")
            link = r.get("url", "")
            snippet = r.get("content", "")
            lines.append(f"{i}. {title}\n   {link}\n   {snippet}")

        return ToolResult(
            success=True,
            output="\n\n".join(lines),
            metadata={"result_count": len(results)},
        )


class WebFetchTool(AgentTool):
    """Fetch a URL and return its content as readable text."""

    tool_id = "web_fetch"
    description = "Fetch a URL and return its content as text/markdown"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_length": {
                "type": "integer",
                "description": "Maximum characters to return (default 5000)",
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

        max_length = params.get("max_length", 5000)

        try:
            import httpx
        except ImportError:
            return ToolResult(
                success=False, output="", error="httpx is required for web fetch"
            )

        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(success=False, output="", error="Request timed out")
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False, output="",
                error=f"HTTP {exc.response.status_code}: {url}",
            )
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        content_type = resp.headers.get("content-type", "")
        body = resp.text

        if "html" in content_type:
            body = _strip_html(body)

        if len(body) > max_length:
            body = body[:max_length] + "\n\n... (truncated)"

        return ToolResult(
            success=True,
            output=body,
            metadata={"url": url, "content_type": content_type,
                       "length": len(body)},
        )


class APICallTool(AgentTool):
    """Make an HTTP request to an API endpoint."""

    tool_id = "api_call"
    description = "Make an HTTP request to an API endpoint"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method",
            },
            "url": {"type": "string", "description": "Request URL"},
            "headers": {
                "type": "object",
                "description": "Request headers (optional)",
            },
            "body": {
                "type": ["string", "object"],
                "description": "Request body (optional)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30)",
            },
        },
        "required": ["method", "url"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        method = params.get("method", "GET").upper()
        url = params.get("url", "").strip()
        if not url:
            return ToolResult(success=False, output="", error="url is required")

        headers = params.get("headers") or {}
        body = params.get("body")
        timeout = params.get("timeout", 30)

        try:
            import httpx
        except ImportError:
            return ToolResult(
                success=False, output="", error="httpx is required for api_call"
            )

        kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
        if body is not None:
            if isinstance(body, dict):
                kwargs["json"] = body
            else:
                kwargs["content"] = str(body)

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            return ToolResult(success=False, output="", error="Request timed out")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        resp_headers = dict(resp.headers)
        resp_body = resp.text

        output_parts = [
            f"Status: {resp.status_code}",
            f"Headers: {json.dumps(resp_headers, indent=2)}",
            f"Body:\n{resp_body}",
        ]

        return ToolResult(
            success=resp.is_success,
            output="\n\n".join(output_parts),
            error="" if resp.is_success else f"HTTP {resp.status_code}",
            metadata={"status_code": resp.status_code},
        )


class SSHTool(AgentTool):
    """Execute a command on a remote host via SSH."""

    tool_id = "ssh_exec"
    description = "Execute a command on a remote host via SSH"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "Remote hostname or IP"},
            "command": {
                "type": "string",
                "description": "Command to execute remotely",
            },
            "user": {
                "type": "string",
                "description": "SSH user (default: current user or SSH_USER env)",
            },
            "port": {
                "type": "integer",
                "description": "SSH port (default 22)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30)",
            },
        },
        "required": ["host", "command"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        host = params.get("host", "").strip()
        command = params.get("command", "").strip()
        if not host or not command:
            return ToolResult(
                success=False, output="",
                error="host and command are required",
            )

        user = params.get("user") or os.environ.get("SSH_USER", "")
        port = params.get("port", 22)
        timeout = params.get("timeout", 30)

        ssh_args = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-p", str(port),
        ]
        if user:
            ssh_args.extend(["-l", user])

        ssh_args.append(host)
        ssh_args.append(command)

        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return ToolResult(
                success=False, output="",
                error=f"SSH command timed out after {timeout}s",
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        out = stdout.decode(errors="replace") if stdout else ""
        err = stderr.decode(errors="replace") if stderr else ""
        ok = proc.returncode == 0

        return ToolResult(
            success=ok,
            output=out,
            error=err if not ok else "",
            metadata={"returncode": proc.returncode, "host": host},
        )
